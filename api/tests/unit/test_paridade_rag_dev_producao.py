"""Paridade da configuracao de RAG entre DEV (`docker-compose.yml`) e
producao (`infra/*.tf`).

Motivacao (o bug que originou este arquivo): o servico `api` do compose - o
equivalente local da Lambda `musclefit-api-core` - era o unico que nao montava
`rag/markdown` nem definia `RAG_MARKDOWN_DIR`. Ele caia no default `/app/rag`,
que nao existe no container, e `GET /training-types/rag-folders` devolvia `[]`.
Em producao nada disso acontecia (a api-core hidrata o RAG do S3 -
`infra/lambda_api_core.tf` + `app/rag_storage.py::ensure_rag_local`), entao o
campo "Fonte de RAG" do modal de Periodizacao aparecia vazio SO em DEV, sem
erro nenhum na tela.

Os dois lados sao verificados:

1. todo servico do compose que roda o pacote `app/` com rotas/handlers de RAG
   tem o bind mount read-only e a env var;
2. o mapa DEV x producao esta completo: se uma Lambda nova passar a receber
   `RAG_MARKDOWN_DIR` em `infra/` sem que o servico local equivalente seja
   registrado aqui, o CI quebra.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app import rag_chunking as rc
from tests.support import compose_services as cs
from tests.support import infra_rag

# Caminho do bind mount de RAG em DEV. Vem de `lambda-ai` (e da antiga `lambda-reports`),
# que ja o usavam antes desta correcao - os servicos novos seguiram o padrao
# existente em vez de inventar um caminho proprio.
RAG_MOUNT = "./api/privado/rag/markdown:/var/task/rag:ro"
RAG_ENV_LINE = "RAG_MARKDOWN_DIR: /var/task/rag"


@pytest.fixture(autouse=True)
def _sem_s3(monkeypatch):
    """`ensure_rag_local()` e chamado por `list_training_type_rag_folders()`;
    sem bucket ele so devolve o diretorio local, que e o que estes testes
    exercitam. Blindagem contra um RAG_S3_BUCKET vazado do ambiente."""
    monkeypatch.delenv("RAG_S3_BUCKET", raising=False)

# servico do compose -> Lambdas de producao que ele representa.
#
# `lambda-ai` hospeda TRES handlers no mesmo emulador (ver
# LAMBDA_LOCAL_FUNCTIONS no compose), por isso mapeia para tres funcoes.
SERVICOS_COM_RAG: dict[str, tuple[str, ...]] = {
    "api": ("musclefit-api-core",),
    "api-engines": ("musclefit-engines",),
    "lambda-ai": (
        "musclefit-plan-generator",
        "musclefit-cardio-agent",
        "musclefit-workout-ai",
    ),
}


@pytest.mark.parametrize("servico", sorted(SERVICOS_COM_RAG))
def test_servico_de_dev_monta_o_rag(servico):
    """Sem o mount, tudo que le RAG neste servico degrada para vazio em
    silencio - foi exatamente o que aconteceu com o `api`."""
    bloco = cs.service_block(servico)
    assert RAG_MOUNT in bloco, f"servico '{servico}' sem o bind mount de RAG ({RAG_MOUNT})"
    assert RAG_ENV_LINE in bloco, f"servico '{servico}' sem {RAG_ENV_LINE}"


def test_mapa_dev_x_producao_esta_completo():
    """Se `infra/` der RAG a uma Lambda nova, o servico local equivalente
    precisa ser registrado (e configurado) aqui na MESMA PR. Os dois lados
    importam: sobrar tambem sinaliza um mapa desatualizado."""
    if not infra_rag.infra_dir().is_dir():
        pytest.skip("infra/ ausente neste checkout")
    esperado = {funcao for funcoes in SERVICOS_COM_RAG.values() for funcao in funcoes}
    assert infra_rag.lambdas_com_rag() == esperado


def test_conteudo_de_rag_versionado_existe_no_repo():
    """O bind mount so resolve o problema porque a origem esta versionada -
    a mesma arvore que `infra/s3_rag.tf` publica no bucket."""
    tipos_treino = cs.ROOT / "api" / "privado" / "rag" / "markdown" / rc.TIPOS_TREINO_DIRNAME
    assert tipos_treino.is_dir()
    assert [p.name for p in tipos_treino.iterdir() if p.is_dir()]


def test_material_de_objetivo_versionado_e_publicado_em_producao():
    """M97 F5: cada pasta de RAG de objetivo (`training_goal.GOAL_RAG_FOLDERS`)
    existe em `api/privado/rag/markdown/OBJETIVOS/`, que o bind mount do DEV ja cobre (a
    arvore inteira) e que `infra/s3_rag.tf` publica no bucket de producao: o
    `fileset` e `**/*.md` (subpastas inclusas) e `rag_storage.ensure_rag_local`
    baixa o prefixo recursivamente. DEV e producao leem a mesma arvore."""
    from app.domain.training_goal import GOAL_RAG_FOLDERS

    raiz = cs.ROOT / "api" / "privado" / "rag" / "markdown" / rc.GOAL_RAG_DIRNAME
    for folder in (f for f in GOAL_RAG_FOLDERS.values() if f):
        assert list((raiz / folder).glob("*.md")), f"{rc.GOAL_RAG_DIRNAME}/{folder} sem .md"
    if not infra_rag.infra_dir().is_dir():
        pytest.skip("infra/ ausente neste checkout")
    s3_rag = (infra_rag.infra_dir() / "s3_rag.tf").read_text(encoding="utf-8")
    assert 'rag_markdown_dir   = "${path.module}/../api/privado/rag/markdown"' in s3_rag
    assert 'fileset(local.rag_markdown_dir, "**/*.md")' in s3_rag


# ---------------------------------------------------------------------------
# Degradacao visivel (a funcao continua devolvendo [], mas nao em silencio)
# ---------------------------------------------------------------------------


def test_lista_as_pastas_quando_o_rag_esta_montado(monkeypatch):
    """Com RAG_MARKDOWN_DIR apontando para o conteudo real do repo - o que o
    bind mount faz dentro do container - o endpoint enxerga as pastas."""
    monkeypatch.setattr(rc, "RAG_MARKDOWN_DIR", cs.ROOT / "api" / "privado" / "rag" / "markdown")
    assert rc.list_training_type_rag_folders()


def test_rag_dir_inexistente_loga_error_e_nao_levanta(monkeypatch, caplog, tmp_path):
    """Diretorio de RAG ausente = configuracao errada, nao conteudo faltando:
    sai em ERROR citando RAG_MARKDOWN_DIR. Continua devolvendo [] (a
    degradacao e intencional; quebrar a tela seria pior)."""
    ausente = tmp_path / "sem-rag"
    monkeypatch.setattr(rc, "RAG_MARKDOWN_DIR", ausente)
    with caplog.at_level(logging.DEBUG, logger=rc.logger.name):
        assert rc.list_training_type_rag_folders() == []
    erros = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert erros, "diretorio de RAG inexistente precisa sair em ERROR"
    assert "RAG_MARKDOWN_DIR" in erros[-1].getMessage()
    assert str(ausente) in erros[-1].getMessage()


def test_apenas_tipos_treino_ausente_segue_em_warning(monkeypatch, caplog, tmp_path):
    """O outro caso: o RAG esta montado, so nao ha TIPOS_TREINO. Isso e
    conteudo, nao configuracao - WARNING, sem ERROR."""
    rag_dir = tmp_path / "rag"
    rag_dir.mkdir()
    monkeypatch.setattr(rc, "RAG_MARKDOWN_DIR", rag_dir)
    with caplog.at_level(logging.DEBUG, logger=rc.logger.name):
        assert rc.list_training_type_rag_folders() == []
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r for r in caplog.records if r.levelno == logging.WARNING]


def test_helper_de_compose_enxerga_todos_os_servicos():
    """Guarda do proprio parser: se ele parar de achar os servicos, os testes
    acima passariam a verificar um bloco vazio."""
    nomes = cs.service_names()
    assert set(SERVICOS_COM_RAG).issubset(set(nomes))
    for servico in SERVICOS_COM_RAG:
        assert cs.service_block(servico).strip()


def test_nenhum_arquivo_de_infra_foi_tocado_por_engano():
    """Esta correcao e exclusivamente de DEV. Marcador barato de intencao: o
    caminho de producao continua sendo o S3 em `/tmp/rag`."""
    if not infra_rag.infra_dir().is_dir():
        pytest.skip("infra/ ausente neste checkout")
    api_core = Path(infra_rag.infra_dir() / "lambda_api_core.tf").read_text(encoding="utf-8")
    assert 'RAG_MARKDOWN_DIR = "/tmp/rag"' in api_core
    assert "RAG_S3_BUCKET" in api_core


# ---------------------------------------------------------------------------
# M103 P0: o RAG mora no submodulo privado `api/privado` (muscle_fit_privado)
# ---------------------------------------------------------------------------


def test_s3_rag_trava_fileset_vazio_e_mantem_as_keys():
    """Sem o submodulo o fileset fica vazio e o plan apagaria todo o RAG do S3
    de producao. A precondition no bucket faz o plan falhar; as keys continuam
    `markdown/<caminho relativo a rag/markdown>` (iguais as de antes do M103)."""
    if not infra_rag.infra_dir().is_dir():
        pytest.skip("infra/ ausente neste checkout")
    s3_rag = (infra_rag.infra_dir() / "s3_rag.tf").read_text(encoding="utf-8")
    assert "condition     = length(local.rag_markdown_files) > 0" in s3_rag
    assert "for_each = local.rag_markdown_files" in s3_rag
    assert 'key          = "markdown/${each.value}"' in s3_rag
    assert "../rag/markdown" not in s3_rag


def test_submodulo_privado_presente_e_nao_vazio():
    """Checkout sem `submodules` (ou clone sem `submodule update --init`)
    deixaria a guarda e os testes de RAG varrendo um diretorio vazio. Falha
    alto em vez de passar em silencio."""
    rag = cs.ROOT / "api" / "privado" / "rag" / "markdown"
    assert list(rag.glob("*.md")), "api/privado/rag/markdown vazio: rode `submodule update --init api/privado`"


@pytest.mark.parametrize(
    "dockerignore", ["Dockerfile.dockerignore", "Dockerfile.ai.dockerignore", "Dockerfile.engines.dockerignore"]
)
def test_rag_privado_fora_das_imagens_de_producao(dockerignore):
    """O RAG nao entra em imagem nenhuma (as Lambdas leem do S3): as
    allowlists de producao comecam com `*` e, do submodulo `privado`, liberam
    SO `privado/prompts` (templates dos prompts, M103 P1), sem o README dele.
    O CI confere a imagem de verdade (`ci-docker-images.yml`, "RAG privado
    fora da imagem", e o passo 2b do smoke)."""
    linhas = [
        linha.strip()
        for linha in (cs.ROOT / "api" / dockerignore).read_text(encoding="utf-8").splitlines()
        if linha.strip() and not linha.strip().startswith("#")
    ]
    assert linhas[0] == "*"
    assert [linha for linha in linhas if linha.startswith("!") and "privado" in linha] == ["!privado/prompts"]
    assert "privado/prompts/README.md" in linhas[linhas.index("!privado/prompts") :]


@pytest.mark.parametrize(
    ("dockerfile", "destino"),
    [("Dockerfile", "/app"), ("Dockerfile.ai", "/var/task"), ("Dockerfile.engines", "/app")],
)
def test_imagens_copiam_so_os_prompts_do_submodulo(dockerfile, destino):
    """M103 P1: `app/prompt_store.py` le `<pacote>/../privado/prompts`; a
    imagem copia SO essa pasta do submodulo, ao lado do `app/`."""
    texto = (cs.ROOT / "api" / dockerfile).read_text(encoding="utf-8")
    copias = [linha.split() for linha in texto.splitlines() if linha.startswith("COPY ") and "privado" in linha]
    assert copias == [["COPY", "privado/prompts", f"{destino}/privado/prompts"]]
    assert f"COPY app {destino}/app" in texto


def test_dev_monta_os_prompts_no_caminho_do_prompt_store():
    """DEV = producao (anti-vazamento): os servicos que rodam o `app/` montam
    os templates no mesmo caminho que a imagem usa."""
    compose = (cs.ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert compose.count("- ./api/privado/prompts:/app/privado/prompts:ro") == 2  # api, api-engines
    assert compose.count("- ./api/privado/prompts:/var/task/privado/prompts:ro") == 1  # lambda-ai
