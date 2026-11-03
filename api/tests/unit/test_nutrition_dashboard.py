"""Agregação do dashboard de nutrição (`domain/nutrition/dashboard`, N5, mapa §82):
janela, estado do dia (antes do plano, hoje aberto, sem registro, incompleto, registrado),
meta × ingestão por dia (partes do gráfico), aderência por dia e por semana, sequência de
versões do plano, gasto médio da fórmula, TDEE adaptativo que só sugere e estados vazios.
Python puro, sem banco."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.domain.nutrition import adaptive_tdee as atdee
from app.domain.nutrition import dashboard as dash
from app.domain.nutrition import meal_math as mm
from app.domain.nutrition import weekly_budget as wb

SEGUNDA = date(2026, 10, 12)
HOJE = date(2026, 10, 17)
INICIO_PLANO = date(2026, 10, 1)


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


def _linhas(dias, metas, totais, hoje=HOJE, plano=INICIO_PLANO):
    estados = dash.day_states(dias, hoje, plano, totais)
    return dash.day_rows(dias, metas, totais, estados), estados


def test_janela_e_normalizada_e_termina_no_dia():
    assert dash.normalize_window_days(None) == dash.DASHBOARD_DEFAULT_DAYS
    assert dash.normalize_window_days("abc") == dash.DASHBOARD_DEFAULT_DAYS
    assert dash.normalize_window_days(1) == dash.DASHBOARD_MIN_DAYS
    assert dash.normalize_window_days(10_000) == dash.DASHBOARD_MAX_DAYS
    assert all(dash.DASHBOARD_MIN_DAYS <= n <= dash.DASHBOARD_MAX_DAYS for n in dash.WINDOW_CHOICES_DAYS)
    dias = dash.window_dates(SEGUNDA, 7)
    assert len(dias) == 7 and dias[-1] == SEGUNDA and dias[0] == SEGUNDA - timedelta(days=6)


def test_leitura_cobre_a_janela_do_tdee_observado_mesmo_com_periodo_curto():
    assert dash.fetch_days(7) == dash.ADAPTIVE_WINDOW_DAYS
    assert dash.fetch_days(90) == 90


def test_alinhamento_por_dia():
    dias = dash.window_dates(SEGUNDA, 3)
    grupos = dash.group_by_day(dias, [(dias[0], "a"), (dias[0], "b"), (dias[2], "c"), (date(2020, 1, 1), "fora")])
    assert grupos == [["a", "b"], [], ["c"]]
    assert dash.last_by_day(dias, [(dias[0], 1), (dias[0], 2), (dias[1], None)]) == [2, None, None]
    assert dash.daily_values([(dias[1], 5), (dias[0], 4), (dias[1], 6), (None, 9), (dias[2], None)]) == [(dias[0], 4), (dias[1], 6)]
    assert dash.count_present([1, None, 0]) == 2


def test_estado_do_dia():
    dias = (date(2026, 9, 30), date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16), HOJE)
    totais = [_total(), None, _total(incompletos=("energy_kcal",)), _total(), _total()]
    estados = dash.day_states(dias, HOJE, INICIO_PLANO, totais)
    assert estados == [dash.DayState.OUT_OF_PLAN, dash.DayState.NO_RECORD, dash.DayState.INCOMPLETE,
                       dash.DayState.LOGGED, dash.DayState.OPEN_TODAY]
    assert set(dash.day_states(dias, HOJE, None, totais)) == {dash.DayState.OUT_OF_PLAN}


def test_dia_mostra_as_partes_do_grafico_vindas_do_motor():
    linha = _linhas((date(2026, 10, 14),), [_meta()], [_total(kcal=1500.0)])[0][0]
    energia = linha.macros["energy_kcal"]
    assert energia.within_target == 1500.0 and energia.to_consume == 500.0 and energia.excess == 0.0
    assert energia.remaining == 500.0 and set(linha.macros) == set(dash.MACRO_FIELDS)
    assert linha.logged and not linha.intake_incomplete and linha.has_target


def test_passou_da_meta_vira_excesso_e_nao_saldo_negativo_no_grafico():
    energia = _linhas((date(2026, 10, 14),), [_meta()], [_total(kcal=2300.0)])[0][0].macros["energy_kcal"]
    assert energia.within_target == 2000.0 and energia.to_consume == 0.0 and energia.excess == 300.0
    assert energia.remaining == -300.0


def test_com_meta_e_sem_refeicao_a_meta_toda_falta_consumir():
    energia = _linhas((date(2026, 10, 14),), [_meta()], [None])[0][0].macros["energy_kcal"]
    assert (energia.within_target, energia.to_consume, energia.excess) == (0.0, 2000.0, 0.0)


def test_ingestao_incompleta_continua_sem_partes_e_sem_meta_nao_inventa():
    incompleto = _linhas((date(2026, 10, 14),), [_meta()], [_total(incompletos=("energy_kcal",))])[0][0]
    energia = incompleto.macros["energy_kcal"]
    assert energia.within_target is None and energia.to_consume is None and incompleto.intake_incomplete
    sem_meta = _linhas((date(2026, 10, 14),), [None], [_total()])[0][0]
    assert not sem_meta.has_target and sem_meta.macros["energy_kcal"].to_consume is None


def test_hoje_e_antes_do_plano_ficam_fora_do_balanco_e_da_aderencia():
    dias = (date(2026, 9, 30), HOJE)
    linhas, _ = _linhas(dias, [_meta(), _meta()], [_total(), _total()])
    for linha in linhas:
        assert linha.macros["energy_kcal"].within_target is None and linha.kcal_within_tolerance is None
        assert not linha.has_target
    resumo = dash.window_adherence(linhas)
    assert resumo.counted_days == 0 and resumo.open_today is True and resumo.logged_days == 0


def test_sem_registro_e_contado_a_parte_e_nao_como_descumprido():
    dias = dash.window_dates(date(2026, 10, 16), 3)
    linhas, _ = _linhas(dias, [_meta()] * 3, [_total(), None, None])
    resumo = dash.window_adherence(linhas)
    assert resumo.counted_days == 3 and resumo.logged_days == 1 and resumo.no_record_days == 2
    assert resumo.kcal_within_tolerance_days == 1 and resumo.meal_days_without_target == 0


def test_dia_incompleto_fica_fora_da_aderencia_e_na_lista():
    dias = (date(2026, 10, 14), date(2026, 10, 15))
    linhas, _ = _linhas(dias, [_meta(), _meta()], [_total(incompletos=("energy_kcal",)), _total()])
    resumo = dash.window_adherence(linhas)
    assert resumo.incomplete_days == (date(2026, 10, 14),)
    assert resumo.logged_days == 1 and resumo.kcal_within_tolerance_days == 1 and resumo.protein_ok_days == 1
    assert resumo.to_dict()["incomplete_days"] == ["2026-10-14"]


def test_dia_com_refeicao_e_sem_meta_e_avisado_mas_sem_refeicao_nao():
    dias = dash.window_dates(date(2026, 10, 15), 3)
    linhas, _ = _linhas(dias, [None, None, _meta()], [_total(), None, _total()])
    resumo = dash.window_adherence(linhas)
    assert resumo.meal_days_without_target == 1 and resumo.no_record_days == 1


def test_aderencia_do_dia_usa_a_tolerancia_do_motor():
    dentro = _linhas((date(2026, 10, 14),), [_meta()], [_total(kcal=2000 * (1 + wb.ADHERENCE_KCAL_TOLERANCE_SHARE))])[0][0]
    fora = _linhas((date(2026, 10, 14),), [_meta()], [_total(kcal=2300.0, proteina=100.0)])[0][0]
    assert dentro.kcal_within_tolerance is True
    assert fora.kcal_within_tolerance is False and fora.protein_ok is False


def test_semanas_seguem_segunda_a_domingo_e_marcam_ponta_parcial():
    dias = dash.window_dates(date(2026, 10, 21), 14)
    metas, totais = [_meta()] * 14, [_total()] * 14
    estados = dash.day_states(dias, date(2026, 10, 22), date(2026, 9, 1), totais)
    blocos = dash.week_blocks(dias, metas, totais, estados)
    assert [b["start"] for b in blocos] == sorted(b["start"] for b in blocos)
    assert all(date.fromisoformat(b["start"]).weekday() == wb.WEEK_STARTS_ON_WEEKDAY for b in blocos)
    assert blocos[0]["partial"] is True and blocos[-1]["partial"] is True
    meio = [b for b in blocos if not b["partial"]]
    assert meio and meio[0]["report"]["adherence"]["valid_week"] is True


def test_semana_com_dia_incompleto_nao_o_conta_como_registrado():
    dias = dash.window_dates(date(2026, 10, 18), 7)  # 12/10 a 18/10
    totais = [_total(incompletos=("energy_kcal",))] + [_total()] * 6
    estados = dash.day_states(dias, date(2026, 10, 19), INICIO_PLANO, totais)
    bloco = dash.week_blocks(dias, [_meta()] * 7, totais, estados)[0]
    assert bloco["report"]["incomplete_days"] == ["2026-10-12"] and bloco["report"]["adherence"]["logged_days"] == 6


def test_semana_antes_do_plano_sai_e_hoje_entra_sem_ingestao():
    dias = dash.window_dates(HOJE, 14)
    totais = [_total()] * 14
    estados = dash.day_states(dias, HOJE, date(2026, 10, 12), totais)
    blocos = dash.week_blocks(dias, [_meta()] * 14, totais, estados)
    assert blocos[0]["start"] == "2026-10-12"  # a semana de 05/10 está antes do plano
    assert HOJE.isoformat() not in blocos[-1]["report"]["logged_days"]


def test_pontos_do_corpo_ignoram_dias_sem_medida_e_levam_a_media_do_dono():
    dias = dash.window_dates(SEGUNDA, 3)
    pontos = dash.body_points(dias, [80.0, None, None], {dias[0]: 79.8}, [None, None, 18.0], [None, None, 36.0])
    assert [p["local_date"] for p in pontos] == [dias[0].isoformat(), dias[2].isoformat()]
    assert pontos[0]["weight_trend_kg"] == 79.8 and pontos[1]["weight_trend_kg"] is None
    assert pontos[1]["body_fat_percent"] == 18.0


def test_ritmo_do_peso_vem_da_inclinacao_do_dono():
    pesos = [80.0 - 0.1 * i for i in range(14)]  # -0,1 kg/dia
    assert dash.weight_trend_public(pesos)["weekly_change_kg"] == pytest.approx(-0.7)
    poucas = dash.weight_trend_public([80.0] + [None] * 13)
    assert poucas["weekly_change_kg"] is None and poucas["limitations"]


def test_gasto_medio_da_formula_so_dos_dias_fechados_com_meta():
    dias = (date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16), HOJE)
    assert dash.window_tdee_kcal(dias, HOJE, [2500.0, None, 2700.0, 9000.0]) == 2600.0  # hoje (aberto) não entra
    assert dash.window_tdee_kcal(dias, HOJE, [None, None, None, 9000.0]) is None


def test_projecao_usa_a_meta_de_hoje_so_quando_nenhum_dia_fechado_tem_meta():
    dias = (date(2026, 10, 15), date(2026, 10, 16), HOJE)
    assert dash.projection_tdee_kcal(dias, HOJE, [None, None, 2800.0]) == 2800.0  # substituto, só para a projeção
    assert dash.window_tdee_kcal(dias, HOJE, [None, None, 2800.0]) is None  # o "pela fórmula" segue sem hoje
    assert dash.projection_tdee_kcal(dias, HOJE, [2500.0, None, 2800.0]) == 2500.0  # com dia fechado, hoje não entra
    assert dash.projection_tdee_kcal(dias, HOJE, [None, None, None]) is None


def test_gasto_medio_da_formula_olha_so_a_janela_do_tdee_observado():
    dias = dash.window_dates(HOJE, dash.ADAPTIVE_WINDOW_DAYS + 5)
    valores = [4000.0] * 5 + [2000.0] * (dash.ADAPTIVE_WINDOW_DAYS - 1) + [9000.0]
    assert dash.window_tdee_kcal(dias, HOJE, valores) == 2000.0


def test_tdee_adaptativo_so_sugere_e_olha_a_janela_preferida():
    pesos = [90.0] * 20 + [80.0 - 0.05 * i for i in range(atdee.PREFERRED_WINDOW_DAYS)]  # só os últimos contam
    bloco = dash.adaptive_block([2200.0] * len(pesos), pesos, 16.0, 2500.0)
    assert bloco["applied"] is False and bloco["observed"]["window_days"] == atdee.PREFERRED_WINDOW_DAYS
    assert bloco["observed"]["kcal"] is not None and bloco["suggestion"]["suggested_kcal"] is not None


def test_tdee_adaptativo_com_janela_curta_nao_sugere():
    bloco = dash.adaptive_block([2000.0] * 7, [80.0] * 7, 16.0, 2500.0)
    assert bloco["applied"] is False and bloco["observed"]["kcal"] is None
    assert bloco["suggestion"]["should_suggest"] is False and bloco["observed"]["limitations"]


def test_ingestao_do_tdee_ignora_hoje_dia_incompleto_e_sem_refeicao_mantendo_o_tamanho():
    dias = (date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16), HOJE)
    serie = dash.adaptive_intake_kcal(dias, HOJE, [_total(kcal=1800.0), _total(incompletos=("energy_kcal",)), None, _total()])
    assert serie == [1800.0, None, None, None]


def _versao(id_, inicio, estrategia="cutting", peso=75.0, gordura=None, publicado=None):
    return dash.PlanVersion(id_, inicio, publicado, estrategia, peso, gordura)


def test_sequencia_de_versoes_com_mesma_estrategia_e_alvo_mantem_o_inicio_mais_antigo():
    versoes = [_versao(1, date(2026, 8, 1)), _versao(2, date(2026, 9, 1)), _versao(3, date(2026, 10, 1))]
    assert dash.goal_sequence_start(versoes, HOJE) == date(2026, 8, 1)
    assert dash.first_plan_start(versoes) == date(2026, 8, 1)


def test_mudar_de_alvo_ou_estrategia_recomeca_o_caminho():
    mudou_alvo = [_versao(1, date(2026, 8, 1), peso=78.0), _versao(2, date(2026, 9, 1)), _versao(3, date(2026, 10, 1))]
    assert dash.goal_sequence_start(mudou_alvo, HOJE) == date(2026, 9, 1)
    mudou_estrategia = [_versao(1, date(2026, 8, 1), "bulking"), _versao(2, date(2026, 10, 1))]
    assert dash.goal_sequence_start(mudou_estrategia, HOJE) == date(2026, 10, 1)


def test_sequencia_ignora_versao_futura_e_desempata_pela_publicacao():
    versoes = [_versao(1, date(2026, 8, 1)), _versao(2, date(2026, 10, 1), publicado=datetime(2026, 10, 1, 9)),
               _versao(3, date(2026, 10, 1), estrategia="maintenance", peso=None, publicado=datetime(2026, 10, 1, 10)),
               _versao(4, date(2026, 11, 1))]
    assert dash.goal_sequence_start(versoes, HOJE) == date(2026, 10, 1)  # a vigente (3) difere das anteriores
    assert dash.goal_sequence_start([_versao(1, date(2026, 11, 1))], HOJE) is None


def test_estados_vazios_dizem_o_que_falta():
    vazio = dash.empty_states(has_plan=False, has_goal=False, weight_readings=0, bioimpedance_readings=0,
                              logged_days=0, meal_days_without_target=7)
    assert vazio == (dash.EMPTY_NO_PLAN, dash.EMPTY_NO_WEIGHT, dash.EMPTY_NO_BIOIMPEDANCE)
    poucos = dash.empty_states(has_plan=True, has_goal=True, weight_readings=5, bioimpedance_readings=1,
                               logged_days=dash.FEW_LOGGED_DAYS - 1, meal_days_without_target=0)
    assert poucos == (dash.EMPTY_FEW_LOGGED_DAYS,)
    sem_plano_com_poucos_dias = dash.empty_states(has_plan=False, has_goal=False, weight_readings=5,
                                                  bioimpedance_readings=1, logged_days=0, meal_days_without_target=0)
    assert sem_plano_com_poucos_dias == (dash.EMPTY_NO_PLAN,)  # sem plano nenhum dia conta
    sem_alvo = dash.empty_states(has_plan=True, has_goal=False, weight_readings=5, bioimpedance_readings=1,
                                 logged_days=10, meal_days_without_target=2)
    assert sem_alvo == (dash.EMPTY_NO_GOAL, dash.EMPTY_DAYS_WITHOUT_TARGET)
    completo = dash.empty_states(has_plan=True, has_goal=True, weight_readings=5, bioimpedance_readings=1,
                                 logged_days=10, meal_days_without_target=0)
    assert completo == ()


def test_todo_codigo_de_estado_vazio_tem_rotulo():
    from app.domain.nutrition import labels

    for codigo in (dash.EMPTY_NO_PLAN, dash.EMPTY_NO_GOAL, dash.EMPTY_NO_WEIGHT, dash.EMPTY_NO_BIOIMPEDANCE,
                   dash.EMPTY_FEW_LOGGED_DAYS, dash.EMPTY_DAYS_WITHOUT_TARGET):
        assert labels.limitation_label(codigo) != codigo
