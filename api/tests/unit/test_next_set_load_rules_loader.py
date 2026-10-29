"""Loader do conjunto de regras de carga gravado (`app/next_set_load_rules.py`,
R2): conversão Pydantic <-> dono <-> linha, fallback para o padrão (sem
linha, tabela ausente, JSON ilegível, conjunto que o dono recusa),
`ruleset_uses_fatigue` (do dono), rascunho único e publicação versionada com
concorrência. Nenhuma regra aqui: a semântica é do dono.

Rodar: cd api && python -m pytest tests/unit/test_next_set_load_rules_loader.py
"""

from __future__ import annotations

import logging
from dataclasses import replace
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app import next_set_load_rules as rules
from app.domain import next_set_load as nsl
from tests.support.next_set_load_rules_fake import FakeRuleSetsDB

# O exemplo do plano: 1ª série, descansado, reps > teto, RPE < máximo, rampa -> +25%.
EXEMPLO = {
    "reps_floor": 6,
    "reps_ceiling": 12,
    "rules": [
        {"position": 1, "set_position": "first", "fatigue": "rested",
         "reps": {"op": ">", "value": "ceiling"}, "rpe": {"op": "<", "value": "max"},
         "respect_planned_ramp": True, "action": "INCREASE_LOAD",
         "magnitude": {"kind": "percent", "value": 25}, "text": "ignorado"},
        {"set_position": "any", "reps": {"op": "<", "value": "floor"}, "rpe": {"op": ">=", "value": 10},
         "action": "DECREASE_LOAD", "magnitude": {"kind": "steps", "value": 1}},
    ],
}


def _rs(data=EXEMPLO, **kwargs) -> nsl.RuleSet:
    return rules.ruleset_from_input(rules.RuleSetIn(**data), **kwargs)


# ---------------------------------------------------------------------------
# Conversão
# ---------------------------------------------------------------------------


def test_pydantic_vira_o_conjunto_do_dono_na_ordem_da_lista():
    ruleset = _rs()
    assert [rule.position for rule in ruleset.rules] == [1, 2]
    primeira = ruleset.rules[0]
    assert primeira.set_position is nsl.SetPosition.FIRST and primeira.fatigue is nsl.FatigueCondition.RESTED
    assert primeira.reps == nsl.Condition(nsl.ConditionOp.GT, "ceiling")
    assert primeira.magnitude == nsl.Magnitude(nsl.MagnitudeKind.PERCENT, Decimal(25))
    assert primeira.reason_code is None  # regra cadastrada: motivo genérico
    assert nsl.ruleset_errors(ruleset) == []


def test_ida_e_volta_pela_linha_preserva_o_conjunto():
    db = FakeRuleSetsDB()
    ruleset = _rs()
    db.add(status=rules.STATUS_PUBLISHED, version_number=4, ruleset_dict=nsl.ruleset_to_dict(ruleset))
    lido = rules.load_published_ruleset(db.conn())
    assert lido.rules == ruleset.rules
    assert (lido.version, lido.source, lido.reps_floor, lido.reps_ceiling) == (4, "published", 6, 12)


@pytest.mark.parametrize(
    "ajuste",
    [
        {"extra": 1},
        {"rules": [{"action": "HOLD", "reason_code": "NEXT_SET_PAIN"}]},  # motivo não é aceito
        {"rules": [{"action": "HOLD", "set_position": "fourth"}]},  # fora do vocabulário do dono
        {"rules": [{"action": "HOLD", "reps": {"op": "!=", "value": 3}}]},
        {"rules": [{"action": "HOLD", "respect_planned_ramp": "sim"}]},
        {"reps_floor": "6"},
    ],
)
def test_forma_invalida_e_recusada_pelo_pydantic(ajuste):
    with pytest.raises(ValidationError):
        rules.RuleSetIn(**{**EXEMPLO, **ajuste})


def test_ruleset_payload_traz_o_texto_do_dono():
    ruleset = _rs()
    payload = rules.ruleset_payload(ruleset)
    assert [item["text"] for item in payload["rules"]] == [nsl.describe_rule(rule, ruleset) for rule in ruleset.rules]


# ---------------------------------------------------------------------------
# Vigente: nunca levanta
# ---------------------------------------------------------------------------


def test_sem_linha_publicada_vale_o_padrao():
    db = FakeRuleSetsDB()
    db.add(status=rules.STATUS_DRAFT, version_number=None, ruleset_dict=EXEMPLO)  # rascunho não vale
    db.add(status=rules.STATUS_PUBLISHED, version_number=1, ruleset_dict=EXEMPLO, superseded=True)  # substituída
    assert rules.load_published_ruleset(db.conn()) is nsl.DEFAULT_RULESET
    assert rules.resolve_published(db.conn()) == (nsl.DEFAULT_RULESET, None)


def test_tabela_ausente_ou_banco_fora_vale_o_padrao(caplog):
    class Explode:
        def cursor(self):
            raise RuntimeError("Table 'fitness.next_set_load_rule_sets' doesn't exist")

    with caplog.at_level(logging.WARNING, logger="fitness.next_set_load_rules"):
        assert rules.load_published_ruleset(Explode()) is nsl.DEFAULT_RULESET
    assert "published_read_failed" in caplog.text


def test_autocommit_usa_uma_leitura_e_cai_no_padrao_em_falha(monkeypatch):
    db = FakeRuleSetsDB()
    db.add(status=rules.STATUS_PUBLISHED, version_number=2, ruleset_dict=EXEMPLO)
    monkeypatch.setattr(rules, "fetch_rows", db.fetch_rows)
    assert rules.load_published_ruleset_autocommit().version == 2
    assert db.statements == [rules._PUBLISHED_SQL]

    def explode(sql, params=None):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(rules, "fetch_rows", explode)
    assert rules.load_published_ruleset_autocommit() is nsl.DEFAULT_RULESET


def test_json_ilegivel_ou_conjunto_recusado_pelo_dono_vale_o_padrao(caplog):
    db = FakeRuleSetsDB()
    db.add(status=rules.STATUS_PUBLISHED, version_number=1, ruleset_dict=EXEMPLO, rules_json="{não é json")
    assert rules.load_published_ruleset(db.conn()) is nsl.DEFAULT_RULESET

    db = FakeRuleSetsDB()
    fora = {**EXEMPLO, "rules": [{**EXEMPLO["rules"][0], "magnitude": {"kind": "percent", "value": 999}}]}
    db.add(status=rules.STATUS_PUBLISHED, version_number=1, ruleset_dict=fora)
    with caplog.at_level(logging.WARNING, logger="fitness.next_set_load_rules"):
        assert rules.load_published_ruleset(db.conn()) is nsl.DEFAULT_RULESET
    assert "published_invalid" in caplog.text


def test_motivo_desconhecido_gravado_e_recusado_pelo_dono():
    """Um `reason_code` fora dos motivos de regra quebraria o rótulo; o dono o
    recusa e o loader cai no padrão."""
    regra = replace(nsl.DEFAULT_RULESET.rules[0], reason_code="QUALQUER")
    assert nsl.ruleset_errors(replace(nsl.DEFAULT_RULESET, rules=(regra,) + nsl.DEFAULT_RULESET.rules[1:])) == [
        nsl.RULE_REASON_CODE_UNKNOWN + ":1"
    ]
    assert nsl.ruleset_errors(nsl.DEFAULT_RULESET) == []  # os motivos do padrão continuam válidos


def test_ruleset_uses_fatigue_pergunta_ao_dono():
    assert nsl.ruleset_uses_fatigue(nsl.DEFAULT_RULESET) is False
    assert nsl.ruleset_uses_fatigue(_rs()) is True
    sem_fadiga = {**EXEMPLO, "rules": [{**EXEMPLO["rules"][0], "fatigue": "any"}]}
    assert nsl.ruleset_uses_fatigue(_rs(sem_fadiga)) is False


# ---------------------------------------------------------------------------
# Cadastro
# ---------------------------------------------------------------------------


def test_rascunho_unico_cria_e_depois_substitui():
    db = FakeRuleSetsDB()
    primeiro = rules.save_draft(db.conn(), _rs(), notes="a", user_id=7)
    sem = {**EXEMPLO, "reps_ceiling": 15}
    segundo = rules.save_draft(db.conn(), _rs(sem), notes="b", user_id=8)
    assert primeiro.id == segundo.id and len(db.rows) == 1
    assert (segundo.notes, segundo.ruleset.reps_ceiling, segundo.created_by) == ("b", sem["reps_ceiling"], 7)
    assert segundo.engine_version == nsl.RULESET_ENGINE_VERSION and segundo.version_number is None
    assert rules.load_draft(db.conn()).id == primeiro.id


def test_publicar_promove_o_rascunho_e_encerra_o_vigente():
    db = FakeRuleSetsDB()
    v1 = rules.publish(db.conn(), rules.save_draft(db.conn(), _rs(), notes=None, user_id=1).id, 0, 1)
    assert (v1.version_number, v1.status, v1.is_current, v1.published_by) == (1, "published", True, 1)
    v2 = rules.publish(db.conn(), rules.save_draft(db.conn(), _rs(), notes=None, user_id=2).id, 1, 2)
    assert v2.version_number == 2
    antiga = db.by_id(v1.id)
    assert antiga["superseded_at"] is not None and antiga["status"] == "published"
    assert rules.load_published_ruleset(db.conn()).version == 2
    assert [item.version_number for item in rules.list_versions(db.conn())] == [2, 1]


def test_publicar_com_versao_esperada_diferente_e_conflito():
    db = FakeRuleSetsDB()
    rules.publish(db.conn(), rules.save_draft(db.conn(), _rs(), notes=None, user_id=1).id, 0, 1)
    rascunho = rules.save_draft(db.conn(), _rs(), notes=None, user_id=1)
    with pytest.raises(rules.RuleSetConflictError) as exc:
        rules.publish(db.conn(), rascunho.id, 0, 1)
    assert (exc.value.code, exc.value.current_version) == ("publish_conflict", 1)


def test_corrida_no_unique_da_vigente_vira_conflito():
    db = FakeRuleSetsDB()
    rascunho = rules.save_draft(db.conn(), _rs(), notes=None, user_id=1)

    def outra_publicacao(fake):
        # outra instância publicou entre a trava e o UPDATE (versão 1 já usada)
        fake.add(status=rules.STATUS_PUBLISHED, version_number=1, ruleset_dict=EXEMPLO)
        fake.before_publish_update = None

    db.before_publish_update = outra_publicacao
    with pytest.raises(rules.RuleSetConflictError) as exc:
        rules.publish(db.conn(), rascunho.id, 0, 1)
    assert exc.value.code == "publish_conflict"


def test_publicar_rascunho_inexistente_ou_recusado_pelo_dono():
    db = FakeRuleSetsDB()
    with pytest.raises(rules.RuleSetNotFoundError):
        rules.publish(db.conn(), 99, 0, 1)
    fora = {**EXEMPLO, "rules": [{**EXEMPLO["rules"][0], "magnitude": {"kind": "percent", "value": 999}}]}
    draft_id = db.add(status=rules.STATUS_DRAFT, version_number=None, ruleset_dict=fora)
    with pytest.raises(rules.RuleSetInvalidError) as exc:
        rules.publish(db.conn(), draft_id, 0, 1)
    assert exc.value.codes == [nsl.RULE_MAGNITUDE_OUT_OF_RANGE + ":1"]
