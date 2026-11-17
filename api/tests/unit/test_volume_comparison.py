"""Dono da variacao/direcao entre periodos (#188 etapa 3).

Rodar: cd api && python -m pytest tests/unit/test_volume_comparison.py -q
"""

from __future__ import annotations

import pytest

from app.intelligence.volume_comparison import (
    DELTA_DECIMALS,
    EFFECTIVE_SETS_STABLE_TOLERANCE,
    RPE_STABLE_TOLERANCE,
    VOLUME_STABLE_TOLERANCE,
    period_delta,
    period_direction,
)


def test_constantes_herdadas_do_cliente():
    assert VOLUME_STABLE_TOLERANCE == 0.0001
    assert RPE_STABLE_TOLERANCE == 0.05
    assert EFFECTIVE_SETS_STABLE_TOLERANCE == 0.01
    assert DELTA_DECIMALS == 2


@pytest.mark.parametrize("tolerance", [VOLUME_STABLE_TOLERANCE, RPE_STABLE_TOLERANCE, EFFECTIVE_SETS_STABLE_TOLERANCE])
def test_limite_exato_e_estavel_e_acima_sobe_ou_desce(tolerance):
    # Limite inclusivo (`<=`), como no cliente; base 0 evita erro de float.
    assert period_direction(tolerance, 0.0, tolerance) == "stable"
    assert period_direction(-tolerance, 0.0, tolerance) == "stable"
    assert period_direction(tolerance * 1.5, 0.0, tolerance) == "up"
    assert period_direction(-tolerance * 1.5, 0.0, tolerance) == "down"


def test_rpe_e_series_efetivas_reais():
    assert period_direction(8.04, 8.0, RPE_STABLE_TOLERANCE) == "stable"
    # 8.05 - 8.0 em float e 0.0500000000000007: "up", igual ao cliente.
    assert period_direction(8.05, 8.0, RPE_STABLE_TOLERANCE) == "up"
    assert period_direction(8.1, 8.0, RPE_STABLE_TOLERANCE) == "up"
    assert period_direction(3.0000001, 3.0, EFFECTIVE_SETS_STABLE_TOLERANCE) == "stable"
    assert period_direction(2.5, 3.0, EFFECTIVE_SETS_STABLE_TOLERANCE) == "down"


@pytest.mark.parametrize("current,previous", [(None, 1.0), (1.0, None), (None, None)])
def test_falta_um_lado_e_none(current, previous):
    assert period_direction(current, previous, VOLUME_STABLE_TOLERANCE) is None
    assert period_delta(current, previous) is None


def test_delta():
    assert period_delta(120.5, 90.0) == 30.5
    assert period_delta(0.3, 0.1) == 0.2
    assert period_delta(0, 0) == 0.0


# ---------------------------------------------------------------------------
# #188 etapa 3, passada B1: janelas da aba de tendencia, tendencia atual x
# anterior e comparativo semanal (antes somados em VolumeGrupamentosClient.tsx).
# Valores esperados calculados a mao com a conta do cliente
# (`buildWindow`, `repsComparison`, `getTrendBadge`, `comparativeWeekRows`).
# ---------------------------------------------------------------------------

import re  # noqa: E402
from datetime import date as _date  # noqa: E402

import pytest as _pytest  # noqa: E402

from app.intelligence import slot_session_comparison as _ssc  # noqa: E402
from app.intelligence import volume_comparison as _vc  # noqa: E402


def _janelas(hoje, granularidade):
    w = _vc.granularity_windows(_date.fromisoformat(hoje), granularidade)
    return (
        w["current"]["from"].isoformat(), w["current"]["to"].isoformat(),
        w["previous"]["from"].isoformat(), w["previous"]["to"].isoformat(),
    )


@_pytest.mark.parametrize(
    "hoje,granularidade,esperado",
    [
        ("2026-11-16", "d7", ("2026-11-10", "2026-11-16", "2026-11-03", "2026-11-09")),
        ("2026-03-03", "d7", ("2026-02-25", "2026-03-03", "2026-02-18", "2026-02-24")),
        # Segunda-feira: a semana atual tem 1 dia; a anterior, so o domingo.
        ("2026-11-16", "week", ("2026-11-16", "2026-11-16", "2026-11-15", "2026-11-15")),
        # Domingo: a semana inteira (segunda a domingo) x a semana anterior inteira.
        ("2026-11-22", "week", ("2026-11-16", "2026-11-22", "2026-11-09", "2026-11-15")),
        # Quinta: segunda..quinta x os 4 ULTIMOS dias da semana anterior (quinta..domingo), como no cliente.
        ("2026-11-19", "week", ("2026-11-16", "2026-11-19", "2026-11-12", "2026-11-15")),
        # Mes de 31 dias contra fevereiro (28): a anterior e truncada ao fim do mes.
        ("2026-03-31", "month", ("2026-03-01", "2026-03-31", "2026-02-01", "2026-02-28")),
        # 31 x 30 dias e 30 x 31 dias.
        ("2026-12-31", "month", ("2026-12-01", "2026-12-31", "2026-11-01", "2026-11-30")),
        ("2026-11-30", "month", ("2026-11-01", "2026-11-30", "2026-10-01", "2026-10-30")),
        # Virada de mes e de ano.
        ("2026-12-01", "month", ("2026-12-01", "2026-12-01", "2026-11-01", "2026-11-01")),
        ("2027-01-05", "month", ("2027-01-01", "2027-01-05", "2026-12-01", "2026-12-05")),
    ],
)
def test_janelas_iguais_ao_build_window_do_cliente(hoje, granularidade, esperado):
    assert _janelas(hoje, granularidade) == esperado


def test_janela_granularidade_invalida():
    with _pytest.raises(ValueError):
        _vc.granularity_windows(_date(2026, 11, 16), "year")


def test_tendencia_sem_tolerancia_qualquer_diferenca_conta():
    assert _vc.TREND_NO_TOLERANCE == 0
    assert _vc.period_direction(100.001, 100.0, _vc.TREND_NO_TOLERANCE) == "up"
    assert _vc.period_direction(99.999, 100.0, _vc.TREND_NO_TOLERANCE) == "down"
    assert _vc.period_direction(100.0, 100.0, _vc.TREND_NO_TOLERANCE) == "stable"


def test_semana_comeca_na_segunda():
    assert _vc.week_start_of(_date(2026, 11, 22)) == _date(2026, 11, 16)  # domingo
    assert _vc.week_start_of(_date(2026, 11, 16)) == _date(2026, 11, 16)  # segunda
    assert _vc.week_start_of(_date(2026, 3, 1)) == _date(2026, 2, 23)  # vira o mes


_GRANDES = {"volume_class_id": 1, "volume_class_name": "Grandes", "volume_class_order": 1}
_PEQUENOS = {"volume_class_id": 2, "volume_class_name": "Pequenos", "volume_class_order": 2}

_SERIE = [
    {"period": "2026-11-02", "muscle_group": "Costas", "reps_total": 10, "volume_kg": 100.0},  # fora das janelas
    {"period": "2026-11-05", "muscle_group": "Peito", "reps_total": 20, "volume_kg": 500.0, **_GRANDES},
    {"period": "2026-11-08", "muscle_group": "Bíceps", "reps_total": 40, "volume_kg": 300.0, **_PEQUENOS},
    {"period": "2026-11-14", "muscle_group": "Peito", "reps_total": 30, "volume_kg": 600.0, **_GRANDES},
    {"period": "2026-11-15", "muscle_group": "Bíceps", "reps_total": 40, "volume_kg": 300.0, **_PEQUENOS},
]


def test_tendencia_atual_x_anterior_paridade_com_o_cliente():
    corpo = _ssc.build_period_comparison(_SERIE, "d7", _date(2026, 11, 16))
    assert corpo["granularity"] == "d7"
    assert corpo["current"] == {"from": "2026-11-10", "to": "2026-11-16"}
    assert corpo["previous"] == {"from": "2026-11-03", "to": "2026-11-09"}
    assert set(corpo["levels"]) == {"volume_class", "parent_group", "muscle_group"}

    grupo = corpo["levels"]["muscle_group"]
    # Ranking por repeticoes atuais; Costas so tem item fora das janelas e entra com zeros.
    assert [r["group"]["label"] for r in grupo] == ["Bíceps", "Peito", "Costas"]
    biceps, peito, costas = grupo
    assert (peito["current_reps"], peito["previous_reps"], peito["reps_delta"]) == (30, 20, 10)
    assert (peito["current_volume_kg"], peito["previous_volume_kg"]) == (600.0, 500.0)
    assert (peito["volume_delta_kg"], peito["volume_direction"]) == (100.0, "up")
    assert (biceps["reps_delta"], biceps["volume_delta_kg"], biceps["volume_direction"]) == (0, 0.0, "stable")
    assert (costas["current_reps"], costas["previous_volume_kg"], costas["volume_direction"]) == (0, 0.0, "stable")
    assert peito["group"] == {"key": "grupo:Peito", "label": "Peito", "order": None}

    # Macro-grupo: ordem do cadastro; sem macro-grupo vai para o fim.
    macro = corpo["levels"]["volume_class"]
    assert [r["group"]["key"] for r in macro] == ["macro:1", "macro:2", "grupo:Costas"]


def test_tendencia_mes_trunca_a_anterior():
    serie = [
        {"period": "2026-02-28", "muscle_group": "Peito", "reps_total": 10, "volume_kg": 100.0},
        {"period": "2026-01-31", "muscle_group": "Peito", "reps_total": 99, "volume_kg": 999.0},
        {"period": "2026-03-31", "muscle_group": "Peito", "reps_total": 5, "volume_kg": 50.0},
    ]
    (peito,) = _ssc.build_period_comparison(serie, "month", _date(2026, 3, 31))["levels"]["muscle_group"]
    assert (peito["current_reps"], peito["previous_reps"], peito["volume_direction"]) == (5, 10, "down")


_RECENTES = [
    {"activity_id": 7, "local_date": "2026-11-18", "muscle_group": "Peito", "volume_kg": 50.0, "sets_count": 1, "avg_rpe": None},
    {"activity_id": 6, "local_date": "2026-11-16", "muscle_group": "Peito", "volume_kg": 100.0, "sets_count": 3, "avg_rpe": 8.0},
    {"activity_id": 6, "local_date": "2026-11-16", "muscle_group": "Bíceps", "volume_kg": 30.0, "sets_count": 2, "avg_rpe": 7.0},
    {"activity_id": 5, "local_date": "2026-11-15", "muscle_group": "Peito", "volume_kg": 200.0, "sets_count": 2, "avg_rpe": 9.0},
    {"activity_id": 4, "local_date": "2026-11-10", "muscle_group": "Peito", "volume_kg": 200.0, "sets_count": 2, "avg_rpe": 9.02},
    {"activity_id": 3, "local_date": "2026-10-20", "muscle_group": "Peito", "volume_kg": 400.0, "sets_count": 4, "avg_rpe": 9.0},
    {"activity_id": 2, "local_date": "2026-10-06", "muscle_group": "Peito", "volume_kg": 10.0, "sets_count": 1, "avg_rpe": 7.0},
    {"activity_id": 1, "local_date": "2026-09-29", "muscle_group": "Peito", "volume_kg": 5.0, "sets_count": 1, "avg_rpe": 6.0},
]


def test_comparativo_semanal_paridade_com_o_cliente():
    corpo = _ssc.build_weekly_comparison(_RECENTES)
    assert corpo["period_keys"] == ["t0", "t1", "t2", "t3"]
    biceps, peito = corpo["levels"]["muscle_group"]  # ordem por rotulo
    assert (biceps["group"]["label"], peito["group"]["label"]) == ("Bíceps", "Peito")

    p = peito["by_period"]
    # Semanas sem treino (10/26, 10/12) sao puladas; a 5a semana (09/28) fica de fora.
    assert [p[k]["week_start"] for k in ("t0", "t1", "t2", "t3")] == ["2026-11-16", "2026-11-09", "2026-10-19", "2026-10-05"]
    assert [p[k]["volume_kg"] for k in ("t0", "t1", "t2", "t3")] == [150.0, 400.0, 400.0, 10.0]
    # RPE ponderado por series; RPE nulo nao entra.
    assert p["t0"]["avg_rpe"] == 8.0
    assert p["t1"]["avg_rpe"] == _pytest.approx(9.01)
    assert (p["t0"]["volume_delta_kg"], p["t0"]["volume_direction"], p["t0"]["rpe_direction"]) == (-250.0, "down", "down")
    # 9.01 x 9.0: dentro da tolerancia de RPE (0.05).
    assert (p["t1"]["volume_direction"], p["t1"]["rpe_direction"]) == ("stable", "stable")
    assert (p["t2"]["volume_delta_kg"], p["t2"]["volume_direction"], p["t2"]["rpe_direction"]) == (390.0, "up", "up")
    assert (p["t3"]["volume_delta_kg"], p["t3"]["volume_direction"], p["t3"]["rpe_direction"]) == (None, None, None)

    b = biceps["by_period"]
    assert (b["t0"]["volume_kg"], b["t0"]["volume_direction"]) == (30.0, None)
    assert (b["t1"], b["t2"], b["t3"]) == (None, None, None)


# --- rotas ------------------------------------------------------------------


@_pytest.fixture
def _sem_hierarquia(monkeypatch):
    from app.intelligence import methodology_volume_service as mvs

    monkeypatch.setattr(mvs, "load_group_hierarchy", lambda: {})
    monkeypatch.setattr(mvs, "annotate_group_hierarchy", lambda items, hierarchy: items)


def test_rota_training_volume_com_granularidade(monkeypatch, _sem_hierarquia):
    import copy

    from app import dashboard_modules
    from app.intelligence import volume_service
    from app.security import Identity

    vs = volume_service.VolumeService
    monkeypatch.setattr(vs, "get_volume_breakdown", lambda self, days, user_id: {"items": []})
    monkeypatch.setattr(vs, "get_volume_trend", lambda self, days, user_id: {"days": days, "items": copy.deepcopy(_SERIE)})
    monkeypatch.setattr(vs, "_user_today", lambda self, user_id: _date(2026, 11, 16))
    identidade = Identity(user_id=1, role="regular", source="session")

    sem = dashboard_modules.training_volume(days=14, identity=identidade)
    assert "period_comparison" not in sem

    corpo = dashboard_modules.training_volume(days=14, identity=identidade, granularity="d7")
    assert corpo["trend"]["items"] == _SERIE  # a serie nao muda
    pc = corpo["period_comparison"]
    assert pc["current"] == {"from": "2026-11-10", "to": "2026-11-16"}
    assert [r["group"]["label"] for r in pc["levels"]["muscle_group"]] == ["Bíceps", "Peito", "Costas"]


def test_rota_training_volume_valida_granularidade():
    from app import dashboard_modules

    for valida in ("d7", "week", "month"):
        assert re.fullmatch(dashboard_modules._GRANULARITY_PATTERN, valida)
    for invalida in ("year", "D7", "", "week "):
        assert not re.fullmatch(dashboard_modules._GRANULARITY_PATTERN, invalida)


def test_rota_volume_recente_com_semanal(monkeypatch, _sem_hierarquia):
    import copy

    from app import analytics
    from app.intelligence import volume_service
    from app.security import Identity

    monkeypatch.setattr(
        volume_service.VolumeService, "get_recent_exercise_volume", lambda self, **kw: copy.deepcopy(_RECENTES)
    )
    identidade = Identity(user_id=1, role="regular", source="session")

    # Sem include: a resposta de antes desta etapa.
    sem = analytics.muscle_groups_recent_exercise_volume(limit=None, date_from=None, date_to=None, identity=identidade)
    assert set(sem) == {"limit", "date_from", "date_to", "items"}

    corpo = analytics.muscle_groups_recent_exercise_volume(
        limit=None, date_from=None, date_to=None, identity=identidade, include="weekly, heatmap,equipment_weekly"
    )
    assert corpo["items"] == _RECENTES
    assert corpo["weekly"] == _ssc.build_weekly_comparison(copy.deepcopy(_RECENTES))
    assert corpo["equipment_weekly"] == _ssc.build_equipment_weekly_comparison(copy.deepcopy(_RECENTES))
    assert corpo["heatmap"] == _ssc.build_heatmap(copy.deepcopy(_RECENTES))

    mapa = analytics.muscle_groups_recent_exercise_volume(
        limit=None,
        date_from=_date(2026, 11, 1),
        date_to=_date(2026, 11, 30),
        identity=identidade,
        include="heatmap",
        activity_id=6,
    )
    assert set(mapa) == {"limit", "date_from", "date_to", "items", "heatmap"}
    assert mapa["heatmap"] == _ssc.build_heatmap(copy.deepcopy(_RECENTES), activity_id=6)


def test_rota_volume_recente_include_invalido_422(monkeypatch, _sem_hierarquia):
    from fastapi import HTTPException

    from app import analytics
    from app.security import Identity

    identidade = Identity(user_id=1, role="regular", source="session")
    with _pytest.raises(HTTPException) as erro:
        analytics.muscle_groups_recent_exercise_volume(
            limit=None, date_from=None, date_to=None, identity=identidade, include="weekly,semanal"
        )
    assert erro.value.status_code == 422
    assert analytics.RECENT_VOLUME_INCLUDES == ("weekly", "equipment_weekly", "heatmap")


_EQUIP = [
    {"activity_id": 7, "local_date": "2026-11-18", "muscle_group": "Peito", "equipment_name": "Barra", "volume_kg": 50.0, "sets_count": 1, "last_weight_kg": 50.0},
    {"activity_id": 6, "local_date": "2026-11-16", "muscle_group": "Peito", "equipment_name": "Halter", "volume_kg": 100.0, "sets_count": 3, "last_weight_kg": 30.0},
    {"activity_id": 6, "local_date": "2026-11-16", "muscle_group": "Bíceps", "equipment_name": "", "volume_kg": 30.0, "sets_count": 2, "last_weight_kg": 15.0},
    {"activity_id": 5, "local_date": "2026-11-15", "muscle_group": "Peito", "equipment_name": "Barra", "volume_kg": 200.0, "sets_count": 2, "last_weight_kg": 100.0},
    {"activity_id": 4, "local_date": "2026-11-10", "muscle_group": "Peito", "equipment_name": "Barra", "volume_kg": 200.0, "sets_count": 2, "last_weight_kg": 90.0},
    {"activity_id": 3, "local_date": "2026-10-20", "muscle_group": "Peito", "equipment_name": None, "volume_kg": 400.0, "sets_count": 4, "last_weight_kg": 100.0},
]


def test_comparativo_por_equipamento_paridade_com_o_cliente():
    corpo = _ssc.build_equipment_weekly_comparison(_EQUIP)
    assert corpo["period_keys"] == ["t0", "t1", "t2", "t3"]
    biceps, peito = corpo["levels"]["muscle_group"]
    # Total primeiro; equipamentos por soma de series (Barra 5, sem equipamento 4, Halter 3).
    assert [(e["equipment_name"], e["is_total"]) for e in peito["equipments"]] == [
        (None, True), ("Barra", False), (None, False), ("Halter", False)
    ]
    total, barra, sem, halter = peito["equipments"]
    t = total["by_period"]
    assert [t[k] and t[k]["week_start"] for k in ("t0", "t1", "t2", "t3")] == ["2026-11-16", "2026-11-09", "2026-10-19", None]
    # Decisao do usuario (17/11/2026): ultimo peso do total = a MAIOR ultima carga
    # entre os aparelhos da semana (Barra 50, Halter 30 -> 50; a soma daria 80).
    assert (t["t0"]["sets_count"], t["t0"]["volume_kg"], t["t0"]["last_weight_kg"]) == (4, 150.0, 50.0)
    assert t["t1"]["last_weight_kg"] == 100.0
    assert (t["t0"]["sets_direction"], t["t0"]["sets_delta"]) == ("stable", 0)
    assert (t["t0"]["last_weight_direction"], t["t0"]["last_weight_delta_kg"]) == ("down", -50.0)
    assert (t["t2"]["sets_direction"], t["t2"]["last_weight_direction"]) == (None, None)
    b = barra["by_period"]
    # Ultimo peso da semana = o da atividade mais recente (a5, nao a4).
    assert (b["t1"]["sets_count"], b["t1"]["volume_kg"], b["t1"]["last_weight_kg"]) == (4, 400.0, 100.0)
    assert (b["t0"]["sets_direction"], b["t0"]["sets_delta"], b["t0"]["last_weight_direction"]) == ("down", -3, "down")
    assert (b["t1"]["sets_direction"], b["t2"]) == (None, None)
    assert (sem["by_period"]["t2"]["sets_count"], sem["by_period"]["t0"]) == (4, None)
    assert halter["by_period"]["t0"]["last_weight_kg"] == 30.0
    # Nome vazio = sem equipamento.
    assert [e["equipment_name"] for e in biceps["equipments"]] == [None, None]


def test_mapa_de_calor_paridade_com_o_cliente():
    todos = _ssc.build_heatmap(_EQUIP)["levels"]["muscle_group"]
    assert (todos["min_volume_kg"], todos["max_volume_kg"]) == (30.0, 950.0)
    peito, biceps = todos["cells"]  # ordem por series
    assert (peito["sets_count"], peito["volume_kg"], peito["volume_ratio"]) == (12, 950.0, 1.0)
    assert peito["avg_weight_kg"] == _pytest.approx(950.0 / 12)
    assert (biceps["sets_count"], biceps["volume_ratio"]) == (2, 0.0)

    sessao = _ssc.build_heatmap(_EQUIP, activity_id=6)["levels"]["muscle_group"]
    assert [(c["group"]["label"], c["volume_ratio"]) for c in sessao["cells"]] == [("Peito", 1.0), ("Bíceps", 0.0)]

    # max == min: posicao 0 (a tela pinta a cor baixa); vazio: 0 e 0.
    unico = _ssc.build_heatmap(_EQUIP, activity_id=7)["levels"]["muscle_group"]
    assert (unico["min_volume_kg"], unico["max_volume_kg"], unico["cells"][0]["volume_ratio"]) == (50.0, 50.0, 0.0)
    vazio = _ssc.build_heatmap(_EQUIP, activity_id=99)["levels"]["muscle_group"]
    assert vazio == {"min_volume_kg": 0.0, "max_volume_kg": 0.0, "cells": []}


def test_equipamento_empate_de_series_ordena_pelo_rotulo_sem_caixa():
    base = {"activity_id": 9, "local_date": "2026-11-16", "muscle_group": "Peito", "volume_kg": 100.0, "sets_count": 1, "last_weight_kg": 10.0}
    rows = [{**base, "equipment_name": n} for n in ("halter", "Smith", None, "Barra")]
    peito = _ssc.build_equipment_weekly_comparison(rows)["levels"]["muscle_group"][0]
    # Ausente vale "Sem equipamento" so na ordem; o payload segue com null.
    assert [e["equipment_name"] for e in peito["equipments"][1:]] == ["Barra", "halter", None, "Smith"]


def test_total_por_equipamento_sem_carga_em_nenhum_aparelho_sai_sem_ultimo_peso():
    linhas = [
        {"activity_id": 2, "local_date": "2026-11-16", "muscle_group": "Abdômen", "equipment_name": "Solo",
         "volume_kg": 0.0, "sets_count": 3, "last_weight_kg": None},
        {"activity_id": 2, "local_date": "2026-11-16", "muscle_group": "Abdômen", "equipment_name": "Bola",
         "volume_kg": 0.0, "sets_count": 2, "last_weight_kg": None},
    ]
    (grupo,) = _ssc.build_equipment_weekly_comparison(linhas)["levels"]["muscle_group"]
    total = grupo["equipments"][0]
    assert total["is_total"] is True and total["by_period"]["t0"]["last_weight_kg"] is None


# #188 (passada B): tendencia por grupo (antes `pressureRows` + `MuscleGroupTrend.tsx`).
from app.intelligence.volume_comparison import (  # noqa: E402
    GROUP_TREND_VARIATION_THRESHOLD_PCT,
    TREND_TONE_DOWN,
    TREND_TONE_STABLE,
    TREND_TONE_UP,
    variation_pct,
    variation_tone,
)


def test_limiar_do_tom_herdado_do_cliente():
    assert GROUP_TREND_VARIATION_THRESHOLD_PCT == 5.0


@pytest.mark.parametrize(
    ("latest", "previous", "esperado"),
    [(1200, 1000, 20.0), (600, 900, -33.333333), (300, 0, None), (5, -1, None), (0, 10, -100.0)],
)
def test_variacao_paridade_com_o_cliente(latest, previous, esperado):
    got = variation_pct(latest, previous)
    assert got == esperado if esperado is None else got == pytest.approx(esperado, abs=1e-5)


@pytest.mark.parametrize(
    ("variation", "tom"),
    [
        (None, None),
        (5.0, TREND_TONE_STABLE),  # estrito, como `variation > 5`
        (5.0000001, TREND_TONE_UP),
        (-5.0, TREND_TONE_STABLE),  # estrito, como `variation < -5`
        (-5.0000001, TREND_TONE_DOWN),
        (0.0, TREND_TONE_STABLE),
    ],
)
def test_tom_nos_limites_exatos(variation, tom):
    assert variation_tone(variation) == tom
