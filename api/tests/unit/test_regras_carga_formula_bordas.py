"""QA independente: bordas da árvore de condições da regra de carga (R1 v2).

Dono: `app/domain/next_set_load.py`. Nenhuma regra é reimplementada: cada
asserção compara o dono com ele mesmo ou com constantes nomeadas do dono.

Rodar: cd api && python -m pytest tests/unit/test_regras_carga_formula_bordas.py
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.domain import next_set_load as nsl

PASSO = Decimal("2.5")
GT, GTE, LT, LTE, EQ = (nsl.ConditionOp.GT, nsl.ConditionOp.GTE, nsl.ConditionOp.LT, nsl.ConditionOp.LTE, nsl.ConditionOp.EQ)
SET_INDEX, FATIGUE, REPS, RPE = nsl.VAR_SET_INDEX, nsl.VAR_FATIGUE_BAND, nsl.VAR_REPS, nsl.VAR_RPE
PREVIOUS = nsl.SCOPE_PREVIOUS
UM_PASSO = nsl.Magnitude(nsl.MagnitudeKind.STEPS, Decimal(1))


def _leaf(variable, op, value, **kw):
    # números entram como Decimal (contrato do dono); tokens e faixas ficam texto
    if isinstance(value, (int, float)):
        value = Decimal(str(value))
    return nsl.leaf(variable, op, value, **kw)


def _facts(**kwargs):
    base = dict(set_type="working", weight_kg=20, step_kg=PASSO, reps=10, rpe=8, working_set_index=1)
    return nsl.SetFacts(**{**base, **kwargs})


def _ruleset(*rules):
    return nsl.RuleSet(reps_floor=6, reps_ceiling=12, rules=tuple(rules), version=5, source="teste")


def _rule(position, when, action=nsl.INCREASE_LOAD, magnitude=None, reason_code=None):
    return nsl.Rule(position=position, when=when, action=action, magnitude=magnitude, reason_code=reason_code)


def _eval(rules, **facts):
    return nsl.evaluate_ruleset(_facts(**facts), _ruleset(*rules))


def _prev(**kw):
    return nsl.PreviousSetFacts(**{"reps": 12, "rpe": 10, "weight_kg": 20, **kw})


def _nested(depth):
    """Grupo raiz com `depth` níveis de grupo (contando a raiz) e uma folha no fundo."""
    node = nsl.all_of(_leaf(REPS, GTE, 1))
    for _ in range(depth - 1):
        node = nsl.all_of(node)
    return node


def _codes(when):
    # códigos vêm como "CODIGO:posicao"; avisos (WARN_) ficam de fora
    brutos = nsl.validate_ruleset(_ruleset(_rule(1, when, action=nsl.HOLD)))
    return [c.split(":")[0] for c in brutos if not c.startswith(nsl.RULESET_WARNING_PREFIX)]


# ---------------------------------------------------------------------------
# OR e fatos ausentes
# ---------------------------------------------------------------------------


def test_or_com_ramo_sem_fato_casa_pelo_outro_ramo():
    when = nsl.any_of(_leaf(RPE, GTE, 9), _leaf(REPS, GT, nsl.REPS_TOKEN_CEILING))
    resultado = _eval([_rule(1, when)], rpe=None, reps=14)
    assert resultado.direction == nsl.INCREASE_LOAD and resultado.rule_position == 1
    # os dois ramos sem fato/sem casar -> nenhuma regra, MANTER
    assert _eval([_rule(1, when)], rpe=None, reps=10).direction == nsl.HOLD


def test_and_com_ramo_sem_fato_nao_casa():
    when = nsl.all_of(_leaf(RPE, GTE, 9), _leaf(REPS, GT, nsl.REPS_TOKEN_CEILING))
    resultado = _eval([_rule(1, when)], rpe=None, reps=14)
    assert resultado.direction == nsl.HOLD and resultado.rule_position is None


def test_serie_anterior_ausente_na_primeira_serie():
    anterior = _leaf(RPE, EQ, nsl.RPE_TOKEN_MAX, scope=PREVIOUS)
    so_anterior = _rule(1, nsl.all_of(anterior))
    assert _eval([so_anterior], previous=None, rpe_ceiling=10).rule_position is None
    ou = _rule(1, nsl.any_of(anterior, _leaf(REPS, GTE, 10)))
    assert _eval([ou], previous=None, rpe_ceiling=10, reps=10).rule_position == 1
    # com anterior no teto de RPE casa
    assert _eval([so_anterior], previous=_prev(rpe=10), rpe_ceiling=10).rule_position == 1
    # anterior sem RPE registrado não casa
    assert _eval([so_anterior], previous=_prev(rpe=None), rpe_ceiling=10).rule_position is None


# ---------------------------------------------------------------------------
# Limites estruturais
# ---------------------------------------------------------------------------


def test_profundidade_maxima_valida_e_maxima_mais_um_invalida():
    assert nsl.RULESET_MAX_DEPTH == 3
    assert nsl.RULE_DEPTH_EXCEEDED not in _codes(_nested(nsl.RULESET_MAX_DEPTH))
    assert nsl.RULE_DEPTH_EXCEEDED in _codes(_nested(nsl.RULESET_MAX_DEPTH + 1))
    assert _codes(_nested(nsl.RULESET_MAX_DEPTH)) == []


def test_oito_folhas_validas_nove_invalidas():
    assert nsl.RULESET_MAX_LEAVES_PER_RULE == 8
    folhas = [_leaf(REPS, GTE, 1) for _ in range(nsl.RULESET_MAX_LEAVES_PER_RULE)]
    assert _codes(nsl.all_of(*folhas)) == []
    assert nsl.RULE_TOO_MANY_LEAVES in _codes(nsl.all_of(*folhas, _leaf(REPS, GTE, 1)))
    # folhas em grupos aninhados contam juntas
    aninhado = nsl.all_of(nsl.all_of(*folhas[:5]), nsl.any_of(*folhas[:4]))
    assert nsl.RULE_TOO_MANY_LEAVES in _codes(aninhado)


def test_grupo_aninhado_vazio_e_invalido():
    assert nsl.RULE_GROUP_EMPTY in _codes(nsl.all_of(_leaf(REPS, GTE, 1), nsl.any_of()))


# ---------------------------------------------------------------------------
# ENGINE_TARGET
# ---------------------------------------------------------------------------


def test_engine_target_sem_alvo_cai_na_proxima_regra():
    primeira = _rule(1, nsl.all_of(_leaf(SET_INDEX, EQ, 1)), action=nsl.ACTION_ENGINE_TARGET)
    segunda = _rule(2, nsl.all_of(_leaf(REPS, GTE, 1)), action=nsl.INCREASE_LOAD)
    sem_alvo = _eval([primeira, segunda], engine_target_kg=None)
    assert sem_alvo.rule_position == 2 and sem_alvo.direction == nsl.INCREASE_LOAD
    com_alvo = _eval([primeira, segunda], engine_target_kg=30)
    assert com_alvo.rule_position == 1 and com_alvo.load_kg == 30.0
    # sozinha e sem alvo: MANTER sem regra
    sozinha = _eval([primeira], engine_target_kg=None)
    assert sozinha.direction == nsl.HOLD and sozinha.rule_position is None


def test_engine_target_percentual_arredonda_ao_passo():
    percent = nsl.Magnitude(nsl.MagnitudeKind.PERCENT, Decimal(10))
    regra = _rule(1, nsl.all_of(_leaf(SET_INDEX, EQ, 1)), action=nsl.ACTION_ENGINE_TARGET, magnitude=percent)
    resultado = _eval([regra], engine_target_kg=30)  # 33 -> passo 2,5
    assert resultado.load_kg % float(PASSO) == 0
    assert resultado.load_kg in (32.5, 35.0)
    assert resultado.direction == nsl.INCREASE_LOAD


def test_engine_target_percentual_limitado_pelo_teto_do_equipamento():
    percent = nsl.Magnitude(nsl.MagnitudeKind.PERCENT, Decimal(10))
    regra = _rule(1, nsl.all_of(_leaf(SET_INDEX, EQ, 1)), action=nsl.ACTION_ENGINE_TARGET, magnitude=percent)
    livre = _eval([regra], engine_target_kg=30)
    limitado = _eval([regra], engine_target_kg=30, max_load_kg=25)
    assert limitado.load_kg <= 25.0
    assert limitado.load_kg <= livre.load_kg
    assert limitado.load_kg % float(PASSO) == 0
    # teto abaixo da carga executada: nunca reduz, mantém pelo motivo do teto
    abaixo = _eval([regra], engine_target_kg=30, max_load_kg=15)
    assert abaixo.direction == nsl.HOLD and abaixo.load_kg == 20.0
    assert abaixo.reason == nsl.REASON_AT_EQUIPMENT_CEILING


def test_engine_target_mais_um_passo_com_teto():
    regra = _rule(1, nsl.all_of(_leaf(SET_INDEX, EQ, 1)), action=nsl.ACTION_ENGINE_TARGET, magnitude=UM_PASSO)
    assert _eval([regra], engine_target_kg=30).load_kg == 32.5
    capado = _eval([regra], engine_target_kg=30, max_load_kg=31)
    assert capado.load_kg <= 31.0


# ---------------------------------------------------------------------------
# ramp_cap
# ---------------------------------------------------------------------------


def test_ramp_cap_preserva_motivo_de_rampa_quando_so_o_cap_barrou():
    folha = _leaf(RPE, LT, 9, ramp_cap=True)
    regra = _rule(1, nsl.all_of(folha))
    # RPE 8 < 9 casaria, mas a rampa planejada (7) limita o limiar a 7: só o cap barrou
    barrado = _eval([regra], rpe=8, planned_rpe=7)
    assert barrado.direction == nsl.HOLD
    assert barrado.reason == nsl.REASON_WITHIN_PLANNED_RAMP
    # sem rampa casa normalmente
    assert _eval([regra], rpe=8, planned_rpe=None).direction == nsl.INCREASE_LOAD
    # RPE 6 < min(9, 7) casa mesmo com rampa
    assert _eval([regra], rpe=6, planned_rpe=7).direction == nsl.INCREASE_LOAD
    # sem cap a rampa é ignorada
    sem_cap = _rule(1, nsl.all_of(_leaf(RPE, LT, 9)))
    assert _eval([sem_cap], rpe=8, planned_rpe=7).direction == nsl.INCREASE_LOAD
    # RPE que nem casaria sem cap não vira "rampa": motivo de sempre
    assert _eval([regra], rpe=9.5, planned_rpe=7).reason == nsl.REASON_WITHIN_RULE


# ---------------------------------------------------------------------------
# v1 gravado vira v2 ao reserializar
# ---------------------------------------------------------------------------

JSON_V1 = {
    "reps_floor": 6,
    "reps_ceiling": 12,
    "rules": [
        {
            "position": 1,
            "set_position": "first",
            "fatigue": "rested",
            "reps": {"op": ">", "value": "ceiling"},
            "rpe": {"op": "<", "value": "max"},
            "respect_planned_ramp": True,
            "action": "INCREASE_LOAD",
            "magnitude": {"kind": "steps", "value": 1},
        }
    ],
}


def test_json_v1_vira_v2_ao_reserializar():
    lido = nsl.ruleset_from_dict(JSON_V1)
    v2 = nsl.ruleset_to_dict(lido)
    regra = v2["rules"][0]
    assert "when" in regra
    for chave_v1 in ("set_position", "fatigue", "reps", "rpe", "respect_planned_ramp"):
        assert chave_v1 not in regra
    folhas = regra["when"]["children"]
    assert {f["variable"] for f in folhas} == {SET_INDEX, FATIGUE, REPS, RPE}
    assert any(f.get("ramp_cap") for f in folhas if f["variable"] == RPE)
    assert nsl.RULESET_ENGINE_VERSION.endswith("v2")
    # idempotente e estável após JSON
    de_novo = nsl.ruleset_from_dict(json.loads(json.dumps(v2)))
    assert de_novo.rules == lido.rules
    assert nsl.ruleset_to_dict(de_novo)["rules"] == v2["rules"]


# ---------------------------------------------------------------------------
# Exemplos literais do usuário
# ---------------------------------------------------------------------------


def _regra_1():
    return _rule(1, nsl.all_of(_leaf(SET_INDEX, EQ, 1)), action=nsl.ACTION_ENGINE_TARGET)


def _regra_2():
    when = nsl.all_of(
        _leaf(SET_INDEX, EQ, 2),
        _leaf(FATIGUE, EQ, "rested"),
        _leaf(REPS, GTE, nsl.REPS_TOKEN_CEILING),
        _leaf(RPE, LT, nsl.RPE_TOKEN_TARGET),
        nsl.all_of(
            _leaf(RPE, EQ, nsl.RPE_TOKEN_MAX, scope=PREVIOUS),
            _leaf(REPS, GTE, nsl.REPS_TOKEN_CEILING, scope=PREVIOUS),
        ),
    )
    return _rule(2, when, action=nsl.ACTION_ENGINE_TARGET, magnitude=UM_PASSO)


def _fatos_regra_2(**kw):
    base = dict(
        working_set_index=2,
        fatigue_band="rested",
        reps=12,
        rpe=7,
        rpe_target=8,
        rpe_ceiling=10,
        engine_target_kg=30,
        previous=_prev(reps=12, rpe=10),
    )
    return {**base, **kw}


def test_exemplo_1_serie_1_recomendacao_do_motor():
    regras = [_regra_1()]
    casa = _eval(regras, working_set_index=1, engine_target_kg=30)
    assert casa.rule_position == 1 and casa.load_kg == 30.0 and casa.direction == nsl.INCREASE_LOAD
    assert _eval(regras, working_set_index=2, engine_target_kg=30).rule_position is None
    assert _eval(regras, working_set_index=1, engine_target_kg=None).rule_position is None


def test_exemplo_2_casa_com_recomendacao_mais_um_passo():
    resultado = _eval([_regra_2()], **_fatos_regra_2())
    assert resultado.rule_position == 2
    assert resultado.load_kg == 32.5  # 30 + 1 passo de 2,5
    assert resultado.direction == nsl.INCREASE_LOAD


@pytest.mark.parametrize(
    "mudanca",
    [
        {"working_set_index": 1},
        {"working_set_index": 3},
        {"fatigue_band": "pre_fatigued"},
        {"fatigue_band": None},
        {"reps": 11},
        {"rpe": 8},  # RPE = alvo: não é < alvo
        {"rpe": None},
        {"rpe_target": None},  # sem alvo não casa
        {"previous": None},  # sem anterior não casa
        {"previous": _prev(rpe=9)},
        {"previous": _prev(reps=11)},
        {"engine_target_kg": None},
    ],
)
def test_exemplo_2_nao_casa(mudanca):
    resultado = _eval([_regra_2()], **_fatos_regra_2(**mudanca))
    assert resultado.rule_position is None
    assert resultado.direction == nsl.HOLD


def test_exemplos_do_usuario_descritos_e_validos():
    texto_1 = nsl.describe_rule(_regra_1(), _ruleset(_regra_1()))
    texto_2 = nsl.describe_rule(_regra_2(), _ruleset(_regra_2()))
    for texto in (texto_1, texto_2):
        assert "SE" in texto and "ENTÃO" in texto
    assert "recomendação do motor" in texto_1 and "recomendação do motor" in texto_2
    assert "passo" in texto_2
    conjunto = _ruleset(_regra_1(), _regra_2())
    erros = [c for c in nsl.validate_ruleset(conjunto) if not c.startswith(nsl.RULESET_WARNING_PREFIX)]
    assert erros == []
    # ida e volta pelo JSON v2 sem perda
    assert nsl.ruleset_from_dict(json.loads(json.dumps(nsl.ruleset_to_dict(conjunto)))).rules == conjunto.rules
