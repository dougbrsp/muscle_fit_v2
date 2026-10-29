"""Aderência do cardio: planejado × realizado (M98 C2; plano
`docs/cardio/PLANO_MOTOR_CARDIO_2026-10-26.md` §4.1 e §C2).

Dono ÚNICO de:

- as MÉTRICAS da aderência de uma sessão de cardio prescrita no Modelo,
  comparada com a atividade de cardio realizada (Intervals.icu):
  - tempo cumprido: tempo realizado / tempo total prescrito
    (`cardio_prescription.total_seconds`: aquecimento + parte principal +
    recuperação);
  - Normal: segundos na zona-alvo; % do tempo realizado na zona-alvo e % da
    parte principal prescrita cumprida na zona-alvo;
  - HIIT: segundos na zona ALTA ou acima (os tiros) e na faixa da zona BAIXA
    até a zona abaixo da alta (os intervalos), cada um contra o prescrito
    (`séries × segundos`);
- os LIMIARES do status (`ADHERENT`, `PARTIAL`, `LOW`, `NOT_DONE`), em
  constantes nomeadas (aprovados pelo usuário em 27/10/2026);
- QUANDO um dia de descanso com cardio prescrito entra na conta
  (`rest_day_due`);
- o resumo por semana (semana começa na segunda, datas locais do usuário).

Fora daqui:

- o que é cardio: `intervals_activity_kind` (dono único);
- qual atividade realizada casa com a sessão: `intervals_hr_match` (o MESMO
  critério de horário do casamento treino × WeightTraining, ancorado no fim
  da sessão de força);
- tempo em zona Z1–Z5 a partir do Intervals: `hr_zones.app_zone_seconds`.

O tempo em zona do Intervals é da atividade inteira (sem fases): aquecimento e
recuperação entram no total, por isso a zona-alvo é medida contra a parte
principal prescrita e limitada a 100%.

Âncora do casamento (M98 C4). Duas, e só duas, ambas executadas por
`intervals_hr_match`:

- `ANCHOR_STRENGTH_END` (em uso desde a C2): dia com sessão de força; o cardio
  é casado pelo horário (regra 2 de `intervals_hr_match.match_session`),
  ancorado no fim da força;
- `ANCHOR_LOCAL_DAY` (decisão do usuário de 27/10/2026; consumidor:
  `cardio_adherence_service`): cardio prescrito num slot de DESCANSO, sem
  sessão de força no dia. O slot do dia passado vem da FICHA SALVA PARA A
  DATA; sem ficha, o dia não é avaliado. A âncora é o dia LOCAL inteiro
  (`intervals_hr_match.match_local_day`): a atividade de cardio do dia; mais
  de uma = a mais longa, `MATCHED_AMBIGUOUS`. A atividade casada com a força
  de um dia vizinho não entra.

Módulo PURO: sem banco, sem FastAPI, sem I/O. Mapa:
`docs/motores/FONTES_DA_VERDADE.md` §69.3.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final, Iterable, Mapping, Optional, Sequence

from . import cardio_prescription as cp
from .hr_zones import HR_ZONE_NUMBERS, zone_label, zone_number

# --- Limiares do status (%, aprovados pelo usuário em 27/10/2026) ----------
# ADHERENT: tempo cumprido >= ADHERENT_TIME_MIN_PCT e, quando há tempo em zona
# e zona prescrita, a zona (alvo no Normal, alta no HIIT) cumprida >=
# ADHERENT_ZONE_MIN_PCT.
ADHERENT_TIME_MIN_PCT: Final[int] = 90
ADHERENT_ZONE_MIN_PCT: Final[int] = 70
# PARTIAL: fez cardio, mas abaixo do ADHERENT, com tempo cumprido >= este.
PARTIAL_TIME_MIN_PCT: Final[int] = 50

STATUS_ADHERENT: Final[str] = "adherent"
STATUS_PARTIAL: Final[str] = "partial"
STATUS_LOW: Final[str] = "low"
STATUS_NOT_DONE: Final[str] = "not_done"
STATUSES: Final[tuple[str, ...]] = (STATUS_ADHERENT, STATUS_PARTIAL, STATUS_LOW, STATUS_NOT_DONE)
# Rótulo de exibição de cada status (a tela não traduz código).
STATUS_LABELS: Final[dict[str, str]] = {
    STATUS_ADHERENT: "Cumprido",
    STATUS_PARTIAL: "Parcial",
    STATUS_LOW: "Abaixo",
    STATUS_NOT_DONE: "Não feito",
}

# Casas decimais dos percentuais devolvidos.
PCT_DECIMALS: Final[int] = 1

# Âncoras do casamento planejado × realizado (ver docstring do módulo).
ANCHOR_STRENGTH_END: Final[str] = "strength_end"
ANCHOR_LOCAL_DAY: Final[str] = "local_day"  # dia de descanso (27/10/2026)


@dataclass(frozen=True)
class RealizedCardio:
    """Atividade de cardio realizada, já casada com a sessão.

    `zone_seconds`: segundos em Z1..Z5 do app (`hr_zones.app_zone_seconds`);
    None = sem tempo em zona."""

    seconds: float
    zone_seconds: Optional[tuple[int, ...]] = None
    intervals_activity_id: Optional[int] = None
    sport_type: Optional[str] = None
    match_status: Optional[str] = None


def _pct(part: float, whole: float, *, cap: bool = False) -> Optional[float]:
    if whole <= 0:
        return None
    value = 100.0 * part / whole
    if cap:
        value = min(value, 100.0)
    return round(value, PCT_DECIMALS)


def _seconds_in(zone_seconds: Sequence[int], zones: Iterable[int]) -> int:
    wanted = set(zones)
    return int(sum(s for z, s in zip(HR_ZONE_NUMBERS, zone_seconds) if z in wanted))


def _status(time_pct: Optional[float], zone_pct: Optional[float]) -> str:
    if time_pct is None:
        return STATUS_LOW
    if time_pct >= ADHERENT_TIME_MIN_PCT and (zone_pct is None or zone_pct >= ADHERENT_ZONE_MIN_PCT):
        return STATUS_ADHERENT
    if time_pct >= PARTIAL_TIME_MIN_PCT:
        return STATUS_PARTIAL
    return STATUS_LOW


def _zone_metrics_continuous(item: Any, realized: RealizedCardio, planned_main: float) -> dict[str, Any]:
    target = zone_number(cp.field_value(item, cp.TARGET_ZONE_FIELD))
    out: dict[str, Any] = {
        "target_zone": target,
        "target_zone_label": zone_label(target) if target is not None else None,
        "target_zone_seconds": None,
        "target_zone_share_pct": None,
        "target_zone_completion_pct": None,
    }
    if target is None or realized.zone_seconds is None:
        return out
    in_target = _seconds_in(realized.zone_seconds, (target,))
    out["target_zone_seconds"] = in_target
    out["target_zone_share_pct"] = _pct(in_target, sum(realized.zone_seconds))
    out["target_zone_completion_pct"] = _pct(in_target, planned_main, cap=True)
    return out


def _zone_metrics_hiit(item: Any, realized: RealizedCardio) -> dict[str, Any]:
    low_zone = zone_number(cp.field_value(item, cp.LOW_ZONE_FIELD))
    high_zone = zone_number(cp.field_value(item, cp.HIGH_ZONE_FIELD))
    planned_high, planned_low = cp.hiit_phase_seconds(item)
    out: dict[str, Any] = {
        "high_zone": high_zone,
        "low_zone": low_zone,
        "planned_high_seconds": int(planned_high),
        "planned_low_seconds": int(planned_low),
        "high_zone_seconds": None,
        "low_zone_seconds": None,
        "high_zone_completion_pct": None,
        "low_zone_completion_pct": None,
    }
    if high_zone is None or low_zone is None or realized.zone_seconds is None:
        return out
    high = _seconds_in(realized.zone_seconds, (z for z in HR_ZONE_NUMBERS if z >= high_zone))
    low = _seconds_in(realized.zone_seconds, (z for z in HR_ZONE_NUMBERS if low_zone <= z < high_zone))
    out.update(
        high_zone_seconds=high,
        low_zone_seconds=low,
        high_zone_completion_pct=_pct(high, planned_high, cap=True),
        low_zone_completion_pct=_pct(low, planned_low, cap=True),
    )
    return out


def evaluate(item: Any, realized: Optional[RealizedCardio]) -> dict[str, Any]:
    """Aderência de UM item de cardio prescrito contra a atividade realizada
    (None = nenhuma atividade de cardio casada)."""
    mode = cp.mode_of(item)
    planned_total = cp.total_seconds(item)
    planned_main = cp.main_seconds(item)
    out: dict[str, Any] = {
        "mode": mode.value,
        "prescription_text": cp.describe(item),
        "planned_seconds": int(math.floor(planned_total + 0.5)),
        "planned_main_seconds": int(math.floor(planned_main + 0.5)),
        "realized_seconds": None,
        "time_completion_pct": None,
        "has_zone_data": False,
        "realized": None,
    }
    if realized is None:
        out["status"] = STATUS_NOT_DONE
        return out

    out["realized_seconds"] = int(math.floor(realized.seconds + 0.5))
    out["time_completion_pct"] = _pct(realized.seconds, planned_total)
    out["has_zone_data"] = realized.zone_seconds is not None
    out["realized"] = {
        "intervals_activity_id": realized.intervals_activity_id,
        "sport_type": realized.sport_type,
        "match_status": realized.match_status,
        "zone_seconds": (
            {zone_label(z): s for z, s in zip(HR_ZONE_NUMBERS, realized.zone_seconds)}
            if realized.zone_seconds is not None
            else None
        ),
    }
    if mode is cp.CardioMode.HIIT:
        zone = _zone_metrics_hiit(item, realized)
        zone_pct = zone["high_zone_completion_pct"]
    else:
        zone = _zone_metrics_continuous(item, realized, planned_main)
        zone_pct = zone["target_zone_completion_pct"]
    out["zones"] = zone
    out["status"] = _status(out["time_completion_pct"], zone_pct)
    return out


def rest_day_due(day: date, today: date, *, matched: bool) -> bool:
    """Dia de descanso com cardio prescrito entra na aderência? Dia passado,
    sempre (feito ou `NOT_DONE`); o dia de hoje só quando o cardio já casou
    (o dia não terminou: sem atividade ainda não é "não feito"); dia futuro,
    nunca."""
    if day < today:
        return True
    return day == today and matched


def week_start(day: date) -> date:
    """Segunda-feira da semana de `day` (data local do usuário)."""
    return day - timedelta(days=day.weekday())


def summarize_weeks(sessions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Resumo por semana das sessões avaliadas (`local_date` + o `evaluate`
    em `adherence`): sessões com cardio prescrito, feitas, por status, tempo
    prescrito e realizado e o tempo cumprido da semana."""
    weeks: dict[date, dict[str, Any]] = {}
    for session in sessions:
        day = session["local_date"]
        evaluation = session["adherence"]
        week = weeks.setdefault(
            week_start(day),
            {"planned_sessions": 0, "done_sessions": 0, "planned_seconds": 0, "realized_seconds": 0,
             "by_status": dict.fromkeys(STATUSES, 0)},
        )
        week["planned_sessions"] += 1
        week["planned_seconds"] += evaluation["planned_seconds"]
        week["by_status"][evaluation["status"]] += 1
        if evaluation["status"] != STATUS_NOT_DONE:
            week["done_sessions"] += 1
            week["realized_seconds"] += evaluation["realized_seconds"] or 0
    return [
        {
            "week_start": start.isoformat(),
            **data,
            "time_completion_pct": _pct(data["realized_seconds"], data["planned_seconds"]),
        }
        for start, data in sorted(weeks.items())
    ]


__all__ = [
    "ANCHOR_LOCAL_DAY",
    "ANCHOR_STRENGTH_END",
    "ADHERENT_TIME_MIN_PCT",
    "ADHERENT_ZONE_MIN_PCT",
    "PARTIAL_TIME_MIN_PCT",
    "PCT_DECIMALS",
    "RealizedCardio",
    "STATUSES",
    "STATUS_LABELS",
    "STATUS_ADHERENT",
    "STATUS_LOW",
    "STATUS_NOT_DONE",
    "STATUS_PARTIAL",
    "evaluate",
    "rest_day_due",
    "summarize_weeks",
    "week_start",
]
