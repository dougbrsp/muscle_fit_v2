"""Envelope determinístico do agente de cardio (M98 C3a; dono
`domain/cardio_envelope.py`). Casos de interferência força × cardio, tetos da
semana, sinais de recuperação e projeção do plano.

Rodar: cd api && python -m pytest tests/unit/test_cardio_envelope.py -q
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.domain import cardio_envelope as ce
from app.domain import cardio_prescription as cp

THU = date(2026, 10, 8)  # quinta-feira
LEGS = ["Quadríceps", "Posterior de coxa", "Glúteos"]
PUSH = ["Peitoral", "Tríceps"]


def _days(groups_by_offset: dict[int, list[str]], *, start: date = THU, n: int = ce.RECOMMENDATION_HORIZON_DAYS, planned=None):
    planned = planned or {}
    return [
        {"date": (start + timedelta(days=i)).isoformat(), "muscle_groups": groups_by_offset.get(i, []),
         "planned_cardio": planned.get(i, [])}
        for i in range(n)
    ]


def _ok_signals(**kwargs) -> ce.RecoverySignals:
    return ce.RecoverySignals(tsb=kwargs.pop("tsb", 0.0), band=kwargs.pop("band", ce.RecoveryBand.OK), **kwargs)


def _hiit(day: date, **overrides) -> dict:
    prescription = {cp.MODE_FIELD: "hiit", **cp.DEFAULTS[cp.CardioMode.HIIT], **overrides}
    return {"date": day.isoformat(), "prescription": prescription}


def _normal(day: date, minutes: int = 30, zone: int = 2) -> dict:
    return {
        "date": day.isoformat(),
        "prescription": {cp.MODE_FIELD: "continuous", cp.DURATION_FIELD: minutes, cp.TARGET_ZONE_FIELD: zone},
    }


# --- interferência ------------------------------------------------------------


def test_hiit_vetado_so_na_vespera_do_dia_de_perna():
    """27/10/2026: o dia de perna permite HIIT (depois da força); só a véspera veta."""
    env = ce.build_envelope(_days({0: PUSH, 2: LEGS}), _ok_signals())
    by_day = {d.day: d for d in env.days}
    assert by_day[THU].allows("hiit")
    assert not by_day[THU + timedelta(days=1)].allows("hiit")
    assert by_day[THU + timedelta(days=1)].reasons == (ce.REASON_EVE_OF_LEG_DAY,)
    leg = by_day[THU + timedelta(days=2)]
    assert leg.leg_day and leg.allows("hiit") and leg.reasons == ()
    assert by_day[THU + timedelta(days=3)].allows("hiit")


def test_dois_dias_de_perna_seguidos_o_primeiro_e_vespera():
    env = ce.build_envelope(_days({1: LEGS, 2: LEGS}), _ok_signals())
    by_day = {d.day: d for d in env.days}
    assert by_day[THU + timedelta(days=1)].reasons == (ce.REASON_EVE_OF_LEG_DAY,)
    assert by_day[THU + timedelta(days=2)].allows("hiit")


def test_vespera_do_ultimo_dia_usa_o_dia_seguinte_ao_horizonte():
    env = ce.build_envelope(_days({}), _ok_signals(), next_day_groups=LEGS)
    assert ce.REASON_EVE_OF_LEG_DAY in env.days[-1].reasons


def test_hiit_na_vespera_de_perna_vira_normal_com_motivo():
    env = ce.build_envelope(_days({2: LEGS}), _ok_signals())
    eve = THU + timedelta(days=1)
    accepted, log = ce.apply_envelope([_hiit(eve)], env)
    assert cp.mode_of(accepted[0]["prescription"]) is cp.CardioMode.CONTINUOUS
    assert accepted[0]["prescription"][cp.DURATION_FIELD] == cp.DEFAULTS[cp.CardioMode.CONTINUOUS][cp.DURATION_FIELD]
    assert log == [{"date": eve.isoformat(), "action": ce.ACTION_ADJUSTED,
                    "code": ce.ADJ_HIIT_VETOED, "detail": ce.REASON_EVE_OF_LEG_DAY}]
    assert cp.validation_error(accepted[0]["prescription"]) is None


def test_hiit_no_dia_de_perna_passa_sem_ajuste():
    env = ce.build_envelope(_days({2: LEGS}), _ok_signals())
    accepted, log = ce.apply_envelope([_hiit(THU + timedelta(days=2))], env)
    assert cp.mode_of(accepted[0]["prescription"]) is cp.CardioMode.HIIT and log == []


def test_panturrilha_sozinha_nao_e_dia_de_perna():
    assert not ce.is_leg_day(["Panturrilha"])
    assert ce.is_leg_day(["GLÚTEOS"]) and ce.is_leg_day(["posterior  de coxa"])


def test_teto_semanal_de_hiit_conta_realizadas_e_hiit_do_plano():
    planned_hiit = [{cp.MODE_FIELD: "hiit", "item_kind": "cardio"}]
    days = _days({}, start=date(2026, 10, 12), planned={5: planned_hiit})  # segunda; HIIT do plano no sábado
    env = ce.build_envelope(days, _ok_signals(), realized_intense_dates=["2026-10-12"])
    monday = date(2026, 10, 12)
    accepted, log = ce.apply_envelope([_hiit(monday + timedelta(days=1)), _hiit(monday + timedelta(days=3))], env)
    # 1 realizada + 1 do plano (sábado, sem recomendação) = teto; as duas recomendadas viram Normal.
    assert ce.WEEKLY_HIIT_MAX == 2
    assert [cp.mode_of(s["prescription"]).value for s in accepted] == ["continuous", "continuous"]
    assert [entry["code"] for entry in log] == [ce.ADJ_WEEKLY_HIIT_CAP, ce.ADJ_WEEKLY_HIIT_CAP]


def test_hiit_do_plano_no_mesmo_dia_da_recomendacao_nao_conta_duas_vezes():
    planned_hiit = [{cp.MODE_FIELD: "hiit", "item_kind": "cardio"}]
    monday = date(2026, 10, 12)
    env = ce.build_envelope(_days({}, start=monday, planned={1: planned_hiit}), _ok_signals())
    accepted, log = ce.apply_envelope([_hiit(monday + timedelta(days=1)), _hiit(monday + timedelta(days=3))], env)
    assert [cp.mode_of(s["prescription"]).value for s in accepted] == ["hiit", "hiit"]
    assert log == []


# --- sinais de recuperação ------------------------------------------------------


def _wellness(tsb=None, hrv=None, rhr=None, n=8):
    rows = []
    for i in range(n):
        day = THU - timedelta(days=n - 1 - i)
        rows.append({"wellness_date": day.isoformat(), "form_tsb": -10.0, "hrv_ms": 40.0, "resting_hr_bpm": 50.0})
    if tsb is not None:
        rows[-1]["form_tsb"] = tsb
    if hrv is not None:
        rows[-1]["hrv_ms"] = hrv
    if rhr is not None:
        rows[-1]["resting_hr_bpm"] = rhr
    return rows


@pytest.mark.parametrize(
    "tsb,band",
    [(None, ce.RecoveryBand.OK), (ce.HIIT_MIN_TSB, ce.RecoveryBand.OK), (ce.HIIT_MIN_TSB - 1, ce.RecoveryBand.LOADED),
     (ce.TSB_HIGH_RISK_BELOW - 1, ce.RecoveryBand.HIGH_RISK)],
)
def test_faixa_pelo_tsb(tsb, band):
    signals = ce.assess_recovery(_wellness(tsb=tsb))
    assert signals.band is band


@pytest.mark.parametrize(
    "tsb,band",
    [(-22.0, ce.RecoveryBand.OK), (-25.0, ce.RecoveryBand.OK), (-25.1, ce.RecoveryBand.LOADED),
     (-30.0, ce.RecoveryBand.LOADED), (-35.0, ce.RecoveryBand.LOADED), (-35.1, ce.RecoveryBand.HIGH_RISK)],
)
def test_cortes_de_tsb_decididos_em_27_10(tsb, band):
    """Decisão do usuário de 27/10/2026: cortes -25/-35 (antes -20/-30)."""
    assert (ce.HIIT_MIN_TSB, ce.TSB_HIGH_RISK_BELOW) == (-25, -35)
    assert ce.recovery_band(tsb) is band


def test_sem_tsb_e_faixa_desconhecida():
    assert ce.recovery_band(None) is ce.RecoveryBand.UNKNOWN
    assert ce.BAND_ALLOWS_HIIT[ce.RecoveryBand.UNKNOWN]


def test_tsb_baixo_tira_o_hiit_e_reduz_zona_e_minutos():
    signals = ce.assess_recovery(_wellness(tsb=ce.HIIT_MIN_TSB - 5))
    env = ce.build_envelope(_days({}), signals)
    assert all(not d.allows("hiit") and d.max_zone == ce.BAND_MAX_ZONE[ce.RecoveryBand.LOADED] for d in env.days)
    assert env.weekly_minutes_max == ce.BAND_WEEKLY_MINUTES_MAX[ce.RecoveryBand.LOADED]
    accepted, log = ce.apply_envelope([_normal(THU, zone=5)], env)
    assert accepted[0]["prescription"][cp.TARGET_ZONE_FIELD] == ce.BAND_MAX_ZONE[ce.RecoveryBand.LOADED]
    assert [e["code"] for e in log] == [ce.ADJ_ZONE_CAPPED]


def test_alto_risco_corta_pelo_teto_semanal_de_minutos():
    signals = ce.assess_recovery(_wellness(tsb=ce.TSB_HIGH_RISK_BELOW - 1))
    env = ce.build_envelope(_days({}), signals)
    cap = ce.BAND_WEEKLY_MINUTES_MAX[ce.RecoveryBand.HIGH_RISK]
    sessions = [_normal(THU, minutes=cap), _normal(THU + timedelta(days=1), minutes=cap)]
    accepted, log = ce.apply_envelope(sessions, env)
    assert len(accepted) == 1
    assert log[-1]["code"] == ce.ADJ_WEEKLY_MINUTES_CAP and log[-1]["action"] == ce.ACTION_REJECTED


def test_queda_de_hrv_veta_hiit_so_nos_primeiros_dias():
    signals = ce.assess_recovery(_wellness(hrv=40.0 * (1 - ce.HRV_DROP_BAD_PCT / 100)))
    assert signals.hrv_bad and ce.REASON_HRV_DROP in signals.bad_reasons
    env = ce.build_envelope(_days({}), signals)
    vetados = [d for d in env.days if not d.allows("hiit")]
    assert len(vetados) == ce.RECOVERY_SIGNAL_DAYS
    assert all(ce.REASON_HRV_DROP in d.reasons for d in vetados)


def test_fc_de_repouso_alta_e_perna_sem_liberacao_sao_sinal_ruim():
    signals = ce.assess_recovery(
        _wellness(rhr=50.0 + ce.RESTING_HR_RISE_BAD_BPM),
        [{"muscle_group": "Quadríceps", "readiness_status": "NO_DIRECT_WORK"}],
    )
    assert signals.bad_reasons == (ce.REASON_RESTING_HR_RISE, ce.REASON_LEGS_NOT_RECOVERED)


def test_linha_de_base_curta_nao_gera_sinal():
    signals = ce.assess_recovery(_wellness(hrv=10.0, n=ce.RECOVERY_BASELINE_MIN_POINTS))
    assert signals.hrv_baseline is None and not signals.hrv_bad


# --- tetos da sessão e da semana -------------------------------------------------


def test_fora_do_horizonte_e_duas_no_mesmo_dia_sao_cortadas():
    env = ce.build_envelope(_days({}), _ok_signals())
    sessions = [_normal(THU - timedelta(days=1)), _normal(THU), _normal(THU)]
    accepted, log = ce.apply_envelope(sessions, env)
    assert len(accepted) == 1
    assert sorted(e["code"] for e in log) == sorted([ce.ADJ_OUTSIDE_HORIZON, ce.ADJ_DAY_LIMIT])


def test_continuo_acima_do_teto_e_limitado():
    env = ce.build_envelope(_days({}), _ok_signals())
    accepted, log = ce.apply_envelope([_normal(THU, minutes=ce.CONTINUOUS_SESSION_MAX_MINUTES + 30)], env)
    assert accepted[0]["prescription"][cp.DURATION_FIELD] == ce.CONTINUOUS_SESSION_MAX_MINUTES
    assert log[0]["code"] == ce.ADJ_DURATION_CAPPED


def test_teto_semanal_de_sessoes():
    start = date(2026, 10, 12)  # segunda: o horizonte inteiro na mesma semana
    env = ce.build_envelope(_days({}, start=start), _ok_signals())
    sessions = [_normal(start + timedelta(days=i), minutes=cp.CARDIO_DURATION_MINUTES_MIN) for i in range(7)]
    accepted, log = ce.apply_envelope(sessions, env)
    assert len(accepted) == ce.WEEKLY_CARDIO_SESSIONS_MAX
    assert log[-1]["code"] == ce.ADJ_WEEKLY_SESSIONS_CAP


def test_sessao_intensa_realizada_pelo_tempo_em_zona_alta():
    low = [600, 600, 600, ce.HIGH_INTENSITY_MIN_SECONDS - 1, 0]
    high = [0, 0, 0, ce.HIGH_INTENSITY_MIN_SECONDS - 100, 100]
    assert not ce.is_high_intensity(low)
    assert ce.is_high_intensity(high)
    assert not ce.is_high_intensity(None)


# --- projeção do plano -----------------------------------------------------------

SLOTS_WEEKDAY = [
    {"sequence": 1, "name": "Push", "weekday": 4},
    {"sequence": 2, "name": "Pull", "weekday": 5},
    {"sequence": 3, "name": "Legs", "weekday": 6},
]


def test_projecao_por_dia_da_semana_e_ficha_salva_vence():
    days = list(ce.horizon_dates(THU, 4))
    out = ce.project_plan_slots(SLOTS_WEEKDAY, days, plans_by_date={THU + timedelta(days=1): 3})
    assert [(s["name"] if s else None, src) for _, s, src in out] == [
        ("Push", ce.SOURCE_WEEKDAY), ("Legs", ce.SOURCE_PLAN), ("Legs", ce.SOURCE_WEEKDAY), (None, ce.SOURCE_NONE),
    ]


def test_projecao_pela_sequencia_quando_falta_dia_da_semana():
    slots = [{**SLOTS_WEEKDAY[0]}, {**SLOTS_WEEKDAY[1], "weekday": None}, {**SLOTS_WEEKDAY[2], "weekday": None}]
    days = list(ce.horizon_dates(THU, 4))
    out = ce.project_plan_slots(slots, days, next_sequence=2)
    assert [s["sequence"] for _, s, _ in out] == [2, 3, 1, 2]
    assert {src for _, _, src in out} == {ce.SOURCE_SEQUENCE}
    # Ficha no meio reposiciona a continuação.
    out = ce.project_plan_slots(slots, days, next_sequence=2, plans_by_date={THU + timedelta(days=1): 1})
    assert [s["sequence"] for _, s, _ in out] == [2, 1, 2, 3]


def test_sem_slots_nao_ha_plano():
    assert [src for _, _, src in ce.project_plan_slots([], [THU])] == [ce.SOURCE_NONE]


def test_export_e_payload_trazem_os_numeros_do_dono():
    env = ce.build_envelope(_days({2: LEGS}), _ok_signals())
    payload = env.to_dict()
    assert payload["weekly_hiit_max"] == ce.WEEKLY_HIIT_MAX
    assert payload["days"][2]["leg_day"] is True
    assert ce.export_constants()["hiit_min_tsb"] == ce.HIIT_MIN_TSB
