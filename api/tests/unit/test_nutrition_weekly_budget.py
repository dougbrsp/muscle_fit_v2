"""Semana de nutrição (dono `app/domain/nutrition/weekly_budget.py`, D-H/E3):
semana na segunda, orçamento = soma das metas, banco ≤ 10% do alvo diário e
≤ 5% do semanal, aderência com tolerância de 5% nas kcal e proteína ≥ 90%."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from app.domain.nutrition import carb_cycling as cc
from app.domain.nutrition import macros as mc
from app.domain.nutrition import meal_math as mm
from app.domain.nutrition import strategy as st
from app.domain.nutrition import weekly_budget as wb

SEGUNDA = date(2026, 10, 12)
ALVO_KCAL = 2000.0
ALVO_PROTEINA = 150.0


def _meta(kcal: float = ALVO_KCAL, proteina: float = ALVO_PROTEINA) -> mc.MacroTargets:
    return mc.MacroTargets(
        kcal=kcal,
        protein_g=proteina,
        carb_g=200.0,
        fat_g=60.0,
        fiber_g=28.0,
        protein_basis=st.ProteinBasis.BODY_WEIGHT,
        protein_basis_kg=75.0,
        protein_g_per_kg=2.0,
    )


def _ingestao(kcal: float, proteina: float = ALVO_PROTEINA) -> mc.Macros:
    return mc.Macros(kcal=kcal, protein_g=proteina, carb_g=200.0, fat_g=60.0)


def test_constantes():
    assert wb.WEEK_STARTS_ON_WEEKDAY == 0
    assert wb.MAX_DAILY_CARRYOVER_SHARE == 0.10 and wb.MAX_WEEKLY_BANK_SHARE == 0.05
    assert wb.ADHERENCE_KCAL_TOLERANCE_SHARE == 0.05 and wb.ADHERENCE_PROTEIN_MIN_SHARE == 0.90
    assert wb.MIN_LOGGED_DAYS_FOR_WEEK == 5


@pytest.mark.parametrize("deslocamento", range(7))
def test_semana_comeca_na_segunda(deslocamento):
    dia = SEGUNDA + timedelta(days=deslocamento)
    assert wb.week_start(dia) == SEGUNDA
    assert wb.week_days(dia) == tuple(SEGUNDA + timedelta(days=i) for i in range(7))


def test_domingo_pertence_a_semana_anterior():
    assert wb.week_start(SEGUNDA - timedelta(days=1)) == SEGUNDA - timedelta(days=7)


def test_orcamento_semanal_e_soma_das_metas():
    orcamento = wb.weekly_budget([_meta()] * 7)
    assert orcamento.kcal == pytest.approx(7 * ALVO_KCAL) and orcamento.protein_g == pytest.approx(7 * ALVO_PROTEINA)
    assert orcamento.days_with_target == 7 and orcamento.limitations == ()
    json.dumps(orcamento.to_dict())


def test_orcamento_ignora_dia_sem_meta_calculavel():
    vazio = mc.macro_split(None, st.StrategyParams.defaults_for(st.Strategy.MAINTENANCE), 75.0)
    orcamento = wb.weekly_budget([_meta(), None, vazio, _meta(2200.0)])
    assert orcamento.days_with_target == 2 and orcamento.kcal == pytest.approx(4200.0)
    nada = wb.weekly_budget([None, vazio])
    assert nada.kcal is None and nada.limitations == (wb.LIMIT_NO_TARGETS,)


def test_orcamento_do_ciclo_de_carbo_e_sete_vezes_o_alvo():
    alvo = mc.macro_split(2600.0, st.StrategyParams.defaults_for(st.Strategy.BULKING), 80.0)
    semana = cc.distribute_week(alvo, cc.day_kinds_for_week((True, False, True, False, True, False, False)), 80.0)
    orcamento = wb.weekly_budget(semana.days)
    assert orcamento.kcal == pytest.approx(7 * alvo.kcal)
    assert orcamento.carb_g == pytest.approx(7 * alvo.carb_g)
    assert orcamento.protein_g == pytest.approx(7 * alvo.protein_g)


ALVOS = [ALVO_KCAL] * 7  # teto diário 200 kcal (10%), teto semanal 700 kcal (5% de 14.000)


@pytest.mark.parametrize(
    ("alvos", "ingestoes", "saldo", "transferencia", "clamps"),
    [
        (ALVOS, [ALVO_KCAL] * 7, 0.0, 0.0, ()),
        (ALVOS, [1900.0] + [ALVO_KCAL] * 6, 100.0, 100.0, ()),
        (ALVOS, [1800.0] + [None] * 6, 200.0, 200.0, ()),  # exatamente no teto diário
        (ALVOS, [1900.0] * 3 + [None] * 4, 300.0, 200.0, (wb.CLAMP_DAILY_CARRYOVER,)),
        (ALVOS, [1850.0] * 7, 1050.0, 200.0, (wb.CLAMP_WEEKLY_BANK, wb.CLAMP_DAILY_CARRYOVER)),
        (ALVOS, [2100.0] * 3 + [None] * 4, -300.0, -200.0, (wb.CLAMP_DAILY_CARRYOVER,)),  # comeu a mais
        # Um dia com meta só: o teto semanal (5% de 2.000 = 100) prende antes do diário (200).
        ([ALVO_KCAL] + [None] * 6, [1700.0] + [None] * 6, 300.0, 100.0, (wb.CLAMP_WEEKLY_BANK,)),
    ],
)
def test_banco_limitado_a_10_por_cento_do_dia_e_5_por_cento_da_semana(alvos, ingestoes, saldo, transferencia, clamps):
    banco = wb.bank_state(alvos, ingestoes)
    validos = [a for a in alvos if a is not None]
    assert banco.max_daily_carryover_kcal == pytest.approx(wb.MAX_DAILY_CARRYOVER_SHARE * sum(validos) / len(validos))
    assert banco.max_weekly_bank_kcal == pytest.approx(wb.MAX_WEEKLY_BANK_SHARE * sum(validos))
    assert banco.balance_kcal == pytest.approx(saldo)
    assert banco.carryover_kcal == pytest.approx(transferencia)
    assert banco.clamped_by == clamps
    assert abs(banco.carryover_kcal) <= banco.max_daily_carryover_kcal + 1e-9
    assert abs(banco.carryover_kcal) <= banco.max_weekly_bank_kcal + 1e-9
    json.dumps(banco.to_dict())


def test_banco_sem_metas_ou_sem_registro():
    sem_meta = wb.bank_state([None] * 7, [1800.0] * 7)
    assert sem_meta.balance_kcal is None and sem_meta.limitations == (wb.LIMIT_NO_TARGETS,)
    sem_registro = wb.bank_state(ALVOS, [None] * 7)
    assert sem_registro.balance_kcal is None and sem_registro.logged_days == 0
    assert sem_registro.limitations == (wb.LIMIT_NO_LOGGED_DAYS,)
    assert sem_registro.max_daily_carryover_kcal == pytest.approx(200.0)


@pytest.mark.parametrize(
    ("kcal_dia", "dentro"),
    [
        (ALVO_KCAL, True),
        (2100.0, True),  # +5% exatos (a razão em float é 1,0500000000000000444)
        (2101.0, False),
        (1900.0, True),  # −5% exatos
        (1899.0, False),
    ],
)
def test_aderencia_kcal_com_tolerancia_de_5_por_cento(kcal_dia, dentro):
    aderencia = wb.weekly_adherence([_meta()] * 7, [_ingestao(kcal_dia)] * 7)
    assert aderencia.kcal_share == pytest.approx(kcal_dia / ALVO_KCAL)
    assert aderencia.kcal_within_tolerance is dentro


@pytest.mark.parametrize(("proteina_dia", "ok"), [(150.0, True), (135.0, True), (134.9, False), (200.0, True)])
def test_aderencia_proteina_minimo_90_por_cento(proteina_dia, ok):
    aderencia = wb.weekly_adherence([_meta()] * 7, [_ingestao(ALVO_KCAL, proteina_dia)] * 7)
    assert aderencia.protein_ok is ok


@pytest.mark.parametrize(("registrados", "valida"), [(7, True), (5, True), (4, False), (1, False)])
def test_semana_valida_com_5_dias_registrados(registrados, valida):
    ingestoes = [_ingestao(ALVO_KCAL)] * registrados + [None] * (7 - registrados)
    aderencia = wb.weekly_adherence([_meta()] * 7, ingestoes)
    assert aderencia.logged_days == registrados and aderencia.valid_week is valida


def test_aderencia_so_nos_dias_com_meta_e_registro():
    metas = [_meta(), _meta(), None, _meta()]
    ingestoes = [_ingestao(2000.0), None, _ingestao(5000.0), _ingestao(2200.0)]
    aderencia = wb.weekly_adherence(metas, ingestoes)
    assert aderencia.logged_days == 2
    assert aderencia.kcal_share == pytest.approx(4200.0 / 4000.0)


def test_aderencia_sem_registro():
    aderencia = wb.weekly_adherence([_meta()] * 7, [None] * 7)
    assert aderencia.kcal_share is None and aderencia.protein_share is None
    assert aderencia.kcal_within_tolerance is None and aderencia.protein_ok is None
    assert aderencia.valid_week is False and aderencia.limitations == (wb.LIMIT_NO_LOGGED_DAYS,)
    json.dumps(aderencia.to_dict())


# ---------------------------------------------------------------------------
# Diário (L4): dia registrado, balanço do dia e relatório da semana
# ---------------------------------------------------------------------------


def _total(kcal=1900.0, proteina=140.0, carbo=200.0, gordura=60.0, incompletos=()) -> mm.NutritionTotals:
    return mm.NutritionTotals(
        nutrients=mm.Nutrients(energy_kcal=kcal, protein_g=proteina, carbohydrates_g=carbo, total_fat_g=gordura,
                               fiber_g=25.0, sodium_mg=1500.0),
        incomplete_fields=tuple(incompletos),
        nature=mm.MealNature.DERIVED,
        item_count=3,
    )


def test_meta_materializada_vira_macro_targets():
    meta = wb.target_macros(2000, 150, 200, 60, 28)
    assert meta.computable and meta.kcal == 2000.0 and meta.fiber_g == 28.0
    assert wb.target_macros(None, None, None, None) is None
    assert wb.target_macros(None, 150, None, None).computable is False  # meta não calculável gravada


def test_ingestao_do_dia_so_com_total_completo():
    assert wb.intake_macros(None) is None
    assert wb.intake_macros(_total()) == mc.Macros(kcal=1900.0, protein_g=140.0, carb_g=200.0, fat_g=60.0, fiber_g=25.0)
    assert wb.intake_macros(_total(incompletos=("energy_kcal",))) is None
    # Sódio ou fibra incompletos não tiram o dia da semana.
    assert wb.intake_macros(_total(incompletos=("sodium_mg",))) is not None


def test_balanco_do_dia_por_nutriente():
    balanco = wb.day_balance(_meta(), _total(), tdee_kcal=2500)
    linhas = {linha.field: linha for linha in balanco.rows}
    assert [linha.field for linha in balanco.rows] == ["energy_kcal", "protein_g", "carbohydrates_g", "total_fat_g", "fiber_g"]
    assert linhas["energy_kcal"].remaining == pytest.approx(100.0) and linhas["energy_kcal"].share == pytest.approx(0.95)
    assert linhas["protein_g"].remaining == pytest.approx(10.0) and linhas["fiber_g"].remaining == pytest.approx(3.0)
    assert balanco.energy_balance_kcal == pytest.approx(-600.0) and balanco.tdee_kcal == 2500.0
    assert balanco.limitations == ()
    json.dumps(balanco.to_dict())


def test_balanco_com_ingestao_incompleta_ou_sem_meta_nao_inventa_numero():
    incompleto = wb.day_balance(_meta(), _total(incompletos=("energy_kcal",)), tdee_kcal=2500)
    energia = incompleto.rows[0]
    assert energia.intake == 1900.0 and energia.intake_complete is False
    assert energia.remaining is None and energia.share is None and incompleto.energy_balance_kcal is None
    assert incompleto.rows[1].remaining == pytest.approx(10.0)  # proteína completa ainda calcula
    assert "total_incomplete:energy_kcal" in incompleto.limitations
    sem_meta = wb.day_balance(None, _total(), tdee_kcal=None)
    assert all(linha.target is None and linha.remaining is None for linha in sem_meta.rows)
    assert sem_meta.energy_balance_kcal is None and sem_meta.limitations == (wb.LIMIT_NO_DAY_TARGET,)
    sem_refeicao = wb.day_balance(_meta(), None, tdee_kcal=2500)
    assert sem_refeicao.rows[0].intake is None and sem_refeicao.limitations == (wb.LIMIT_NO_INTAKE,)


def test_relatorio_da_semana_deixa_fora_o_dia_incompleto():
    dias = wb.week_days(SEGUNDA)
    metas = [_meta()] * 7
    totais = [_total(kcal=2000.0, proteina=150.0)] * 5 + [_total(incompletos=("energy_kcal",)), None]
    relatorio = wb.week_report(dias, metas, totais)
    assert relatorio.logged_days == dias[:5] and relatorio.incomplete_days == (dias[5],)
    assert relatorio.budget.kcal == pytest.approx(7 * ALVO_KCAL)
    assert relatorio.adherence.logged_days == 5 and relatorio.adherence.valid_week is True
    assert relatorio.adherence.kcal_share == pytest.approx(1.0)
    assert relatorio.bank.logged_days == 5 and relatorio.bank.balance_kcal == pytest.approx(0.0)
    assert relatorio.limitations == (wb.LIMIT_INCOMPLETE_INTAKE_DAYS,)
    json.dumps(relatorio.to_dict())


def test_relatorio_sem_meta_nem_refeicao():
    dias = wb.week_days(SEGUNDA)
    relatorio = wb.week_report(dias, [None] * 7, [None] * 7)
    assert relatorio.logged_days == () and relatorio.budget.kcal is None
    assert wb.LIMIT_NO_TARGETS in relatorio.limitations and wb.LIMIT_NO_LOGGED_DAYS in relatorio.limitations
    acima = wb.week_report(dias, [_meta()] * 7, [_total(kcal=1000.0)] * 7)
    assert wb.CLAMP_WEEKLY_BANK in acima.limitations and wb.CLAMP_DAILY_CARRYOVER in acima.limitations
