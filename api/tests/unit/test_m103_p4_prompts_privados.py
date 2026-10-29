"""M103 P4 (o último): prompts da avaliação de série (IA por série do Modo
Treino) e da ilustração do card de exercício moram no repositório privado
(`api/privado/prompts/`), e uma varredura final confere que NENHUM template do
privado vazou para o código público.

Testes ESTRUTURAIS (sem frase de prompt; o log do CI público é público):

- cada consumidor lê os templates da sua versão, todo fragmento existe e todo
  `.md` das duas pastas é usado (sem prosa morta);
- a versão dos templates do card só nomeia os arquivos e não entra no texto;
- varredura final: nenhuma linha de nenhum template do privado (todas as
  pastas de `prompts/`) aparece em `api/app`, nos testes públicos
  (`api/tests`), em `frontend/src` nem em `docs/`. A falha diz só o arquivo e
  a contagem, nunca o texto.

A prosa em si é travada pelo snapshot da avaliação de série
(`test_llm_prompt_renderizado_snapshot`, hashes em
`fixtures/llm_prompts/index.json`), pelos hashes de
`test_m103_p4_hash_prompts.py` (card e ramos fora do snapshot) e pelos testes
do privado (`api/privado/tests/unit/test_set_evaluation_prompt.py`,
`test_exercise_card_prompt.py`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import exercise_card_prompt, exercise_set_evaluation_ai, prompt_store, rag_placeholders, workout_plan_ai
from tests.support.privado import exigir
from tests.unit import test_m103_p4_hash_prompts as hashes

API = Path(__file__).resolve().parents[2]
RAIZ = API.parent

# (pasta, versão, fragmentos que o consumidor renderiza)
TEMPLATES = (
    (
        exercise_set_evaluation_ai.PROMPT_FEATURE_DIR,
        exercise_set_evaluation_ai.PROMPT_VERSION,
        (
            "", ".sem_series_anteriores", ".sem_sinais", ".anotacao", ".passo", ".nenhuma_decisao",
            ".motor_sessao", ".motor_sem_decisao", ".motor_limite", ".motor_sem_limite", ".motor",
            ".status_sem_alvo", ".status", ".evento", ".evento_alerta", ".evento_informativo",
            ".status_explicar", ".proxima_serie", ".proxima_serie_sem_passo", ".proxima_serie_uso_carga",
            ".proxima_serie_uso_sem_carga", ".rampa", ".dor_alta", ".ultima_serie_recuperacao",
            ".ultima_serie_par", ".falha_permitida", ".falha_negada_par", ".ultima_serie_sem_composicao",
            ".regra_intensidade_proxima_serie", ".regra_intensidade", ".escopo_proxima_serie", ".escopo_motor",
        ),
    ),
    (
        exercise_card_prompt.PROMPT_FEATURE_DIR,
        exercise_card_prompt.PROMPT_TEMPLATE_VERSION,
        (
            ".intro", ".formato", ".cenas", ".referencia", ".nome_ingles", ".outros_nomes",
            ".equipamento_ausente", ".equipamento_regra", ".equipamento_um", ".equipamento_alternativas",
            ".carga", ".carga_bodyweight", ".carga_bodyweight_plus", ".carga_assisted", ".unilateral",
            ".passos_inicio", ".passos_movimento", ".passos_forma", ".tags", ".biomecanica", ".estilo",
            ".evitar", ".revisao", ".revisao_motivo", ".revisao_pedido", ".revisao_fim", ".verificacao_final",
        ),
    ),
    # Achada na varredura final: seção do Intervals.icu da ficha legada.
    (
        workout_plan_ai.INTERVALS_PROMPT_FEATURE_DIR,
        workout_plan_ai.INTERVALS_PROMPT_TEMPLATE_VERSION,
        ("", ".sem_dados"),
    ),
)
CASOS = [(pasta, f"{versao}{parte}") for pasta, versao, partes in TEMPLATES for parte in partes]


@pytest.mark.parametrize("pasta, nome", CASOS, ids=[f"{p}/{n}" for p, n in CASOS])
def test_template_existe(pasta, nome):
    assert prompt_store.template_path(pasta, nome).is_file()


def test_secao_do_intervals_sem_placeholder_nem_versao():
    for wellness in hashes.INTERVALS.values():
        secao = workout_plan_ai.format_intervals_wellness_section(wellness)
        exigir(workout_plan_ai.INTERVALS_PROMPT_TEMPLATE_VERSION not in secao, "versão fora do prompt")
        exigir(not rag_placeholders.placeholders_in(secao), "nenhum placeholder sobrando")


def test_nenhum_template_sobrando_nas_pastas_do_p4():
    """Todo `.md` das 3 pastas é renderizado por um consumidor (sem prosa morta)."""
    esperados = set(CASOS)
    for pasta, _versao, _partes in TEMPLATES:
        achados = {(pasta, p.stem) for p in (prompt_store.prompts_dir() / pasta).glob("*.md")}
        assert achados <= esperados, sorted(nome for _p, nome in achados - esperados)


def test_dicas_de_carga_do_card_tem_um_fragmento_por_convencao():
    for convencao in exercise_card_prompt._LOAD_HINT_CONVENTIONS:
        assert prompt_store.template_path(
            exercise_card_prompt.PROMPT_FEATURE_DIR, f"{exercise_card_prompt.PROMPT_TEMPLATE_VERSION}.carga_{convencao}"
        ).is_file()


def test_versao_dos_templates_do_card_nao_entra_no_prompt():
    for ctx, instrucao, motivo in hashes.CARD.values():
        prompt = exercise_card_prompt.build_prompt(ctx, "minimal", instrucao, motivo)
        exigir(exercise_card_prompt.PROMPT_TEMPLATE_VERSION not in prompt, "versão dos templates fora do prompt")
        exigir(exercise_card_prompt.PROMPT_VERSION not in prompt, "PROMPT_VERSION fora do prompt")
        exigir(not rag_placeholders.placeholders_in(prompt), "nenhum placeholder sobrando")


def test_avaliacao_de_serie_sem_placeholder_sobrando():
    for entrada in hashes.SET_EVAL.values():
        entrada = dict(entrada)
        payload = entrada.pop("payload")
        prompt = exercise_set_evaluation_ai.build_prompt(payload, hashes._RAG, **entrada)
        # `{{x}}` vem da anotação do usuário (dado, não reinterpretado)
        exigir(set(rag_placeholders.placeholders_in(prompt)) <= {"x"}, "nenhum placeholder sobrando")


# ---------------------------------------------------------------------------
# Varredura final: nenhum template do privado no código público
# ---------------------------------------------------------------------------

# Onde o texto de prompt não pode estar (repositório público).
ALVOS = (
    (API / "app", ("*.py",)),
    (API / "tests", ("*.py", "*.json", "*.md")),
    (RAIZ / "frontend" / "src", ("*.ts", "*.tsx")),
    (RAIZ / "docs", ("*.md",)),
)
MIN_CHARS = 40


def _linhas_de_prosa() -> list[str]:
    """Toda linha de todo template do privado (todas as pastas de
    `prompts/`), sem marcador de lista, com 40+ caracteres e sem placeholder."""
    linhas = set()
    for arquivo in prompt_store.prompts_dir().rglob("*.md"):
        if arquivo.name == "README.md":
            continue
        for linha in arquivo.read_text(encoding="utf-8").split("\n"):
            trecho = " ".join(linha.strip(" -").split())
            if len(trecho) >= MIN_CHARS and "{{" not in trecho:
                linhas.add(trecho)
    return sorted(linhas)


def _sem_quebras(texto: str) -> str:
    """Espaços e quebras de linha colapsados: pega a frase quebrada em duas
    linhas de um docstring ou de um Markdown."""
    return " ".join(texto.split())


def _fontes_publicas():
    for base, padroes in ALVOS:
        for padrao in padroes:
            for fonte in base.rglob(padrao):
                # o submódulo privado (api/privado) não é público
                if "privado" in fonte.relative_to(API.parent).parts[:2]:
                    continue
                if "node_modules" in fonte.parts:
                    continue
                yield fonte


def test_prosa_de_nenhum_template_do_privado_esta_no_codigo_publico():
    linhas = _linhas_de_prosa()
    assert len(linhas) > 100  # todas as pastas, não só as do P4
    vazamentos = []
    for fonte in _fontes_publicas():
        texto = _sem_quebras(fonte.read_text(encoding="utf-8", errors="replace"))
        vazou = sum(1 for linha in linhas if linha in texto)
        if vazou:
            vazamentos.append(f"{fonte.relative_to(RAIZ).as_posix()}: {vazou}")
    # So o arquivo e a contagem na mensagem: o log do CI publico e publico.
    assert not vazamentos, f"linha(s) de template do privado no código público: {vazamentos}"
