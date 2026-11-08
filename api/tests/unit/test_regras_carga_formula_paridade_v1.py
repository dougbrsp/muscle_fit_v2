"""QA independente: paridade v1 -> árvore da regra de carga da próxima série.

Não reimplementa regra: compara o dono (`app/domain/next_set_load.py`) com ele
mesmo por três caminhos (JSON v1 -> from_dict; rule_from_v1; JSON v1 -> v2 ->
from_dict) e confere o conjunto publicado em produção (fixture, sem dado de
usuário) contra a avaliação regra a regra via rule_from_v1.

Rodar: cd api && python -m pytest tests/unit/test_regras_carga_formula_paridade_v1.py
"""

from __future__ import annotations

import copy
import itertools
import json
import random
from decimal import Decimal

import pytest

from app.domain import next_set_load as nsl

PASSO = Decimal("2.5")

# Conjunto publicado em produção (v1, piso 6, teto 12, 11 regras), copiado da leitura de 2026-11-08.
CONJUNTO_PRODUCAO_V1 = json.loads(r"""{
 "reps_floor": 6,
 "reps_ceiling": 12,
 "version": 2,
 "source": "published",
 "rules": [
  {
   "position": 1,
   "set_position": "any",
   "fatigue": "rested",
   "reps": {
    "op": ">",
    "value": "ceiling"
   },
   "rpe": {
    "op": ">=",
    "value": "max"
   },
   "respect_planned_ramp": false,
   "action": "INCREASE_LOAD",
   "magnitude": {
    "kind": "steps",
    "value": 1
   }
  },
  {
   "position": 2,
   "set_position": "first",
   "fatigue": "pre_fatigued",
   "reps": {
    "op": ">",
    "value": 14
   },
   "rpe": null,
   "respect_planned_ramp": false,
   "action": "INCREASE_LOAD",
   "magnitude": {
    "kind": "steps",
    "value": 1
   }
  },
  {
   "position": 3,
   "set_position": "first",
   "fatigue": "pre_fatigued",
   "reps": {
    "op": ">",
    "value": "ceiling"
   },
   "rpe": {
    "op": "<",
    "value": "max"
   },
   "respect_planned_ramp": false,
   "action": "INCREASE_LOAD",
   "magnitude": {
    "kind": "steps",
    "value": 1
   }
  },
  {
   "position": 4,
   "set_position": "first",
   "fatigue": "heavily_fatigued",
   "reps": {
    "op": ">",
    "value": "ceiling"
   },
   "rpe": null,
   "respect_planned_ramp": false,
   "action": "INCREASE_LOAD",
   "magnitude": {
    "kind": "steps",
    "value": 1
   }
  },
  {
   "position": 5,
   "set_position": "second",
   "fatigue": "any",
   "reps": {
    "op": ">",
    "value": "ceiling"
   },
   "rpe": null,
   "respect_planned_ramp": false,
   "action": "INCREASE_LOAD",
   "magnitude": {
    "kind": "steps",
    "value": 1
   }
  },
  {
   "position": 6,
   "set_position": "third_plus",
   "fatigue": "any",
   "reps": {
    "op": ">",
    "value": "ceiling"
   },
   "rpe": null,
   "respect_planned_ramp": false,
   "action": "INCREASE_LOAD",
   "magnitude": {
    "kind": "steps",
    "value": 1
   }
  },
  {
   "position": 7,
   "set_position": "first",
   "fatigue": "pre_fatigued",
   "reps": {
    "op": "<",
    "value": "floor"
   },
   "rpe": null,
   "respect_planned_ramp": false,
   "action": "DECREASE_LOAD",
   "magnitude": {
    "kind": "steps",
    "value": 1
   }
  },
  {
   "position": 8,
   "set_position": "first",
   "fatigue": "heavily_fatigued",
   "reps": {
    "op": "<=",
    "value": "floor"
   },
   "rpe": null,
   "respect_planned_ramp": false,
   "action": "DECREASE_LOAD",
   "magnitude": {
    "kind": "steps",
    "value": 1
   }
  },
  {
   "position": 9,
   "set_position": "second",
   "fatigue": "any",
   "reps": {
    "op": "<",
    "value": "floor"
   },
   "rpe": null,
   "respect_planned_ramp": false,
   "action": "DECREASE_LOAD",
   "magnitude": {
    "kind": "steps",
    "value": 1
   }
  },
  {
   "position": 10,
   "set_position": "third_plus",
   "fatigue": "any",
   "reps": {
    "op": "<",
    "value": "floor"
   },
   "rpe": null,
   "respect_planned_ramp": false,
   "action": "DECREASE_LOAD",
   "magnitude": {
    "kind": "steps",
    "value": 1
   }
  },
  {
   "position": 11,
   "set_position": "any",
   "fatigue": "rested",
   "reps": {
    "op": "<=",
    "value": "floor"
   },
   "rpe": null,
   "respect_planned_ramp": false,
   "action": "DECREASE_LOAD",
   "magnitude": {
    "kind": "steps",
    "value": 1
   }
  }
 ]
}""")

POSICOES = ("any", "first", "second", "third_plus")
FADIGAS = ("any", "rested", "pre_fatigued", "heavily_fatigued")
REPS_CONDS = (
    None,
    {"op": ">", "value": "ceiling"},
    {"op": "<", "value": "floor"},
    {"op": "<=", "value": "floor"},
    {"op": ">", "value": 14},
    {"op": ">=", "value": 10},
    {"op": "between", "value": 8, "value_to": 10},
)
RPE_CONDS = (
    None,
    {"op": ">=", "value": "max"},
    {"op": "<", "value": "max"},
    {"op": "<", "value": 8},
    {"op": "<=", "value": 9},
    {"op": "between", "value": 7, "value_to": 9},
)
ACOES = (
    ("HOLD", None),
    ("INCREASE_LOAD", None),
    ("INCREASE_LOAD", {"kind": "steps", "value": 2}),
    ("INCREASE_LOAD", {"kind": "percent", "value": 10}),
    ("DECREASE_LOAD", None),
    ("DECREASE_LOAD", {"kind": "steps", "value": 1}),
    ("DECREASE_LOAD", {"kind": "percent", "value": 15}),
)


def _regra_v1(rng: random.Random) -> dict:
    acao, magnitude = rng.choice(ACOES)
    return {
        "set_position": rng.choice(POSICOES),
        "fatigue": rng.choice(FADIGAS),
        "reps": rng.choice(REPS_CONDS),
        "rpe": rng.choice(RPE_CONDS),
        "respect_planned_ramp": rng.random() < 0.5,
        "action": acao,
        "magnitude": magnitude,
    }


def _conjunto_v1(rng: random.Random, n: int = 3) -> dict:
    return {
        "reps_floor": 6,
        "reps_ceiling": 12,
        "version": 3,
        "source": "teste",
        "rules": [{"position": i, **_regra_v1(rng)} for i in range(1, n + 1)],
    }


def _valor(v):
    return v if isinstance(v, str) else Decimal(str(v))


def _condition(data):
    if data is None:
        return None
    return nsl.Condition(
        op=nsl.ConditionOp(data["op"]),
        value=_valor(data["value"]),
        value_to=_valor(data["value_to"]) if data.get("value_to") is not None else None,
    )


def _rule_from_v1_spec(position: int, spec: dict) -> nsl.Rule:
    magnitude = spec.get("magnitude")
    return nsl.rule_from_v1(
        position,
        set_position=nsl.SetPosition(spec["set_position"]),
        fatigue=nsl.FatigueCondition(spec["fatigue"]),
        reps=_condition(spec.get("reps")),
        rpe=_condition(spec.get("rpe")),
        respect_planned_ramp=bool(spec.get("respect_planned_ramp")),
        action=spec["action"],
        magnitude=(
            nsl.Magnitude(nsl.MagnitudeKind(magnitude["kind"]), Decimal(str(magnitude["value"]))) if magnitude else None
        ),
    )


def _ruleset_via_rule_from_v1(data: dict) -> nsl.RuleSet:
    return nsl.RuleSet(
        reps_floor=data["reps_floor"],
        reps_ceiling=data["reps_ceiling"],
        rules=tuple(_rule_from_v1_spec(r["position"], r) for r in data["rules"]),
        version=data.get("version", 0),
        source=data.get("source", "default"),
    )


def _facts(reps, rpe, indice, faixa, planejado):
    return nsl.SetFacts(
        set_type="working",
        weight_kg=20,
        step_kg=PASSO,
        reps=reps,
        rpe=rpe,
        working_set_index=indice,
        fatigue_band=faixa,
        planned_rpe=planejado,
    )


def _matriz_fatos(amostra: int, seed: int) -> list[nsl.SetFacts]:
    espaco = list(
        itertools.product(
            range(1, 21),
            (None, 6, 7, 8, 9, 10),
            (1, 2, 3, 4),
            (None, "rested", "pre_fatigued", "heavily_fatigued"),
            (None, 8),
        )
    )
    escolhidos = random.Random(seed).sample(espaco, amostra) if amostra < len(espaco) else espaco
    return [_facts(*item) for item in escolhidos]


def _resumo(resultado):
    if resultado is None:
        return None
    return (resultado.direction, resultado.load_kg, resultado.reason, resultado.rule_position)


def _avaliar(ruleset, fatos):
    return [_resumo(nsl.evaluate_ruleset(f, ruleset)) for f in fatos]


FATOS_SINTETICOS = _matriz_fatos(350, seed=7)
FATOS_PRODUCAO = _matriz_fatos(1200, seed=11)


@pytest.mark.parametrize("seed", range(40))
def test_paridade_v1_arvore_tres_caminhos(seed):
    dados = _conjunto_v1(random.Random(seed))
    via_dict = nsl.ruleset_from_dict(copy.deepcopy(dados))
    via_rule = _ruleset_via_rule_from_v1(dados)
    via_v2 = nsl.ruleset_from_dict(json.loads(json.dumps(nsl.ruleset_to_dict(via_dict))))
    referencia = _avaliar(via_rule, FATOS_SINTETICOS)
    assert _avaliar(via_dict, FATOS_SINTETICOS) == referencia
    assert _avaliar(via_v2, FATOS_SINTETICOS) == referencia


def test_paridade_exercita_subir_descer_manter():
    """Sanidade da matriz: sem isso a paridade seria vácua."""
    vistos = set()
    motivos = set()
    for seed in range(40):
        ruleset = nsl.ruleset_from_dict(_conjunto_v1(random.Random(seed)))
        for resumo in _avaliar(ruleset, FATOS_SINTETICOS):
            if resumo:
                vistos.add(resumo[0])
                motivos.add(resumo[2])
    assert {"INCREASE_LOAD", "DECREASE_LOAD", "HOLD"} <= vistos
    assert nsl.REASON_RULE_MATCHED in motivos
    assert nsl.REASON_WITHIN_PLANNED_RAMP in motivos


# ---------------------------------------------------------------------------
# Conjunto publicado em produção
# ---------------------------------------------------------------------------


def test_producao_lida_como_v1_igual_a_referencia_regra_a_regra():
    lido = nsl.ruleset_from_dict(copy.deepcopy(CONJUNTO_PRODUCAO_V1))
    referencia = _ruleset_via_rule_from_v1(CONJUNTO_PRODUCAO_V1)
    assert len(lido.rules) == 11
    assert _avaliar(lido, FATOS_PRODUCAO) == _avaliar(referencia, FATOS_PRODUCAO)


def test_producao_reserializa_em_v2_com_11_regras_na_mesma_ordem():
    lido = nsl.ruleset_from_dict(copy.deepcopy(CONJUNTO_PRODUCAO_V1))
    v2 = nsl.ruleset_to_dict(lido)
    assert [r["position"] for r in v2["rules"]] == list(range(1, 12))
    assert all("when" in r and "set_position" not in r and "fatigue" not in r for r in v2["rules"])
    assert [r["action"] for r in v2["rules"]] == [r["action"] for r in CONJUNTO_PRODUCAO_V1["rules"]]
    assert (v2["reps_floor"], v2["reps_ceiling"]) == (6, 12)
    reidratado = nsl.ruleset_from_dict(json.loads(json.dumps(v2)))
    assert reidratado.rules == lido.rules
    assert _avaliar(reidratado, FATOS_PRODUCAO) == _avaliar(lido, FATOS_PRODUCAO)


def test_producao_valida_sem_erros():
    lido = nsl.ruleset_from_dict(copy.deepcopy(CONJUNTO_PRODUCAO_V1))
    erros = [c for c in nsl.validate_ruleset(lido) if not c.startswith(nsl.RULESET_WARNING_PREFIX)]
    assert erros == []
    reidratado = nsl.ruleset_from_dict(nsl.ruleset_to_dict(lido))
    assert [c for c in nsl.validate_ruleset(reidratado) if not c.startswith(nsl.RULESET_WARNING_PREFIX)] == []
