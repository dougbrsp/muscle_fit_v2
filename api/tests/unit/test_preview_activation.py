"""Caracterizacao do preview de ativacao semanal de modelos
(workout_plan_templates._build_cycle / _compute_slot_activation /
_compute_slot_activation_equipment_series / preview_activation).

I/O isolado: `_load_muscle_group_names` (banco) e
`VolumeService.get_muscle_activation_for_exercises` (banco) substituidos.

Fase 7: a janela e sempre 7 dias a partir do primeiro slot de treino (mesma
regra de `methodology_volume.evaluation_window_days`), dando a volta no ciclo
curto e cortando o excedente do ciclo longo. Substitui a janela `max(7, ciclo)`
do PR-13 (G7)."""

from __future__ import annotations

import pytest

from app import workout_plan_templates as wpt
from app.intelligence.volume_service import VolumeService
from app.models import WorkoutPlanTemplateSlotIn

NAMES = {1: "Peitoral", 2: "Tríceps", 3: "Costas"}


def mg(group_id, sets_count=3, order=1):
    return {"item_order": order, "item_kind": "muscle_group", "muscle_group_id": group_id, "sets_count": sets_count}


def slot(sequence, items=None, slot_type="normal"):
    return {"sequence": sequence, "name": f"Slot {sequence}", "slot_type": slot_type, "items": items or []}


def preview(slots, volume_mode="percent_budget"):
    return wpt.preview_activation(wpt.WorkoutPlanTemplatePreviewIn(slots=slots, volume_mode=volume_mode))


@pytest.fixture(autouse=True)
def isolate_db(monkeypatch):
    monkeypatch.setattr(wpt, "_load_muscle_group_names", lambda ids: {i: NAMES[i] for i in ids if i in NAMES})

    def no_db(self, exercises):  # pragma: no cover - so dispara se algum teste esquecer o mock
        raise AssertionError("VolumeService nao deveria ser chamado neste teste")

    monkeypatch.setattr(VolumeService, "get_muscle_activation_for_exercises", no_db)
    # M11: o detalhamento por item le as contribuicoes a parte; aqui fica vazio
    # (coberto em test_preview_activation_items.py).
    monkeypatch.setattr(wpt, "_load_preview_contributions", lambda ids: {})


def test_build_cycle_ordena_por_sequence():
    slots = [WorkoutPlanTemplateSlotIn(**slot(s)) for s in (3, 1, 2)]
    assert [s.sequence for s in wpt._build_cycle(slots)] == [1, 2, 3]


def test_compute_slot_activation_muscle_group_e_primario_puro():
    model = WorkoutPlanTemplateSlotIn(
        **slot(1, [mg(2, sets_count=2), mg(1, sets_count=4, order=2), mg(1, sets_count=1, order=3), mg(99, order=4)])
    )
    assert wpt._compute_slot_activation(model, NAMES) == [
        # ordenado por nome; itens do mesmo grupo somam; grupo sem nome e ignorado
        {"muscle_group": "Peitoral", "primary_equivalent": 5.0, "secondary_equivalent": 0.0, "stabilizer_equivalent": 0.0, "total_equivalent": 5.0},
        {"muscle_group": "Tríceps", "primary_equivalent": 2.0, "secondary_equivalent": 0.0, "stabilizer_equivalent": 0.0, "total_equivalent": 2.0},
    ]


def test_compute_slot_activation_sets_count_none_vale_zero():
    model = WorkoutPlanTemplateSlotIn(**slot(1, [mg(1, sets_count=None)]))
    [row] = wpt._compute_slot_activation(model, NAMES)
    assert row["total_equivalent"] == 0.0


def test_compute_slot_activation_descanso_e_vazio():
    model = WorkoutPlanTemplateSlotIn(**slot(1, [mg(1)], slot_type="descanso"))
    assert wpt._compute_slot_activation(model, NAMES) == []


def test_compute_slot_activation_item_exercise_legado_usa_volume_service(monkeypatch):
    calls = []

    def fake(self, exercises):
        calls.append(exercises)
        return [{"muscle_group": "Peitoral", "primary_equivalent": 3.0, "secondary_equivalent": 0.5, "stabilizer_equivalent": 0.0}]

    monkeypatch.setattr(VolumeService, "get_muscle_activation_for_exercises", fake)
    model = WorkoutPlanTemplateSlotIn(
        **slot(1, [{"item_order": 1, "item_kind": "exercise", "mapping_id": 100, "sets_count": 3}, mg(1, sets_count=2, order=2)])
    )
    [row] = wpt._compute_slot_activation(model, NAMES)
    assert calls == [[{"mapping_id": 100, "sets_count": 3}]]
    assert row == {
        "muscle_group": "Peitoral",
        "primary_equivalent": 5.0,
        "secondary_equivalent": 0.5,
        "stabilizer_equivalent": 0.0,
        "total_equivalent": 5.5,
    }


def test_preview_sem_slots():
    assert preview([]) == {
        "cycle_length": 0,
        "days": [],
        "message": "Preencha ao menos um slot para ver o preview de ativação.",
    }


def test_preview_ciclo_de_3_slots_repete_em_7_dias():
    result = preview([slot(1, [mg(1)]), slot(3, [], slot_type="descanso"), slot(2, [mg(2, sets_count=4)])])
    assert result["cycle_length"] == 3
    assert [d["day"] for d in result["days"]] == [1, 2, 3, 4, 5, 6, 7]
    assert [d["slot_sequence"] for d in result["days"]] == [1, 2, 3, 1, 2, 3, 1]
    assert result["days"][0]["groups"][0]["primary_equivalent"] == 3.0
    assert result["days"][1]["groups"][0] == {
        "muscle_group": "Tríceps",
        "primary_equivalent": 4.0,
        "secondary_equivalent": 0.0,
        "stabilizer_equivalent": 0.0,
        "total_equivalent": 4.0,
    }
    assert result["days"][2]["slot_type"] == "descanso"
    assert result["days"][2]["groups"] == []


def test_preview_equipment_series_delega_ao_volume_service(monkeypatch):
    def fake(self, exercises):
        assert exercises == [{"mapping_id": 100, "sets_count": 4}]
        return [
            {"muscle_group": "Tríceps", "primary_equivalent": 0.0, "secondary_equivalent": 2.0, "stabilizer_equivalent": 0.0, "total_equivalent": 2.0, "primary_series": 0},
            {"muscle_group": "Peitoral", "primary_equivalent": 4.0, "secondary_equivalent": 0.0, "stabilizer_equivalent": 0.0, "total_equivalent": 4.0, "primary_series": 4},
        ]

    monkeypatch.setattr(VolumeService, "get_muscle_activation_for_exercises", fake)
    item = {"item_order": 1, "item_kind": "equipment", "muscle_group_id": 1, "equipment_id": 5, "mapping_id": 100, "sets_count": 4}
    result = preview([slot(1, [item])], volume_mode="equipment_series")
    groups = result["days"][0]["groups"]
    # reordenado por nome e so com as 5 chaves do contrato do frontend
    assert [g["muscle_group"] for g in groups] == ["Peitoral", "Tríceps"]
    assert set(groups[0]) == {"muscle_group", "primary_equivalent", "secondary_equivalent", "stabilizer_equivalent", "total_equivalent"}


def test_preview_equipment_series_engole_erro_e_zera_o_dia(monkeypatch):
    def boom(self, exercises):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(VolumeService, "get_muscle_activation_for_exercises", boom)
    item = {"item_order": 1, "item_kind": "equipment", "muscle_group_id": 1, "equipment_id": 5, "mapping_id": 100, "sets_count": 4}
    result = preview([slot(1, [item])], volume_mode="equipment_series")
    assert all(day["groups"] == [] for day in result["days"])


def test_preview_ciclo_de_8_slots_corta_o_8o():
    """Fase 7: janela de 7 dias (mesma da metodologia). O 8o slot fica fora -
    antes a janela era `max(7, ciclo)` e o 8o entrava."""
    result = preview([slot(s, [mg(1)]) for s in range(1, 8)] + [slot(8, [mg(3)])])
    assert result["cycle_length"] == 8
    assert result["window_days"] == 7
    assert [d["slot_sequence"] for d in result["days"]] == list(range(1, 8))
    assert result["group_totals"] == [{"muscle_group_id": 1, "muscle_group": "Peitoral", "total_equivalent": 21.0}]


def test_preview_comeca_no_primeiro_slot_de_treino():
    """Descanso no inicio do ciclo nao abre a janela, mas ocupa dia depois."""
    result = preview([slot(1, [], slot_type="descanso"), slot(2, [mg(1)]), slot(3, [mg(2)])])
    assert result["window_days"] == 7
    assert [d["slot_sequence"] for d in result["days"]] == [2, 3, 1, 2, 3, 1, 2]
    assert result["days"][2]["groups"] == []


def test_preview_so_descanso_devolve_janela_vazia_com_mensagem():
    result = preview([slot(1, [mg(1)], slot_type="descanso")])
    assert result["days"] == []
    assert result["window_days"] == 0
    assert "message" in result


@pytest.mark.parametrize("cycle_length", [1, 2, 3, 4, 5, 6, 7])
def test_preview_ciclo_de_ate_7_slots_continua_em_7_dias(cycle_length):
    """Ciclo curto repete dentro dos 7 dias (3 slots -> 1,2,3,1,2,3,1); com 7
    slots cada um aparece uma vez - identico ao comportamento anterior."""
    result = preview([slot(s, [mg(1)]) for s in range(1, cycle_length + 1)])
    assert result["window_days"] == 7
    assert [d["day"] for d in result["days"]] == list(range(1, 8))
    assert [d["slot_sequence"] for d in result["days"]] == [
        ((day - 1) % cycle_length) + 1 for day in range(1, 8)
    ]


# ---------------------------------------------------------------------------
# Modo A (percent_budget) com tipo de treino: o item 'muscle_group' sem
# `sets_count` entra pelo PONTO MEDIO do orcamento (meta semanal x faixa %).
# ---------------------------------------------------------------------------

# muscle_group_id -> weekly_target (Costas, id 3, sem meta)
WEEKLY = {1: 20.0, 2: 10.0}


def budget(group_id, min_pct=None, max_pct=None, order=1, sets_count=None):
    return {
        "item_order": order,
        "item_kind": "muscle_group",
        "muscle_group_id": group_id,
        "sets_count": sets_count,
        "priority_min_pct": min_pct,
        "priority_max_pct": max_pct,
    }


def budget_preview(slots, training_type_id=7):
    return wpt.preview_activation(
        wpt.WorkoutPlanTemplatePreviewIn(slots=slots, volume_mode="percent_budget", training_type_id=training_type_id)
    )


@pytest.fixture
def weekly_targets(monkeypatch):
    calls = []

    def fake(training_type_id):
        calls.append(training_type_id)
        return dict(WEEKLY)

    monkeypatch.setattr(wpt, "_load_weekly_targets", fake)
    return calls


def test_modo_a_com_tipo_de_treino_usa_ponto_medio_e_lista_todos_os_grupos(weekly_targets):
    result = budget_preview([slot(1, [budget(1, 20, 30), budget(2, 40, 60, order=2), budget(3, 10, 20, order=3)])])
    assert weekly_targets == [7]
    groups = {g["muscle_group"]: g for g in result["days"][0]["groups"]}
    # Peitoral: 20 x 25% = 5; Triceps: 10 x 50% = 5; Costas sem meta fica 0
    assert groups["Peitoral"]["primary_equivalent"] == 5.0
    assert groups["Tríceps"]["primary_equivalent"] == 5.0
    assert groups["Costas"]["primary_equivalent"] == 0.0
    # janela de 7 dias com 1 slot: 7 ocorrencias
    totals = {g["muscle_group"]: g["total_equivalent"] for g in result["group_totals"]}
    assert totals == {"Peitoral": 35.0, "Tríceps": 35.0}
    lines = {i["muscle_group_id"]: i["contributions"] for i in result["items"]}
    assert lines[1] == [{"muscle_group_id": 1, "muscle_group": "Peitoral", "role": "primary", "value": 35.0}]
    assert 3 not in lines


def test_modo_a_item_budgets_cobre_o_ciclo_com_fallback_e_sem_meta(weekly_targets):
    # 8 slots: o 8o fica fora da janela, mas entra em item_budgets
    slots = [slot(s, [budget(1, 50, 50)]) for s in range(1, 8)]
    slots.append(slot(8, [budget(1, 60, 70), budget(2, order=2), budget(3, order=3)]))
    slots.append(slot(9, [budget(2)], slot_type="descanso"))
    result = budget_preview(slots)
    assert [d["slot_sequence"] for d in result["days"]] == list(range(1, 8))
    eighth = [b for b in result["item_budgets"] if b["slot_sequence"] == 8]
    assert eighth == [
        {"slot_sequence": 8, "item_index": 0, "item_order": 1, "muscle_group_id": 1, "pct_min": 60, "pct_max": 70,
         "sets_min": 12.0, "sets_max": 14.0, "has_target": True},
        # sem % -> divide igualmente o que sobra ate 100% (40/2 e 30/2)
        {"slot_sequence": 8, "item_index": 1, "item_order": 2, "muscle_group_id": 2, "pct_min": 20.0, "pct_max": 15.0,
         "sets_min": 2.0, "sets_max": 1.5, "has_target": True},
        {"slot_sequence": 8, "item_index": 2, "item_order": 3, "muscle_group_id": 3, "pct_min": 20.0, "pct_max": 15.0,
         "sets_min": None, "sets_max": None, "has_target": False},
    ]
    assert len(result["item_budgets"]) == 10  # descanso fica de fora


def test_modo_a_com_meta_ignora_sets_count_e_usa_ponto_medio(weekly_targets):
    """Igual ao motor v2 (e ao tooltip do card): com tipo de treino e meta para
    o grupo, o `sets_count` escondido (3, o default do item novo) nao conta."""
    result = budget_preview([slot(1, [budget(1, 20, 30, sets_count=3)])])
    assert result["days"][0]["groups"][0]["primary_equivalent"] == 5.0
    assert result["items"][0]["contributions"][0]["value"] == 35.0


def test_modo_a_grupo_sem_meta_usa_sets_count(weekly_targets):
    # Costas (id 3) nao tem meta: vale o sets_count, como antes
    result = budget_preview([slot(1, [budget(3, 20, 30, sets_count=4)])])
    assert result["days"][0]["groups"][0]["primary_equivalent"] == 4.0
    assert result["item_budgets"][0]["has_target"] is False


def test_modo_a_sem_tipo_de_treino_usa_sets_count(weekly_targets):
    result = budget_preview([slot(1, [budget(1, 20, 30, sets_count=4)])], training_type_id=None)
    assert result["days"][0]["groups"][0]["primary_equivalent"] == 4.0


def test_modo_a_sem_tipo_de_treino_mantem_comportamento_antigo(weekly_targets):
    result = budget_preview([slot(1, [budget(1, 20, 30)])], training_type_id=None)
    assert weekly_targets == []
    assert "item_budgets" not in result
    assert result["days"][0]["groups"][0]["total_equivalent"] == 0.0


def test_modo_a_falha_ao_ler_metas_nao_quebra_o_preview(monkeypatch):
    def boom(training_type_id):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(wpt, "_load_weekly_targets", boom)
    result = budget_preview([slot(1, [budget(1, 20, 30)])])
    assert "item_budgets" not in result
    assert result["days"][0]["groups"][0]["total_equivalent"] == 0.0


def test_equipment_series_ignora_training_type_id(weekly_targets):
    result = wpt.preview_activation(
        wpt.WorkoutPlanTemplatePreviewIn(slots=[slot(1, [])], volume_mode="equipment_series", training_type_id=7)
    )
    assert weekly_targets == []
    assert "item_budgets" not in result
