"""Dono da aderência do cardio (M98 C2): `domain/cardio_adherence.py`.

Tempo cumprido, % na zona-alvo (Normal), zona alta e baixa (HIIT), status
pelos limiares nomeados e resumo semanal.

Rodar: cd api && python -m pytest tests/unit/test_cardio_adherence.py
"""

from __future__ import annotations

from datetime import date

import pytest

from app.domain import cardio_adherence as ca
from app.domain import cardio_prescription as cp

NORMAL = {
    "item_kind": "cardio",
    "cardio_mode": "continuous",
    "cardio_warmup_minutes": 5,
    "cardio_duration_minutes": 20,
    "cardio_target_zone": 2,
    "cardio_cooldown_minutes": 5,
}  # 30 min no total, 20 min na Z2
HIIT = {
    "item_kind": "cardio",
    "cardio_mode": "hiit",
    "cardio_warmup_minutes": 5,
    "cardio_cooldown_minutes": 5,
    "hiit_rounds": 8,
    "hiit_high_seconds": 30,
    "hiit_low_seconds": 90,
    "hiit_high_zone": 4,
    "hiit_low_zone": 2,
}  # 8 x 30 s Z4 = 240 s; 8 x 90 s Z2 = 720 s; total 1560 s
ANTIGO = {"item_kind": "cardio", "cardio_duration_minutes": 20}


def _realizado(seconds, zones=None):
    return ca.RealizedCardio(seconds=seconds, zone_seconds=zones, intervals_activity_id=7, sport_type="VirtualRun")


def test_limiares_nomeados_e_coerentes():
    assert 0 < ca.PARTIAL_TIME_MIN_PCT < ca.ADHERENT_TIME_MIN_PCT <= 100
    assert 0 < ca.ADHERENT_ZONE_MIN_PCT <= 100


def test_sem_atividade_e_nao_feito():
    out = ca.evaluate(NORMAL, None)
    assert out["status"] == ca.STATUS_NOT_DONE
    assert out["planned_seconds"] == 1800 and out["realized_seconds"] is None


def test_normal_cumprido_na_zona_alvo():
    # 30 min feitos, 18 min na Z2 (90% da parte principal de 20 min).
    out = ca.evaluate(NORMAL, _realizado(1800, (300, 1080, 300, 120, 0)))
    assert out["time_completion_pct"] == 100.0
    assert out["zones"]["target_zone_seconds"] == 1080
    assert out["zones"]["target_zone_share_pct"] == 60.0
    assert out["zones"]["target_zone_completion_pct"] == 90.0
    assert out["status"] == ca.STATUS_ADHERENT


def test_normal_tempo_cumprido_mas_fora_da_zona_e_parcial():
    out = ca.evaluate(NORMAL, _realizado(1800, (100, 300, 1400, 0, 0)))
    assert out["zones"]["target_zone_completion_pct"] == 25.0
    assert out["status"] == ca.STATUS_PARTIAL


def test_zona_cumprida_e_limitada_a_100():
    out = ca.evaluate(NORMAL, _realizado(3600, (0, 3600, 0, 0, 0)))
    assert out["zones"]["target_zone_completion_pct"] == 100.0
    assert out["time_completion_pct"] == 200.0


def test_pouco_tempo_e_baixo():
    out = ca.evaluate(NORMAL, _realizado(600, (0, 600, 0, 0, 0)))
    assert out["status"] == ca.STATUS_LOW


def test_sem_tempo_em_zona_o_status_sai_so_do_tempo():
    out = ca.evaluate(NORMAL, _realizado(1750))
    assert out["has_zone_data"] is False
    assert out["zones"]["target_zone_completion_pct"] is None
    assert out["status"] == ca.STATUS_ADHERENT


def test_item_antigo_sem_zona_avalia_so_o_tempo():
    out = ca.evaluate(ANTIGO, _realizado(1200, (0, 0, 1200, 0, 0)))
    assert out["planned_seconds"] == 1200
    assert out["zones"]["target_zone"] is None
    assert out["status"] == ca.STATUS_ADHERENT


def test_hiit_zona_alta_e_baixa():
    # Z4 ou acima = 200 s (de 240); Z2..Z3 = 700 s (de 720).
    out = ca.evaluate(HIIT, _realizado(1560, (600, 500, 200, 150, 50)))
    zones = out["zones"]
    assert (zones["planned_high_seconds"], zones["planned_low_seconds"]) == (240, 720)
    assert zones["high_zone_seconds"] == 200
    assert zones["low_zone_seconds"] == 700
    assert zones["high_zone_completion_pct"] == pytest.approx(83.3)
    assert zones["low_zone_completion_pct"] == pytest.approx(97.2)
    assert out["status"] == ca.STATUS_ADHERENT


def test_hiit_sem_chegar_na_zona_alta_e_parcial():
    out = ca.evaluate(HIIT, _realizado(1560, (600, 900, 60, 0, 0)))
    assert out["zones"]["high_zone_completion_pct"] == 0.0
    assert out["status"] == ca.STATUS_PARTIAL


def test_tempo_prescrito_e_o_do_dono_da_prescricao():
    assert ca.evaluate(HIIT, None)["planned_seconds"] == int(cp.total_seconds(HIIT))
    assert cp.hiit_phase_seconds(HIIT) == (240.0, 720.0)
    assert cp.hiit_phase_seconds(NORMAL) == (0.0, 0.0)


def test_resumo_semanal_comeca_na_segunda():
    feito = ca.evaluate(NORMAL, _realizado(1800, (0, 1200, 600, 0, 0)))
    nao_feito = ca.evaluate(NORMAL, None)
    sessoes = [
        {"local_date": date(2026, 10, 19), "adherence": feito},  # segunda
        {"local_date": date(2026, 10, 25), "adherence": nao_feito},  # domingo, mesma semana
        {"local_date": date(2026, 10, 26), "adherence": feito},  # segunda seguinte
    ]
    semanas = ca.summarize_weeks(sessoes)
    assert [s["week_start"] for s in semanas] == ["2026-10-19", "2026-10-26"]
    primeira = semanas[0]
    assert (primeira["planned_sessions"], primeira["done_sessions"]) == (2, 1)
    assert primeira["planned_seconds"] == 3600 and primeira["realized_seconds"] == 1800
    assert primeira["time_completion_pct"] == 50.0
    assert primeira["by_status"][ca.STATUS_NOT_DONE] == 1


def test_dia_de_descanso_entra_se_passou_ou_se_hoje_ja_casou():
    hoje = date(2026, 10, 27)
    assert ca.rest_day_due(date(2026, 10, 26), hoje, matched=False)
    assert not ca.rest_day_due(hoje, hoje, matched=False)
    assert ca.rest_day_due(hoje, hoje, matched=True)
    assert not ca.rest_day_due(date(2026, 10, 28), hoje, matched=True)
