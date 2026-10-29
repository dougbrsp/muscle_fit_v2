"""Caracterizacao de template_volume_planner.build_template_volume_plan e
do envelope de MuscleSlotBudget (template_volume_domain.py).

I/O isolado: `build_template_volume_plan` so recebe um TemplateCycleContext
montado em memoria; `build_template_cycle_context` e exercitado com os
loaders de banco/HTTP substituidos por monkeypatch."""

from __future__ import annotations

import pytest

from app import training_types
from app.intelligence import template_volume_planner as planner
from app.intelligence.template_volume_domain import TemplateCycleContext, WeeklyMuscleTarget
from app.intelligence.template_volume_planner import (
    TemplateVolumeEngineNotApplicable,
    build_template_cycle_context,
    build_template_volume_plan,
)

PEITO, TRICEPS, OMBRO, BICEPS = 1, 2, 3, 4


def mg_item(group_id, order, min_pct=None, max_pct=None, **extra):
    return {
        "item_kind": "muscle_group",
        "muscle_group_id": group_id,
        "item_order": order,
        "priority_min_pct": min_pct,
        "priority_max_pct": max_pct,
        **extra,
    }


def target(group_id, name, weekly_target, weekly_min=None, weekly_max=None):
    return WeeklyMuscleTarget(
        muscle_group_id=group_id,
        muscle_group_name=name,
        weekly_min=weekly_target if weekly_min is None else weekly_min,
        weekly_target=weekly_target,
        weekly_max=weekly_target if weekly_max is None else weekly_max,
    )


def context(items, targets, recovery_state=None, slot_id=10):
    return TemplateCycleContext(
        template_id=1,
        training_type_id=1,
        slots=[{"id": slot_id, "sequence": 1, "name": "A", "slot_type": "normal", "items": items}],
        muscle_targets={t.muscle_group_id: t for t in targets},
        recovery_state=recovery_state or {},
    )


def by_group(budgets):
    return {b.muscle_group_id: b for b in budgets}


def test_slot_inexistente_retorna_lista_vazia():
    assert build_template_volume_plan(context([], []), slot_id=999) == []


def test_faixa_configurada_vira_orcamento_nominal_sobre_weekly_target():
    ctx = context(
        [mg_item(PEITO, 1, 60, 70, reps_target="8-12", rpe_target="8", prioritize=True)],
        [target(PEITO, "Peitoral", 10, weekly_min=8, weekly_max=14)],
    )
    [budget] = build_template_volume_plan(ctx, 10)
    assert budget.muscle_group_name == "Peitoral"
    assert (budget.weekly_min, budget.weekly_target, budget.weekly_max) == (8, 10, 14)
    assert budget.slot_pct_min == pytest.approx(0.6)
    assert budget.slot_pct_max == pytest.approx(0.7)
    # so weekly_target alimenta o nominal (weekly_min/max ficam de guardrail)
    assert budget.nominal_slot_min == pytest.approx(6.0)
    assert budget.nominal_slot_max == pytest.approx(7.0)
    assert budget.nominal_slot_mid == pytest.approx(6.5)
    assert budget.recovery_status == "READY"
    assert budget.recovery_multiplier == 1.0
    assert budget.effective_slot_min == pytest.approx(6.0)
    assert budget.effective_slot_max == pytest.approx(7.0)
    assert (budget.reps_target, budget.rpe_target, budget.prioritize) == ("8-12", "8", True)


def test_itens_sem_faixa_dividem_o_percentual_restante():
    ctx = context(
        [
            mg_item(PEITO, 1, 60, 70),
            mg_item(TRICEPS, 2),
            mg_item(OMBRO, 3),
            mg_item(BICEPS, 4),
        ],
        [target(PEITO, "Peitoral", 10), target(TRICEPS, "Tríceps", 9), target(OMBRO, "Ombros", 9), target(BICEPS, "Bíceps", 9)],
    )
    budgets = by_group(build_template_volume_plan(ctx, 10))
    triceps = budgets[TRICEPS]
    # min: (100-60)/3 = 13.33%  |  max: (100-70)/3 = 10%
    assert triceps.slot_pct_min == pytest.approx(40 / 3 / 100)
    assert triceps.slot_pct_max == pytest.approx(0.10)
    # comportamento atual (bug): o fallback pode gerar min > max, porque o
    # restante de min e o restante de max sao divididos independentemente.
    assert triceps.nominal_slot_min > triceps.nominal_slot_max
    assert triceps.nominal_slot_min == pytest.approx(1.2)
    assert triceps.nominal_slot_max == pytest.approx(0.9)


def test_itens_configurados_somando_100_ou_mais_zeram_o_fallback():
    ctx = context(
        [mg_item(PEITO, 1, 70, 80), mg_item(TRICEPS, 2, 40, 50), mg_item(OMBRO, 3)],
        [target(PEITO, "Peitoral", 10), target(TRICEPS, "Tríceps", 10), target(OMBRO, "Ombros", 10)],
    )
    ombro = by_group(build_template_volume_plan(ctx, 10))[OMBRO]
    assert (ombro.nominal_slot_min, ombro.nominal_slot_max) == (0.0, 0.0)


def test_item_com_so_um_lado_da_faixa():
    # comportamento atual: o lado preenchido entra na soma configurada E o
    # item conta como "nao configurado" para dividir o restante.
    ctx = context(
        [mg_item(PEITO, 1, 50, None), mg_item(TRICEPS, 2)],
        [target(PEITO, "Peitoral", 10), target(TRICEPS, "Tríceps", 10)],
    )
    budgets = by_group(build_template_volume_plan(ctx, 10))
    # fallback_min = (100-50)/2 = 25 ; fallback_max = (100-0)/2 = 50
    assert budgets[PEITO].slot_pct_min == pytest.approx(0.50)
    assert budgets[PEITO].slot_pct_max == pytest.approx(0.50)
    assert budgets[TRICEPS].slot_pct_min == pytest.approx(0.25)
    assert budgets[TRICEPS].slot_pct_max == pytest.approx(0.50)


def test_grupo_sem_meta_semanal_e_omitido_mas_dilui_o_fallback():
    ctx = context(
        [mg_item(PEITO, 1), mg_item(BICEPS, 2)],
        [target(PEITO, "Peitoral", 10)],  # sem meta para BICEPS
    )
    budgets = build_template_volume_plan(ctx, 10)
    assert [b.muscle_group_id for b in budgets] == [PEITO]
    # comportamento atual: BICEPS (sem meta) ainda conta no divisor -> 50%.
    assert budgets[0].slot_pct_max == pytest.approx(0.50)


def test_project_muscle_item_budgets_funcao_pura_do_orcamento():
    """Fonte unica da regra (planner v2 + preview do builder): faixa
    configurada, divisao igual do que sobra ate 100% e grupo sem meta
    marcado como tal (sem series), na ordem recebida, com a posicao original."""
    items = [
        {"item_kind": "cardio", "item_order": 1},
        mg_item(PEITO, 2, 60, 70),
        mg_item(TRICEPS, 3),
        mg_item(BICEPS, 4),
    ]
    peito, triceps, biceps = planner.project_muscle_item_budgets(items, {PEITO: 10.0, TRICEPS: 9.0})
    assert (peito.index, peito.pct_min, peito.pct_max) == (1, 60, 70)
    assert (peito.sets_min, peito.sets_max, peito.sets_mid) == pytest.approx((6.0, 7.0, 6.5))
    assert (triceps.index, triceps.pct_min, triceps.pct_max) == (2, 20.0, 15.0)
    assert (triceps.sets_min, triceps.sets_max) == pytest.approx((1.8, 1.35))
    assert triceps.has_target and not biceps.has_target
    assert (biceps.pct_min, biceps.sets_min, biceps.sets_max, biceps.sets_mid) == (20.0, None, None, None)


def test_project_muscle_item_budgets_aceita_item_pydantic():
    from app.models import WorkoutPlanTemplateSlotItemIn

    item = WorkoutPlanTemplateSlotItemIn(
        item_order=1, item_kind="muscle_group", muscle_group_id=PEITO, priority_min_pct=20, priority_max_pct=30
    )
    [budget] = planner.project_muscle_item_budgets([item], {PEITO: 20.0})
    assert budget.item is item
    assert budget.sets_mid == pytest.approx(5.0)


def test_ignora_itens_nao_muscle_group_e_ordena_por_item_order():
    ctx = context(
        [
            mg_item(TRICEPS, 3, 20, 30),
            {"item_kind": "cardio", "item_order": 1, "cardio_duration_minutes": 20},
            {"item_kind": "muscle_group", "muscle_group_id": None, "item_order": 2},
            mg_item(PEITO, 2, 50, 60),
        ],
        [target(PEITO, "Peitoral", 10), target(TRICEPS, "Tríceps", 10)],
    )
    assert [b.muscle_group_id for b in build_template_volume_plan(ctx, 10)] == [PEITO, TRICEPS]


@pytest.mark.parametrize(
    ("status", "multiplier", "eff_min", "eff_max", "excluded"),
    [
        ("READY", 1.0, 6.0, 8.0, False),
        ("REDUCED_DOSE", 0.75, 4.5, 6.0, False),
        ("NO_DIRECT_WORK", 0.0, 0.0, 0.0, True),
    ],
)
def test_recuperacao_aplica_multiplicador_no_envelope_efetivo(status, multiplier, eff_min, eff_max, excluded):
    ctx = context(
        [mg_item(PEITO, 1, 60, 80)],
        [target(PEITO, "Peitoral", 10)],
        recovery_state={PEITO: {"status": status, "multiplier": multiplier}},
    )
    [budget] = build_template_volume_plan(ctx, 10)
    assert budget.recovery_status == status
    assert budget.nominal_slot_min == pytest.approx(6.0)
    assert budget.effective_slot_min == pytest.approx(eff_min)
    assert budget.effective_slot_max == pytest.approx(eff_max)
    assert budget.is_hard_excluded is excluded


@pytest.mark.parametrize(
    ("status", "multiplier", "activation", "expected"),
    [
        ("READY", 1.0, 5.99, "UNDER_ALLOCATED"),
        ("READY", 1.0, 6.0, "BALANCED"),
        ("READY", 1.0, 8.0, "BALANCED"),
        ("READY", 1.0, 8.01, "OVER_ALLOCATED"),
        ("NO_DIRECT_WORK", 0.0, 0.0, "BALANCED"),
        ("NO_DIRECT_WORK", 0.0, 0.1, "OVER_ALLOCATED"),
    ],
)
def test_deviation_status(status, multiplier, activation, expected):
    ctx = context(
        [mg_item(PEITO, 1, 60, 80)],
        [target(PEITO, "Peitoral", 10)],
        recovery_state={PEITO: {"status": status, "multiplier": multiplier}},
    )
    [budget] = build_template_volume_plan(ctx, 10)
    assert budget.deviation_status(activation) == expected


def test_prioritize_nao_altera_orcamento():
    base = [target(PEITO, "Peitoral", 10)]
    [a] = build_template_volume_plan(context([mg_item(PEITO, 1, 60, 70, prioritize=False)], base), 10)
    [b] = build_template_volume_plan(context([mg_item(PEITO, 1, 60, 70, prioritize=True)], base), 10)
    assert (a.nominal_slot_min, a.nominal_slot_max) == (b.nominal_slot_min, b.nominal_slot_max)


# ---------------------------------------------------------------------------
# build_template_cycle_context (I/O substituido)
# ---------------------------------------------------------------------------


def test_build_template_cycle_context_exige_training_type():
    with pytest.raises(TemplateVolumeEngineNotApplicable):
        build_template_cycle_context({"id": 5, "training_type_id": None, "slots": []})


def test_build_template_cycle_context_ponta_a_ponta_com_io_isolado(monkeypatch):
    monkeypatch.setattr(
        training_types,
        "get_training_type_muscle_group_targets",
        lambda training_type_id: {
            PEITO: {"min": 8, "target": 10, "max": 14},
            TRICEPS: {"min": 4, "target": 6, "max": 8},
        },
    )
    monkeypatch.setattr(planner, "_load_muscle_group_names", lambda ids: {PEITO: "Peitoral", TRICEPS: "Tríceps"})
    recovery_calls: list[dict] = []

    def fake_load_projected_recovery(plan_date, plan_time=None, user_id=None):
        recovery_calls.append({"plan_date": plan_date, "plan_time": plan_time, "user_id": user_id})
        return [
            {"muscle_group_id": TRICEPS, "projected_readiness_status": "REDUCED_DOSE"},
            {"muscle_group_id": None, "projected_readiness_status": "NO_DIRECT_WORK"},
        ]

    monkeypatch.setattr(planner, "_load_projected_recovery", fake_load_projected_recovery)
    template = {
        "id": 7,
        "training_type_id": 3,
        "slots": [
            {"id": 21, "sequence": 2, "name": "B", "slot_type": "normal", "items": [mg_item(TRICEPS, 1, 50, 50)]},
            {"id": 20, "sequence": 1, "name": "A", "slot_type": "normal", "items": [mg_item(PEITO, 1, 50, 60), mg_item(TRICEPS, 2, 50, 50)]},
        ],
    }
    ctx = build_template_cycle_context(template, user_id=42, plan_date="2026-09-14", plan_time="07:00")

    # Regressao G26a (PR-09): a recuperacao projetada deve ser carregada para
    # o usuario do contexto, nunca via token mestre (admin).
    assert recovery_calls == [{"plan_date": "2026-09-14", "plan_time": "07:00", "user_id": 42}]
    assert ctx.ordered_slot_ids() == [20, 21]
    assert [a.slot_sequence for a in ctx.allocations_for_group(TRICEPS)] == [1, 2]
    assert ctx.recovery_state == {TRICEPS: {"status": "REDUCED_DOSE", "multiplier": 0.75}}

    budgets = by_group(build_template_volume_plan(ctx, 20))
    assert budgets[PEITO].effective_slot_max == pytest.approx(6.0)
    assert budgets[TRICEPS].nominal_slot_min == pytest.approx(3.0)
    assert budgets[TRICEPS].effective_slot_min == pytest.approx(2.25)
