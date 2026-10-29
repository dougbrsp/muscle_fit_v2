"""Medida da série nas versões do Modelo (Lote T2): os 3 campos entram em
`ITEM_FIELDS` (SELECT, INSERT e cópia da reversão), `measure` é gravada e
comparada pelo `normalize_measure` do dono — o cliente antigo (sem o campo)
nem abre versão nova nem grava NULL na coluna NOT NULL."""

from __future__ import annotations

from app import template_versions as tv
from app.domain.set_measures import SetMeasure
from app.models import WorkoutPlanTemplateSlotItemIn

CAMPOS = ("measure", "duration_seconds_target", "time_window_seconds")


def _item(**extra):
    base = {"item_order": 1, "item_kind": "equipment", "muscle_group_id": 1, "mapping_id": 2, "equipment_id": 3}
    return {**base, **extra}


def test_campos_da_medida_estao_em_todo_sql_de_item():
    for campo in CAMPOS:
        assert campo in tv.ITEM_FIELDS
        assert f"i.{campo}" in tv.VERSION_ITEMS_SQL
        assert f"i.{campo}" in tv.COPY_ITEMS_SQL
        assert campo in tv.INSERT_ITEM_SQL
    assert tv.INSERT_ITEM_SQL.count("%s") == len(tv.ITEM_FIELDS) + 1


def test_cliente_antigo_sem_medida_e_igual_a_linha_gravada_reps():
    """Payload sem medida (None) x linha do banco ('reps', NULL, NULL): mesma
    forma canônica -> nenhum PUT cria versão idêntica."""
    payload = [{"sequence": 1, "name": "A", "items": [WorkoutPlanTemplateSlotItemIn(**_item(sets_count=3))]}]
    banco = [
        {
            "sequence": 1,
            "name": "A",
            "items": [
                _item(
                    sets_count="3",
                    prioritize=0,
                    measure=SetMeasure.REPS.value,
                    duration_seconds_target=None,
                    time_window_seconds=None,
                )
            ],
        }
    ]
    assert tv.canonical_slots(payload) == tv.canonical_slots(banco)


def test_trocar_a_medida_muda_a_forma_canonica():
    reps = [{"sequence": 1, "name": "A", "items": [_item(sets_count=3)]}]
    tempo = [
        {
            "sequence": 1,
            "name": "A",
            "items": [_item(sets_count=3, measure=SetMeasure.DURATION.value, duration_seconds_target=60)],
        }
    ]
    assert tv.canonical_slots(reps) != tv.canonical_slots(tempo)
    # Caixa/espaço não abrem versão: o dono normaliza.
    tempo_caixa = [
        {
            "sequence": 1,
            "name": "A",
            "items": [_item(sets_count=3, measure=" DURATION ", duration_seconds_target="60")],
        }
    ]
    assert tv.canonical_slots(tempo) == tv.canonical_slots(tempo_caixa)


class _Cursor:
    def __init__(self):
        self.calls = []
        self.lastrowid = 0

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        self.lastrowid += 1


def _valores_do_insert(item):
    cur = _Cursor()
    tv.insert_version_slots(cur, 9, 7, [{"sequence": 1, "name": "A", "items": [item]}])
    sql, params = cur.calls[-1]
    assert sql == tv.INSERT_ITEM_SQL
    return dict(zip(tv.ITEM_FIELDS, params[1:]))


def test_insert_grava_reps_quando_o_item_nao_diz_a_medida():
    valores = _valores_do_insert(WorkoutPlanTemplateSlotItemIn(**_item(sets_count=3)))
    assert valores["measure"] == SetMeasure.REPS.value
    assert valores["duration_seconds_target"] is None and valores["time_window_seconds"] is None


def test_insert_grava_a_medida_canonica_e_os_alvos():
    valores = _valores_do_insert(
        WorkoutPlanTemplateSlotItemIn(**_item(sets_count=3, measure=" Max_Reps ", time_window_seconds=30))
    )
    assert valores["measure"] == SetMeasure.MAX_REPS.value
    assert valores["time_window_seconds"] == 30
