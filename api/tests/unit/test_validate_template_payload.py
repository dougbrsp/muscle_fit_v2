"""Caracterizacao de workout_plan_templates._validate_template_payload.

Funcao pura (pydantic + HTTPException), sem banco. Congela as regras de
negocio do save de modelos (POST/PUT) - inclusive o que hoje NAO e validado."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.models import WorkoutPlanTemplateIn
from app.workout_plan_templates import _validate_template_payload


def mg(group_id=1, **extra):
    return {"item_order": 1, "item_kind": "muscle_group", "muscle_group_id": group_id, **extra}


def equip(**extra):
    return {
        "item_order": 1,
        "item_kind": "equipment",
        "muscle_group_id": 1,
        "equipment_id": 5,
        "mapping_id": 100,
        "sets_count": 3,
        **extra,
    }


def cardio(**extra):
    return {"item_order": 1, "item_kind": "cardio", "cardio_duration_minutes": 20, **extra}


def slot(sequence=1, items=None, **extra):
    return {"sequence": sequence, "name": f"S{sequence}", "items": items or [], **extra}


def payload(slots, volume_mode="percent_budget"):
    return WorkoutPlanTemplateIn(name="Modelo", volume_mode=volume_mode, slots=slots)


def assert_422(model, fragment):
    with pytest.raises(HTTPException) as exc_info:
        _validate_template_payload(model)
    assert exc_info.value.status_code == 422
    assert fragment in exc_info.value.detail


# ---------------------------------------------------------------------------
# Payloads validos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    [
        payload(
            [
                slot(1, [mg(1, priority_min_pct=40, priority_max_pct=60), cardio(item_order=2)]),
                slot(2, [cardio()], slot_type="descanso"),
                slot(3, [], slot_type="descanso"),
            ]
        ),
        payload(
            [
                slot(
                    1,
                    [
                        equip(
                            advanced_technique="drop_set",
                            advanced_technique_applications=2,
                            warmup_reps=12,
                            warmup_sets_count=2,
                            feeler_set_reps=5,
                            peak_contraction_seconds=2,
                            rest_seconds=45,
                        ),
                        cardio(item_order=2),
                    ],
                )
            ],
            volume_mode="equipment_series",
        ),
        # slots fora de ordem no payload: so importa o conjunto 1..N
        payload([slot(2, [mg()]), slot(1, [mg()])]),
        # 3 priorizados e o limite
        payload([slot(1, [mg(i, prioritize=True) for i in (1, 2, 3)])]),
        # faixa do slot preenchida nos dois lados, limites inclusivos
        payload([slot(1, [mg()], priority_min_pct=0, priority_max_pct=100)]),
        # item muscle_group com so um lado da faixa e aceito
        payload([slot(1, [mg(priority_min_pct=30)])]),
        # comportamento atual: weekday nao e validado
        payload([slot(1, [mg()], weekday=9)]),
        # comportamento atual: slot_type livre (so 'descanso' tem regra)
        payload([slot(1, [mg()], slot_type="qualquer")]),
        # comportamento atual: equipment_series aceita slot sem itens
        payload([slot(1, [])], volume_mode="equipment_series"),
        # M101b: so o grupo = exercicio livre (o motor escolhe o exercicio)
        payload([slot(1, [equip(equipment_id=None, mapping_id=None)])], volume_mode="equipment_series"),
    ],
)
def test_payloads_validos(model):
    assert _validate_template_payload(model) is None


# ---------------------------------------------------------------------------
# Payloads invalidos (primeira regra violada vence)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "fragment"),
    [
        (payload([]), "pelo menos 1 slot"),
        (payload([slot(1), slot(3)]), "sequence únicos de 1 a 2"),
        (payload([slot(1), slot(1)]), "sequence únicos de 1 a 2"),
        (payload([slot(0)]), "sequence únicos de 1 a 1"),
        (payload([slot(1, [mg()], priority_min_pct=10)]), "exige mínimo e máximo"),
        (payload([slot(1, [mg()], priority_max_pct=10)]), "exige mínimo e máximo"),
        (payload([slot(1, [mg()], priority_min_pct=-1, priority_max_pct=10)]), "entre 0 e 100"),
        (payload([slot(1, [mg()], priority_min_pct=10, priority_max_pct=101)]), "entre 0 e 100"),
        (payload([slot(1, [mg()], priority_min_pct=60, priority_max_pct=50)]), "mínima não pode ser maior"),
        (payload([slot(1, [mg()], slot_type="descanso")]), "(descanso) não pode ter itens"),
        (
            payload([slot(1, [equip()], slot_type="descanso")], volume_mode="equipment_series"),
            "(descanso) não pode ter itens",
        ),
        (payload([slot(1, [mg(i, prioritize=True) for i in (1, 2, 3, 4)])]), "no máximo 3 itens"),
        (payload([slot(1, [mg(advanced_technique="rest_pause")])]), "só são suportados em item_kind='equipment'"),
        (payload([slot(1, [mg(warmup_reps=10)])]), "só são suportados em item_kind='equipment'"),
        (payload([slot(1, [cardio(feeler_set_reps=3)])]), "só são suportados em item_kind='equipment'"),
        (payload([slot(1, [mg(peak_contraction_seconds=2)])]), "só são suportados em item_kind='equipment'"),
        (payload([slot(1, [mg(rest_seconds=60)])]), "só são suportados em item_kind='equipment'"),
        (payload([slot(1, [cardio(rest_seconds=60)])]), "só são suportados em item_kind='equipment'"),
        (payload([slot(1, [mg()])], volume_mode="equipment_series"), "não permite itens de grupo muscular"),
        (payload([slot(1, [mg(group_id=None)])]), "exige muscle_group_id"),
        (payload([slot(1, [mg(mapping_id=100)])]), "não pode ter mapping_id"),
        (payload([slot(1, [mg(priority_min_pct=101)])]), "priority_min_pct deve estar entre 0 e 100"),
        (payload([slot(1, [mg(priority_max_pct=-5)])]), "priority_max_pct deve estar entre 0 e 100"),
        (payload([slot(1, [mg(priority_min_pct=70, priority_max_pct=60)])]), "não pode ser maior que priority_max_pct"),
        (
            payload([slot(1, [{"item_order": 1, "item_kind": "exercise", "mapping_id": 100}])]),
            "'exercise' não é mais suportado",
        ),
        (payload([slot(1, [equip()])]), "só é permitido quando volume_mode='equipment_series'"),
        # M101b: par pela metade (so exercicio ou so equipamento) e invalido
        (payload([slot(1, [equip(equipment_id=None)])], volume_mode="equipment_series"), "par equipamento + exercício"),
        (payload([slot(1, [equip(mapping_id=None)])], volume_mode="equipment_series"), "par equipamento + exercício"),
        (payload([slot(1, [equip(muscle_group_id=None)])], volume_mode="equipment_series"), "exige muscle_group_id"),
        (
            payload([slot(1, [equip(muscle_group_id=None, mapping_id=None, equipment_id=None)])], volume_mode="equipment_series"),
            "exige muscle_group_id",
        ),
        (payload([slot(1, [equip(sets_count=0)])], volume_mode="equipment_series"), "sets_count >= 1"),
        (payload([slot(1, [equip(sets_count=None)])], volume_mode="equipment_series"), "sets_count >= 1"),
        (payload([slot(1, [cardio(muscle_group_id=1)])]), "não usa muscle_group_id/mapping_id"),
        (payload([slot(1, [cardio(cardio_duration_minutes=0)])]), "cardio_duration_minutes > 0"),
        (payload([slot(1, [cardio(cardio_duration_minutes=None)])]), "cardio_duration_minutes > 0"),
        (payload([slot(1, [{"item_order": 1, "item_kind": "foo"}])]), "item_kind inválido 'foo'"),
    ],
)
def test_payloads_invalidos(model, fragment):
    assert_422(model, fragment)


def test_prioritize_em_cardio_conta_para_o_limite_de_3():
    # comportamento atual: o limite conta QUALQUER item com prioritize=True.
    items = [mg(1, prioritize=True), mg(2, prioritize=True), mg(3, prioritize=True), cardio(prioritize=True)]
    assert_422(payload([slot(1, items)]), "no máximo 3 itens")


def test_sets_count_default_do_item_e_3():
    # Default do model (WorkoutPlanTemplateSlotItemIn.sets_count) usado pelo
    # preview de ativacao do modo percent_budget.
    model = payload([slot(1, [mg()])])
    assert model.slots[0].items[0].sets_count == 3


@pytest.mark.parametrize("valor", [1, 600])
def test_rest_seconds_aceita_de_1_a_600_no_item_equipment(valor):
    """Fase 7, Frente B: descanso por exercício, mesmos limites do aquecimento."""
    model = payload([slot(1, [equip(rest_seconds=valor)])], volume_mode="equipment_series")
    _validate_template_payload(model)
    assert model.slots[0].items[0].rest_seconds == valor


@pytest.mark.parametrize("valor", [0, 601])
def test_rest_seconds_fora_de_1_a_600_e_recusado_pelo_modelo(valor):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        payload([slot(1, [equip(rest_seconds=valor)])], volume_mode="equipment_series")


def test_rest_seconds_ausente_e_none():
    model = payload([slot(1, [equip()])], volume_mode="equipment_series")
    assert model.slots[0].items[0].rest_seconds is None


# ---------------------------------------------------------------------------
# Grade do editor e teto de slots (migracao 062)
# ---------------------------------------------------------------------------


def _ciclo(n):
    return [slot(seq) for seq in range(1, n + 1)]


def _com_grade(n, rows=None, cols=None):
    return WorkoutPlanTemplateIn(name="Modelo", slots=_ciclo(n), grid_rows=rows, grid_cols=cols)


def test_teto_de_slots_e_8():
    from app.workout_plan_templates import MAX_TEMPLATE_SLOTS

    assert MAX_TEMPLATE_SLOTS == 8
    _validate_template_payload(_com_grade(8))
    assert_422(_com_grade(9), "no máximo 8 slots")


@pytest.mark.parametrize("n, rows, cols", [(8, 2, 4), (6, 2, 3), (4, 1, 4), (1, 1, 1), (3, 2, 2)])
def test_grade_que_comporta_os_slots_e_aceita(n, rows, cols):
    _validate_template_payload(_com_grade(n, rows, cols))


@pytest.mark.parametrize("n, rows, cols", [(5, 1, 4), (8, 2, 3), (2, 1, 1)])
def test_grade_menor_que_os_slots_e_recusada(n, rows, cols):
    assert_422(_com_grade(n, rows, cols), f"comporta {rows * cols} slots")


@pytest.mark.parametrize("rows, cols", [(None, None), (2, None), (None, 1)])
def test_grade_nula_ou_incompleta_nao_restringe_alem_do_teto(rows, cols):
    """NULL = automatica: so o teto de 8 vale (a capacidade exige os dois)."""
    _validate_template_payload(_com_grade(8, rows, cols))


@pytest.mark.parametrize("rows, cols", [(0, 1), (3, 1), (1, 0), (1, 5), (3, 5)])
def test_grade_fora_da_faixa_e_recusada_pelo_modelo(rows, cols):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _com_grade(1, rows, cols)


def test_grade_ausente_no_payload_e_none():
    model = payload([slot(1)])
    assert (model.grid_rows, model.grid_cols) == (None, None)


def test_legado_no_put_edita_mas_nao_cresce():
    """O PUT passa `max_slots` = max(8, slots gravados): um Modelo legado com
    10 slots continua salvavel com 10, mas nao com 11."""
    _validate_template_payload(_com_grade(10), max_slots=10)
    with pytest.raises(HTTPException) as exc_info:
        _validate_template_payload(_com_grade(11), max_slots=10)
    assert exc_info.value.status_code == 422
    assert "no máximo 10 slots" in exc_info.value.detail


# ---------------------------------------------------------------------------
# M98 C0: prescrição de cardio (Normal/HIIT) validada pelo dono
# (`domain/cardio_prescription.validate`); a rota só vira 422 com o slot.
# ---------------------------------------------------------------------------

HIIT = {
    "cardio_mode": "hiit",
    "cardio_duration_minutes": None,
    "cardio_warmup_minutes": 10,
    "hiit_low_seconds": 90,
    "hiit_high_seconds": 30,
    "hiit_low_zone": 2,
    "hiit_high_zone": 4,
    "hiit_rounds": 8,
    "cardio_cooldown_minutes": 5,
}


@pytest.mark.parametrize(
    "item",
    [
        cardio(),  # item antigo: só a duração
        cardio(cardio_mode="continuous", cardio_warmup_minutes=5, cardio_target_zone=2, cardio_cooldown_minutes=5),
        cardio(**HIIT),
    ],
)
def test_prescricao_de_cardio_valida(item):
    assert _validate_template_payload(payload([slot(1, [item])])) is None
    assert _validate_template_payload(payload([slot(1, [item], slot_type="descanso")])) is None


@pytest.mark.parametrize(
    "item,fragmento",
    [
        (cardio(**{**HIIT, "hiit_high_zone": 2}), "zona alta precisa ser maior que a zona baixa"),
        (cardio(**{**HIIT, "hiit_rounds": None}), "cardio HIIT exige nº de séries"),
        (cardio(cardio_mode="fartlek"), "tipo de cardio inválido"),
        (cardio(cardio_target_zone=6), "zona-alvo precisa estar entre Z1 e Z5"),
        (cardio(cardio_duration_minutes=10_000), "tempo de cardio (min) precisa estar entre"),
    ],
)
def test_prescricao_de_cardio_invalida_vira_422_com_o_slot(item, fragmento):
    assert_422(payload([slot(1, [item])]), "Slot 'S1': ")
    assert_422(payload([slot(1, [item])]), fragmento)
