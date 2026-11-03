"""Agregação do dashboard de nutrição (`domain/nutrition/dashboard`, N5, mapa §82):
janela, meta × ingestão por dia (partes do gráfico), aderência por dia e por
semana, dias incompletos, média móvel e ritmo do peso, TDEE adaptativo que só
sugere e estados vazios. Python puro, sem banco."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.domain.nutrition import dashboard as dash
from app.domain.nutrition import meal_math as mm
from app.domain.nutrition import weekly_budget as wb

SEGUNDA = date(2026, 10, 12)


def _total(kcal=2000.0, proteina=150.0, carbo=200.0, gordura=60.0, incompletos=()) -> mm.NutritionTotals:
    return mm.NutritionTotals(
        nutrients=mm.Nutrients(energy_kcal=kcal, protein_g=proteina, carbohydrates_g=carbo, total_fat_g=gordura,
                               fiber_g=25.0, sodium_mg=1500.0),
        incomplete_fields=tuple(incompletos),
        nature=mm.MealNature.DERIVED,
        item_count=3,
    )


def _meta(kcal=2000.0):
    return wb.target_macros(kcal, 150, 200, 60, 28)


def test_janela_e_normalizada_e_termina_no_dia():
    assert dash.normalize_window_days(None) == dash.DASHBOARD_DEFAULT_DAYS
    assert dash.normalize_window_days("abc") == dash.DASHBOARD_DEFAULT_DAYS
    assert dash.normalize_window_days(1) == dash.DASHBOARD_MIN_DAYS
    assert dash.normalize_window_days(10_000) == dash.DASHBOARD_MAX_DAYS
    dias = dash.window_dates(SEGUNDA, 7)
    assert len(dias) == 7 and dias[-1] == SEGUNDA and dias[0] == SEGUNDA - timedelta(days=6)


def test_dia_mostra_as_partes_do_grafico_vindas_do_motor():
    dias = (SEGUNDA,)
    linha = dash.day_rows(dias, [_meta()], [_total(kcal=1500.0)])[0]
    energia = linha.macros["energy_kcal"]
    assert energia.within_target == 1500.0 and energia.to_consume == 500.0 and energia.excess == 0.0
    assert energia.remaining == 500.0
    assert set(linha.macros) == set(dash.MACRO_FIELDS)
    assert linha.logged and not linha.intake_incomplete and linha.has_target


def test_passou_da_meta_vira_excesso_e_nao_saldo_negativo_no_grafico():
    energia = dash.day_rows((SEGUNDA,), [_meta()], [_total(kcal=2300.0)])[0].macros["energy_kcal"]
    assert energia.within_target == 2000.0 and energia.to_consume == 0.0 and energia.excess == 300.0
    assert energia.remaining == -300.0


def test_sem_meta_ou_sem_refeicao_nao_inventa_partes():
    sem_meta = dash.day_rows((SEGUNDA,), [None], [_total()])[0]
    assert not sem_meta.has_target and sem_meta.macros["energy_kcal"].to_consume is None
    sem_refeicao = dash.day_rows((SEGUNDA,), [_meta()], [None])[0]
    assert not sem_refeicao.logged and sem_refeicao.macros["energy_kcal"].within_target is None


def test_dia_incompleto_fica_fora_da_aderencia_e_na_lista():
    dias = (SEGUNDA, SEGUNDA + timedelta(days=1))
    linhas = dash.day_rows(dias, [_meta(), _meta()], [_total(incompletos=("energy_kcal",)), _total()])
    resumo = dash.window_adherence(linhas)
    assert resumo.incomplete_days == (SEGUNDA,)
    assert resumo.logged_days == 1 and resumo.kcal_within_tolerance_days == 1 and resumo.protein_ok_days == 1
    assert linhas[0].kcal_within_tolerance is None
    assert resumo.to_dict()["incomplete_days"] == [SEGUNDA.isoformat()]


def test_aderencia_do_dia_usa_a_tolerancia_do_motor():
    dentro = dash.day_rows((SEGUNDA,), [_meta()], [_total(kcal=2000 * (1 + wb.ADHERENCE_KCAL_TOLERANCE_SHARE))])[0]
    fora = dash.day_rows((SEGUNDA,), [_meta()], [_total(kcal=2300.0, proteina=100.0)])[0]
    assert dentro.kcal_within_tolerance is True
    assert fora.kcal_within_tolerance is False and fora.protein_ok is False


def test_dias_sem_meta_sao_contados():
    dias = dash.window_dates(SEGUNDA, 7)
    resumo = dash.window_adherence(dash.day_rows(dias, [_meta()] + [None] * 6, [_total()] + [None] * 6))
    assert resumo.days_with_target == 1 and resumo.days_without_target == 6 and resumo.window_days == 7


def test_semanas_seguem_segunda_a_domingo_e_marcam_ponta_parcial():
    dias = dash.window_dates(SEGUNDA + timedelta(days=9), 14)  # 29/09... termina na quarta 21/10
    blocos = dash.week_blocks(dias, [_meta()] * 14, [_total()] * 14)
    assert [b["start"] for b in blocos] == sorted(b["start"] for b in blocos)
    assert all(date.fromisoformat(b["start"]).weekday() == wb.WEEK_STARTS_ON_WEEKDAY for b in blocos)
    assert blocos[0]["partial"] is True and blocos[-1]["partial"] is True
    meio = [b for b in blocos if not b["partial"]]
    assert meio and meio[0]["report"]["adherence"]["valid_week"] is True


def test_media_movel_so_olha_a_janela_corrida():
    dias = dash.window_dates(SEGUNDA, 10)
    valores = [80.0, None, 82.0, None, None, None, None, None, None, 90.0]
    medias = dash.trailing_mean(dias, valores, window=7)
    assert medias[0] == 80.0 and medias[1] is None and medias[2] == pytest.approx(81.0)
    assert medias[9] == 90.0  # as pesagens anteriores saíram da janela de 7 dias


def test_pontos_do_corpo_ignoram_dias_sem_medida_e_levam_a_media():
    dias = dash.window_dates(SEGUNDA, 3)
    pontos = dash.body_points(dias, [80.0, None, None], [None, None, 18.0], [None, None, 36.0])
    assert [p["local_date"] for p in pontos] == [dias[0].isoformat(), dias[2].isoformat()]
    assert pontos[0]["weight_trend_kg"] == 80.0 and pontos[1]["body_fat_percent"] == 18.0


def test_ritmo_do_peso_vem_da_inclinacao_do_dono():
    pesos = [80.0 - 0.1 * i for i in range(14)]  # -0,1 kg/dia
    tendencia = dash.weight_trend_public(pesos)
    assert tendencia["weekly_change_kg"] == pytest.approx(-0.7)
    poucas = dash.weight_trend_public([80.0] + [None] * 13)
    assert poucas["weekly_change_kg"] is None and poucas["limitations"]


def test_tdee_adaptativo_so_sugere_e_nunca_aplica():
    pesos = [80.0 - 0.05 * i for i in range(28)]
    ingestao = [2200.0] * 28
    bloco = dash.adaptive_block(ingestao, pesos, 16.0, 2500.0)
    assert bloco["applied"] is False
    assert bloco["observed"]["kcal"] is not None and bloco["suggestion"]["formula_kcal"] == 2500.0
    assert bloco["suggestion"]["suggested_kcal"] is not None


def test_tdee_adaptativo_com_janela_curta_nao_sugere():
    bloco = dash.adaptive_block([2000.0] * 7, [80.0] * 7, 16.0, 2500.0)
    assert bloco["applied"] is False and bloco["observed"]["kcal"] is None
    assert bloco["suggestion"]["should_suggest"] is False and bloco["observed"]["limitations"]


def test_ingestao_do_tdee_ignora_dia_incompleto():
    serie = dash.complete_intake_kcal([_total(kcal=1800.0), _total(incompletos=("energy_kcal",)), None])
    assert serie == [1800.0, None, None]


def test_estados_vazios_dizem_o_que_falta():
    vazio = dash.empty_states(has_plan=False, has_goal=False, weight_readings=0, bioimpedance_readings=0,
                              logged_days=0, days_without_target=7)
    assert vazio == (dash.EMPTY_NO_PLAN, dash.EMPTY_NO_WEIGHT, dash.EMPTY_NO_BIOIMPEDANCE, dash.EMPTY_FEW_LOGGED_DAYS)
    sem_alvo = dash.empty_states(has_plan=True, has_goal=False, weight_readings=5, bioimpedance_readings=1,
                                 logged_days=10, days_without_target=2)
    assert sem_alvo == (dash.EMPTY_NO_GOAL, dash.EMPTY_DAYS_WITHOUT_TARGET)
    completo = dash.empty_states(has_plan=True, has_goal=True, weight_readings=5, bioimpedance_readings=1,
                                 logged_days=10, days_without_target=0)
    assert completo == ()


def test_todo_codigo_de_estado_vazio_tem_rotulo():
    from app.domain.nutrition import labels

    for codigo in (dash.EMPTY_NO_PLAN, dash.EMPTY_NO_GOAL, dash.EMPTY_NO_WEIGHT, dash.EMPTY_NO_BIOIMPEDANCE,
                   dash.EMPTY_FEW_LOGGED_DAYS, dash.EMPTY_DAYS_WITHOUT_TARGET):
        assert labels.limitation_label(codigo) != codigo
