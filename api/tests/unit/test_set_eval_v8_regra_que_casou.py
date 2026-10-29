"""Prompt set-eval-v8 (Feature R, R3): a seção da próxima série cita a regra
que casou (`NextSetLoad.rule_text`) e o conjunto vigente na ordem
(`next_set_load.describe_ruleset`, com a versão), em vez da regra escrita a
partir das constantes. Nenhum número do texto é escrito aqui: tudo sai do dono.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from app import exercise_set_evaluation_ai as set_ai
from app import workout_ai_generation_core as core
from app.domain import next_set_load as nsl
from app.domain import progression_engine as pe
from app.engine_decision_context import PairEngineContext, next_set_load_for
from app.intelligence.muscle_recovery_service import MUSCLE_PAIN_BLOCK_ABOVE
from tests.support.privado import exigir

PASSO = Decimal("2.5")
MOTOR = PairEngineContext(step_kg=PASSO)

PAYLOAD = {
    "exercise_name": "Remada",
    "set_number": 1,
    "sets_count": 3,
    "reps_target": "10-12",
    "load_target": "40kg",
    "rpe_target": "10",
    "reps": 17,
    "weight_kg": 40,
    "rpe": 10,
}


def _cadastrado(reps_floor: int, reps_ceiling: int, *rules: nsl.Rule, version: int) -> nsl.RuleSet:
    return nsl.RuleSet(
        reps_floor=reps_floor,
        reps_ceiling=reps_ceiling,
        rules=rules,
        version=version,
        source=nsl.RULESET_SOURCE_PUBLISHED,
    )


# Conjunto cadastrado com regra percentual: reps acima do teto -> +25%.
CADASTRADO = _cadastrado(
    nsl.DEFAULT_RULESET.reps_floor,
    nsl.DEFAULT_RULESET.reps_ceiling,
    nsl.Rule(
        position=1,
        reps=nsl.Condition(nsl.ConditionOp.GT, nsl.REPS_TOKEN_CEILING),
        action=pe.INCREASE_LOAD,
        magnitude=nsl.Magnitude(nsl.MagnitudeKind.PERCENT, Decimal(25)),
    ),
    version=3,
)


def _secao(payload: dict, proxima: nsl.NextSetLoad, ruleset: nsl.RuleSet | None = None) -> str:
    """A seção da próxima série, conferida dentro do prompt inteiro (a redação
    da seção é conferida no privado, `test_set_evaluation_prompt.py`)."""
    secao = set_ai._format_next_set_section(proxima, ruleset)
    prompt = set_ai.build_prompt(payload, "", engine=MOTOR, next_set=proxima, ruleset=ruleset)
    exigir(secao is not None and secao in prompt, "seção da próxima série no prompt")
    return secao


def test_versao_do_prompt():
    assert set_ai.PROMPT_VERSION == "set-eval-v8"


def test_padrao_cita_a_regra_que_casou_e_o_conjunto_v0():
    proxima = next_set_load_for(PAYLOAD, MOTOR)
    assert proxima.rule_text
    secao = _secao(PAYLOAD, proxima)
    exigir(f"- Regra que casou: {proxima.rule_text}.\n" in secao, "regra que casou")
    exigir(f"(v{nsl.DEFAULT_RULESET.version}, conjunto padrão)" in secao, "versão do conjunto padrão")
    for linha in nsl.describe_ruleset(nsl.DEFAULT_RULESET).splitlines():
        exigir(f"  {linha}\n" in secao, "regra do conjunto vigente")
    exigir(f": {nsl.describe_pain_rule()}.\n" in secao, "regra de dor do dono")
    # a regra fixa da v7 (escrita a partir das constantes) saiu
    exigir("Regra do motor:" not in secao, "sem a regra fixa da v7")


def test_sem_ruleset_explicito_usa_o_padrao():
    proxima = next_set_load_for(PAYLOAD, MOTOR)
    sem = set_ai.build_prompt(PAYLOAD, "", engine=MOTOR, next_set=proxima)
    com = set_ai.build_prompt(PAYLOAD, "", engine=MOTOR, next_set=proxima, ruleset=nsl.DEFAULT_RULESET)
    exigir(sem == com, "sem conjunto = conjunto padrão")


def test_cadastrado_percentual_cita_a_regra_e_a_versao():
    proxima = next_set_load_for(PAYLOAD, MOTOR, ruleset=CADASTRADO)
    assert proxima.reason == nsl.REASON_RULE_MATCHED
    assert proxima.ruleset_version == CADASTRADO.version
    regra = nsl.describe_rule(CADASTRADO.rules[0], CADASTRADO)
    secao = _secao(PAYLOAD, proxima, CADASTRADO)
    exigir(f"- Regra que casou: {regra}.\n" in secao, "regra cadastrada que casou")
    exigir(f"(v{CADASTRADO.version})" in secao, "versão do conjunto cadastrado")
    exigir("conjunto padrão" not in secao, "sem o rótulo do padrão")
    exigir(f"  1. {regra}\n" in secao, "regra cadastrada na lista")
    # as regras do padrão não aparecem quando o cadastrado vale
    exigir(nsl.describe_rule(nsl.DEFAULT_RULESET.rules[0], nsl.DEFAULT_RULESET) not in secao, "sem as regras do padrão")


def test_nenhuma_regra_casou_manter():
    payload = dict(PAYLOAD, reps=8, rpe=8)
    proxima = next_set_load_for(payload, MOTOR)
    assert proxima.direction == pe.HOLD and proxima.rule_text is None
    secao = _secao(payload, proxima)
    exigir("- Nenhuma regra casou: manter a carga.\n" in secao, "nenhuma regra casou")
    exigir("Regra que casou" not in secao, "sem regra que casou")


def test_dor_nao_cita_regra_que_casou():
    payload = dict(PAYLOAD, joint_pain=True)
    proxima = next_set_load_for(payload, MOTOR)
    assert proxima.reason == nsl.REASON_PAIN
    secao = _secao(payload, proxima)
    exigir("Regra que casou" not in secao and "Nenhuma regra casou" not in secao, "dor decide antes das regras")
    exigir(f"- Motivo: {proxima.reason_label}." in secao, "motivo do dono")


def test_rampa_planejada_continua_interpolada():
    payload = dict(PAYLOAD, reps=12, rpe=8)
    proxima = replace(next_set_load_for(payload, MOTOR), planned_rpe=8.0)
    secao = _secao(payload, proxima)
    rampa = set_ai._text(".rampa", {"rpe_planejado": set_ai._format_number(proxima.planned_rpe)})
    exigir(rampa in secao, "rampa com o RPE planejado interpolado")
    sem_rampa = _secao(payload, replace(proxima, planned_rpe=None))
    exigir(rampa not in sem_rampa, "sem rampa, sem a linha")


def test_dispatch_repassa_o_conjunto_publicado(monkeypatch):
    recebido = {}

    def fake_evaluate_set(payload, **kwargs):
        recebido.update(kwargs)
        return {"category": pe.HOLD, "suggested_next_load_kg": None, "suggested_next_reps": None, "suggested_next_rpe": None}

    monkeypatch.setattr(core, "load_published_ruleset_autocommit", lambda: CADASTRADO)
    monkeypatch.setattr(core, "evaluate_set", fake_evaluate_set)
    monkeypatch.setattr(core, "_evaluate_set_engine", lambda payload, user_id: MOTOR)
    monkeypatch.setattr(core, "_evaluate_set_logged_intensity", lambda engine: None)
    monkeypatch.setattr(core, "apply_engine_load_bound", lambda result, *a, **k: {**result, "clamps": []})
    core.dispatch_evaluate_set(dict(PAYLOAD), user_id=1)
    assert recebido["ruleset"] is CADASTRADO
    assert recebido["next_set"].ruleset_version == CADASTRADO.version


def test_dono_texto_da_dor_e_do_que_casou():
    assert f"acima de {MUSCLE_PAIN_BLOCK_ABOVE}/10" in nsl.describe_pain_rule()
    assert nsl.describe_pain_rule().endswith(nsl._magnitude_text(nsl.ONE_STEP))
    dor = next_set_load_for(dict(PAYLOAD, joint_pain=True), MOTOR)
    assert nsl.describe_matched_rule(dor) is None
    casou = next_set_load_for(PAYLOAD, MOTOR)
    assert nsl.describe_matched_rule(casou) == f"Regra que casou: {casou.rule_text}"
    manter = next_set_load_for(dict(PAYLOAD, reps=8, rpe=8), MOTOR)
    assert nsl.describe_matched_rule(manter) == "Nenhuma regra casou: manter a carga"
