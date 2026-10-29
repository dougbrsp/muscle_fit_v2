"""M103 P2: prompts de insights, "Análise por IA", resumos de exercício e de
treino e os fragmentos compartilhados (objetivo/deload e regra do dado do
usuário) moram no repositório privado (`api/privado/prompts/`).

Testes ESTRUTURAIS (sem frase de prompt; o log do CI público é público):

- cada builder lê o template da sua `PROMPT_VERSION` e não deixa placeholder
  sem valor;
- a impressão digital do resumo de treino (`input_fingerprint`, chave de dedupe
  gravada em `activities.ai_summary_input_sha256`) não mudou com a extração: os
  valores abaixo foram calculados com o código de antes do P2 (texto nos `.py`);
- a prosa saiu do código público: nenhuma linha dos templates aparece em
  `api/app`.

A prosa em si é travada pelos snapshots (`test_llm_prompt_renderizado_snapshot`,
hashes em `fixtures/llm_prompts/index.json`) e pelos testes do privado
(`api/privado/tests/unit/test_*_prompt.py`).
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from app import exercise_summary_ai, goal_prompt_context, insights_prompt, prompt_store, prompt_user_data, report_ai
from app import workout_summary_ai
from app.domain.training_goal import GOAL_VALUES
from app.engine_decision_context import EngineDecision, GroupDecision
from tests.support import llm_casos

APP = Path(__file__).resolve().parents[2] / "app"

# (pasta do template, módulo consumidor, versão base)
FEATURES = (
    (insights_prompt.PROMPT_FEATURE_DIR, insights_prompt, insights_prompt.PROMPT_VERSION),
    (report_ai.PROMPT_FEATURE_DIR, report_ai, report_ai.PROMPT_VERSION),
    (exercise_summary_ai.PROMPT_FEATURE_DIR, exercise_summary_ai, exercise_summary_ai.PROMPT_VERSION),
    (workout_summary_ai.PROMPT_FEATURE_DIR, workout_summary_ai, workout_summary_ai.PROMPT_VERSION),
    (goal_prompt_context.PROMPT_FEATURE_DIR, goal_prompt_context, goal_prompt_context.GOAL_PROMPT_VERSION),
    (goal_prompt_context.PROMPT_FEATURE_DIR, goal_prompt_context, goal_prompt_context.DELOAD_PROMPT_VERSION),
    (prompt_user_data.PROMPT_FEATURE_DIR, prompt_user_data, prompt_user_data.USER_DATA_RULE_VERSION),
)


@pytest.mark.parametrize("pasta, _modulo, versao", FEATURES, ids=[f[2] for f in FEATURES])
def test_template_da_versao_existe(pasta, _modulo, versao):
    assert prompt_store.template_path(pasta, versao).is_file()


def _workout_summary_entrada(caso: str):
    entrada = next(c for c in llm_casos.carregar("workout_summary") if c["id"] == caso)["input"]
    entrada = copy.deepcopy(entrada)
    decisions = entrada["engine_decisions"]
    revividas = {
        "exercises": {int(k): EngineDecision(**v) for k, v in decisions["exercises"].items()},
        "groups": [GroupDecision(**g) for g in decisions["groups"]],
    }
    return entrada["activity"], revividas, entrada.get("technique_names")


# sha256 de `input_fingerprint` calculado com o código de ANTES do M103 P2.
FINGERPRINT_ANTES = {
    ("costas_biceps_increase", None, None, False): "60f3bf628d1535a33a99cd1efe00b0b67004d34f1efc4129261f5058cd60443d",
    ("peito_decrease_load", None, None, False): "b04e1b27c4da7ca8a1d78eb5c372cf672823d2ddd7a03e12fab8a547f7b9ec93",
    ("peito_ombro_triceps_queda", None, None, False): "46d7f5d4e60e9b75af5a9246b98f4d770f3f71f1ef7ac775fcea461692d6ed7f",
    ("posterior_gluteos", None, None, False): "c9d3572a889e0b4b6358a9745bc897bba67e338fb742473a788f987ccd922f33",
    ("quadriceps_tecnicas", None, None, False): "caa922f6a147e1380e0c377105d4c4c6a8cdf0dfdf0a612b4e3c2fa88b6bd5bd",
    ("sessao_longa_outra_academia", None, None, False): "503779889874bbe2a74dfd4687861974fba5cbef04fb97524fe3e395922379f4",
    # ramo de deload, sem duração/RPE/notas e sem decisão por grupo
    ("quadriceps_tecnicas", GOAL_VALUES[0], "period", True): "afd380f5ffcf97db4a6c8eae3e1a91961d40954439d3260ec92632aa402d73ca",
}


@pytest.mark.parametrize("caso, objetivo, deload, enxuto", list(FINGERPRINT_ANTES), ids=lambda v: str(v))
def test_chave_de_dedupe_do_resumo_de_treino_nao_mudou(caso, objetivo, deload, enxuto):
    activity, decisoes, nomes = _workout_summary_entrada(caso)
    for exercicio in activity.get("exercises") or []:
        exercicio["training_goal"] = objetivo
    activity["deload_source"] = deload
    if enxuto:
        activity.update(duration_seconds=None, avg_rpe=None, notes=None)
        decisoes = dict(decisoes, groups=[])
    prompt = workout_summary_ai.build_prompt(activity, decisoes, nomes)
    assert workout_summary_ai.input_fingerprint(prompt) == FINGERPRINT_ANTES[(caso, objetivo, deload, enxuto)]


def _linhas_de_prosa() -> list[str]:
    linhas = set()
    for pasta, _modulo, _versao in FEATURES:
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
