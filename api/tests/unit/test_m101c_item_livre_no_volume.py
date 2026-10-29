"""M101c: o item de exercicio livre do Modelo Modo B (M101b: grupo e SEM par)
conta no volume/ativacao PLANEJADOS como series do grupo PRIMARIO, peso
`FREE_EXERCISE_PRIMARY_WEIGHT`, com a MESMA regra em todos os consumidores:
previa do editor, metodologia de volume e planejado do dashboard. Dono:
`domain/free_exercise_item` (+ `technique_volume.free_exercise_primary_sets`).
Itens com par e itens do Modo A nao mudam. Sem banco.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.free_exercise_item import FREE_EXERCISE_PRIMARY_WEIGHT, is_free_exercise_item
from app.intelligence import methodology_volume as mv
from app.intelligence import methodology_volume_service as mvs
from app.intelligence.dashboard import muscle_summary_service as mss
from app.intelligence.technique_volume import free_exercise_primary_sets
from tests.support.methodology_paridade import grupo, seed_volume_classes

SEED = seed_volume_classes()
PEITO, COSTAS, TRICEPS = 52, 6, 14
GROUPS = (grupo(PEITO, "Peito", "large"), grupo(COSTAS, "Costas", "large"), grupo(TRICEPS, "Triceps", "small"))
# Par fixo 100 = supino (PRIMARY peito).
PRIMARY = {100: {PEITO}}

FREE = {"item_kind": "equipment", "muscle_group_id": PEITO, "mapping_id": None, "equipment_id": None, "sets_count": 4}
PAIR = {"item_kind": "equipment", "muscle_group_id": PEITO, "mapping_id": 100, "equipment_id": 1, "sets_count": 3}


# ---------------------------------------------------------------------------
# Dono
# ---------------------------------------------------------------------------


def test_dono_reconhece_so_o_item_livre():
    assert is_free_exercise_item(FREE)
    assert not is_free_exercise_item(PAIR)
    assert not is_free_exercise_item({**FREE, "muscle_group_id": None})
    assert not is_free_exercise_item({**FREE, "equipment_id": 7})  # par pela metade
    assert not is_free_exercise_item({**FREE, "item_kind": "muscle_group"})  # Modo A
    # geracao/congelamento: itens ja sao `equipment`, sem o campo
    without_kind = {k: v for k, v in FREE.items() if k != "item_kind"}
    assert not is_free_exercise_item(without_kind)
    assert is_free_exercise_item(without_kind, require_kind=False)


def test_series_do_item_livre_no_grupo_primario():
    assert FREE_EXERCISE_PRIMARY_WEIGHT == 1.0
    assert free_exercise_primary_sets(FREE, None) == 4.0
    assert free_exercise_primary_sets(PAIR, None) == 0.0


# ---------------------------------------------------------------------------
# Metodologia de volume
# ---------------------------------------------------------------------------


def _slot_in(*items: dict[str, Any], sequence: int = 1):
    from app.models import WorkoutPlanTemplateSlotIn

    return WorkoutPlanTemplateSlotIn(
        sequence=sequence,
        name=f"Dia {sequence}",
        slot_type="normal",
        items=[{"item_order": i, **item} for i, item in enumerate(items, start=1)],
    )


def _week(*training):
    """Ciclo de 7 dias: os slots dados + descanso ate completar 7."""
    from app.models import WorkoutPlanTemplateSlotIn

    slots = list(training)
    while len(slots) < 7:
        seq = len(slots) + 1
        slots.append(WorkoutPlanTemplateSlotIn(sequence=seq, name=f"Descanso {seq}", slot_type="descanso", items=[]))
    return slots


def _evaluate(slots, counting_mode=mv.DEFAULT_VOLUME_COUNTING_MODE):
    inputs = mvs.build_slot_inputs(slots, "equipment_series", {100: 1000}, catalog={})
    return mv.evaluate_methodology_volume(
        inputs, PRIMARY, GROUPS, "equipment_series", None, SEED, counting_mode
    )


def _block(result, group_id):
    return next(g for g in result.groups if g.muscle_group_id == group_id)


def test_servico_marca_so_o_item_livre():
    inputs = mvs.build_slot_inputs([_slot_in(FREE, PAIR)], "equipment_series", {100: 1000}, catalog={})
    assert [i.free_exercise for i in inputs[0].items] == [True, False]
    # Modo A nunca marca
    mode_a = mvs.build_slot_inputs(
        [_slot_in({**FREE, "item_kind": "muscle_group"})], "percent_budget", {}, catalog={}
    )
    assert mode_a[0].items[0].free_exercise is False


def test_metodologia_conta_o_item_livre_no_grupo():
    result = _evaluate(_week(_slot_in(FREE, PAIR)))
    peito = _block(result, PEITO)
    assert peito.direct_sets_cycle_total == 7  # 4 livres + 3 do par
    # Cada item livre e um exercicio distinto da sessao (a geracao nao repete)
    assert peito.direct_exercises_cycle_total == 2
    assert peito.frequency_raw == 1
    # Nao e mais "item sem mapping" (falha de cadastro)
    assert result.data_quality.items_without_mapping == 0
    share = next(s for s in result.item_shares if s.item_index == 0)
    assert share.mapping_id is None and share.muscle_group_id == PEITO
    assert [(g.muscle_group_id, g.direct_sets, g.is_primary) for g in share.groups] == [(PEITO, 4, True)]


def test_metodologia_sessao_so_com_item_livre_conta_na_frequencia():
    result = _evaluate(_week(_slot_in(FREE, sequence=1), _slot_in(FREE, sequence=2)))
    peito = _block(result, PEITO)
    assert peito.frequency_raw == 2 and peito.direct_sets_cycle_total == 8


def test_metodologia_item_livre_no_ponderado_e_primario_puro():
    result = _evaluate([_slot_in(FREE)], counting_mode="primary_secondary")
    assert _block(result, PEITO).direct_sets_cycle_total == 4
    assert _block(result, TRICEPS).direct_sets_cycle_total == 0


def test_metodologia_itens_com_par_inalterados():
    """Sem item livre o resultado e identico ao de antes (o flag nasce False)."""
    with_flag = _evaluate([_slot_in(PAIR)])
    legacy = mv.evaluate_methodology_volume(
        (
            mv.MethodologySlotInput(
                sequence=1,
                slot_type="normal",
                name="Dia 1",
                items=(
                    mv.MethodologyItemInput(
                        mapping_id=100, canonical_exercise_id=1000, sets_count=3, muscle_group_id=PEITO, item_index=0
                    ),
                ),
            ),
        ),
        PRIMARY,
        GROUPS,
        "equipment_series",
        None,
        SEED,
    )
    assert with_flag == legacy


def test_item_sem_par_que_nao_e_livre_segue_sem_mapping():
    result = _evaluate([_slot_in({**FREE, "muscle_group_id": None})])
    assert result.data_quality.items_without_mapping == 1
    assert _block(result, PEITO).direct_sets_cycle_total == 0


# ---------------------------------------------------------------------------
# Planejado do dashboard (`resolve_template_plan`)
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, conn):
        self._conn = conn
        self._rows: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self._conn.executed.append((sql, tuple(params or ())))
        self._rows = self._conn.head if "FROM workout_plan_template_assignments" in sql else self._conn.activation

    def fetchall(self):
        return list(self._rows)


class _Conn:
    def __init__(self, activation):
        self.head = [
            {"template_id": 7, "template_name": "B", "volume_mode": "equipment_series", "slots_count": 2, "version_id": 3}
        ]
        self.activation = activation
        self.executed: list[tuple[str, tuple]] = []

    def cursor(self):
        return _Cursor(self)


def _unpaired(sequence: int, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "row_kind": mss.TEMPLATE_PLAN_ROW_UNPAIRED,
        "slot_sequence": sequence,
        "advanced_technique": None,
        "advanced_technique_applications": None,
        "planned_sets": None,
        "planned_weight": None,
        **item,
    }


def test_dashboard_conta_o_item_livre_no_grupo_numa_consulta_so():
    activation = [
        {"row_kind": mss.TEMPLATE_PLAN_ROW_PAIR, "slot_sequence": 1, "muscle_group_id": PEITO,
         "planned_sets": 3.0, "planned_weight": 1.0},
        _unpaired(1, FREE),
        _unpaired(2, {**FREE, "muscle_group_id": COSTAS, "sets_count": 2}),
        # sem par e sem grupo / par pela metade: nao e livre, soma 0
        _unpaired(2, {**FREE, "muscle_group_id": None}),
        _unpaired(2, {**FREE, "muscle_group_id": TRICEPS, "equipment_id": 9}),
    ]
    conn = _Conn(activation)
    plan = mss.resolve_template_plan(conn, user_id=1)
    assert plan is not None
    assert plan.activation_by_group == {PEITO: pytest.approx(7.0), COSTAS: pytest.approx(2.0)}
    assert plan.activation_by_slot == {1: {PEITO: pytest.approx(7.0)}, 2: {COSTAS: pytest.approx(2.0)}}
    # cabecalho + ativacao: o item livre vem no MESMO statement (UNION ALL)
    assert len(conn.executed) == 2
    sql, params = conn.executed[1]
    assert "UNION ALL" in sql and params == (7, 3, 7, 3)


def test_dashboard_linhas_de_par_inalteradas():
    """Linha agregada sem `row_kind` (forma anterior) continua somando igual."""
    conn = _Conn([{"slot_sequence": 1, "muscle_group_id": PEITO, "planned_sets": 9.0, "planned_weight": 3.0}])
    plan = mss.resolve_template_plan(conn, user_id=1)
    assert plan is not None and plan.activation_by_group == {PEITO: pytest.approx(9.0)}


def test_projetado_nao_conta_duas_vezes_o_slot_ja_gerado():
    """A ficha gerada do item livre tem exercicio concreto e conta pelo caminho
    normal (planejado das fichas); o PROJETADO do Modelo soma so os slots
    pendentes, entao o slot ja gerado sai do Modelo."""
    by_slot = {1: {PEITO: 4.0}, 2: {COSTAS: 2.0}}
    assert mss.sum_projected_activation_from_pending_slots(by_slot, [2]) == {COSTAS: 2.0}


# ---------------------------------------------------------------------------
# Paridade: previa do editor = metodologia = dashboard
# ---------------------------------------------------------------------------


def test_paridade_previa_metodologia_dashboard():
    from app import workout_plan_templates as wpt

    slot = _slot_in(FREE, {**FREE, "muscle_group_id": COSTAS, "sets_count": 2})
    preview = {
        g["muscle_group"]: g["total_equivalent"]
        for g in wpt._compute_slot_activation_equipment_series(slot, None, {PEITO: "Peito", COSTAS: "Costas"})
    }
    methodology = _evaluate([slot])
    conn = _Conn([_unpaired(1, item.model_dump()) for item in slot.items])
    plan = mss.resolve_template_plan(conn, user_id=1)
    assert plan is not None
    for group_id, name in ((PEITO, "Peito"), (COSTAS, "Costas")):
        assert preview[name] == _block(methodology, group_id).direct_sets_cycle_total
        assert preview[name] == pytest.approx(plan.activation_by_group[group_id])


@pytest.mark.parametrize(
    "extra",
    [
        {"advanced_technique": "rest_pause", "advanced_technique_applications": 2, "sets_count": 4},
        {"sets_count": 0},
    ],
    ids=["com_tecnica", "zero_series"],
)
def test_paridade_com_tecnica_e_sem_series(monkeypatch, extra):
    """Os tres caminhos dao o MESMO numero para o mesmo item livre, inclusive
    com o equivalente da tecnica e com 0 series."""
    from app import workout_plan_templates as wpt
    from app.intelligence import technique_volume

    catalog_rows = {
        "rest_pause": {"activation_set_equivalent": 0.40, "max_equivalent_per_anchor": 1.00, "default_sets_count": None}
    }
    monkeypatch.setattr(technique_volume, "load_catalog", lambda: catalog_rows)
    slot = _slot_in({**FREE, **extra})
    expected = free_exercise_primary_sets(slot.items[0], technique_volume.new_catalog())

    preview = {
        g["muscle_group"]: g["total_equivalent"]
        for g in wpt._compute_slot_activation_equipment_series(slot, technique_volume.new_catalog(), {PEITO: "Peito"})
    }
    inputs = mvs.build_slot_inputs([slot], "equipment_series", {}, catalog=None)
    methodology = mv.evaluate_methodology_volume(inputs, PRIMARY, GROUPS, "equipment_series", None, SEED)
    conn = _Conn([_unpaired(1, slot.items[0].model_dump())])
    plan = mss.resolve_template_plan(conn, user_id=1)
    assert plan is not None

    assert preview.get("Peito", 0.0) == pytest.approx(expected)
    assert float(_block(methodology, PEITO).direct_sets_cycle_total) == pytest.approx(expected)
    assert plan.activation_by_group.get(PEITO, 0.0) == pytest.approx(expected)
    if extra.get("advanced_technique"):
        assert expected == pytest.approx(4.8)  # 4 + 2 x 0,40
