"""M11: detalhamento POR ITEM do preview de ativacao semanal
(`preview_activation` -> `items` + `group_totals`, aditivos a resposta).

O banco fica de fora substituindo UMA fonte so: o resolvedor de contribuicoes
(`_resolve_muscle_contributions_for_mappings`), que alimenta TANTO o calculo dos
dias (`VolumeService.get_muscle_activation_for_exercises`, caminho real, sem
mock) QUANTO o detalhamento por item. Assim o teste prova que as linhas de um
grupo somam exatamente o total que a tabela mostra (soma dos `days`)."""

from __future__ import annotations

from collections import defaultdict

import pytest

from app import workout_plan_templates as wpt
from app.intelligence import muscle_contribution_resolver

NAMES = {1: "Peitoral", 2: "Tríceps", 3: "Costas miolo/densidade", 4: "Bíceps", 5: "Core", 6: "Lombar"}

# mapping_id -> contribuicoes (mesmo formato de _resolve_muscle_contributions_for_mappings)
CONTRIBUTIONS = {
    # Remada curvada no orbital
    100: [
        {"muscle_group_id": 3, "muscle_group_name": "Costas miolo/densidade", "role": "PRIMARY", "stimulus_weight": 1.0},
        {"muscle_group_id": 4, "muscle_group_name": "Bíceps", "role": "SECONDARY", "stimulus_weight": 0.5},
        {"muscle_group_id": 5, "muscle_group_name": "Core", "role": "STABILIZER", "stimulus_weight": 0.3},
        {"muscle_group_id": 6, "muscle_group_name": "Lombar", "role": "STABILIZER", "stimulus_weight": 0.25},
    ],
    # Rosca direta
    200: [
        {"muscle_group_id": 4, "muscle_group_name": "Bíceps", "role": "PRIMARY", "stimulus_weight": 1.0},
        {"muscle_group_id": 5, "muscle_group_name": "Core", "role": "STABILIZER", "stimulus_weight": 0.1},
    ],
    # Puxada frente
    300: [
        {"muscle_group_id": 3, "muscle_group_name": "Costas miolo/densidade", "role": "PRIMARY", "stimulus_weight": 0.8},
        {"muscle_group_id": 4, "muscle_group_name": "Bíceps", "role": "secondary", "stimulus_weight": 0.4},
    ],
}


@pytest.fixture(autouse=True)
def isolate_db(monkeypatch):
    calls: list[set[int]] = []

    def fake_resolver(mapping_ids):
        calls.append(set(mapping_ids))
        return {mapping_id: [dict(c) for c in CONTRIBUTIONS.get(mapping_id, [])] for mapping_id in mapping_ids}

    monkeypatch.setattr(muscle_contribution_resolver, "_resolve_muscle_contributions_for_mappings", fake_resolver)
    monkeypatch.setattr(wpt, "_load_muscle_group_names", lambda ids: {i: NAMES[i] for i in ids if i in NAMES})
    return calls


def equipment(mapping_id, sets_count=4, order=1):
    return {
        "item_order": order,
        "item_kind": "equipment",
        "muscle_group_id": 3,
        "equipment_id": 9,
        "mapping_id": mapping_id,
        "sets_count": sets_count,
    }


def mg(group_id, sets_count=3, order=1):
    return {"item_order": order, "item_kind": "muscle_group", "muscle_group_id": group_id, "sets_count": sets_count}


def slot(sequence, items=None, slot_type="normal"):
    return {"sequence": sequence, "name": f"Slot {sequence}", "slot_type": slot_type, "items": items or []}


def preview(slots, volume_mode="equipment_series"):
    return wpt.preview_activation(wpt.WorkoutPlanTemplatePreviewIn(slots=slots, volume_mode=volume_mode))


def table_totals(result, metric="total_equivalent"):
    """O que a tabela do frontend soma: `days[].groups[].<metric>` por nome."""
    totals: dict[str, float] = defaultdict(float)
    for day in result["days"]:
        for group in day["groups"]:
            totals[group["muscle_group"]] += group[metric]
    return totals


def item_sums(result):
    sums: dict[str, float] = defaultdict(float)
    for item in result["items"]:
        for contribution in item["contributions"]:
            sums[contribution["muscle_group"]] += contribution["value"]
    return sums


def test_equipment_series_item_traz_papeis_e_valor_da_janela():
    # ciclo de 2 slots em 7 dias: slot 1 nos dias 1,3,5,7 (4x), slot 2 nos dias 2,4,6 (3x)
    result = preview([slot(1, [equipment(100, sets_count=4)]), slot(2, [equipment(200, sets_count=3)])])

    first = next(i for i in result["items"] if i["slot_sequence"] == 1)
    assert first["item_index"] == 0
    assert first["item_kind"] == "equipment"
    assert first["mapping_id"] == 100
    assert first["occurrences"] == 4
    # primario, depois secundario, depois estabilizadores (maior valor primeiro)
    assert first["contributions"] == [
        {"muscle_group_id": 3, "muscle_group": "Costas miolo/densidade", "role": "primary", "value": 16.0},
        {"muscle_group_id": 4, "muscle_group": "Bíceps", "role": "secondary", "value": 8.0},
        {"muscle_group_id": 5, "muscle_group": "Core", "role": "stabilizer", "value": 4.8},
        {"muscle_group_id": 6, "muscle_group": "Lombar", "role": "stabilizer", "value": 4.0},
    ]

    totals = {g["muscle_group"]: g["total_equivalent"] for g in result["group_totals"]}
    # Biceps: 4 x (4 x 0.5) do slot 1 + 3 x (3 x 1.0) do slot 2
    assert totals["Bíceps"] == 17.0
    assert totals["Core"] == pytest.approx(4.8 + 3 * 3 * 0.1)


def test_soma_das_linhas_bate_com_o_total_da_tabela_por_grupo():
    slots = [
        slot(1, [equipment(100, sets_count=4), equipment(200, sets_count=3, order=2)]),
        slot(2, [equipment(300, sets_count=5), equipment(100, sets_count=2, order=2)]),
        slot(3, [], slot_type="descanso"),
    ]
    result = preview(slots)

    expected = table_totals(result)
    sums = item_sums(result)
    group_totals = {g["muscle_group"]: g["total_equivalent"] for g in result["group_totals"]}

    assert set(sums) == set(expected) == set(group_totals)
    for name, total in expected.items():
        assert group_totals[name] == pytest.approx(total, abs=0.01)
        assert sums[name] == pytest.approx(total, abs=0.02)
        # numerador de cada linha nunca passa o denominador do grupo
        for item in result["items"]:
            for contribution in item["contributions"]:
                if contribution["muscle_group"] == name:
                    assert contribution["value"] <= group_totals[name] + 0.01


def test_chave_do_item_e_a_posicao_no_payload_e_pula_itens_que_nao_contribuem():
    slots = [
        slot(
            1,
            [
                {"item_order": 1, "item_kind": "cardio", "cardio_duration_minutes": 10, "sets_count": None},
                equipment(None, order=2),  # equipamento sem exercicio: nao entra
                equipment(100, sets_count=0, order=3),  # 0 series: nao entra
                equipment(200, sets_count=2, order=4),
            ],
        )
    ]
    result = preview(slots)
    assert [(i["slot_sequence"], i["item_index"], i["item_order"]) for i in result["items"]] == [(1, 3, 4)]


def test_itens_seguem_a_janela_de_7_dias_a_partir_do_primeiro_treino():
    # descanso + 7 treinos + 1 treino: janela = slots 2..8; o slot 9 fica fora
    slots = [slot(1, [], slot_type="descanso")]
    slots += [slot(s, [equipment(100, sets_count=1)]) for s in range(2, 9)]
    slots.append(slot(9, [equipment(200, sets_count=5)]))
    result = preview(slots)
    assert {i["slot_sequence"] for i in result["items"]} == set(range(2, 9))
    assert all(i["occurrences"] == 1 for i in result["items"])
    assert set(item_sums(result)) == set(table_totals(result))


def test_slot_descanso_nao_gera_item():
    result = preview([slot(1, [equipment(100)]), slot(2, [equipment(200)], slot_type="descanso")])
    assert {i["slot_sequence"] for i in result["items"]} == {1}


def test_percent_budget_grupo_muscular_e_primario_puro_e_soma_o_total():
    # ciclo de 3: slot 1 nos dias 1,4,7 (3x); slot 2 nos dias 2,5 (2x)
    slots = [slot(1, [mg(1, sets_count=4), mg(2, sets_count=2, order=2)]), slot(2, [mg(1, sets_count=3)]), slot(3, [mg(99)])]
    result = preview(slots, volume_mode="percent_budget")

    by_key = {(i["slot_sequence"], i["item_index"]): i for i in result["items"]}
    assert by_key[(1, 0)]["contributions"] == [
        {"muscle_group_id": 1, "muscle_group": "Peitoral", "role": "primary", "value": 12.0}
    ]
    assert by_key[(2, 0)]["contributions"][0]["value"] == 6.0
    # grupo sem nome (99) nao aparece na tabela nem na linha
    assert by_key[(3, 0)]["contributions"] == []

    assert {g["muscle_group"]: g["total_equivalent"] for g in result["group_totals"]} == {"Peitoral": 18.0, "Tríceps": 6.0}
    assert item_sums(result) == table_totals(result)


def test_percent_budget_ignora_item_equipment_como_a_tabela():
    result = preview([slot(1, [equipment(100), mg(1, order=2)])], volume_mode="percent_budget")
    assert [i["item_index"] for i in result["items"]] == [1]


def test_resolvedor_de_contribuicoes_chamado_uma_vez_so_para_o_detalhamento(isolate_db):
    preview([slot(1, [equipment(100), equipment(200, order=2)]), slot(2, [equipment(100)])])
    # 7 chamadas do calculo dos dias (um por dia) + 1 do detalhamento com TODOS os mappings
    assert {100, 200} in isolate_db
    assert isolate_db[-1] == {100, 200}


def test_falha_no_detalhamento_nao_quebra_o_preview(monkeypatch):
    def boom(mapping_ids):
        raise RuntimeError("banco fora")

    monkeypatch.setattr(wpt, "_load_preview_contributions", boom)
    result = preview([slot(1, [mg(1)])], volume_mode="percent_budget")
    assert "items" not in result and "group_totals" not in result
    assert result["days"][0]["groups"][0]["total_equivalent"] == 3.0


def test_percent_budget_sem_mapping_nao_consulta_contribuicoes(monkeypatch):
    def no_db(mapping_ids):
        raise AssertionError("nao deveria consultar")

    monkeypatch.setattr(wpt, "_load_preview_contributions", lambda ids: no_db(ids) if ids else {})
    result = preview([slot(1, [mg(1)])], volume_mode="percent_budget")
    assert result["items"][0]["contributions"][0]["value"] == 21.0


def test_percent_budget_com_tipo_de_treino_linha_do_item_usa_ponto_medio(monkeypatch):
    """Modo A sem `sets_count`: a linha por item e a tabela usam o MESMO ponto
    medio do orcamento, e as linhas de um grupo somam o total da tabela."""
    monkeypatch.setattr(wpt, "_load_weekly_targets", lambda training_type_id: {1: 20.0, 2: 12.0})
    items = [
        {"item_order": 1, "item_kind": "muscle_group", "muscle_group_id": 1, "sets_count": None,
         "priority_min_pct": 20, "priority_max_pct": 30},
        {"item_order": 2, "item_kind": "muscle_group", "muscle_group_id": 2, "sets_count": None},
    ]
    result = wpt.preview_activation(
        wpt.WorkoutPlanTemplatePreviewIn(
            slots=[slot(1, items), slot(2, [], slot_type="descanso")], volume_mode="percent_budget", training_type_id=3
        )
    )
    # slot 1 aparece nos dias 1,3,5,7 (4x). Peitoral: 20 x 25% = 5/sessao;
    # Triceps sem %: fallback (100-20)=80% e (100-30)=70% -> 12 x 75% = 9/sessao
    values = {i["muscle_group_id"]: i["contributions"][0]["value"] for i in result["items"]}
    assert values == {1: 20.0, 2: 36.0}
    assert item_sums(result) == pytest.approx(table_totals(result))
