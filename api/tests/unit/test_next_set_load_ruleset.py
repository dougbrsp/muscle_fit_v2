"""Motor de CONJUNTO DE REGRAS da carga da próxima série (R1, 2026-10-20).

Dono: `app/domain/next_set_load.py` (`evaluate_ruleset`, `SetFacts`,
`DEFAULT_RULESET`, `describe_rule`, `validate_ruleset`, `ruleset_from_dict`).

1. Equivalência: `evaluate_ruleset(DEFAULT_RULESET)` ≡ a classificação de
   antes do R1 (cópia literal aqui, de propósito) numa grade de reps × RPE ×
   rampa × dor; `classify_next_set` e `suggest_next_set_load` seguem iguais.
2. Vocabulário novo: posição da série, faixa de fadiga, tokens, `max`,
   `between`, rampa, primeira regra que casa vence.
3. Carga: `steps` n, `percent` p arredondado ao passo com ≥ 1 passo na
   direção, reduzir abaixo do primeiro passo.
4. `validate_ruleset`, `describe_rule`, round-trip do JSON e o espelho do FE.

Rodar: cd api && python -m pytest tests/unit/test_next_set_load_ruleset.py
"""

from __future__ import annotations

import itertools
import json
from decimal import Decimal

import pytest

from app.domain import next_set_load as nsl
from app.domain import progression_engine as pe
from app.domain.exercise_briefing import DECISION_LABELS
from app.domain.prior_fatigue import PRIOR_FATIGUE_BAND_LABELS, PriorFatigueBand
from app.intelligence.muscle_recovery_service import MUSCLE_PAIN_BLOCK_ABOVE, muscle_pain_blocks_direct_work

PASSO = Decimal("2.5")
GT, GTE, LT, LTE, BETWEEN = (
    nsl.ConditionOp.GT,
    nsl.ConditionOp.GTE,
    nsl.ConditionOp.LT,
    nsl.ConditionOp.LTE,
    nsl.ConditionOp.BETWEEN,
)
PERCENT_25 = nsl.Magnitude(nsl.MagnitudeKind.PERCENT, Decimal(25))
STEPS_2 = nsl.Magnitude(nsl.MagnitudeKind.STEPS, Decimal(2))


def _facts(**kwargs):
    base = dict(set_type="working", weight_kg=20, step_kg=PASSO, reps=10, rpe=8)
    return nsl.SetFacts(**{**base, **kwargs})


def regra_v1(position=1, **kw):
    """Regra no formato v1 (campos fixos) convertida para a árvore: paridade v1 -> v2."""
    return nsl.rule_from_v1(position, **kw)


def _ruleset(*rules, reps_floor=6, reps_ceiling=12):
    return nsl.RuleSet(
        reps_floor=reps_floor,
        reps_ceiling=reps_ceiling,
        rules=tuple(regra_v1(index, **rule) for index, rule in enumerate(rules, start=1)),
        version=7,
        source="teste",
    )


# ---------------------------------------------------------------------------
# 1. Equivalência com a regra de antes do R1
# ---------------------------------------------------------------------------


def _classificacao_anterior(reps, rpe, *, joint_pain=None, muscle_pain=None, planned_rpe=None):
    """Cópia LITERAL de `classify_next_set` como era antes do R1 (os números
    escritos à mão aqui são o retrato da regra antiga, só neste teste)."""
    reps_value = nsl._decimal(reps)
    if reps_value is None or reps_value <= 0:
        return None
    rpe_value = nsl._decimal(rpe)
    if bool(joint_pain) or muscle_pain_blocks_direct_work(muscle_pain):
        return pe.DECREASE_LOAD, nsl.REASON_PAIN
    if reps_value > 15:
        return pe.INCREASE_LOAD, nsl.REASON_REPS_ABOVE_LIMIT
    if rpe_value is not None:
        planned = nsl._decimal(planned_rpe)
        low_rpe_below = min(Decimal(9), planned) if planned is not None else 9
        if reps_value >= 12 and rpe_value < low_rpe_below:
            return pe.INCREASE_LOAD, nsl.REASON_LOW_RPE_AT_REPS
        if reps_value >= 12 and rpe_value < 9:
            return pe.HOLD, nsl.REASON_WITHIN_PLANNED_RAMP
        if rpe_value >= 10 and reps_value < 6:
            return pe.DECREASE_LOAD, nsl.REASON_FAILURE_BEFORE_MIN_REPS
    return pe.HOLD, nsl.REASON_WITHIN_RULE


GRADE = list(
    itertools.product(
        range(1, 21),
        [None, 6, 7, 8, 9, 10],
        [None, 8],
        [{}, {"joint_pain": True}, {"muscle_pain": MUSCLE_PAIN_BLOCK_ABOVE + 1}],
    )
)


@pytest.mark.parametrize(("reps", "rpe", "rampa", "dor"), GRADE)
def test_conjunto_padrao_equivale_a_regra_anterior(reps, rpe, rampa, dor):
    esperado = _classificacao_anterior(reps, rpe, planned_rpe=rampa, **dor)
    assert nsl.classify_next_set(reps, rpe, planned_rpe=rampa, **dor) == esperado
    resultado = nsl.evaluate_ruleset(_facts(reps=reps, rpe=rpe, planned_rpe=rampa, **dor), nsl.DEFAULT_RULESET)
    assert (resultado.direction, resultado.reason) == esperado
    # um passo do equipamento, como antes
    carga = {pe.INCREASE_LOAD: 22.5, pe.HOLD: 20.0, pe.DECREASE_LOAD: 17.5}[resultado.direction]
    assert resultado.load_kg == carga
    assert resultado.ruleset_version == 0
    # a mesma chamada antiga de `suggest_next_set_load` continua idêntica
    antiga = nsl.suggest_next_set_load(
        set_type="working", reps=reps, weight_kg=20, rpe=rpe, step_kg=PASSO, planned_rpe=rampa, **dor
    )
    assert (antiga.direction, antiga.reason, antiga.load_kg) == (resultado.direction, resultado.reason, carga)


def test_conjunto_padrao_e_a_regra_de_2026_10_12():
    padrao = nsl.DEFAULT_RULESET
    assert (padrao.reps_floor, padrao.reps_ceiling, padrao.version, padrao.source) == (
        nsl.NEXT_SET_DECREASE_BELOW_REPS,
        nsl.NEXT_SET_INCREASE_MIN_REPS_LOW_RPE,
        0,
        "default",
    )
    assert [rule.position for rule in padrao.rules] == [1, 2, 3]
    assert [rule.action for rule in padrao.rules] == [pe.INCREASE_LOAD, pe.INCREASE_LOAD, pe.DECREASE_LOAD]
    assert [rule.reason_code for rule in padrao.rules] == [
        nsl.REASON_REPS_ABOVE_LIMIT,
        nsl.REASON_LOW_RPE_AT_REPS,
        nsl.REASON_FAILURE_BEFORE_MIN_REPS,
    ]
    # a árvore do padrão é a conversão v1 das mesmas condições (paridade)
    esperado = [
        nsl.when_from_v1(reps=nsl.Condition(GT, Decimal(15))),
        nsl.when_from_v1(reps=nsl.Condition(GTE, Decimal(12)), rpe=nsl.Condition(LT, Decimal(9)), respect_planned_ramp=True),
        nsl.when_from_v1(reps=nsl.Condition(LT, Decimal(6)), rpe=nsl.Condition(GTE, Decimal(10))),
    ]
    assert [rule.when for rule in padrao.rules] == esperado
    assert padrao.rules[1].when.children[1].ramp_cap is True
    assert all(rule.magnitude == nsl.ONE_STEP for rule in padrao.rules)
    assert nsl.validate_ruleset(padrao) == []


def test_campos_aditivos_do_resultado_no_conjunto_padrao():
    sobe = nsl.evaluate_ruleset(_facts(reps=16), nsl.DEFAULT_RULESET)
    assert (sobe.rule_position, sobe.ruleset_version) == (1, 0)
    assert sobe.rule_text == nsl.describe_rule(nsl.DEFAULT_RULESET.rules[0], nsl.DEFAULT_RULESET)
    assert sobe.magnitude == nsl.ONE_STEP
    assert sobe.to_dict()["magnitude"] == {"kind": "steps", "value": 1}
    # o motivo (e o rótulo) continuam os de sempre: o prompt v7 não muda
    assert sobe.reason_label == nsl.reason_labels()[nsl.REASON_REPS_ABOVE_LIMIT]
    dor = nsl.evaluate_ruleset(_facts(joint_pain=True), nsl.DEFAULT_RULESET)
    assert (dor.rule_position, dor.rule_text, dor.magnitude) == (None, None, nsl.ONE_STEP)
    mantem = nsl.evaluate_ruleset(_facts(reps=10, rpe=8), nsl.DEFAULT_RULESET)
    assert (mantem.rule_position, mantem.rule_text, mantem.magnitude) == (None, None, None)
    assert mantem.to_dict()["magnitude"] is None
    assert set(mantem.to_dict()) >= {"rule_position", "rule_text", "ruleset_version", "magnitude"}


# ---------------------------------------------------------------------------
# 2. Vocabulário novo
# ---------------------------------------------------------------------------

SOBE_PRIMEIRA = dict(
    set_position=nsl.SetPosition.FIRST,
    reps=nsl.Condition(GT, nsl.REPS_TOKEN_CEILING),
    action=pe.INCREASE_LOAD,
    magnitude=PERCENT_25,
)


@pytest.mark.parametrize(
    ("posicao", "indice", "casa"),
    [
        (nsl.SetPosition.FIRST, 1, True),
        (nsl.SetPosition.FIRST, 2, False),
        (nsl.SetPosition.FIRST, None, False),
        (nsl.SetPosition.SECOND, 2, True),
        (nsl.SetPosition.SECOND, 1, False),
        (nsl.SetPosition.SECOND, 3, False),
        (nsl.SetPosition.THIRD_PLUS, 3, True),
        (nsl.SetPosition.THIRD_PLUS, 7, True),
        (nsl.SetPosition.THIRD_PLUS, 2, False),
        (nsl.SetPosition.ANY, None, True),
        (nsl.SetPosition.ANY, 5, True),
    ],
)
def test_posicao_da_serie(posicao, indice, casa):
    conjunto = _ruleset(dict(SOBE_PRIMEIRA, set_position=posicao))
    resultado = nsl.evaluate_ruleset(_facts(reps=13, working_set_index=indice), conjunto)
    if casa:
        assert (resultado.direction, resultado.reason, resultado.rule_position) == (pe.INCREASE_LOAD, nsl.REASON_RULE_MATCHED, 1)
        assert resultado.ruleset_version == 7
    else:
        assert (resultado.direction, resultado.reason, resultado.rule_position) == (pe.HOLD, nsl.REASON_WITHIN_RULE, None)


@pytest.mark.parametrize(
    ("condicao", "faixa", "casa"),
    [
        (nsl.FatigueCondition.RESTED, PriorFatigueBand.RESTED, True),
        (nsl.FatigueCondition.RESTED, "rested", True),
        (nsl.FatigueCondition.RESTED, PriorFatigueBand.PRE_FATIGUED, False),
        (nsl.FatigueCondition.RESTED, None, False),
        (nsl.FatigueCondition.HEAVILY_FATIGUED, PriorFatigueBand.HEAVILY_FATIGUED, True),
        (nsl.FatigueCondition.ANY, None, True),
        (nsl.FatigueCondition.ANY, PriorFatigueBand.PRE_FATIGUED, True),
    ],
)
def test_faixa_de_fadiga(condicao, faixa, casa):
    conjunto = _ruleset(dict(SOBE_PRIMEIRA, set_position=nsl.SetPosition.ANY, fatigue=condicao))
    resultado = nsl.evaluate_ruleset(_facts(reps=13, fatigue_band=faixa), conjunto)
    assert resultado.direction == (pe.INCREASE_LOAD if casa else pe.HOLD)


def test_faixas_sao_as_do_dono_da_fadiga_previa():
    assert {item.value for item in nsl.FatigueCondition} == {"any", *(band.value for band in PriorFatigueBand)}
    rotulos = nsl.fatigue_condition_labels()
    assert rotulos["any"] == "qualquer"
    for band in PriorFatigueBand:
        assert rotulos[band.value] == PRIOR_FATIGUE_BAND_LABELS[band]


def test_tokens_de_reps_resolvem_pelo_conjunto():
    conjunto = _ruleset(
        dict(reps=nsl.Condition(GTE, nsl.REPS_TOKEN_CEILING), action=pe.INCREASE_LOAD, magnitude=nsl.ONE_STEP),
        dict(reps=nsl.Condition(LT, nsl.REPS_TOKEN_FLOOR), action=pe.DECREASE_LOAD, magnitude=nsl.ONE_STEP),
        reps_floor=8,
        reps_ceiling=14,
    )
    assert nsl.evaluate_ruleset(_facts(reps=14), conjunto).direction == pe.INCREASE_LOAD
    assert nsl.evaluate_ruleset(_facts(reps=13), conjunto).direction == pe.HOLD
    assert nsl.evaluate_ruleset(_facts(reps=7), conjunto).direction == pe.DECREASE_LOAD
    assert nsl.evaluate_ruleset(_facts(reps=8), conjunto).direction == pe.HOLD


def test_token_max_e_o_teto_efetivo_da_serie():
    conjunto = _ruleset(
        dict(reps=nsl.Condition(GT, nsl.REPS_TOKEN_CEILING), rpe=nsl.Condition(LT, nsl.RPE_TOKEN_MAX), action=pe.INCREASE_LOAD, magnitude=PERCENT_25)
    )
    assert nsl.evaluate_ruleset(_facts(reps=13, rpe=8, rpe_ceiling=9), conjunto).direction == pe.INCREASE_LOAD
    assert nsl.evaluate_ruleset(_facts(reps=13, rpe=9, rpe_ceiling=9), conjunto).direction == pe.HOLD
    # teto desconhecido ou RPE ausente: a condição de RPE não casa
    assert nsl.evaluate_ruleset(_facts(reps=13, rpe=8, rpe_ceiling=None), conjunto).direction == pe.HOLD
    assert nsl.evaluate_ruleset(_facts(reps=13, rpe=None, rpe_ceiling=9), conjunto).direction == pe.HOLD


def test_regra_sem_condicao_de_rpe_nao_exige_rpe():
    conjunto = _ruleset(dict(reps=nsl.Condition(GT, Decimal(12)), action=pe.INCREASE_LOAD, magnitude=nsl.ONE_STEP))
    assert nsl.evaluate_ruleset(_facts(reps=13, rpe=None), conjunto).direction == pe.INCREASE_LOAD


@pytest.mark.parametrize(("reps", "casa"), [(7, False), (8, True), (10, True), (12, True), (13, False)])
def test_between_e_fechado_nos_dois_lados(reps, casa):
    conjunto = _ruleset(dict(reps=nsl.Condition(BETWEEN, Decimal(8), Decimal(12)), action=pe.HOLD))
    resultado = nsl.evaluate_ruleset(_facts(reps=reps), conjunto)
    assert (resultado.reason == nsl.REASON_RULE_MATCHED) is casa


def test_between_com_tokens_e_sem_maximo():
    conjunto = _ruleset(dict(reps=nsl.Condition(BETWEEN, nsl.REPS_TOKEN_FLOOR, nsl.REPS_TOKEN_CEILING), action=pe.HOLD))
    assert nsl.evaluate_ruleset(_facts(reps=6), conjunto).reason == nsl.REASON_RULE_MATCHED
    assert nsl.evaluate_ruleset(_facts(reps=13), conjunto).reason == nsl.REASON_WITHIN_RULE
    sem_maximo = _ruleset(dict(reps=nsl.Condition(BETWEEN, Decimal(8)), action=pe.HOLD))
    assert nsl.evaluate_ruleset(_facts(reps=10), sem_maximo).reason == nsl.REASON_WITHIN_RULE


def test_rampa_planejada_barra_a_regra_e_vira_motivo_de_rampa():
    conjunto = _ruleset(
        dict(reps=nsl.Condition(GTE, Decimal(12)), rpe=nsl.Condition(LT, Decimal(9)), respect_planned_ramp=True, action=pe.INCREASE_LOAD, magnitude=nsl.ONE_STEP)
    )
    barrada = nsl.evaluate_ruleset(_facts(reps=12, rpe=8, planned_rpe=8), conjunto)
    assert (barrada.direction, barrada.reason, barrada.planned_rpe) == (pe.HOLD, nsl.REASON_WITHIN_PLANNED_RAMP, 8.0)
    assert nsl.evaluate_ruleset(_facts(reps=12, rpe=7, planned_rpe=8), conjunto).direction == pe.INCREASE_LOAD
    # sem `respect_planned_ramp` a rampa não existe para a regra
    sem_rampa = _ruleset(dict(reps=nsl.Condition(GTE, Decimal(12)), rpe=nsl.Condition(LT, Decimal(9)), action=pe.INCREASE_LOAD, magnitude=nsl.ONE_STEP))
    assert nsl.evaluate_ruleset(_facts(reps=12, rpe=8, planned_rpe=8), sem_rampa).direction == pe.INCREASE_LOAD
    # `between`: a rampa corta o máximo; `>` não muda
    entre = _ruleset(dict(rpe=nsl.Condition(BETWEEN, Decimal(7), Decimal(9)), respect_planned_ramp=True, action=pe.HOLD))
    assert nsl.evaluate_ruleset(_facts(rpe=8.5, planned_rpe=8), entre).reason == nsl.REASON_WITHIN_PLANNED_RAMP
    assert nsl.evaluate_ruleset(_facts(rpe=8, planned_rpe=8), entre).reason == nsl.REASON_RULE_MATCHED
    acima = _ruleset(dict(rpe=nsl.Condition(GT, Decimal(7)), respect_planned_ramp=True, action=pe.HOLD))
    assert nsl.evaluate_ruleset(_facts(rpe=7.5, planned_rpe=8), acima).reason == nsl.REASON_RULE_MATCHED


def test_primeira_regra_que_casa_vence_mesmo_depois_de_uma_barrada_pela_rampa():
    conjunto = _ruleset(
        dict(reps=nsl.Condition(GTE, Decimal(12)), rpe=nsl.Condition(LT, Decimal(9)), respect_planned_ramp=True, action=pe.INCREASE_LOAD, magnitude=nsl.ONE_STEP),
        dict(reps=nsl.Condition(GTE, Decimal(12)), action=pe.DECREASE_LOAD, magnitude=nsl.ONE_STEP),
        dict(reps=nsl.Condition(GTE, Decimal(12)), action=pe.INCREASE_LOAD, magnitude=STEPS_2),
    )
    resultado = nsl.evaluate_ruleset(_facts(reps=12, rpe=8, planned_rpe=8), conjunto)
    assert (resultado.direction, resultado.reason, resultado.rule_position, resultado.load_kg) == (
        pe.DECREASE_LOAD,
        nsl.REASON_RULE_MATCHED,
        2,
        17.5,
    )


def test_dor_vale_antes_de_qualquer_regra_e_nao_e_configuravel():
    conjunto = _ruleset(dict(reps=nsl.Condition(GT, Decimal(1)), action=pe.INCREASE_LOAD, magnitude=PERCENT_25))
    dor = nsl.evaluate_ruleset(_facts(reps=16, joint_pain=True), conjunto)
    assert (dor.direction, dor.reason, dor.load_kg, dor.rule_position) == (pe.DECREASE_LOAD, nsl.REASON_PAIN, 17.5, None)
    muscular = nsl.evaluate_ruleset(_facts(reps=16, muscle_pain=MUSCLE_PAIN_BLOCK_ABOVE + 1), conjunto)
    assert muscular.reason == nsl.REASON_PAIN
    assert nsl.evaluate_ruleset(_facts(reps=16, muscle_pain=MUSCLE_PAIN_BLOCK_ABOVE), conjunto).direction == pe.INCREASE_LOAD


@pytest.mark.parametrize(
    "kwargs",
    [
        {"set_type": "warmup"},
        {"set_type": "drop_set"},
        {"weight_kg": 0},
        {"weight_kg": None},
        {"reps": None},
        {"reps": 0},
        {"load_convention": "assisted"},
        {"load_convention": "bodyweight"},
    ],
)
def test_fora_de_escopo_continua_sem_sugestao(kwargs):
    assert nsl.evaluate_ruleset(_facts(**{"reps": 16, **kwargs}), nsl.DEFAULT_RULESET) is None


# ---------------------------------------------------------------------------
# 3. Carga: passos e percentual (só no dono, arredondado ao passo)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("carga", "magnitude", "acao", "esperado"),
    [
        (21, PERCENT_25, pe.INCREASE_LOAD, 27.5),  # 26,25 -> 27,5
        (21, nsl.Magnitude(nsl.MagnitudeKind.PERCENT, Decimal(5)), pe.INCREASE_LOAD, 22.5),  # 22,05 -> 22,5
        (20, nsl.Magnitude(nsl.MagnitudeKind.PERCENT, Decimal(5)), pe.INCREASE_LOAD, 22.5),  # 21 -> 20 == executada: um passo
        (20, PERCENT_25, pe.INCREASE_LOAD, 25.0),
        (25, nsl.Magnitude(nsl.MagnitudeKind.PERCENT, Decimal(20)), pe.DECREASE_LOAD, 20.0),
        (20, nsl.Magnitude(nsl.MagnitudeKind.PERCENT, Decimal(5)), pe.DECREASE_LOAD, 17.5),  # 19 -> 20: um passo abaixo
        (2.5, nsl.Magnitude(nsl.MagnitudeKind.PERCENT, Decimal(20)), pe.DECREASE_LOAD, None),  # sem passo abaixo
        (21.3, PERCENT_25, pe.INCREASE_LOAD, 27.5),  # fora do passo (libra): 26,6 -> 27,5
        (20, STEPS_2, pe.INCREASE_LOAD, 25.0),
        (20, STEPS_2, pe.DECREASE_LOAD, 15.0),
        (5, STEPS_2, pe.DECREASE_LOAD, None),  # 5 - 5 = 0
        (2.5, nsl.ONE_STEP, pe.DECREASE_LOAD, None),
        (21.3, nsl.ONE_STEP, pe.INCREASE_LOAD, 25.0),
        (20, None, pe.INCREASE_LOAD, 22.5),  # sem magnitude: um passo
    ],
)
def test_magnitude_em_passos_e_percentual(carga, magnitude, acao, esperado):
    conjunto = _ruleset(dict(reps=nsl.Condition(GT, Decimal(1)), action=acao, magnitude=magnitude))
    resultado = nsl.evaluate_ruleset(_facts(reps=10, weight_kg=carga), conjunto)
    assert (resultado.direction, resultado.load_kg) == (acao, esperado)
    assert resultado.executed_kg == float(carga) and resultado.step_kg == 2.5
    assert resultado.magnitude == (magnitude or nsl.ONE_STEP)


def test_percentual_com_passo_de_1_kg():
    conjunto = _ruleset(dict(reps=nsl.Condition(GT, Decimal(1)), action=pe.INCREASE_LOAD, magnitude=nsl.Magnitude(nsl.MagnitudeKind.PERCENT, Decimal(10))))
    assert nsl.evaluate_ruleset(_facts(reps=10, weight_kg=33, step_kg=1), conjunto).load_kg == 36.0  # 36,3 -> 36
    assert nsl.evaluate_ruleset(_facts(reps=10, weight_kg=4, step_kg=1), conjunto).load_kg == 5.0  # 4,4 -> 4: um passo


def test_manter_devolve_a_carga_executada_exata():
    conjunto = _ruleset(dict(reps=nsl.Condition(GT, Decimal(1)), action=pe.HOLD))
    resultado = nsl.evaluate_ruleset(_facts(reps=10, weight_kg=45.36), conjunto)
    assert (resultado.direction, resultado.load_kg, resultado.magnitude) == (pe.HOLD, 45.36, None)


# ---------------------------------------------------------------------------
# 4. Validação, texto, JSON e espelho
# ---------------------------------------------------------------------------


def _regra(**kwargs):
    return {"reps": nsl.Condition(GT, Decimal(12)), "action": pe.INCREASE_LOAD, "magnitude": nsl.ONE_STEP, **kwargs}


def test_validate_ruleset_limites_nomeados():
    assert nsl.validate_ruleset(_ruleset()) == [nsl.RULESET_EMPTY]
    demais = _ruleset(*[_regra() for _ in range(nsl.RULESET_MAX_RULES + 1)])
    assert nsl.RULESET_TOO_MANY_RULES in nsl.validate_ruleset(demais)
    assert nsl.REPS_FLOOR_BELOW_MIN in nsl.validate_ruleset(_ruleset(_regra(), reps_floor=nsl.REPS_FLOOR_MIN - 1))
    assert nsl.REPS_CEILING_ABOVE_MAX in nsl.validate_ruleset(_ruleset(_regra(), reps_ceiling=nsl.REPS_CEILING_MAX + 1))
    assert nsl.REPS_FLOOR_NOT_BELOW_CEILING in nsl.validate_ruleset(_ruleset(_regra(), reps_floor=12, reps_ceiling=12))
    fora_de_ordem = nsl.RuleSet(reps_floor=6, reps_ceiling=12, rules=(regra_v1(2, **_regra()),))
    assert nsl.RULESET_POSITIONS_NOT_SEQUENTIAL in nsl.validate_ruleset(fora_de_ordem)


@pytest.mark.parametrize(
    ("regra", "codigo"),
    [
        (dict(reps=nsl.Condition(GT, Decimal(12)), action="JUMP"), nsl.RULE_ACTION_UNKNOWN),
        (dict(action=pe.HOLD), nsl.RULE_WITHOUT_CONDITION),
        (dict(reps=nsl.Condition(GT, Decimal(12)), action=pe.HOLD, magnitude=nsl.ONE_STEP), nsl.RULE_HOLD_WITH_MAGNITUDE),
        (dict(reps=nsl.Condition(GT, Decimal(12)), action=pe.INCREASE_LOAD), nsl.RULE_MAGNITUDE_MISSING),
        (_regra(magnitude=nsl.Magnitude(nsl.MagnitudeKind.PERCENT, Decimal(nsl.MAGNITUDE_PERCENT_MAX + 1))), nsl.RULE_MAGNITUDE_OUT_OF_RANGE),
        (_regra(magnitude=nsl.Magnitude(nsl.MagnitudeKind.PERCENT, Decimal(nsl.MAGNITUDE_PERCENT_MIN - 1))), nsl.RULE_MAGNITUDE_OUT_OF_RANGE),
        (_regra(magnitude=nsl.Magnitude(nsl.MagnitudeKind.STEPS, Decimal(nsl.MAGNITUDE_STEPS_MAX + 1))), nsl.RULE_MAGNITUDE_OUT_OF_RANGE),
        (_regra(magnitude=nsl.Magnitude(nsl.MagnitudeKind.STEPS, Decimal("1.5"))), nsl.RULE_STEPS_NOT_INTEGER),
        (_regra(reps=nsl.Condition(GT, nsl.RPE_TOKEN_MAX)), nsl.RULE_REPS_TOKEN_UNKNOWN),
        (_regra(reps=nsl.Condition(GT, Decimal(nsl.REPS_CEILING_MAX + 1))), nsl.RULE_REPS_OUT_OF_RANGE),
        (_regra(rpe=nsl.Condition(LT, nsl.REPS_TOKEN_FLOOR)), nsl.RULE_RPE_TOKEN_UNKNOWN),
        (_regra(rpe=nsl.Condition(LT, Decimal(nsl.RULE_RPE_MAX + 1))), nsl.RULE_RPE_OUT_OF_RANGE),
        (_regra(rpe=nsl.Condition(LT, Decimal(nsl.RULE_RPE_MIN - 1))), nsl.RULE_RPE_OUT_OF_RANGE),
        (_regra(reps=nsl.Condition(BETWEEN, Decimal(12), Decimal(8))), nsl.RULE_BETWEEN_INVALID),
        (_regra(reps=nsl.Condition(BETWEEN, Decimal(8))), nsl.RULE_BETWEEN_INVALID),
        (_regra(reps=nsl.Condition(GT, Decimal(8), Decimal(12))), nsl.RULE_BETWEEN_INVALID),
        (_regra(respect_planned_ramp=True), nsl.RULE_RAMP_WITHOUT_RPE),
    ],
)
def test_validate_ruleset_por_regra(regra, codigo):
    codigos = nsl.validate_ruleset(_ruleset(regra, dict(reps=nsl.Condition(LT, Decimal(6)), action=pe.DECREASE_LOAD, magnitude=nsl.ONE_STEP)))
    assert f"{codigo}:1" in codigos
    assert nsl.WARN_NO_DECREASE_RULE not in codigos


def test_sem_regra_de_reducao_e_aviso_e_regra_inalcancavel_tambem():
    conjunto = _ruleset(_regra(), dict(action=pe.HOLD), _regra())
    codigos = nsl.validate_ruleset(conjunto)
    assert nsl.WARN_NO_DECREASE_RULE in codigos
    assert f"{nsl.RULE_WITHOUT_CONDITION}:2" in codigos and f"{nsl.WARN_RULE_UNREACHABLE}:3" in codigos
    assert nsl.ruleset_errors(conjunto) == [f"{nsl.RULE_WITHOUT_CONDITION}:2"]
    assert all(code.startswith(nsl.RULESET_WARNING_PREFIX) for code in set(codigos) - set(nsl.ruleset_errors(conjunto)))


def test_describe_rule_em_pt_br():
    conjunto = _ruleset(
        dict(
            set_position=nsl.SetPosition.SECOND,
            fatigue=nsl.FatigueCondition.RESTED,
            reps=nsl.Condition(GT, nsl.REPS_TOKEN_CEILING),
            rpe=nsl.Condition(LT, nsl.RPE_TOKEN_MAX),
            respect_planned_ramp=True,
            action=pe.INCREASE_LOAD,
            magnitude=PERCENT_25,
        ),
        dict(reps=nsl.Condition(BETWEEN, nsl.REPS_TOKEN_FLOOR, Decimal(10)), rpe=nsl.Condition(GTE, Decimal("8.5")), action=pe.DECREASE_LOAD, magnitude=STEPS_2),
        dict(set_position=nsl.SetPosition.THIRD_PLUS, fatigue=nsl.FatigueCondition.HEAVILY_FATIGUED, action=pe.HOLD),
        dict(action=pe.HOLD),
    )
    textos = [nsl.describe_rule(rule, conjunto) for rule in conjunto.rules]
    assert textos == [
        "SE série = 2 E fadiga = descansado E reps > teto E RPE < máximo (limitado à rampa planejada) ENTÃO carga = carga executada + 25%",
        "SE reps entre piso e 10 E RPE ≥ 8,5 ENTÃO carga = carga executada − 2 passos",
        "SE série ≥ 3 E fadiga = muito fatigado ENTÃO carga = carga executada",
        "SE sempre ENTÃO carga = carga executada",
    ]
    assert nsl.describe_ruleset(conjunto).splitlines()[:2] == ["piso 6 reps · teto 12 reps", f"1. {textos[0]}"]
    assert nsl.describe_ruleset(nsl.DEFAULT_RULESET).splitlines()[1:] == [
        f"{rule.position}. {nsl.describe_rule(rule, nsl.DEFAULT_RULESET)}" for rule in nsl.DEFAULT_RULESET.rules
    ]


def test_regra_cadastrada_sem_codigo_usa_o_texto_como_rotulo():
    conjunto = _ruleset(dict(SOBE_PRIMEIRA, set_position=nsl.SetPosition.ANY))
    resultado = nsl.evaluate_ruleset(_facts(reps=13), conjunto)
    assert resultado.reason == nsl.REASON_RULE_MATCHED
    assert resultado.reason_label == resultado.rule_text == nsl.describe_rule(conjunto.rules[0], conjunto)
    assert resultado.to_dict()["reason_label"] == resultado.rule_text
    assert nsl.reason_labels()[nsl.REASON_RULE_MATCHED] == "Regra de carga cadastrada"
    assert nsl.REASON_RULE_MATCHED in nsl.NEXT_SET_REASONS


def test_round_trip_do_json():
    conjunto = _ruleset(
        dict(
            set_position=nsl.SetPosition.FIRST,
            fatigue=nsl.FatigueCondition.RESTED,
            reps=nsl.Condition(GT, nsl.REPS_TOKEN_CEILING),
            rpe=nsl.Condition(LT, nsl.RPE_TOKEN_MAX),
            respect_planned_ramp=True,
            action=pe.INCREASE_LOAD,
            magnitude=PERCENT_25,
        ),
        dict(reps=nsl.Condition(BETWEEN, Decimal(8), Decimal("12.5")), rpe=nsl.Condition(GTE, Decimal(10)), action=pe.DECREASE_LOAD, magnitude=STEPS_2),
        dict(action=pe.HOLD, reps=nsl.Condition(LTE, Decimal(5))),
    )
    dados = nsl.ruleset_to_dict(conjunto)
    json.dumps(dados)  # serializável
    assert dados["rules"][0] == {
        "position": 1,
        "when": {
            "logic": "and",
            "children": [
                {"variable": "set_index", "scope": "current", "op": "=", "value": 1},
                {"variable": "fatigue_band", "scope": "current", "op": "=", "value": "rested"},
                {"variable": "reps", "scope": "current", "op": ">", "value": "ceiling"},
                {"variable": "rpe", "scope": "current", "op": "<", "value": "max", "ramp_cap": True},
            ],
        },
        "action": pe.INCREASE_LOAD,
        "magnitude": {"kind": "percent", "value": 25},
    }
    assert dados["rules"][1]["when"]["children"][0] == {"variable": "reps", "scope": "current", "op": "between", "value": 8, "value_to": 12.5}
    assert dados["rules"][2]["magnitude"] is None and "reason_code" not in dados["rules"][2]
    assert nsl.ruleset_from_dict(dados) == conjunto
    assert nsl.ruleset_to_dict(nsl.ruleset_from_dict(json.loads(json.dumps(dados)))) == dados
    # o padrão também vai e volta (com os `reason_code`)
    assert nsl.ruleset_from_dict(nsl.ruleset_to_dict(nsl.DEFAULT_RULESET)) == nsl.DEFAULT_RULESET
    # `position` vem da ordem na lista; version/source explícitos vencem
    sem_posicao = {**dados, "rules": [{k: v for k, v in rule.items() if k != "position"} for rule in dados["rules"]]}
    lido = nsl.ruleset_from_dict(sem_posicao, version=3, source="banco")
    assert [rule.position for rule in lido.rules] == [1, 2, 3] and (lido.version, lido.source) == (3, "banco")


@pytest.mark.parametrize(
    "dados",
    [
        {"reps_floor": 6, "reps_ceiling": 12, "rules": [{"action": "HOLD", "reps": {"op": "~", "value": 1}}]},
        {"reps_floor": 6, "reps_ceiling": 12, "rules": [{"action": "HOLD", "set_position": "fourth"}]},
        {"reps_floor": 6, "reps_ceiling": 12, "rules": [{"action": "HOLD", "fatigue": "tired"}]},
        {"reps_floor": 6, "reps_ceiling": 12, "rules": [{"action": "HOLD", "reps": {"op": ">", "value": "teto"}}]},
        {"reps_floor": 6, "reps_ceiling": 12, "rules": [{"action": "INCREASE_LOAD", "magnitude": {"kind": "kg", "value": 1}}]},
        {"reps_floor": 6, "rules": []},
        {"reps_floor": 6, "reps_ceiling": 12, "rules": {}},
    ],
)
def test_json_invalido_levanta_value_error(dados):
    with pytest.raises(ValueError):
        nsl.ruleset_from_dict(dados)


def test_export_para_o_fe_tem_vocabulario_limites_e_conjunto_padrao():
    bloco = nsl.export_for_frontend()
    json.dumps(bloco, ensure_ascii=False)  # serializável (Decimal já convertido)
    assert bloco["reason_rule_matched"] == nsl.REASON_RULE_MATCHED
    assert bloco["default_ruleset"] == nsl.ruleset_to_dict(nsl.DEFAULT_RULESET)
    vocabulario = bloco["rule_vocabulary"]
    assert [item["value"] for item in vocabulario["set_positions"]] == [item.value for item in nsl.SetPosition]
    assert [item["value"] for item in vocabulario["fatigue_conditions"]] == ["any", *(band.value for band in PriorFatigueBand)]
    assert {item["value"]: item["label"] for item in vocabulario["fatigue_conditions"]} == nsl.fatigue_condition_labels()
    assert [item["value"] for item in vocabulario["condition_ops"]] == [">", ">=", "<", "<=", "between", "=", "!="]
    assert [item["value"] for item in vocabulario["reps_tokens"]] == list(nsl.REPS_TOKENS)
    assert [item["value"] for item in vocabulario["rpe_tokens"]] == list(nsl.RPE_TOKENS)
    assert {item["value"]: item["label"] for item in vocabulario["actions"]} == {
        a: DECISION_LABELS.get(a, nsl.ENGINE_TARGET_LABEL) for a in nsl.RULE_ACTIONS
    }
    assert {item["value"]: item["magnitude"] for item in vocabulario["actions"]} == dict(nsl.ACTION_MAGNITUDE_RULE)
    assert {item["value"] for item in vocabulario["variables"]} == set(nsl.VARIABLES)
    assert [item["value"] for item in vocabulario["scopes"]] == list(nsl.SCOPES)
    assert [item["value"] for item in vocabulario["logic_ops"]] == list(nsl.LOGIC_OPS)
    assert nsl.RULESET_ENGINE_VERSION == "next-set-load-rules-v2"
    assert [item["value"] for item in vocabulario["magnitude_kinds"]] == ["steps", "percent"]
    assert bloco["rule_limits"] == {
        "max_rules": nsl.RULESET_MAX_RULES,
        "max_depth": nsl.RULESET_MAX_DEPTH,
        "max_leaves_per_rule": nsl.RULESET_MAX_LEAVES_PER_RULE,
        "set_index_max": nsl.RULE_SET_INDEX_MAX,
        "magnitude_percent_min": nsl.MAGNITUDE_PERCENT_MIN,
        "magnitude_percent_max": nsl.MAGNITUDE_PERCENT_MAX,
        "magnitude_steps_min": nsl.MAGNITUDE_STEPS_MIN,
        "magnitude_steps_max": nsl.MAGNITUDE_STEPS_MAX,
        "reps_floor_min": nsl.REPS_FLOOR_MIN,
        "reps_ceiling_max": nsl.REPS_CEILING_MAX,
        "rule_rpe_min": nsl.RULE_RPE_MIN,
        "rule_rpe_max": nsl.RULE_RPE_MAX,
    }


def test_suggest_next_set_load_aceita_os_fatos_novos():
    conjunto = _ruleset(dict(SOBE_PRIMEIRA, set_position=nsl.SetPosition.SECOND, fatigue=nsl.FatigueCondition.RESTED, rpe=nsl.Condition(LT, nsl.RPE_TOKEN_MAX)))
    resultado = nsl.suggest_next_set_load(
        set_type="working",
        reps=13,
        weight_kg=20,
        rpe=8,
        step_kg=PASSO,
        working_set_index=2,
        fatigue_band=PriorFatigueBand.RESTED,
        rpe_ceiling=10,
        ruleset=conjunto,
    )
    assert (resultado.direction, resultado.load_kg, resultado.rule_position, resultado.ruleset_version) == (pe.INCREASE_LOAD, 25.0, 1, 7)


# ---------------------------------------------------------------------------
# 5. Árvore v2: OR, aninhamento, limites, escopo previous, ENGINE_TARGET, ramp_cap, alvo
# ---------------------------------------------------------------------------

VR, VP = nsl.VAR_REPS, nsl.VAR_RPE
EQ = nsl.ConditionOp.EQ


def _arvore(when, *, action=pe.INCREASE_LOAD, magnitude=nsl.ONE_STEP):
    return nsl.RuleSet(
        reps_floor=6,
        reps_ceiling=12,
        rules=(nsl.Rule(position=1, when=when, action=action, magnitude=magnitude),),
        version=9,
        source="teste",
    )


def _dir(when, facts, **kw):
    return nsl.evaluate_ruleset(facts, _arvore(when, **kw))


def test_or_basta_um_filho():
    when = nsl.any_of(nsl.leaf(VR, GT, Decimal(14)), nsl.leaf(VP, LTE, Decimal(6)))
    assert _dir(when, _facts(reps=15, rpe=9)).direction == pe.INCREASE_LOAD
    assert _dir(when, _facts(reps=8, rpe=6)).direction == pe.INCREASE_LOAD
    assert _dir(when, _facts(reps=8, rpe=9)).direction == pe.HOLD


def test_aninhamento_ate_a_profundidade_3_e_falha_na_4():
    folha = nsl.leaf(VR, GT, Decimal(12))
    d3 = nsl.all_of(nsl.any_of(nsl.all_of(folha)))
    d4 = nsl.all_of(nsl.any_of(nsl.all_of(nsl.any_of(folha))))
    assert nsl.RULESET_MAX_DEPTH == 3
    assert f"{nsl.RULE_DEPTH_EXCEEDED}:1" not in nsl.validate_ruleset(_arvore(d3))
    assert f"{nsl.RULE_DEPTH_EXCEEDED}:1" in nsl.validate_ruleset(_arvore(d4))
    assert _dir(d3, _facts(reps=13)).direction == pe.INCREASE_LOAD


def test_limite_de_8_folhas_por_regra():
    folhas = [nsl.leaf(VR, GT, Decimal(i)) for i in range(1, 10)]
    assert nsl.RULESET_MAX_LEAVES_PER_RULE == 8
    assert f"{nsl.RULE_TOO_MANY_LEAVES}:1" not in nsl.validate_ruleset(_arvore(nsl.all_of(*folhas[:8])))
    assert f"{nsl.RULE_TOO_MANY_LEAVES}:1" in nsl.validate_ruleset(_arvore(nsl.all_of(*folhas)))


def test_codigos_de_validacao_da_arvore():
    def codigos(when):
        return nsl.validate_ruleset(_arvore(when))

    assert f"{nsl.RULE_GROUP_EMPTY}:1" in codigos(nsl.all_of(nsl.all_of()))
    assert f"{nsl.RULE_VARIABLE_UNKNOWN}:1" in codigos(nsl.all_of(nsl.leaf("peso", GT, 1)))
    assert f"{nsl.RULE_SCOPE_INVALID}:1" in codigos(nsl.all_of(nsl.leaf(nsl.VAR_FATIGUE_BAND, EQ, "rested", scope=nsl.SCOPE_PREVIOUS)))
    assert f"{nsl.RULE_OP_INVALID_FOR_VARIABLE}:1" in codigos(nsl.all_of(nsl.leaf(nsl.VAR_FATIGUE_BAND, GT, "rested")))
    assert f"{nsl.RULE_RAMP_CAP_INVALID}:1" in codigos(nsl.all_of(nsl.leaf(VR, GT, Decimal(1), ramp_cap=True)))
    assert f"{nsl.RULE_SET_INDEX_OUT_OF_RANGE}:1" in codigos(nsl.all_of(nsl.leaf(nsl.VAR_SET_INDEX, EQ, nsl.RULE_SET_INDEX_MAX + 1)))
    assert f"{nsl.RULE_ENUM_VALUE_UNKNOWN}:1" in codigos(nsl.all_of(nsl.leaf(nsl.VAR_FATIGUE_BAND, EQ, "tired")))
    assert f"{nsl.RULE_LOGIC_UNKNOWN}:1" in codigos(nsl.ConditionGroup("xor", (nsl.leaf(VR, GT, Decimal(1)),)))


def test_escopo_previous_com_fato_ausente_nao_casa():
    when = nsl.all_of(nsl.leaf(VP, GTE, Decimal(9), scope=nsl.SCOPE_PREVIOUS))
    assert _dir(when, _facts()).direction == pe.HOLD  # 1ª série: sem anterior
    assert _dir(when, _facts(previous=nsl.PreviousSetFacts(reps=10, rpe=9.5, weight_kg=20))).direction == pe.INCREASE_LOAD
    assert _dir(when, _facts(previous=nsl.PreviousSetFacts(reps=10, rpe=None))).direction == pe.HOLD
    assert _dir(when, _facts(previous=nsl.PreviousSetFacts(reps=10, rpe=8))).direction == pe.HOLD


def test_token_target_e_o_rpe_alvo_da_serie():
    when = nsl.all_of(nsl.leaf(VP, LT, nsl.RPE_TOKEN_TARGET))
    assert _dir(when, _facts(rpe=7, rpe_target=8)).direction == pe.INCREASE_LOAD
    assert _dir(when, _facts(rpe=8, rpe_target=8)).direction == pe.HOLD
    assert _dir(when, _facts(rpe=7, rpe_target=None)).direction == pe.HOLD  # sem alvo não casa
    igual = nsl.all_of(nsl.leaf(VP, EQ, nsl.RPE_TOKEN_TARGET))
    assert _dir(igual, _facts(rpe=8, rpe_target=8)).direction == pe.INCREASE_LOAD
    assert _dir(igual, _facts(rpe=8, rpe_target=None)).direction == pe.HOLD


@pytest.mark.parametrize(
    ("alvo", "magnitude", "executada", "direcao", "carga"),
    [
        (30, None, 20, pe.INCREASE_LOAD, 30.0),
        (30, STEPS_2, 20, pe.INCREASE_LOAD, 35.0),
        (30, nsl.Magnitude(nsl.MagnitudeKind.STEPS, Decimal(-2)), 20, pe.INCREASE_LOAD, 25.0),
        (30, nsl.Magnitude(nsl.MagnitudeKind.PERCENT, Decimal(10)), 20, pe.INCREASE_LOAD, 32.5),  # 33 -> 32,5
        (30, nsl.Magnitude(nsl.MagnitudeKind.PERCENT, Decimal(-10)), 20, pe.INCREASE_LOAD, 27.5),  # 27 -> 27,5
        (20, None, 20, pe.HOLD, 20.0),
        (15, None, 20, pe.DECREASE_LOAD, 15.0),
    ],
)
def test_engine_target_com_alvo_e_direcao_derivada(alvo, magnitude, executada, direcao, carga):
    when = nsl.all_of(nsl.leaf(VR, GT, Decimal(1)))
    r = _dir(when, _facts(weight_kg=executada, engine_target_kg=alvo), action=nsl.ACTION_ENGINE_TARGET, magnitude=magnitude)
    assert (r.direction, r.load_kg, r.reason, r.rule_position) == (direcao, carga, nsl.REASON_RULE_MATCHED, 1)


def test_engine_target_e_limitado_pelo_teto_do_equipamento():
    when = nsl.all_of(nsl.leaf(VR, GT, Decimal(1)))
    r = _dir(when, _facts(weight_kg=20, engine_target_kg=40, max_load_kg=30), action=nsl.ACTION_ENGINE_TARGET, magnitude=None)
    assert (r.direction, r.load_kg) == (pe.INCREASE_LOAD, 30.0)
    topo = _dir(when, _facts(weight_kg=30, engine_target_kg=40, max_load_kg=30), action=nsl.ACTION_ENGINE_TARGET, magnitude=None)
    assert (topo.direction, topo.reason, topo.load_kg) == (pe.HOLD, nsl.REASON_AT_EQUIPMENT_CEILING, 30.0)


def test_engine_target_sem_alvo_cai_na_proxima_regra():
    when = nsl.all_of(nsl.leaf(VR, GT, Decimal(1)))
    conjunto = nsl.RuleSet(
        reps_floor=6,
        reps_ceiling=12,
        rules=(
            nsl.Rule(position=1, when=when, action=nsl.ACTION_ENGINE_TARGET),
            nsl.Rule(position=2, when=when, action=pe.INCREASE_LOAD, magnitude=nsl.ONE_STEP),
        ),
    )
    assert nsl.evaluate_ruleset(_facts(engine_target_kg=None), conjunto).rule_position == 2
    assert nsl.evaluate_ruleset(_facts(engine_target_kg=0), conjunto).rule_position == 2
    assert nsl.evaluate_ruleset(_facts(engine_target_kg=30), conjunto).rule_position == 1
    assert nsl.ruleset_uses_engine_target(conjunto) is True


def test_ramp_cap_preserva_o_motivo_de_rampa_quando_so_o_cap_barrou():
    when = nsl.all_of(nsl.leaf(VR, GTE, Decimal(12)), nsl.leaf(VP, LT, Decimal(9), ramp_cap=True))
    barrada = _dir(when, _facts(reps=12, rpe=8, planned_rpe=8))
    assert (barrada.direction, barrada.reason) == (pe.HOLD, nsl.REASON_WITHIN_PLANNED_RAMP)
    assert _dir(when, _facts(reps=12, rpe=7, planned_rpe=8)).direction == pe.INCREASE_LOAD
    # barrada por outra folha (reps) não é motivo de rampa
    assert _dir(when, _facts(reps=10, rpe=8, planned_rpe=8)).reason == nsl.REASON_WITHIN_RULE
    # ramp_cap dentro de OR: outro ramo que casa vence
    ou = nsl.any_of(nsl.leaf(VP, LT, Decimal(9), ramp_cap=True), nsl.leaf(VR, GT, Decimal(14)))
    assert _dir(ou, _facts(reps=15, rpe=8, planned_rpe=8)).direction == pe.INCREASE_LOAD
    assert _dir(ou, _facts(reps=10, rpe=8, planned_rpe=8)).reason == nsl.REASON_WITHIN_PLANNED_RAMP


def test_to_dict_do_resultado_e_aditivo_e_traz_as_chaves_antigas():
    dados = nsl.evaluate_ruleset(_facts(reps=16), nsl.DEFAULT_RULESET).to_dict()
    for chave in ("direction", "load_kg", "reason", "reason_label", "executed_kg", "step_kg", "planned_rpe", "rule_position", "rule_text", "ruleset_version"):
        assert chave in dados, chave
    json.dumps(dados, ensure_ascii=False)


def test_json_v1_le_e_escreve_v2_com_when():
    v1 = {
        "reps_floor": 6,
        "reps_ceiling": 12,
        "rules": [
            {
                "set_position": "first",
                "fatigue": "rested",
                "reps": {"op": ">", "value": "ceiling"},
                "rpe": {"op": "<", "value": "max"},
                "respect_planned_ramp": True,
                "action": "INCREASE_LOAD",
                "magnitude": {"kind": "percent", "value": 25},
            },
            {"action": "DECREASE_LOAD", "magnitude": {"kind": "steps", "value": 1}, "reps": {"op": "<", "value": "floor"}},
        ],
    }
    lido = nsl.ruleset_from_dict(v1)
    assert lido.rules[0] == regra_v1(
        1,
        set_position=nsl.SetPosition.FIRST,
        fatigue=nsl.FatigueCondition.RESTED,
        reps=nsl.Condition(GT, nsl.REPS_TOKEN_CEILING),
        rpe=nsl.Condition(LT, nsl.RPE_TOKEN_MAX),
        respect_planned_ramp=True,
        action=pe.INCREASE_LOAD,
        magnitude=PERCENT_25,
    )
    escrito = nsl.ruleset_to_dict(lido)
    assert all("when" in rule and "set_position" not in rule and "reps" not in rule for rule in escrito["rules"])
    assert nsl.ruleset_from_dict(json.loads(json.dumps(escrito))) == lido
    fatos = _facts(reps=13, rpe=8, working_set_index=1, fatigue_band=PriorFatigueBand.RESTED, rpe_ceiling=10)
    r = nsl.evaluate_ruleset(fatos, lido)
    assert (r.direction, r.load_kg, r.rule_position) == (pe.INCREASE_LOAD, 25.0, 1)


def test_conjunto_padrao_em_arvore_nos_limites_da_regra_de_2026_10_12():
    casos = [(16, 8, pe.INCREASE_LOAD), (15, 8, pe.INCREASE_LOAD), (12, 8.5, pe.INCREASE_LOAD), (12, 9, pe.HOLD), (5, 10, pe.DECREASE_LOAD), (5, 9, pe.HOLD)]
    for reps, rpe, esperado in casos:
        assert nsl.evaluate_ruleset(_facts(reps=reps, rpe=rpe), nsl.DEFAULT_RULESET).direction == esperado, (reps, rpe)


def test_v1_com_rampa_e_rpe_maior_ou_igual_continua_valido_e_ignora_a_rampa():
    def conjunto(rampa):
        return nsl.ruleset_from_dict(
            {
                "reps_floor": 6,
                "reps_ceiling": 12,
                "rules": [
                    {"rpe": {"op": ">=", "value": 8}, "respect_planned_ramp": rampa, "action": "HOLD"},
                    {"action": "DECREASE_LOAD", "magnitude": {"kind": "steps", "value": 1}, "reps": {"op": "<", "value": "floor"}},
                ],
            }
        )

    com, sem = conjunto(True), conjunto(False)
    assert not [c for c in nsl.validate_ruleset(com) if c.startswith("RULE_RAMP")]
    folha = com.rules[0].when.children[0]
    assert folha.ramp_cap is False
    for rpe in (7, 8, 9):
        fatos = _facts(reps=10, rpe=rpe, planned_rpe=8)
        assert nsl.evaluate_ruleset(fatos, com).reason == nsl.evaluate_ruleset(fatos, sem).reason


def test_engine_target_sem_alvo_nao_vira_motivo_de_rampa():
    when = nsl.all_of(nsl.leaf(VR, GTE, Decimal(12)), nsl.leaf(VP, LT, Decimal(9), ramp_cap=True))
    conjunto = nsl.RuleSet(
        reps_floor=6,
        reps_ceiling=12,
        rules=(nsl.Rule(position=1, when=when, action=nsl.ACTION_ENGINE_TARGET),),
    )
    for alvo in (None, 0):
        r = nsl.evaluate_ruleset(_facts(reps=12, rpe=8, planned_rpe=8, engine_target_kg=alvo), conjunto)
        assert (r.direction, r.reason) == (pe.HOLD, nsl.REASON_WITHIN_RULE), alvo
