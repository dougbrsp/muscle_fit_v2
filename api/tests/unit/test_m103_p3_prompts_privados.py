"""M103 P3: prompts da Periodização (com a seção de cardio) e dos dois prompts
legados de geração de ficha (Modelo v1 e seleção v2) moram no repositório
privado (`api/privado/prompts/`).

Testes ESTRUTURAIS (sem frase de prompt; o log do CI público é público):

- cada consumidor lê os templates da sua versão, e todo fragmento existe;
- a versão dos templates da Periodização é a `core.PROMPT_VERSION`; as dos
  dois prompts legados (sem `PROMPT_VERSION` própria) só nomeiam os templates
  e não entram no texto;
- a prosa saiu do código público: nenhuma linha dos templates aparece em
  `api/app`.

A prosa em si é travada pelos snapshots da Periodização
(`test_llm_prompt_renderizado_snapshot`, hashes em
`fixtures/llm_prompts/index.json`), pelos hashes dos dois legados
(`test_m103_p3_hash_prompts_sem_versao.py`) e pelos testes do privado
(`api/privado/tests/unit/test_periodization_prompt.py`,
`test_fichas_legado_prompt.py`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import prompt_store, rag_placeholders, workout_plan_templates_engine
from app.intelligence import template_ai_selector_v2
from app.periodization import cardio_plan, context
from app.periodization.core import PROMPT_VERSION
from tests.support.privado import exigir
from tests.unit import test_m103_p3_hash_prompts_sem_versao as hashes

APP = Path(__file__).resolve().parents[2] / "app"

# (pasta, versão, fragmentos que o consumidor renderiza)
TEMPLATES = (
    (
        context.PROMPT_FEATURE_DIR,
        PROMPT_VERSION,
        (
            ".cabecalho", ".regras", ".modo_a", ".modo_b", ".contagem_primario", ".contagem_ponderada",
            ".checklist", ".checklist_contagem_primario", ".checklist_contagem_ponderada", ".dados_usuario",
            ".sem_faixas", ".rag", ".grupos", ".tecnicas", ".tabela", ".catalogo", ".metas_perfil",
            ".metas_derivadas", ".sem_metas", ".objetivo", ".objetivo_dia", ".objetivo_regras",
            ".objetivo_modo_a", ".objetivo_compostos", ".objetivo_isoladores", ".objetivo_tecnicas",
            ".objetivo_descanso", ".objetivo_minimo", ".exemplo_celula", ".exemplo_celula_exercicios",
            ".exemplo_tecnica",
        ),
    ),
    (
        cardio_plan.PROMPT_FEATURE_DIR,
        PROMPT_VERSION,
        ("", ".rag", ".origem_flag", ".origem_preferencias", ".banda_sem_hiit", ".banda_com_hiit", ".tsb_sem_dado"),
    ),
    (
        workout_plan_templates_engine.PROMPT_FEATURE_DIR,
        workout_plan_templates_engine.PROMPT_TEMPLATE_VERSION,
        ("", ".dose_reduzida", ".schema"),
    ),
    (
        template_ai_selector_v2.PROMPT_FEATURE_DIR,
        template_ai_selector_v2.PROMPT_TEMPLATE_VERSION,
        ("", ".schema"),
    ),
)
CASOS = [(pasta, f"{versao}{parte}") for pasta, versao, partes in TEMPLATES for parte in partes]


@pytest.mark.parametrize("pasta, nome", CASOS, ids=[f"{p}/{n}" for p, n in CASOS])
def test_template_existe(pasta, nome):
    assert prompt_store.template_path(pasta, nome).is_file()


def test_nenhum_template_sobrando_nas_pastas_do_p3():
    """Todo `.md` das 4 pastas é renderizado por um consumidor (sem prosa morta)."""
    esperados = set(CASOS)
    for pasta, _versao, _partes in TEMPLATES:
        achados = {(pasta, p.stem) for p in (prompt_store.prompts_dir() / pasta).glob("*.md")}
        assert achados <= esperados, sorted(nome for _p, nome in achados - esperados)


def test_versao_da_periodizacao_e_a_do_core():
    assert context.prompt_template_version() == PROMPT_VERSION


def test_versao_dos_legados_nao_entra_no_prompt():
    for modulo, prompt in (
        (workout_plan_templates_engine, workout_plan_templates_engine.build_template_prompt(**hashes.PLANO_MODELO["minimo"])),
        (template_ai_selector_v2, template_ai_selector_v2.build_v2_selection_prompt(**hashes.SELECAO_V2["minimo"])),
    ):
        exigir(modulo.PROMPT_TEMPLATE_VERSION not in prompt, f"{modulo.__name__}: versão fora do prompt")
        exigir(not rag_placeholders.placeholders_in(prompt), f"{modulo.__name__}: nenhum placeholder sobrando")


def _linhas_de_prosa() -> list[str]:
    linhas = set()
    for pasta, _versao, _partes in TEMPLATES:
        for arquivo in (prompt_store.prompts_dir() / pasta).glob("*.md"):
            for linha in arquivo.read_text(encoding="utf-8").split("\n"):
                trecho = linha.strip(" -")
                if len(trecho) >= 40 and "{{" not in trecho:
                    linhas.add(trecho)
    return sorted(linhas)


def test_prosa_dos_templates_nao_esta_no_codigo_publico():
    linhas = _linhas_de_prosa()
    assert linhas
    for fonte in APP.rglob("*.py"):
        texto = fonte.read_text(encoding="utf-8")
        # So a contagem na mensagem: o log do CI publico e publico.
        vazou = sum(1 for linha in linhas if linha in texto)
        assert not vazou, f"{fonte.relative_to(APP)}: {vazou} linha(s) de template no código público"
