"""Os exemplos literais do usuário (2026-10-20) como conjunto CADASTRADO, na
ordem em que ele os deu, provados com cargas reais pelo dono
`app/domain/next_set_load.py` (R1).

- R1: 1ª série · músculo descansado · reps > teto · RPE < máximo → +25%
- R2: 1ª série · músculo descansado · reps > teto → manter
- R3: 2ª série · músculo descansado · reps > teto → +25%
- R4: 1ª série · muito fatigado · reps < piso · RPE ≥ 10 → −20%

O conjunto entra pelo JSON (`ruleset_from_dict`), como o R2 vai guardá-lo na
migração 088, e é avaliado por `engine_decision_context.next_set_load_for`,
que só monta os `SetFacts`.

Rodar: cd api && python -m pytest tests/unit/test_next_set_load_exemplos_usuario.py
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain import next_set_load as nsl
from app.domain import progression_engine as pe
from app.domain.prior_fatigue import PriorFatigueBand
from app.engine_decision_context import PairEngineContext, next_set_load_for

PASSO = Decimal("2.5")
TETO_RPE = 10

CONJUNTO_JSON = {
    "reps_floor": 6,
    "reps_ceiling": 12,
    "rules": [
        {
            "set_position": "first",
            "fatigue": "rested",
            "reps": {"op": ">", "value": "ceiling"},
            "rpe": {"op": "<", "value": "max"},
            "respect_planned_ramp": False,
            "action": "INCREASE_LOAD",
            "magnitude": {"kind": "percent", "value": 25},
        },
        {
            "set_position": "first",
            "fatigue": "rested",
            "reps": {"op": ">", "value": "ceiling"},
            "rpe": None,
            "respect_planned_ramp": False,
            "action": "HOLD",
            "magnitude": None,
        },
        {
            "set_position": "second",
            "fatigue": "rested",
            "reps": {"op": ">", "value": "ceiling"},
            "rpe": None,
            "respect_planned_ramp": False,
            "action": "INCREASE_LOAD",
            "magnitude": {"kind": "percent", "value": 25},
        },
        {
            "set_position": "first",
            "fatigue": "heavily_fatigued",
            "reps": {"op": "<", "value": "floor"},
            "rpe": {"op": ">=", "value": 10},
            "respect_planned_ramp": False,
            "action": "DECREASE_LOAD",
            "magnitude": {"kind": "percent", "value": 20},
        },
    ],
}
CONJUNTO = nsl.ruleset_from_dict(CONJUNTO_JSON, version=1, source="usuario")


def _avaliar(*, reps, rpe, carga, indice, faixa):
    return next_set_load_for(
        {"set_type": "working", "reps": reps, "weight_kg": carga, "rpe": rpe},
        PairEngineContext(step_kg=PASSO),
        planned_rpe=None,
        working_set_index=indice,
        fatigue_band=faixa,
        rpe_ceiling=TETO_RPE,
        ruleset=CONJUNTO,
    )


def test_conjunto_do_usuario_e_valido_e_descrito_em_pt_br():
    assert nsl.validate_ruleset(CONJUNTO) == []
    assert (CONJUNTO.version, CONJUNTO.source) == (1, "usuario")
    assert [nsl.describe_rule(rule, CONJUNTO) for rule in CONJUNTO.rules] == [
        "1ª série · músculo descansado · reps > teto (12) · RPE < máximo → Aumentar 25% (no passo do equipamento)",
        "1ª série · músculo descansado · reps > teto (12) → Manter a carga",
        "2ª série · músculo descansado · reps > teto (12) → Aumentar 25% (no passo do equipamento)",
        "1ª série · músculo muito fatigado · reps < piso (6) · RPE ≥ 10 → Reduzir 20% (no passo do equipamento)",
    ]
    assert nsl.ruleset_to_dict(nsl.ruleset_from_dict(nsl.ruleset_to_dict(CONJUNTO))) == nsl.ruleset_to_dict(CONJUNTO)


def test_r1_primeira_serie_descansado_13_reps_rpe_8_sobe_25_por_cento():
    resultado = _avaliar(reps=13, rpe=8, carga=20, indice=1, faixa=PriorFatigueBand.RESTED)
    assert (resultado.direction, resultado.load_kg, resultado.rule_position) == (pe.INCREASE_LOAD, 25.0, 1)
    assert resultado.reason == nsl.REASON_RULE_MATCHED
    assert resultado.reason_label == resultado.rule_text == nsl.describe_rule(CONJUNTO.rules[0], CONJUNTO)
    assert resultado.to_dict()["magnitude"] == {"kind": "percent", "value": 25}
    assert resultado.ruleset_version == 1


def test_r2_primeira_serie_descansado_13_reps_no_rpe_maximo_mantem():
    resultado = _avaliar(reps=13, rpe=10, carga=20, indice=1, faixa=PriorFatigueBand.RESTED)
    assert (resultado.direction, resultado.load_kg, resultado.rule_position) == (pe.HOLD, 20.0, 2)
    assert resultado.reason == nsl.REASON_RULE_MATCHED and resultado.magnitude is None


def test_r3_segunda_serie_descansado_13_reps_rpe_10_sobe_25_por_cento():
    resultado = _avaliar(reps=13, rpe=10, carga=20, indice=2, faixa=PriorFatigueBand.RESTED)
    assert (resultado.direction, resultado.load_kg, resultado.rule_position) == (pe.INCREASE_LOAD, 25.0, 3)


def test_r4_primeira_serie_muito_fatigado_4_reps_rpe_10_reduz_20_por_cento():
    resultado = _avaliar(reps=4, rpe=10, carga=25, indice=1, faixa=PriorFatigueBand.HEAVILY_FATIGUED)
    assert (resultado.direction, resultado.load_kg, resultado.rule_position) == (pe.DECREASE_LOAD, 20.0, 4)
    assert resultado.to_dict()["magnitude"] == {"kind": "percent", "value": 20}


def test_pre_fatigado_nao_casa_nenhuma_e_mantem():
    resultado = _avaliar(reps=13, rpe=8, carga=20, indice=1, faixa=PriorFatigueBand.PRE_FATIGUED)
    assert (resultado.direction, resultado.load_kg, resultado.reason, resultado.rule_position) == (
        pe.HOLD,
        20.0,
        nsl.REASON_WITHIN_RULE,
        None,
    )
    assert resultado.reason_label == nsl.reason_labels()[nsl.REASON_WITHIN_RULE]


@pytest.mark.parametrize(
    ("indice", "faixa", "direcao"),
    [
        (3, PriorFatigueBand.RESTED, pe.HOLD),  # 3ª série: nenhuma regra fala dela
        (None, PriorFatigueBand.RESTED, pe.HOLD),  # posição desconhecida só casa "qualquer"
        (1, None, pe.HOLD),  # faixa desconhecida só casa "qualquer"
        (2, PriorFatigueBand.HEAVILY_FATIGUED, pe.HOLD),
    ],
)
def test_fatos_fora_das_regras_cadastradas_mantem(indice, faixa, direcao):
    resultado = _avaliar(reps=13, rpe=8, carga=20, indice=indice, faixa=faixa)
    assert (resultado.direction, resultado.reason) == (direcao, nsl.REASON_WITHIN_RULE)


def test_percentual_e_arredondado_ao_passo_do_equipamento():
    # 21 kg + 25% = 26,25 -> 27,5 no passo 2,5; 22 kg - 20% = 17,6 -> 17,5
    assert _avaliar(reps=13, rpe=8, carga=21, indice=1, faixa=PriorFatigueBand.RESTED).load_kg == 27.5
    assert _avaliar(reps=4, rpe=10, carga=22, indice=1, faixa=PriorFatigueBand.HEAVILY_FATIGUED).load_kg == 17.5
    # reduzir abaixo do primeiro passo: direção sem carga
    sem_passo = _avaliar(reps=4, rpe=10, carga=2.5, indice=1, faixa=PriorFatigueBand.HEAVILY_FATIGUED)
    assert (sem_passo.direction, sem_passo.load_kg) == (pe.DECREASE_LOAD, None)


def test_dor_continua_valendo_antes_do_conjunto_do_usuario():
    resultado = next_set_load_for(
        {"set_type": "working", "reps": 13, "weight_kg": 20, "rpe": 8, "joint_pain": True},
        PairEngineContext(step_kg=PASSO),
        working_set_index=1,
        fatigue_band=PriorFatigueBand.RESTED,
        rpe_ceiling=TETO_RPE,
        ruleset=CONJUNTO,
    )
    assert (resultado.direction, resultado.reason, resultado.load_kg) == (pe.DECREASE_LOAD, nsl.REASON_PAIN, 17.5)
