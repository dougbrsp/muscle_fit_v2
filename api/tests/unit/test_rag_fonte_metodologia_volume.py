"""Fase 4 / PR A - a metodologia de volume por grupo (EXG, Leandro Twin) como
fonte de RAG selecionavel.

O PR nao tem codigo novo: ele acrescenta uma pasta em
`rag/markdown/TIPOS_TREINO/`, que e o unico mecanismo de fonte selecionavel
que ja existe (`list_training_type_rag_folders` descobre por `iterdir`). O que
precisa de teste, entao, nao e comportamento novo - e o CONTEUDO respeitar os
contratos que o codigo ja assume, porque todos falham em silencio:

1. **Tamanho.** `periodization/context.py::load_rag_text` TRUNCA a fonte em
   `MAX_RAG_FOLDER_CHARS` (20.000). Um arquivo maior nao quebra nada: ele entra
   pela metade no prompt, e a metade que some e justamente o fim do documento
   (tabela consolidada, checklist, resumo). Este e o teste que importa.

2. **Formato de chunk.** O arquivo chegou do export com a pontuacao escapada
   (`## EXG\\-001 — ...`), e com a barra invertida NENHUM dos 34 chunks casava
   com `CHUNK_HEADER_RE`. Consequencia real: no fallback de
   `discover_training_type_files()` (geracao legada, `has_training_type=False`)
   um arquivo sem chunks reconhecidos entra INTEIRO em toda ficha - o mesmo
   mecanismo que levou o `CARDIO.md` para `EXCLUDED_FILES` depois de estourar o
   contexto da OpenAI.

3. **Nome da pasta.** E o valor que viaja em `rag_sources` e que o endpoint
   valida contra a lista real (422 para inexistente).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import rag_chunking as rc
from app.models import PeriodizationDraftIn
from app.periodization.context import MAX_RAG_FOLDER_CHARS
from app.workout_plan_templates import _validate_periodization_request
from tests.support import compose_services as cs

PASTA = "6. Volume por Grupo Muscular (Leandro Twin)"
ARQUIVO = "EXERCICIOS_GRUPOS.md"
PREFIXO_CHUNK = "EXG-"
MINIMO_DE_CHUNKS = 30


@pytest.fixture(autouse=True)
def _rag_do_repo(monkeypatch):
    """Aponta o modulo para a arvore versionada do repo - a mesma que o bind
    mount entrega em DEV e que `infra/s3_rag.tf` publica no bucket. E tambem
    blindagem contra um `RAG_S3_BUCKET` vazado do ambiente, ja que
    `ensure_rag_local()` e chamado por `list_training_type_rag_folders()`."""
    monkeypatch.delenv("RAG_S3_BUCKET", raising=False)
    monkeypatch.setattr(rc, "RAG_MARKDOWN_DIR", cs.ROOT / "api" / "privado" / "rag" / "markdown")


def _texto_da_pasta() -> str:
    return rc.load_rag_folder_text(PASTA)


# ---------------------------------------------------------------------------
# A pasta e descoberta e selecionavel
# ---------------------------------------------------------------------------


def test_pasta_aparece_entre_as_fontes_selecionaveis():
    """Se o nome mudar (numero lider, acentuacao, parenteses), ele deixa de
    aparecer no modal E qualquer Modelo que ja tenha a fonte salva passa a
    receber 422 na regeracao."""
    assert PASTA in rc.list_training_type_rag_folders()


def test_ordenacao_das_fontes_segue_o_inteiro_lider():
    """`list_training_type_rag_folders` ordena pelo inteiro lider, nao por
    string. Com a pasta 6 no acervo a diferenca ainda nao aparece, mas a
    propriedade e o que o modal exibe - e ela quebra em silencio."""
    numeros = [
        int(rc._FOLDER_LEADING_NUMBER_RE.match(pasta).group(1))
        for pasta in rc.list_training_type_rag_folders()
        if rc._FOLDER_LEADING_NUMBER_RE.match(pasta)
    ]
    assert numeros == sorted(numeros)


# ---------------------------------------------------------------------------
# Tamanho: o risco principal do PR
# ---------------------------------------------------------------------------


def test_texto_da_pasta_cabe_no_teto_de_contexto():
    """`load_rag_text` corta em MAX_RAG_FOLDER_CHARS com uma marca de
    truncamento. Passar do teto nao levanta excecao nenhuma - so entrega meio
    documento ao modelo, sem sinal na tela."""
    texto = _texto_da_pasta()
    assert len(texto) <= MAX_RAG_FOLDER_CHARS, (
        f"a fonte '{PASTA}' ocupa {len(texto)} caracteres e seria truncada em "
        f"{MAX_RAG_FOLDER_CHARS}. Encurte o conteudo (andaime de recuperacao: "
        f"indice, aliases, mapa de relacoes) em vez de subir o teto - ele e o "
        f"que segura o custo e a latencia da geracao."
    )


def test_texto_da_pasta_termina_inteiro():
    """Guarda complementar ao teto: prova que o fim do documento (as regras
    consolidadas) esta presente. Um corte no meio some exatamente com isso."""
    texto = _texto_da_pasta()
    assert "Regras Operacionais Consolidadas" in texto


# ---------------------------------------------------------------------------
# Formato de chunk
# ---------------------------------------------------------------------------


def test_os_chunks_exg_sao_reconhecidos_pelo_parser():
    """Sem isso o arquivo e 'conteudo geral' para `_select_additional_file_text`
    e entra inteiro em toda geracao legada."""
    caminho = cs.ROOT / "api" / "privado" / "rag" / "markdown" / rc.TIPOS_TREINO_DIRNAME / PASTA / ARQUIVO
    chunks = rc.parse_chunks(caminho.read_text(encoding="utf-8"), ARQUIVO)
    exg = [c for c in chunks if c.id.startswith(PREFIXO_CHUNK)]
    assert len(exg) >= MINIMO_DE_CHUNKS, (
        f"apenas {len(exg)} chunks {PREFIXO_CHUNK}* reconhecidos. Verifique se a "
        f"pontuacao do cabecalho voltou escapada (`## EXG\\-001`) ou se o travessao "
        f"deixou de ser ' — '."
    )
    assert [c.id for c in exg] == sorted({c.id for c in exg}), "ids duplicados ou fora de ordem"


def test_os_chunks_de_grupo_especifico_tem_raiz_de_grupo_muscular():
    """`select_chunks_for_groups` inclui SEMPRE todo chunk sem raiz de grupo
    muscular nas tags. Os chunks que falam de um grupo so precisam carregar a
    raiz, senao entram em ficha de qualquer grupo. (As prescricoes gerais -
    grandes/pequenos/abdomen - continuam sem raiz de proposito: valem para
    qualquer grupo.)"""
    caminho = cs.ROOT / "api" / "privado" / "rag" / "markdown" / rc.TIPOS_TREINO_DIRNAME / PASTA / ARQUIVO
    por_id = {c.id: c for c in rc.parse_chunks(caminho.read_text(encoding="utf-8"), ARQUIVO)}
    especificos = {
        "EXG-007": "peitoral",
        "EXG-009": "deltoide",
        "EXG-012": "triceps",
        "EXG-013": "biceps",
        "EXG-024": "panturrilha",
        "EXG-026": "trapezio",
        "EXG-028": "biceps",
    }
    for chunk_id, raiz in especificos.items():
        assert chunk_id in por_id, f"{chunk_id} sumiu do documento"
        assert raiz in rc._chunk_roots(por_id[chunk_id]), (
            f"{chunk_id} perdeu a raiz '{raiz}' nas tags e passaria a entrar em "
            f"toda geracao, independente do grupo treinado"
        )


# ---------------------------------------------------------------------------
# Validacao de `rag_sources`
# ---------------------------------------------------------------------------


def _draft(**extra) -> PeriodizationDraftIn:
    """Rascunho SEM `training_type_id` e SEM `gym_id` (e `volume_mode` padrao,
    que nao exige academia): `_validate_periodization_request` confere esses ids
    no banco DEPOIS de validar `rag_sources`, e `tests/unit` roda sem MySQL. Com
    os dois em None a funcao retorna antes de abrir `transaction()`, e o teste
    exercita exatamente a parte que interessa: a lista real de pastas."""
    return PeriodizationDraftIn(**extra)


def test_rag_sources_com_a_pasta_nova_passa():
    _validate_periodization_request(_draft(rag_sources=[PASTA]))


def test_rag_sources_com_nome_inventado_continua_422():
    """A validacao existe como defesa de path traversal (o nome vira segmento
    de Path em `load_rag_folder_text`); adicionar uma pasta nao pode afrouxa-la."""
    with pytest.raises(HTTPException) as exc:
        _validate_periodization_request(_draft(rag_sources=["6. Pasta Que Nao Existe"]))
    assert exc.value.status_code == 422
