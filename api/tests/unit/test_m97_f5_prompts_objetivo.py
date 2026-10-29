"""M97 F5: prompts de IA do treino por OBJETIVO e por DELOAD (mapa §68).

Três garantias:

1. Hipertrofia (default) e sessão normal: o prompt é BYTE A BYTE o de sempre
   (o snapshot `test_llm_prompt_renderizado_snapshot.py` trava o caminho sem
   objetivo; aqui o objetivo default EXPLÍCITO tem de render igual) e a versão
   do prompt não muda.
2. Força e deload: o texto novo é travado por snapshot próprio
   (`api/privado/tests/fixtures/llm_prompts_objetivo/<caso>.txt`), montado a partir de casos
   congelados reais (`tests/eval/casos`) com só o objetivo/deload trocado.
   Regerar de propósito: `LLM_PROMPT_SNAPSHOT_WRITE=1 python -m pytest
   tests/unit/test_m97_f5_prompts_objetivo.py`.
3. Todo número dos blocos novos sai do dono (`defaults_for`,
   `training_safety`, `deload`), nunca escrito à mão.

Nenhuma chamada paga: só as funções `build_prompt` reais.
"""

from __future__ import annotations

import copy
import os
from decimal import Decimal

import pytest

from app import exercise_set_evaluation_ai as set_eval
from app import exercise_summary_ai as exercise_summary
from app import goal_prompt_context as gpc
from app import insights_prompt
from app import workout_summary_ai as workout_summary
from app.domain.deload import DELOAD_TARGET_RPE, DeloadPrescription, deload_reason_label
from app.domain.prescription_defaults import defaults_for
from app.domain.training_goal import DEFAULT_GOAL, GOAL_VALUES, is_default
from app.domain.training_safety import goal_disallowed_techniques, goal_rpe_ceiling, goal_target_rpe
from app.engine_decision_context import EngineDecision, GroupDecision, PairEngineContext, next_set_load_for
from tests.support import llm_casos, privado

# Snapshots com o texto do prompt: repositorio privado (M103 P1), em
# `api/privado/tests/fixtures/llm_prompts_objetivo/`.
DEFAULT = DEFAULT_GOAL.value
# O objetivo não-default do vocabulário (hoje força), sem literal escrito aqui.
OUTRO = next(goal for goal in GOAL_VALUES if not is_default(goal))


def _snapshot(nome: str, prompt: str) -> None:
    destino = privado.fixture_privada("llm_prompts_objetivo", f"{nome}.txt")
    if os.environ.get("LLM_PROMPT_SNAPSHOT_WRITE"):
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(prompt + "\n", encoding="utf-8", newline="\n")
    privado.comparar_arquivo_sem_vazar(
        prompt + "\n", destino, f"prompt {nome} (regere de propósito)", regerar="LLM_PROMPT_SNAPSHOT_WRITE"
    )


# ---------------------------------------------------------------------------
# Montagem a partir dos casos congelados
# ---------------------------------------------------------------------------


def _set_eval_prompt(goal=None, deload: DeloadPrescription | None = None, caso: int = 0) -> tuple[str, PairEngineContext]:
    from app.domain.training_safety import IntensityComposition

    entrada = copy.deepcopy(llm_casos.carregar("set_evaluation")[caso]["input"])
    decision = entrada["engine"]["decision"]
    extra = {} if goal is None else {"training_goal": goal}
    engine = PairEngineContext(
        step_kg=Decimal(entrada["engine"]["step_kg"]),
        decision=EngineDecision(**decision) if decision else None,
        load_convention=entrada["engine"].get("load_convention"),
        deload=deload,
        **extra,
    )
    composition = IntensityComposition(**entrada["composition"])
    payload = entrada["payload"]
    reading = llm_casos.leitura_da_serie(payload, composition, engine)
    next_set = next_set_load_for(payload, engine)
    prompt = set_eval.build_prompt(
        payload,
        llm_casos.RAG_FIXO,
        engine=engine,
        composition=composition,
        set_reading=reading,
        next_set=next_set,
        technique_names=entrada.get("technique_names"),
    )
    return prompt, engine


def _exercise_summary_payload(caso: int = 0, **extra) -> dict:
    payload = copy.deepcopy(llm_casos.carregar("exercise_summary")[caso]["input"]["payload"])
    if payload.get("engine_decision"):
        payload["engine_decision"] = EngineDecision(**payload["engine_decision"])
    payload.update(extra)
    return payload


def _workout_summary_inputs(caso: int = 0):
    entrada = copy.deepcopy(llm_casos.carregar("workout_summary")[caso]["input"])
    decisions = entrada["engine_decisions"]
    revividas = {
        "exercises": {int(k): EngineDecision(**v) for k, v in decisions["exercises"].items()},
        "groups": [GroupDecision(**g) for g in decisions["groups"]],
    }
    return entrada["activity"], revividas, entrada.get("technique_names")


def _insights_inputs(caso: int = 0):
    entrada = copy.deepcopy(llm_casos.carregar("insights")[caso]["input"])
    return llm_casos.reviver_pacote(entrada["package"]), entrada["question"], entrada.get("technique_names")


DELOAD_ITEM = DeloadPrescription(
    training_goal=DEFAULT,
    working_load_kg=47.5,
    load_kg=27.5,
    target_rpe=DELOAD_TARGET_RPE,
    reps_target=10,
    sets_count=2,
)


# ---------------------------------------------------------------------------
# 1. Hipertrofia: igual ao de sempre
# ---------------------------------------------------------------------------


def test_default_explicito_e_igual_ao_sem_objetivo_em_todos_os_prompts():
    privado.exigir(_set_eval_prompt()[0] == _set_eval_prompt(DEFAULT)[0], "avaliação de série: default = sem objetivo")
    privado.exigir(
        exercise_summary.build_prompt(_exercise_summary_payload())
        == exercise_summary.build_prompt(_exercise_summary_payload(training_goal=DEFAULT, deload=False)),
        "resumo de exercício: default = sem objetivo",
    )
    activity, decisions, names = _workout_summary_inputs()
    marcada = copy.deepcopy(activity)
    for exercise in marcada["exercises"]:
        exercise["training_goal"] = DEFAULT
    marcada["deload_source"] = None
    privado.exigir(
        workout_summary.build_prompt(activity, decisions, names) == workout_summary.build_prompt(marcada, decisions, names),
        "resumo de treino: default = sem objetivo",
    )
    package, question, _ = _insights_inputs()
    privado.exigir(
        insights_prompt.build_insight_prompt(package, "RAG", question=question)
        == insights_prompt.build_insight_prompt(package, "RAG", question=question, training_goals=(DEFAULT,), deload=False),
        "insights: default = sem objetivo",
    )


def test_versao_do_prompt_nao_muda_no_default_e_ganha_sufixo_nos_ramos_novos():
    assert set_eval.effective_prompt_version(None) == set_eval.PROMPT_VERSION
    assert set_eval.effective_prompt_version(_set_eval_prompt(DEFAULT)[1]) == set_eval.PROMPT_VERSION
    assert set_eval.effective_prompt_version(_set_eval_prompt(OUTRO)[1]) == (
        f"{set_eval.PROMPT_VERSION}+{gpc.GOAL_PROMPT_VERSION}"
    )
    assert set_eval.effective_prompt_version(_set_eval_prompt(deload=DELOAD_ITEM)[1]) == (
        f"{set_eval.PROMPT_VERSION}+{gpc.DELOAD_PROMPT_VERSION}"
    )
    assert exercise_summary.effective_prompt_version({}) == exercise_summary.PROMPT_VERSION
    assert exercise_summary.effective_prompt_version({"training_goal": OUTRO, "deload": True}) == (
        f"{exercise_summary.PROMPT_VERSION}+{gpc.GOAL_PROMPT_VERSION}+{gpc.DELOAD_PROMPT_VERSION}"
    )
    activity, _, _ = _workout_summary_inputs()
    assert workout_summary.effective_prompt_version(activity) == workout_summary.PROMPT_VERSION
    assert insights_prompt.effective_prompt_version() == insights_prompt.PROMPT_VERSION
    assert insights_prompt.effective_prompt_version((OUTRO,), True).startswith(insights_prompt.PROMPT_VERSION + "+")


# ---------------------------------------------------------------------------
# 2. Força e deload: snapshots
# ---------------------------------------------------------------------------


def test_snapshot_avaliacao_de_serie_forca_e_deload():
    _snapshot("set_evaluation__forca", _set_eval_prompt(OUTRO)[0])
    _snapshot("set_evaluation__deload", _set_eval_prompt(deload=DELOAD_ITEM)[0])


def test_snapshot_resumo_de_exercicio_forca_e_deload():
    _snapshot("exercise_summary__forca", exercise_summary.build_prompt(_exercise_summary_payload(training_goal=OUTRO)))
    _snapshot("exercise_summary__deload", exercise_summary.build_prompt(_exercise_summary_payload(deload=True)))


def test_snapshot_resumo_de_treino_hibrido_e_deload():
    activity, decisions, names = _workout_summary_inputs()
    hibrido = copy.deepcopy(activity)
    hibrido["exercises"][0]["training_goal"] = OUTRO
    _snapshot("workout_summary__hibrido", workout_summary.build_prompt(hibrido, decisions, names))
    deload = copy.deepcopy(activity)
    deload["deload_source"] = "manual"
    _snapshot("workout_summary__deload", workout_summary.build_prompt(deload, decisions, names))


def test_snapshot_insights_forca_com_deload_na_janela():
    package, question, names = _insights_inputs()
    prompt = insights_prompt.build_insight_prompt(
        package,
        llm_casos.RAG_FIXO,
        question=question,
        training_goals=(DEFAULT, OUTRO),
        deload=True,
        technique_names=names,
    )
    _snapshot("insights__forca_deload", prompt)


# ---------------------------------------------------------------------------
# 3. Conteúdo dos blocos: números dos donos, persona, deload
# ---------------------------------------------------------------------------


def test_bloco_do_objetivo_interpola_os_donos():
    texto = gpc.goal_section(OUTRO)
    perfil = defaults_for(OUTRO)
    assert perfil.reps_target in texto
    assert perfil.rpe_target in texto
    # Os rotulos de cada numero sao prosa do template: privado, M103 P2
    # (`api/privado/tests/unit/test_goal_context_prompt.py`).
    for numero in (goal_target_rpe(OUTRO), goal_rpe_ceiling(OUTRO), perfil.min_rest_seconds,
                   perfil.max_rest_seconds, perfil.sets_count):
        assert f"{numero}" in texto
    assert "{{" not in texto
    # M95c: sem catálogo, as técnicas vetadas nunca saem pelo código cru.
    for code in goal_disallowed_techniques(OUTRO):
        assert code not in texto
    assert gpc.goal_section(DEFAULT) == ""
    assert gpc.goal_section(None) == ""


def test_bloco_do_objetivo_usa_o_nome_do_catalogo_da_tecnica():
    codigo = sorted(goal_disallowed_techniques(OUTRO))[0]
    texto = gpc.goal_section(OUTRO, technique_names={codigo: "Nome Legível"})
    assert "Nome Legível" in texto


def test_persona_por_objetivo():
    assert gpc.focus_text(None) == "hipertrofia"
    assert gpc.focus_text([DEFAULT, DEFAULT]) == "hipertrofia"
    assert gpc.focus_text([OUTRO]) != "hipertrofia"
    assert gpc.focus_text([OUTRO, DEFAULT]).startswith("hipertrofia, ")


def test_bloco_do_deload_diz_que_nao_e_regressao_com_os_numeros_do_dono():
    texto = gpc.deload_section(True, prescription=DELOAD_ITEM)
    assert deload_reason_label() in texto
    assert "27.5 kg" in texto and "10 reps" in texto and f"RPE-alvo {DELOAD_TARGET_RPE}" in texto
    assert gpc.deload_section(False) == ""
    janela = gpc.deload_section(True, scope=gpc.SCOPE_WINDOW)
    assert deload_reason_label() in janela and janela != gpc.deload_section(True)
    assert "27.5 kg" not in janela  # a janela nao tem prescricao de exercicio


def test_prompt_de_serie_em_deload_traz_a_prescricao_e_o_aviso():
    prompt, _ = _set_eval_prompt(deload=DELOAD_ITEM)
    privado.exigir(gpc.deload_section(True, prescription=DELOAD_ITEM) in prompt, "bloco do deload com a prescrição")


def test_resumo_de_treino_marca_so_o_exercicio_nao_default():
    activity, decisions, names = _workout_summary_inputs()
    hibrido = copy.deepcopy(activity)
    hibrido["exercises"][0]["training_goal"] = OUTRO
    prompt = workout_summary.build_prompt(hibrido, decisions, names)
    assert prompt.count(gpc.goal_label_suffix(OUTRO)) == 1


@pytest.mark.parametrize(
    "texto",
    [gpc.goal_section(OUTRO), gpc.deload_section(True), gpc.deload_section(True, scope=gpc.SCOPE_WINDOW)],
)
def test_blocos_novos_nao_trazem_numero_fora_dos_donos(texto):
    """Todo número dos blocos é de um dono: o texto renderizado com os donos
    trocados por zero não pode guardar nenhum dígito escrito à mão."""
    import re

    from app.domain import deload as deload_owner

    permitidos = {
        str(v)
        for v in (
            *defaults_for(OUTRO).to_dict().values(),
            goal_target_rpe(OUTRO),
            goal_rpe_ceiling(OUTRO),
            deload_owner.DELOAD_TARGET_RPE,
            deload_owner.DELOAD_SETS_REDUCTION_FRACTION.numerator,
            deload_owner.DELOAD_SETS_REDUCTION_FRACTION.denominator,
            int(deload_owner.DELOAD_LOAD_FACTOR * 100),
        )
    }
    numeros = set(re.findall(r"\d+", texto))
    soltos = {n for n in numeros if not any(n in p for p in permitidos)}
    assert not soltos, f"número sem dono no bloco: {sorted(soltos)}"
