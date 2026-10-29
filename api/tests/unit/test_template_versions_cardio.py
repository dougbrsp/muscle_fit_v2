"""Prescrição de cardio nas versões do Modelo (M98 C0, migração 095).

- A forma canônica de um Modelo com cardio antigo (só duração) é IDÊNTICA à
  de antes da 095, com ou sem o backfill (`cardio_mode='continuous'`): o
  primeiro PUT depois do deploy (inclusive do editor atual, que não conhece
  os campos) não abre versão.
- Mudar a prescrição (aquecimento, zona, HIIT) abre versão.
- O INSERT grava pela normalização do dono: modo resolvido, só os campos do
  modo, item que não é cardio com tudo NULL; a cópia e a leitura levam as
  colunas novas.

Rodar: cd api && python -m pytest tests/unit/test_template_versions_cardio.py
"""

from __future__ import annotations

from app import template_versions as tv
from app.domain import cardio_prescription as cp
from app.models import WorkoutPlanTemplateSlotIn, WorkoutPlanTemplateSlotItemIn
from tests.unit.test_template_versions import FakeCur, _item, _payload, _slot, _version_row
from tests.unit.test_training_goal_versoes import _canonico_antes_da_093


def _cardio(**overrides):
    base = {name: None for name in tv.ITEM_FIELDS}
    base.update(item_order=2, item_kind="cardio", prioritize=False, sets_count=None, cardio_duration_minutes=20)
    base.update(overrides)
    return base


def _com_cardio(**cardio):
    return _payload(slots=[_slot(1, [_item(), _cardio(**cardio)])])


def _pydantic(slots):
    return [
        WorkoutPlanTemplateSlotIn(**{**s, "items": [WorkoutPlanTemplateSlotItemIn(**i) for i in s["items"]]})
        for s in slots
    ]


def test_colunas_novas_no_insert_na_copia_e_na_leitura():
    for coluna in cp.NEW_COLUMNS:
        assert coluna in tv.ITEM_FIELDS
        assert f"i.{coluna}" in tv.VERSION_ITEMS_SQL and f"i.{coluna}" in tv.COPY_ITEMS_SQL
        assert coluna not in tv._CANONICAL_ITEM_FIELDS
    assert tv.INSERT_ITEM_SQL.count("%s") == len(tv.ITEM_FIELDS) + 1


def test_cardio_antigo_tem_a_forma_canonica_de_antes():
    antigo = _com_cardio()["slots"]
    assert tv.canonical_slots(antigo) == _canonico_antes_da_093(antigo)
    com_backfill = _com_cardio(cardio_mode="continuous")["slots"]
    assert tv.canonical_slots(com_backfill) == tv.canonical_slots(antigo)
    zerado = _com_cardio(cardio_warmup_minutes=0, cardio_cooldown_minutes=0)["slots"]
    assert tv.canonical_slots(zerado) == tv.canonical_slots(antigo)


def test_put_do_editor_atual_contra_vigente_com_backfill_nao_abre_versao():
    """Vigente lida do banco já com o backfill; o editor atual manda o item
    só com a duração (Pydantic preenche o resto com None)."""
    vigente_slots = _com_cardio(cardio_mode="continuous")["slots"]
    payload = _com_cardio()
    sem_campos_novos = [
        dict(s, items=[{k: v for k, v in i.items() if k not in cp.NEW_COLUMNS} for i in s["items"]])
        for s in payload["slots"]
    ]
    cur = FakeCur(vigente=_version_row(), slots=vigente_slots)
    assert tv.write_structural_version(cur, 7, dict(payload, slots=_pydantic(sem_campos_novos)), None, actor=1) is None
    assert cur.writes() == []


def test_mudar_a_prescricao_abre_versao():
    vigente_slots = _com_cardio(cardio_mode="continuous")["slots"]
    for mudanca in (
        {"cardio_target_zone": 2},
        {"cardio_warmup_minutes": 5},
        {"cardio_mode": "hiit", "cardio_duration_minutes": None, "hiit_low_seconds": 90, "hiit_high_seconds": 30,
         "hiit_low_zone": 2, "hiit_high_zone": 4, "hiit_rounds": 8},
    ):
        payload = _com_cardio(**mudanca)
        cur = FakeCur(vigente=_version_row(), slots=vigente_slots)
        assert tv.write_structural_version(cur, 7, dict(payload, slots=_pydantic(payload["slots"])), None, actor=1)
        assert any(sql.startswith("INSERT INTO workout_plan_template_slot_items") for sql in cur.writes())


def test_insert_grava_a_normalizacao_do_dono():
    payload = _com_cardio(cardio_mode="hiit", cardio_duration_minutes=20, cardio_target_zone=3, hiit_low_seconds=90,
                          hiit_high_seconds=30, hiit_low_zone=2, hiit_high_zone=4, hiit_rounds=8)
    payload["slots"][0]["items"][0]["cardio_duration_minutes"] = 15  # lixo num item de equipamento
    cur = FakeCur(vigente=_version_row(), slots=_payload()["slots"])
    assert tv.write_structural_version(cur, 7, dict(payload, slots=_pydantic(payload["slots"])), None, actor=1)
    linhas = [dict(zip(("slot_id", *tv.ITEM_FIELDS), p)) for p in cur.params_of("INSERT INTO workout_plan_template_slot_items")]
    equipamento, cardio = linhas
    assert all(equipamento[c] is None for c in cp.PRESCRIPTION_FIELDS)
    assert cardio["cardio_mode"] == "hiit" and cardio["hiit_rounds"] == 8
    assert cardio["cardio_duration_minutes"] is None and cardio["cardio_target_zone"] is None


def test_insert_de_cardio_antigo_grava_o_modo_normal():
    payload = _com_cardio()
    cur = FakeCur(vigente=_version_row(), slots=_payload()["slots"])
    assert tv.write_structural_version(cur, 7, dict(payload, slots=_pydantic(payload["slots"])), None, actor=1)
    linhas = [dict(zip(("slot_id", *tv.ITEM_FIELDS), p)) for p in cur.params_of("INSERT INTO workout_plan_template_slot_items")]
    assert linhas[1]["cardio_mode"] == "continuous" and linhas[1]["cardio_duration_minutes"] == 20
