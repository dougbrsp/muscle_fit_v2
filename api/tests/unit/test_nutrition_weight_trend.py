"""Peso de tendência (EMA, outliers, aquecimento, suficiência). Spec §8 e §21."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.domain.nutrition import weight_trend as wt

D0 = date(2026, 1, 1)


def _dia(i: int) -> date:
    return D0 + timedelta(days=i)


def _rampa(n: int, kg_por_dia: float, inicio: float = 90.0):
    return [(_dia(i), inicio + kg_por_dia * i) for i in range(n)]


def test_alpha_derivado_da_meia_vida():
    assert wt.WEIGHT_ALPHA == pytest.approx(0.0943, abs=1e-4)
    # Depois de uma meia-vida, metade da distância até o novo patamar foi percorrida.
    estado = wt.TrendState()
    estado, _ = wt.trend_step(estado, _dia(0), [80.0])
    for i in range(1, 1 + int(wt.WEIGHT_HALF_LIFE_DAYS)):
        estado, _ = wt.trend_step(estado, _dia(i), [82.0])
    assert estado.trend_kg == pytest.approx(81.0, abs=1e-6)


def test_primeira_pesagem_semeia_a_tendencia():
    estado, ponto = wt.trend_step(wt.TrendState(), _dia(0), [80.0])
    assert estado.trend_kg == 80.0 and estado.first_day == _dia(0)
    assert ponto.measured_kg == 80.0 and ponto.used


def test_peso_ausente_carrega_tendencia_sem_pesagem_ficticia():
    estado, _ = wt.trend_step(wt.TrendState(), _dia(0), [80.0])
    estado, ponto = wt.trend_step(estado, _dia(1), [])
    assert ponto.measured_kg is None and ponto.trend_kg == 80.0 and not ponto.used
    assert estado.trend_kg == 80.0


def test_sem_nenhuma_pesagem_nao_ha_tendencia():
    serie = wt.weight_trend_series([], _dia(30))
    assert all(p.trend_kg is None for p in serie.points)
    assert serie.window.slope_kg_per_day is None
    assert not serie.window.sufficient
    assert wt.FLAG_WEIGHT_MISSING in serie.window.flags
    assert serie.window.history_days == 0


def test_varias_pesagens_no_dia_valem_a_media():
    _, ponto = wt.trend_step(wt.TrendState(), _dia(0), [80.0, 82.0])
    assert ponto.measured_kg == 81.0


def test_valores_impossiveis_sao_ignorados_e_contados():
    estado, ponto = wt.trend_step(wt.TrendState(), _dia(0), [0, -5, "x", None, 10.0, 500.0, True, 80.0])
    assert ponto.measured_kg == 80.0
    assert ponto.invalid_readings == 7
    assert estado.trend_kg == 80.0
    estado, ponto = wt.trend_step(wt.TrendState(), _dia(0), [0.0])
    assert ponto.measured_kg is None and estado.trend_kg is None


def test_outlier_e_sinalizado_e_nao_move_a_tendencia():
    estado, _ = wt.trend_step(wt.TrendState(), _dia(0), [80.0])
    estado, ponto = wt.trend_step(estado, _dia(1), [80.0 + wt.OUTLIER_DEVIATION_KG + 1.0])
    assert ponto.outlier and not ponto.used and not ponto.level_shift
    assert estado.trend_kg == 80.0 and estado.outlier_streak == 1


def test_outlier_persistente_vira_mudanca_de_patamar():
    estado, _ = wt.trend_step(wt.TrendState(), _dia(0), [80.0])
    novo = 90.0
    pontos = []
    for i in range(1, wt.OUTLIER_ACCEPT_STREAK + 1):
        estado, ponto = wt.trend_step(estado, _dia(i), [novo])
        pontos.append(ponto)
    assert [p.outlier for p in pontos] == [True] * (wt.OUTLIER_ACCEPT_STREAK - 1) + [False]
    assert pontos[-1].level_shift and estado.trend_kg == novo and estado.outlier_streak == 0


def test_pesagem_normal_zera_a_sequencia_de_outliers():
    estado, _ = wt.trend_step(wt.TrendState(), _dia(0), [80.0])
    estado, _ = wt.trend_step(estado, _dia(1), [90.0])
    assert estado.outlier_streak == 1
    estado, _ = wt.trend_step(estado, _dia(2), [80.5])
    assert estado.outlier_streak == 0


@pytest.mark.parametrize("kg_por_dia", [-0.06, 0.04])
def test_inclinacao_de_perda_e_ganho_tem_o_sinal_certo(kg_por_dia):
    serie = wt.weight_trend_series(_rampa(80, kg_por_dia), _dia(79))
    inclinacao = serie.window.slope_kg_per_day
    assert inclinacao is not None
    assert inclinacao == pytest.approx(kg_por_dia, rel=0.02)


def test_aquecimento_com_historico_anterior_a_janela_melhora_a_inclinacao():
    leituras = _rampa(60, -0.06)
    fim = _dia(59)
    com_historico = wt.weight_trend_series(leituras, fim).window.slope_kg_per_day
    so_janela = wt.weight_trend_series(leituras[-wt.TREND_WINDOW_DAYS :], fim).window.slope_kg_per_day
    assert com_historico is not None and so_janela is not None
    assert abs(com_historico - (-0.06)) < abs(so_janela - (-0.06))
    # Sem aquecimento a EMA subestima a inclinação (em módulo) de forma material.
    assert abs(so_janela) < abs(com_historico) * 0.9


def test_serie_devolve_exatamente_a_janela_e_ignora_futuro():
    leituras = _rampa(40, 0.0, 80.0) + [(_dia(50), 99.0)]
    serie = wt.weight_trend_series(leituras, _dia(39))
    assert len(serie.points) == wt.TREND_WINDOW_DAYS
    assert serie.points[-1].day == _dia(39) and serie.points[0].day == _dia(39 - 20)
    assert serie.state.trend_kg == 80.0


def test_janela_comeca_antes_da_primeira_pesagem_sem_tendencia():
    serie = wt.weight_trend_series(_rampa(5, 0.0, 80.0), _dia(10))
    assert serie.points[0].trend_kg is None
    assert serie.points[-1].trend_kg == 80.0
    assert serie.window.history_days == 11


def test_suficiencia_historico_e_pesagens_por_semana():
    curto = wt.weight_trend_series(_rampa(10, -0.05), _dia(9)).window
    assert not curto.sufficient and wt.FLAG_WEIGHT_HISTORY_SHORT in curto.flags

    diario = wt.weight_trend_series(_rampa(40, -0.05), _dia(39)).window
    assert diario.sufficient and diario.history_days == 40
    assert diario.weighins_per_week == pytest.approx(wt.DAYS_PER_WEEK)
    assert wt.FLAG_WEIGHINS_BELOW_RECOMMENDED not in diario.flags

    ideal_apenas = wt.weight_trend_series(_rampa(14, -0.05), _dia(13)).window
    assert wt.FLAG_WEIGHT_HISTORY_BELOW_IDEAL in ideal_apenas.flags

    # 3 pesagens por semana: acima do mínimo duro, abaixo do recomendado.
    tres = [(d, kg) for d, kg in _rampa(42, -0.05) if (d - D0).days % 7 in (0, 2, 4)]
    janela = wt.weight_trend_series(tres, _dia(41)).window
    assert janela.sufficient and wt.FLAG_WEIGHINS_BELOW_RECOMMENDED in janela.flags

    # 1 pesagem por semana: abaixo do mínimo duro.
    uma = [(d, kg) for d, kg in _rampa(42, -0.05) if (d - D0).days % 7 == 0]
    janela = wt.weight_trend_series(uma, _dia(41)).window
    assert not janela.sufficient and wt.FLAG_WEIGHINS_BELOW_MIN in janela.flags


def test_outlier_aparece_nas_flags_da_janela():
    leituras = _rampa(30, 0.0, 80.0) + [(_dia(30), 95.0)]
    janela = wt.weight_trend_series(leituras, _dia(30)).window
    assert wt.FLAG_WEIGHT_OUTLIER in janela.flags


def test_to_dict_serializavel():
    import json

    serie = wt.weight_trend_series(_rampa(30, -0.05), _dia(29))
    json.dumps(serie.to_dict())
    assert set(serie.window.to_dict()) == {"slope", "weighin_days", "weighins_per_week", "history_days", "flags", "sufficient"}


def test_mudanca_de_patamar_nao_vira_inclinacao():
    leituras = _rampa(40, 0.0, 80.0) + [(_dia(i), 90.0) for i in range(40, 52)]
    janela = wt.weight_trend_series(leituras, _dia(51)).window
    assert wt.FLAG_WEIGHT_LEVEL_SHIFT in janela.flags
    assert janela.slope_kg_per_day == pytest.approx(0.0, abs=1e-9)


def test_pouco_tempo_depois_da_mudanca_de_patamar_a_inclinacao_fica_indisponivel():
    leituras = _rampa(40, 0.0, 80.0) + [(_dia(i), 90.0) for i in range(40, 44)]
    janela = wt.weight_trend_series(leituras, _dia(43)).window
    assert janela.slope_kg_per_day is None and not janela.sufficient
