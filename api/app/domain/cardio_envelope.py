"""Envelope determinístico do agente de cardio (M98 C3a; plano
`docs/cardio/PLANO_MOTOR_CARDIO_2026-10-26.md` §C3).

Dono ÚNICO de:

- o horizonte da recomendação (quantos dias à frente);
- a projeção do plano nos próximos dias (qual slot do Modelo cai em cada data:
  ficha salva para a data > dia da semana do slot > sequência do Modelo);
- o que é "dia de perna" (grupos musculares de membros inferiores);
- os sinais de recuperação ruim (TSB, queda de HRV, alta da FC de repouso,
  grupo de perna sem liberação para trabalho direto) e a faixa de carga;
- o que conta como sessão INTENSA já realizada (tempo em zona alta);
- o envelope por dia (modos permitidos e zona máxima, com o motivo) e os tetos
  da semana (sessões, HIIT e minutos de cardio recomendados);
- a aplicação do envelope DEPOIS do LLM (`apply_envelope`): o envelope vence a
  IA; cada corte ou ajuste sai com o motivo.

Regras de interferência força × cardio (aprovadas pelo usuário em 27/10/2026;
ver o mapa §69.4):

- dia anterior a um treino de perna: HIIT vetado; dia de perna: HIIT liberado,
  desde que depois da força (decisão de 27/10/2026);
- no máximo `WEEKLY_HIIT_MAX` sessões intensas por semana (as recomendadas, as
  de HIIT do plano em dias sem recomendação e as intensas já feitas);
- nada de HIIT nos primeiros `RECOVERY_SIGNAL_DAYS` dias do horizonte com
  sinal de recuperação ruim (HRV, FC de repouso ou perna sem liberação);
- TSB abaixo de `HIIT_MIN_TSB`: sem HIIT e zona máxima reduzida; abaixo de
  `TSB_HIGH_RISK_BELOW`: zona máxima ainda menor e teto semanal menor.

A semana é a do `cardio_adherence.week_start` (começa na segunda, data local).

Os números moram aqui; o prompt do agente (`cardio_agent_ai.py`) e o RAG
(`rag/markdown/CARDIO.md`, via `rag_placeholders`) só interpolam.
Mapa: `docs/motores/FONTES_DA_VERDADE.md` §3.4 e §69.4. Guarda:
`api/tests/unit/test_guarda_fontes_da_verdade.py` (`envelope_cardio_paralelo`).

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Any, Final, Iterable, Mapping, Optional, Sequence

from . import cardio_policy, cardio_prescription
from .cardio_adherence import week_start
from .hr_zones import HR_ZONE_MAX, HR_ZONE_MIN, zone_number

# --- Horizonte e tetos da semana (aprovados em 27/10/2026) -------------------------------
# Dias recomendados a partir da data de referência (inclusive).
RECOMMENDATION_HORIZON_DAYS: Final[int] = 7
# Sessões de cardio por dia.
SESSIONS_PER_DAY_MAX: Final[int] = 1
# Frequência semanal: o mínimo é só orientação (prompt/RAG); o máximo é corte.
WEEKLY_CARDIO_SESSIONS_MIN: Final[int] = 3
WEEKLY_CARDIO_SESSIONS_MAX: Final[int] = 6
# Sessões intensas (HIIT) por semana.
WEEKLY_HIIT_MAX: Final[int] = 2
# Teto prático da parte principal do contínuo, por sessão (min).
CONTINUOUS_SESSION_MAX_MINUTES: Final[int] = 60

# --- Faixa de carga pelo TSB (Forma do Intervals.icu) -----------------------
# Cortes decididos pelo usuário em 27/10/2026 (antes -20/-30, calibrados no
# usuário 1: jun–out/2026, TSB entre -26 e -2, média -14).
HIIT_MIN_TSB: Final[int] = -25
# Faixa "alto risco": zona e minutos da semana ainda menores.
TSB_HIGH_RISK_BELOW: Final[int] = -35


class RecoveryBand(str, Enum):
    OK = "ok"
    LOADED = "loaded"  # TSB < HIIT_MIN_TSB
    HIGH_RISK = "high_risk"  # TSB < TSB_HIGH_RISK_BELOW
    UNKNOWN = "unknown"  # sem TSB: as outras regras continuam valendo


BAND_ALLOWS_HIIT: Final[dict[RecoveryBand, bool]] = {
    RecoveryBand.OK: True,
    RecoveryBand.UNKNOWN: True,
    RecoveryBand.LOADED: False,
    RecoveryBand.HIGH_RISK: False,
}
BAND_MAX_ZONE: Final[dict[RecoveryBand, int]] = {
    RecoveryBand.OK: HR_ZONE_MAX,
    RecoveryBand.UNKNOWN: HR_ZONE_MAX,
    RecoveryBand.LOADED: 3,
    RecoveryBand.HIGH_RISK: 2,
}
# Minutos de cardio RECOMENDADOS por semana (aquecimento + principal + recuperação).
BAND_WEEKLY_MINUTES_MAX: Final[dict[RecoveryBand, int]] = {
    RecoveryBand.OK: 240,
    RecoveryBand.UNKNOWN: 240,
    RecoveryBand.LOADED: 150,
    RecoveryBand.HIGH_RISK: 60,
}

# --- Sinais de recuperação ruim (aprovados em 27/10/2026) -------------------------------
# HRV do último dia abaixo da linha de base em pelo menos N%.
HRV_DROP_BAD_PCT: Final[int] = 15
# FC de repouso do último dia acima da linha de base em pelo menos N bpm.
RESTING_HR_RISE_BAD_BPM: Final[int] = 5
# Linha de base = média dos N dias ANTERIORES ao último, com um mínimo de pontos.
RECOVERY_BASELINE_DAYS: Final[int] = 7
RECOVERY_BASELINE_MIN_POINTS: Final[int] = 3
# Com sinal ruim, HIIT vetado nos N primeiros dias do horizonte.
RECOVERY_SIGNAL_DAYS: Final[int] = 2
# Grupos de perna (nome normalizado: minúsculo, sem acento). Panturrilha fica fora.
LEG_MUSCLE_GROUPS: Final[tuple[str, ...]] = ("quadriceps", "posterior de coxa", "gluteos", "adutores")
# Prontidão da recuperação muscular que bloqueia o HIIT quando cai num grupo de perna.
LEG_BLOCKING_READINESS: Final[tuple[str, ...]] = ("NO_DIRECT_WORK",)

# --- Sessão intensa já realizada -------------------------------------------
# Atividade de cardio com pelo menos N s na zona >= HIGH_INTENSITY_ZONE_MIN conta
# no teto semanal de HIIT.
HIGH_INTENSITY_ZONE_MIN: Final[int] = 4
HIGH_INTENSITY_MIN_SECONDS: Final[int] = 300

# --- Projeção do plano ------------------------------------------------------
SOURCE_PLAN: Final[str] = "ficha"  # ficha salva para a data
SOURCE_WEEKDAY: Final[str] = "weekday"  # dia da semana do slot
SOURCE_SEQUENCE: Final[str] = "sequence"  # sequência do Modelo (slots sem dia da semana)
SOURCE_NONE: Final[str] = "none"  # sem slot para a data
REST_SLOT_TYPE: Final[str] = "descanso"

# --- Motivos (códigos estáveis: vão para o banco e para a C3b) -------------
# `leg_day` NÃO veta mais o HIIT (27/10/2026): o dia de perna só sai marcado em
# `DayEnvelope.leg_day`. O código continua estável porque recomendações já
# gravadas o citam em `reasons`/`detail`.
REASON_LEG_DAY: Final[str] = "leg_day"
REASON_EVE_OF_LEG_DAY: Final[str] = "eve_of_leg_day"
REASON_TSB_LOW: Final[str] = "tsb_low"
REASON_TSB_HIGH_RISK: Final[str] = "tsb_high_risk"
REASON_HRV_DROP: Final[str] = "hrv_drop"
REASON_RESTING_HR_RISE: Final[str] = "resting_hr_rise"
REASON_LEGS_NOT_RECOVERED: Final[str] = "legs_not_recovered"

ADJ_OUTSIDE_HORIZON: Final[str] = "outside_horizon"
ADJ_DAY_LIMIT: Final[str] = "day_limit"
ADJ_HIIT_VETOED: Final[str] = "hiit_vetoed"
ADJ_WEEKLY_HIIT_CAP: Final[str] = "weekly_hiit_cap"
ADJ_ZONE_CAPPED: Final[str] = "zone_capped"
ADJ_DURATION_CAPPED: Final[str] = "duration_capped"
ADJ_WEEKLY_SESSIONS_CAP: Final[str] = "weekly_sessions_cap"
ADJ_WEEKLY_MINUTES_CAP: Final[str] = "weekly_minutes_cap"

ACTION_REJECTED: Final[str] = "rejected"
ACTION_ADJUSTED: Final[str] = "adjusted"

ISO_WEEKDAY_MIN: Final[int] = 1
ISO_WEEKDAY_MAX: Final[int] = 7


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoverySignals:
    tsb: Optional[float]
    band: RecoveryBand
    hrv_latest: Optional[float] = None
    hrv_baseline: Optional[float] = None
    resting_hr_latest: Optional[float] = None
    resting_hr_baseline: Optional[float] = None
    hrv_bad: bool = False
    resting_hr_bad: bool = False
    legs_not_recovered: bool = False

    @property
    def bad_reasons(self) -> tuple[str, ...]:
        reasons = []
        if self.hrv_bad:
            reasons.append(REASON_HRV_DROP)
        if self.resting_hr_bad:
            reasons.append(REASON_RESTING_HR_RISE)
        if self.legs_not_recovered:
            reasons.append(REASON_LEGS_NOT_RECOVERED)
        return tuple(reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tsb": self.tsb,
            "band": self.band.value,
            "hrv_latest": self.hrv_latest,
            "hrv_baseline": self.hrv_baseline,
            "resting_hr_latest": self.resting_hr_latest,
            "resting_hr_baseline": self.resting_hr_baseline,
            "bad_recovery_reasons": list(self.bad_reasons),
        }


@dataclass(frozen=True)
class DayEnvelope:
    day: date
    allowed_modes: tuple[str, ...]
    max_zone: int
    leg_day: bool
    reasons: tuple[str, ...]

    def allows(self, mode: str) -> bool:
        return mode in self.allowed_modes

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.day.isoformat(),
            "allowed_modes": list(self.allowed_modes),
            "max_zone": self.max_zone,
            "leg_day": self.leg_day,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class Envelope:
    days: tuple[DayEnvelope, ...]
    signals: RecoverySignals
    weekly_minutes_max: int
    weekly_sessions_max: int
    weekly_hiit_max: int
    sessions_per_day_max: int
    continuous_session_max_minutes: int
    # Intensas JÁ REALIZADAS que contam na semana, por segunda-feira.
    hiit_already_by_week: tuple[tuple[date, int], ...]
    # Dias do horizonte com HIIT no plano (contam na semana se o agente não
    # recomendar outra sessão para o mesmo dia).
    planned_hiit_dates: tuple[date, ...] = ()

    def day(self, value: date) -> Optional[DayEnvelope]:
        return next((d for d in self.days if d.day == value), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "days": [d.to_dict() for d in self.days],
            "signals": self.signals.to_dict(),
            "weekly_minutes_max": self.weekly_minutes_max,
            "weekly_sessions_max": self.weekly_sessions_max,
            "weekly_sessions_min": WEEKLY_CARDIO_SESSIONS_MIN,
            "weekly_hiit_max": self.weekly_hiit_max,
            "sessions_per_day_max": self.sessions_per_day_max,
            "continuous_session_max_minutes": self.continuous_session_max_minutes,
            "hiit_already_by_week": {week.isoformat(): count for week, count in self.hiit_already_by_week},
            "planned_hiit_dates": [day.isoformat() for day in self.planned_hiit_dates],
        }


# ---------------------------------------------------------------------------
# Leitura tolerante
# ---------------------------------------------------------------------------


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_group_name(name: Any) -> str:
    """Nome de grupo muscular comparável: minúsculo, sem acento, espaços simples."""
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(text.lower().split())


def is_leg_day(muscle_groups: Iterable[Any]) -> bool:
    """Há pelo menos um grupo de perna (`LEG_MUSCLE_GROUPS`) na sessão."""
    return any(normalize_group_name(group) in LEG_MUSCLE_GROUPS for group in muscle_groups or ())


def horizon_dates(start: date, days: int = RECOMMENDATION_HORIZON_DAYS) -> tuple[date, ...]:
    return tuple(start + timedelta(days=offset) for offset in range(days))


# ---------------------------------------------------------------------------
# Projeção do plano
# ---------------------------------------------------------------------------


def _slot_weekday(slot: Mapping[str, Any]) -> Optional[int]:
    value = _number(slot.get("weekday"))
    if value is None or not value.is_integer():
        return None
    weekday = int(value)
    return weekday if ISO_WEEKDAY_MIN <= weekday <= ISO_WEEKDAY_MAX else None


def project_plan_slots(
    slots: Sequence[Mapping[str, Any]],
    days: Sequence[date],
    *,
    next_sequence: Optional[int] = None,
    plans_by_date: Optional[Mapping[date, int]] = None,
) -> list[tuple[date, Optional[Mapping[str, Any]], str]]:
    """Slot do Modelo que cai em cada data, com a origem:

    1. ficha salva para a data (`plans_by_date`: data -> sequência do slot);
    2. todos os slots com dia da semana (ISO, 1 = segunda): o slot do dia
       (vários no mesmo dia: o de menor sequência); nenhum = sem slot;
    3. senão, a sequência do Modelo a partir de `next_sequence` (o próximo slot
       da continuação automática), um slot por dia, com volta ao início; uma
       ficha no meio da sequência reposiciona a continuação.
    """
    ordered = sorted(slots or [], key=lambda slot: int(slot["sequence"]))
    by_sequence = {int(slot["sequence"]): slot for slot in ordered}
    plans_by_date = plans_by_date or {}
    use_weekday = bool(ordered) and all(_slot_weekday(slot) is not None for slot in ordered)
    position = 0
    if next_sequence is not None and int(next_sequence) in by_sequence:
        position = [int(slot["sequence"]) for slot in ordered].index(int(next_sequence))

    out: list[tuple[date, Optional[Mapping[str, Any]], str]] = []
    for day in days:
        planned_sequence = plans_by_date.get(day)
        if planned_sequence is not None and int(planned_sequence) in by_sequence:
            slot = by_sequence[int(planned_sequence)]
            out.append((day, slot, SOURCE_PLAN))
            if not use_weekday:
                position = (ordered.index(slot) + 1) % len(ordered)
            continue
        if not ordered:
            out.append((day, None, SOURCE_NONE))
        elif use_weekday:
            slot = next((s for s in ordered if _slot_weekday(s) == day.isoweekday()), None)
            out.append((day, slot, SOURCE_WEEKDAY if slot is not None else SOURCE_NONE))
        else:
            out.append((day, ordered[position], SOURCE_SEQUENCE))
            position = (position + 1) % len(ordered)
    return out


# ---------------------------------------------------------------------------
# Sinais de recuperação
# ---------------------------------------------------------------------------


def recovery_band(tsb: Any) -> RecoveryBand:
    value = _number(tsb)
    if value is None:
        return RecoveryBand.UNKNOWN
    if value < TSB_HIGH_RISK_BELOW:
        return RecoveryBand.HIGH_RISK
    if value < HIIT_MIN_TSB:
        return RecoveryBand.LOADED
    return RecoveryBand.OK


def _latest_and_baseline(rows: Sequence[tuple[date, float]]) -> tuple[Optional[float], Optional[float]]:
    """(valor do último dia, média dos `RECOVERY_BASELINE_DAYS` anteriores) ou None."""
    if not rows:
        return None, None
    ordered = sorted(rows, key=lambda item: item[0])
    last_day, last_value = ordered[-1]
    since = last_day - timedelta(days=RECOVERY_BASELINE_DAYS)
    previous = [value for day, value in ordered[:-1] if since <= day < last_day]
    if len(previous) < RECOVERY_BASELINE_MIN_POINTS:
        return last_value, None
    return last_value, sum(previous) / len(previous)


def _series(wellness: Iterable[Mapping[str, Any]], key: str) -> list[tuple[date, float]]:
    out = []
    for row in wellness or ():
        day = _as_date(row.get("wellness_date") or row.get("date"))
        value = _number(row.get(key))
        if day is not None and value is not None:
            out.append((day, value))
    return out


def _round1(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 1)


def assess_recovery(
    wellness: Iterable[Mapping[str, Any]],
    muscle_recovery: Iterable[Mapping[str, Any]] = (),
) -> RecoverySignals:
    """Sinais de recuperação a partir do wellness do Intervals (`form_tsb`,
    `hrv_ms`, `resting_hr_bpm`, já com a precedência do sync/edição manual) e
    da prontidão por grupo (`muscle_group`/`muscle_group_name` +
    `readiness_status` da recuperação muscular)."""
    rows = list(wellness or ())
    tsb_series = _series(rows, "form_tsb")
    tsb = sorted(tsb_series)[-1][1] if tsb_series else None
    hrv_latest, hrv_baseline = _latest_and_baseline(_series(rows, "hrv_ms"))
    rhr_latest, rhr_baseline = _latest_and_baseline(_series(rows, "resting_hr_bpm"))
    hrv_bad = (
        hrv_latest is not None
        and hrv_baseline is not None
        and hrv_baseline > 0
        and hrv_latest <= hrv_baseline * (1 - HRV_DROP_BAD_PCT / 100)
    )
    rhr_bad = rhr_latest is not None and rhr_baseline is not None and rhr_latest >= rhr_baseline + RESTING_HR_RISE_BAD_BPM
    legs_blocked = any(
        normalize_group_name(item.get("muscle_group") or item.get("muscle_group_name")) in LEG_MUSCLE_GROUPS
        and str(item.get("readiness_status") or "").upper() in LEG_BLOCKING_READINESS
        for item in muscle_recovery or ()
    )
    return RecoverySignals(
        tsb=_round1(tsb),
        band=recovery_band(tsb),
        hrv_latest=_round1(hrv_latest),
        hrv_baseline=_round1(hrv_baseline),
        resting_hr_latest=_round1(rhr_latest),
        resting_hr_baseline=_round1(rhr_baseline),
        hrv_bad=bool(hrv_bad),
        resting_hr_bad=bool(rhr_bad),
        legs_not_recovered=legs_blocked,
    )


def is_high_intensity(app_zone_seconds: Optional[Sequence[Any]]) -> bool:
    """Atividade realizada intensa: segundos em Z`HIGH_INTENSITY_ZONE_MIN`+ do
    app (`hr_zones.app_zone_seconds`, Z1 no índice 0) >= `HIGH_INTENSITY_MIN_SECONDS`."""
    if not app_zone_seconds:
        return False
    high = sum(_number(value) or 0 for value in list(app_zone_seconds)[HIGH_INTENSITY_ZONE_MIN - HR_ZONE_MIN :])
    return high >= HIGH_INTENSITY_MIN_SECONDS


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


def _is_planned_hiit(item: Mapping[str, Any]) -> bool:
    return cardio_prescription.mode_of(item) is cardio_prescription.CardioMode.HIIT


def build_envelope(
    plan_days: Sequence[Mapping[str, Any]],
    signals: RecoverySignals,
    *,
    realized_intense_dates: Iterable[Any] = (),
    next_day_groups: Iterable[Any] = (),
) -> Envelope:
    """Envelope por dia do horizonte (`plan_days`: `date`, `muscle_groups`,
    `planned_cardio` na forma do contexto do agente) e os tetos da semana.

    `next_day_groups`: grupos do dia SEGUINTE ao último do horizonte (para a
    regra da véspera do último dia)."""
    days = [(_as_date(day.get("date")), day) for day in plan_days]
    days = [(d, day) for d, day in days if d is not None]
    days.sort(key=lambda item: item[0])
    leg_by_date = {d: is_leg_day(day.get("muscle_groups") or ()) for d, day in days}
    if days:
        leg_by_date.setdefault(days[-1][0] + timedelta(days=1), is_leg_day(next_day_groups))
    first_day = days[0][0] if days else None
    band = signals.band
    bad = signals.bad_reasons

    out: list[DayEnvelope] = []
    for day, _plan in days:
        reasons: list[str] = []
        # Dia de perna: HIIT permitido depois da força (só a véspera veta).
        if leg_by_date.get(day + timedelta(days=1)):
            reasons.append(REASON_EVE_OF_LEG_DAY)
        if not BAND_ALLOWS_HIIT[band]:
            reasons.append(REASON_TSB_HIGH_RISK if band is RecoveryBand.HIGH_RISK else REASON_TSB_LOW)
        if bad and first_day is not None and (day - first_day).days < RECOVERY_SIGNAL_DAYS:
            reasons.extend(bad)
        hiit_ok = not reasons
        modes = cardio_prescription.CARDIO_MODE_VALUES if hiit_ok else (cardio_prescription.CardioMode.CONTINUOUS.value,)
        out.append(
            DayEnvelope(
                day=day,
                allowed_modes=tuple(modes),
                max_zone=BAND_MAX_ZONE[band],
                leg_day=bool(leg_by_date.get(day)),
                reasons=tuple(reasons),
            )
        )

    already: dict[date, int] = {}
    for value in realized_intense_dates or ():
        realized = _as_date(value)
        if realized is not None:
            week = week_start(realized)
            already[week] = already.get(week, 0) + 1
    planned_hiit = tuple(
        day for day, plan in days if any(_is_planned_hiit(item) for item in plan.get("planned_cardio") or ())
    )

    return Envelope(
        days=tuple(out),
        signals=signals,
        weekly_minutes_max=BAND_WEEKLY_MINUTES_MAX[band],
        weekly_sessions_max=WEEKLY_CARDIO_SESSIONS_MAX,
        weekly_hiit_max=WEEKLY_HIIT_MAX,
        sessions_per_day_max=SESSIONS_PER_DAY_MAX,
        continuous_session_max_minutes=CONTINUOUS_SESSION_MAX_MINUTES,
        hiit_already_by_week=tuple(sorted(already.items())),
        planned_hiit_dates=planned_hiit,
    )


def _to_continuous(item: Mapping[str, Any], max_zone: int) -> dict[str, Any]:
    """HIIT vetado vira Normal: aquecimento e recuperação mantidos; parte
    principal e zona = padrão do Normal (`cardio_prescription.DEFAULTS`),
    com a zona limitada pela zona máxima do dia."""
    defaults = cardio_prescription.DEFAULTS[cardio_prescription.CardioMode.CONTINUOUS]
    return {
        cardio_prescription.MODE_FIELD: cardio_prescription.CardioMode.CONTINUOUS.value,
        cardio_prescription.WARMUP_FIELD: item.get(cardio_prescription.WARMUP_FIELD),
        cardio_prescription.DURATION_FIELD: defaults[cardio_prescription.DURATION_FIELD],
        cardio_prescription.TARGET_ZONE_FIELD: min(defaults[cardio_prescription.TARGET_ZONE_FIELD], max_zone),
        cardio_prescription.COOLDOWN_FIELD: item.get(cardio_prescription.COOLDOWN_FIELD),
    }


def _adjustment(session: Mapping[str, Any], action: str, code: str, detail: Optional[str] = None) -> dict[str, Any]:
    return {"date": session.get("date"), "action": action, "code": code, "detail": detail}


def _minutes(prescription: Mapping[str, Any]) -> float:
    return cardio_prescription.total_seconds(prescription) / cardio_prescription.SECONDS_PER_MINUTE


def apply_envelope(
    sessions: Sequence[Mapping[str, Any]], envelope: Envelope
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reaplica o envelope à saída JÁ VALIDADA do agente. Cada sessão:
    `{"date": "YYYY-MM-DD", "prescription": {...campos do dono...}, ...}`.

    Ordem (por data): fora do horizonte (rejeita) -> limite por dia (rejeita)
    -> HIIT vetado no dia ou teto semanal de HIIT (vira Normal) -> zona acima da
    máxima do dia (limita; HIIT sem espaço vira Normal) -> parte principal do
    Normal acima do teto (limita) -> teto semanal de sessões e de minutos
    (rejeita). Devolve (sessões aceitas, ajustes/cortes com o motivo)."""
    accepted: list[dict[str, Any]] = []
    adjustments: list[dict[str, Any]] = []
    per_day: dict[date, int] = {}
    hiit_by_week = dict(envelope.hiit_already_by_week)
    session_days = {_as_date(s.get("date")) for s in sessions}
    for planned in envelope.planned_hiit_dates:
        if planned not in session_days:
            hiit_by_week[week_start(planned)] = hiit_by_week.get(week_start(planned), 0) + 1
    sessions_by_week: dict[date, int] = {}
    minutes_by_week: dict[date, float] = {}
    hiit_mode = cardio_prescription.CardioMode.HIIT.value

    for session in sorted(sessions, key=lambda s: str(s.get("date") or "")):
        day = _as_date(session.get("date"))
        day_env = envelope.day(day) if day is not None else None
        if day_env is None:
            adjustments.append(_adjustment(session, ACTION_REJECTED, ADJ_OUTSIDE_HORIZON))
            continue
        if per_day.get(day, 0) >= envelope.sessions_per_day_max:
            adjustments.append(_adjustment(session, ACTION_REJECTED, ADJ_DAY_LIMIT))
            continue
        week = week_start(day)
        prescription = dict(session["prescription"])
        mode = cardio_prescription.mode_of(prescription).value

        if mode == hiit_mode and not day_env.allows(hiit_mode):
            prescription = _to_continuous(prescription, day_env.max_zone)
            adjustments.append(_adjustment(session, ACTION_ADJUSTED, ADJ_HIIT_VETOED, ",".join(day_env.reasons)))
        elif mode == hiit_mode and hiit_by_week.get(week, 0) >= envelope.weekly_hiit_max:
            prescription = _to_continuous(prescription, day_env.max_zone)
            adjustments.append(_adjustment(session, ACTION_ADJUSTED, ADJ_WEEKLY_HIIT_CAP))

        mode = cardio_prescription.mode_of(prescription).value
        if mode == hiit_mode:
            high = zone_number(prescription.get(cardio_prescription.HIGH_ZONE_FIELD))
            low = zone_number(prescription.get(cardio_prescription.LOW_ZONE_FIELD))
            if high is not None and high > day_env.max_zone:
                if low is not None and low >= day_env.max_zone:
                    prescription = _to_continuous(prescription, day_env.max_zone)
                else:
                    prescription[cardio_prescription.HIGH_ZONE_FIELD] = day_env.max_zone
                adjustments.append(_adjustment(session, ACTION_ADJUSTED, ADJ_ZONE_CAPPED))
        if cardio_prescription.mode_of(prescription).value != hiit_mode:
            target = zone_number(prescription.get(cardio_prescription.TARGET_ZONE_FIELD))
            if target is not None and target > day_env.max_zone:
                prescription[cardio_prescription.TARGET_ZONE_FIELD] = day_env.max_zone
                adjustments.append(_adjustment(session, ACTION_ADJUSTED, ADJ_ZONE_CAPPED))
            duration = _number(prescription.get(cardio_prescription.DURATION_FIELD))
            if duration is not None and duration > envelope.continuous_session_max_minutes:
                prescription[cardio_prescription.DURATION_FIELD] = envelope.continuous_session_max_minutes
                adjustments.append(_adjustment(session, ACTION_ADJUSTED, ADJ_DURATION_CAPPED))

        if sessions_by_week.get(week, 0) >= envelope.weekly_sessions_max:
            adjustments.append(_adjustment(session, ACTION_REJECTED, ADJ_WEEKLY_SESSIONS_CAP))
            continue
        minutes = _minutes(prescription)
        if minutes_by_week.get(week, 0.0) + minutes > envelope.weekly_minutes_max:
            adjustments.append(_adjustment(session, ACTION_REJECTED, ADJ_WEEKLY_MINUTES_CAP))
            continue

        per_day[day] = per_day.get(day, 0) + 1
        sessions_by_week[week] = sessions_by_week.get(week, 0) + 1
        minutes_by_week[week] = minutes_by_week.get(week, 0.0) + minutes
        if cardio_prescription.mode_of(prescription).value == hiit_mode:
            hiit_by_week[week] = hiit_by_week.get(week, 0) + 1
        accepted.append({**session, "prescription": prescription})
    return accepted, adjustments


# ---------------------------------------------------------------------------
# Modelo de treino (periodização por IA, M98 C5)
# ---------------------------------------------------------------------------

# Segunda-feira qualquer: só ancora a projeção do ciclo do Modelo em datas para
# reaproveitar `build_envelope`/`apply_envelope` (a data em si não significa
# nada; só o dia da semana e a ordem dos dias).
TEMPLATE_ANCHOR_MONDAY: Final[date] = date(2024, 1, 1)


def template_signals(tsb: Any) -> RecoverySignals:
    """Sinais que valem para um Modelo (ciclo recorrente): só a faixa do TSB
    (Forma do Intervals.icu) no momento da geração. Queda de HRV, FC de repouso
    alta e perna sem liberação valem para os `RECOVERY_SIGNAL_DAYS` primeiros
    dias de uma recomendação com data; num Modelo, que se repete por semanas,
    não moldam o ciclo."""
    return RecoverySignals(tsb=_round1(_number(tsb)), band=recovery_band(tsb))


def _project_template(slots: Sequence[Mapping[str, Any]]) -> tuple[list[tuple[Mapping[str, Any], date]], bool]:
    ordered = sorted(slots or [], key=lambda slot: int(slot["sequence"]))
    weekdays = [_slot_weekday(slot) for slot in ordered]
    if ordered and None not in weekdays and len(set(weekdays)) == len(weekdays):
        projected = [
            (slot, TEMPLATE_ANCHOR_MONDAY + timedelta(days=int(weekday) - ISO_WEEKDAY_MIN))
            for slot, weekday in zip(ordered, weekdays)
        ]
        return sorted(projected, key=lambda item: item[1]), True
    return [(slot, TEMPLATE_ANCHOR_MONDAY + timedelta(days=index)) for index, slot in enumerate(ordered)], False


def project_template_slots(slots: Sequence[Mapping[str, Any]]) -> list[tuple[Mapping[str, Any], date]]:
    """Data sintética de cada slot do Modelo, em ordem de data:

    - todos os slots com dia da semana (ISO, 1 = segunda) e sem repetir: o dia
      da semana do slot a partir de `TEMPLATE_ANCHOR_MONDAY` (a véspera segue
      o calendário: a véspera de segunda é o domingo);
    - senão: um slot por dia, na ordem de `sequence` (a véspera do 1º slot é o
      último, porque o ciclo se repete)."""
    return _project_template(slots)[0]


def _template_next_day_groups(projected: Sequence[tuple[Mapping[str, Any], date]], by_weekday: bool) -> list[Any]:
    """Grupos do dia SEGUINTE ao último dia projetado (regra da véspera do
    último dia): no ciclo por sequência, o 1º slot; por dia da semana, o slot
    de segunda quando o último dia é domingo (senão o dia seguinte não tem slot)."""
    if not projected:
        return []
    following: Optional[Mapping[str, Any]] = projected[0][0]
    if by_weekday:
        last_weekday = projected[-1][1].isoweekday()
        following = projected[0][0] if last_weekday == ISO_WEEKDAY_MAX and projected[0][1].isoweekday() == ISO_WEEKDAY_MIN else None
    return list((following or {}).get("muscle_groups") or [])


def build_template_envelope(
    slots: Sequence[Mapping[str, Any]], signals: RecoverySignals
) -> tuple[Envelope, dict[int, date]]:
    """Envelope de um Modelo (`slots`: `sequence`, `weekday`, `muscle_groups`
    com os NOMES dos grupos de força do slot). Mesmas regras do agente
    (`build_envelope`): HIIT vetado na véspera do dia de perna, faixa do
    TSB (zona máxima, HIIT e minutos da semana), tetos da semana. Sem
    sessões intensas já feitas: o Modelo é recorrente. Devolve o envelope e a
    data sintética de cada `sequence`."""
    projected, by_weekday = _project_template(slots)
    plan_days = [{"date": day, "muscle_groups": list(slot.get("muscle_groups") or [])} for slot, day in projected]
    envelope = build_envelope(plan_days, signals, next_day_groups=_template_next_day_groups(projected, by_weekday))
    return envelope, {int(slot["sequence"]): day for slot, day in projected}


def apply_template_envelope(
    sessions: Sequence[Mapping[str, Any]],
    slots: Sequence[Mapping[str, Any]],
    signals: RecoverySignals,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """`apply_envelope` sobre o cardio de um Modelo. Cada sessão:
    `{"sequence": n, "prescription": {...}}` (já validada pelo dono da
    prescrição). Devolve (aceitas, ajustes/cortes), as duas com `sequence` no
    lugar da data sintética. Sessão de slot inexistente sai como
    `ADJ_OUTSIDE_HORIZON`."""
    envelope, day_of = build_template_envelope(slots, signals)
    sequence_of = {day.isoformat(): sequence for sequence, day in day_of.items()}
    dated = []
    for session in sessions:
        sequence = int(session["sequence"])
        day = day_of.get(sequence)
        dated.append({**session, "date": day.isoformat() if day else f"slot-{sequence}"})
    accepted, adjustments = apply_envelope(dated, envelope)
    out_accepted = [{k: v for k, v in s.items() if k != "date"} for s in accepted]
    out_adjustments = []
    for entry in adjustments:
        sequence = sequence_of.get(str(entry.get("date")))
        if sequence is None and str(entry.get("date") or "").startswith("slot-"):
            sequence = int(str(entry["date"])[len("slot-"):])
        out_adjustments.append({**{k: v for k, v in entry.items() if k != "date"}, "sequence": sequence})
    return out_accepted, out_adjustments


def export_constants() -> dict[str, Any]:
    """Números do envelope para quem só exibe/interpola (prompt e RAG)."""
    return {
        "horizon_days": RECOMMENDATION_HORIZON_DAYS,
        "sessions_per_day_max": SESSIONS_PER_DAY_MAX,
        "weekly_sessions_min": WEEKLY_CARDIO_SESSIONS_MIN,
        "weekly_sessions_max": WEEKLY_CARDIO_SESSIONS_MAX,
        "weekly_hiit_max": WEEKLY_HIIT_MAX,
        "continuous_session_max_minutes": CONTINUOUS_SESSION_MAX_MINUTES,
        "hiit_min_tsb": HIIT_MIN_TSB,
        "tsb_high_risk_below": TSB_HIGH_RISK_BELOW,
        "band_max_zone": {band.value: zone for band, zone in BAND_MAX_ZONE.items()},
        "band_weekly_minutes_max": {band.value: minutes for band, minutes in BAND_WEEKLY_MINUTES_MAX.items()},
        "hrv_drop_bad_pct": HRV_DROP_BAD_PCT,
        "resting_hr_rise_bad_bpm": RESTING_HR_RISE_BAD_BPM,
        "recovery_signal_days": RECOVERY_SIGNAL_DAYS,
        "high_intensity_zone_min": HIGH_INTENSITY_ZONE_MIN,
        "high_intensity_min_seconds": HIGH_INTENSITY_MIN_SECONDS,
        "progression_step_minutes": cardio_policy.CONTINUOUS_PROGRESSION_STEP_MINUTES,
    }


__all__ = [
    "ACTION_ADJUSTED",
    "ACTION_REJECTED",
    "BAND_ALLOWS_HIIT",
    "BAND_MAX_ZONE",
    "BAND_WEEKLY_MINUTES_MAX",
    "CONTINUOUS_SESSION_MAX_MINUTES",
    "DayEnvelope",
    "Envelope",
    "HIIT_MIN_TSB",
    "LEG_MUSCLE_GROUPS",
    "RECOMMENDATION_HORIZON_DAYS",
    "RecoveryBand",
    "RecoverySignals",
    "TSB_HIGH_RISK_BELOW",
    "WEEKLY_CARDIO_SESSIONS_MAX",
    "WEEKLY_CARDIO_SESSIONS_MIN",
    "WEEKLY_HIIT_MAX",
    "TEMPLATE_ANCHOR_MONDAY",
    "apply_envelope",
    "apply_template_envelope",
    "assess_recovery",
    "build_envelope",
    "build_template_envelope",
    "export_constants",
    "horizon_dates",
    "is_high_intensity",
    "is_leg_day",
    "normalize_group_name",
    "project_plan_slots",
    "project_template_slots",
    "recovery_band",
    "template_signals",
]
