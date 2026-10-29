"""Objetivo de treino nas versões do Modelo (M97 F0, migração 093).

A forma canônica (o que decide se um save abre versão nova) de um Modelo SEM
objetivo é IDÊNTICA à de antes da 093: o objetivo do cabeçalho só entra quando
difere do default do dono e o override do item só entra quando preenchido.
Nenhuma versão espúria nasce no primeiro PUT depois do deploy.

Rodar: cd api && python -m pytest tests/unit/test_training_goal_versoes.py
"""

from __future__ import annotations

from app import template_versions as tv
from app.domain.training_goal import DEFAULT_GOAL, TrainingGoal
from app.models import WorkoutPlanTemplateSlotIn, WorkoutPlanTemplateSlotItemIn
from tests.unit.test_template_versions import FakeCur, _item, _payload, _slot, _version_row

STRENGTH = TrainingGoal.STRENGTH.value

# ITEM_FIELDS de `origin/develop` antes da 093, na ordem (cópia de propósito:
# é a forma canônica que as versões já gravadas comparam).
ITEM_FIELDS_ANTES_DA_093 = (
    "item_order", "item_kind", "muscle_group_id", "mapping_id", "equipment_id", "sets_count",
    "peak_contraction_seconds", "reps_target", "rpe_target", "prioritize", "priority_min_pct",
    "priority_max_pct", "cardio_duration_minutes", "advanced_technique", "advanced_technique_applications",
    "advanced_technique_duration_seconds", "warmup_sets_count", "warmup_reps", "warmup_rest_seconds",
    "feeler_set_sets_count", "feeler_set_reps", "feeler_set_rest_seconds", "rest_seconds", "measure",
    "duration_seconds_target", "time_window_seconds",
)


def _canonico_antes_da_093(slots):
    """`canonical_slots` como era antes da 093 (mesmo `_norm`, campos antigos)."""
    linhas = []
    for slot in slots:
        cabeca = tuple(tv._norm(f, tv._get(slot, f)) for f in tv.SLOT_FIELDS)
        itens = [tuple(tv._norm(f, tv._get(i, f)) for f in ITEM_FIELDS_ANTES_DA_093) for i in slot["items"]]
        itens = tuple(sorted(itens, key=lambda r: (r[0] if r[0] is not None else -1, repr(r))))
        linhas.append(cabeca + (itens,))
    return tuple(sorted(linhas, key=lambda r: (r[0] if r[0] is not None else -1, repr(r))))


def test_campos_antigos_na_mesma_ordem_e_o_objetivo_no_insert_e_na_copia():
    assert tv._CANONICAL_ITEM_FIELDS == ITEM_FIELDS_ANTES_DA_093
    # A 095 (prescrição de cardio, M98 C0) acrescenta as suas colunas DEPOIS do objetivo.
    assert tv.ITEM_FIELDS[len(ITEM_FIELDS_ANTES_DA_093)] == "training_goal"
    assert tv.INSERT_ITEM_SQL.count("%s") == len(tv.ITEM_FIELDS) + 1
    assert "i.training_goal" in tv.VERSION_ITEMS_SQL and "i.training_goal" in tv.COPY_ITEMS_SQL
    assert "training_goal" in tv.INSERT_VERSION_SQL and tv.INSERT_VERSION_SQL.count("%s") == 15
    assert "volume_counting_mode, training_goal\nFROM" in tv.REVERT_INSERT_SQL
    assert "v.training_goal" in tv.CURRENT_VERSION_SQL


def test_hash_canonico_de_modelo_sem_objetivo_e_o_de_antes_da_093():
    payload = _payload(slots=[_slot(1), _slot(2, [_item(mapping_id=7101), _item(item_order=2, prioritize=True)])])
    assert tv.canonical_slots(payload["slots"]) == _canonico_antes_da_093(payload["slots"])
    cabecalho_antes = (2, "equipment_series", "abc", 2, 4, tv.counting_mode_of(payload))
    assert tv.canonical_header(payload) == cabecalho_antes
    # Versão lida do banco antes da 093 (sem a coluna) e depois (default): mesma forma.
    assert tv.canonical_header(tv.row_to_version(_version_row())) == tv.canonical_header(
        tv.row_to_version(_version_row(training_goal=DEFAULT_GOAL.value))
    )
    # Objetivo default explícito e override NULL não mudam nada.
    explicito = dict(payload, training_goal=DEFAULT_GOAL.value)
    assert tv.canonical_header(explicito) == cabecalho_antes
    com_null = [dict(s, items=[dict(i, training_goal=None) for i in s["items"]]) for s in payload["slots"]]
    assert tv.canonical_slots(com_null) == tv.canonical_slots(payload["slots"])


def test_put_sem_objetivo_contra_vigente_anterior_a_093_nao_abre_versao():
    payload = _payload()
    pydantic_slots = [
        WorkoutPlanTemplateSlotIn(**{**s, "items": [WorkoutPlanTemplateSlotItemIn(**i) for i in s["items"]]})
        for s in payload["slots"]
    ]
    cur = FakeCur(vigente=_version_row(), slots=payload["slots"])
    resultado = tv.write_structural_version(
        cur, 7, dict(payload, slots=pydantic_slots, training_goal=DEFAULT_GOAL.value), None, actor=1
    )
    assert resultado is None
    assert cur.writes() == []


def test_objetivo_forca_no_modelo_abre_versao_e_grava_o_objetivo():
    payload = _payload(training_goal=STRENGTH)
    cur = FakeCur(vigente=_version_row(), slots=payload["slots"])
    assert tv.write_structural_version(cur, 7, payload, None, actor=1) is not None
    (params,) = cur.params_of("INSERT INTO workout_plan_template_versions")
    assert params[-1] == STRENGTH


def test_override_do_item_abre_versao_e_e_gravado_no_item():
    base = _payload()
    payload = _payload(slots=[_slot(1, [_item(training_goal=STRENGTH)]), base["slots"][1]])
    assert tv.canonical_slots(payload["slots"]) != tv.canonical_slots(base["slots"])
    cur = FakeCur(vigente=_version_row(), slots=base["slots"])
    assert tv.write_structural_version(cur, 7, payload, None, actor=1) is not None
    itens = [dict(zip(tv.ITEM_FIELDS, p[1:])) for p in cur.params_of("INSERT INTO workout_plan_template_slot_items")]
    assert [i["training_goal"] for i in itens] == [STRENGTH, None]
    # Versao nova sem objetivo no cabecalho grava o default.
    (versao,) = cur.params_of("INSERT INTO workout_plan_template_versions")
    assert versao[-1] == DEFAULT_GOAL.value


def test_versao_lida_expoe_o_objetivo_pela_leitura_tolerante():
    assert tv.row_to_version(_version_row()).training_goal == DEFAULT_GOAL.value
    assert tv.row_to_version(_version_row(training_goal=STRENGTH.encode())).training_goal == STRENGTH
    assert tv.training_goal_of(tv.row_to_version(_version_row(training_goal=STRENGTH))) == STRENGTH
