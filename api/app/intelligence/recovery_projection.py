"""Projecao pura do contrato de prontidao muscular para um instante alvo
(PR-09, plano §5.8 / gap G3).

Funcoes sem I/O: recebem o snapshot "agora" de um grupo (item de
`MuscleRecoveryService.get_recovery_overview`) e devolvem o estado projetado
para `target`, reaplicando classify_readiness + reconcile_recovery_pct e o
contrato (`max_rpe`, `allow_failure`, `volume_multiplier`, `blocking_signals`).

Politica de sinais na projecao:
- `joint_pain`/`muscle_pain` PERSISTEM, qualquer que seja a distancia do
  alvo. A dor so deixa de valer quando uma sessao posterior do mesmo grupo
  for registrada sem dor (o VolumeService ja usa sempre a ultima sessao).
- Sinais de performance (`performance_ratio`, `excessive_rep_drop`) tambem
  PERSISTEM ate a proxima sessao do grupo - a MESMA regra do retrato do
  dashboard (M76 B3, decisao 2 do usuario em 2026-10-06; antes eram
  resetados a partir de 72h a frente, e a tela e a ficha discordavam do mesmo
  grupo). Politica unica: `MuscleRecoveryService.SIGNALS_POLICY`.
- RPE alto nao e sinal (2026-09-14): `rpe_anticipated` do snapshot e ignorado.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .muscle_recovery_service import RECOVERY_MAX_HOURS, MuscleRecoveryService
from .volume_service import DENSITY_WINDOW_HOURS

# --- "Bom para": quando o grupo volta a READY (2026-10-17) -----------------
# Horizonte da busca: a partir dele a projecao nao melhora mais - a densidade
# de 72h ja saiu (`DENSITY_WINDOW_HOURS`) e o tempo de recuperacao de uma
# sessao nunca passa de `RECOVERY_MAX_HOURS`. Se o grupo nao estiver READY
# nesse horizonte, quem o segura e um sinal (dor, queda de desempenho ou de
# repeticoes) que so uma nova sessao do grupo substitui (`SIGNALS_POLICY`):
# nao ha previsao por tempo.
READY_SEARCH_HORIZON_HOURS = max(RECOVERY_MAX_HOURS, DENSITY_WINDOW_HOURS)
# Resolucao do instante devolvido: a tela mostra hora e minuto.
READY_AT_RESOLUTION_MINUTES = 1
# Rotulo da coluna "Bom para" quando o grupo ja esta READY agora (inclusive
# grupo sem sessao na janela, que o modelo trata como pronto).
READY_NOW_LABEL = "Pronto"


def hours_until(now: datetime, target: datetime) -> float:
    """Horas de `now` ate `target`, nunca negativo (alvo no passado = agora)."""
    return max((target - now).total_seconds() / 3600.0, 0.0)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def project_recovery_state(item: dict[str, Any], now: datetime, target: datetime) -> dict[str, Any]:
    """Projeta um item de recuperacao para `target`. Nao altera os campos
    originais do snapshot (recovery_pct/readiness_status/recommendation
    continuam descrevendo "agora"); adiciona os `projected_*` historicos e o
    contrato plano, que passa a descrever o instante alvo."""
    ahead_hours = hours_until(now, target)
    is_projection = ahead_hours > 0

    session_dose = float(item.get("session_dose") or 0.0)
    dose_last_72h = float(item.get("dose_last_72h") or 0.0)
    estimated_hours = float(item.get("estimated_recovery_hours") or 0.0)
    hours_since_trained = float(item.get("hours_since_trained") or 0.0)

    # Dor e sinais de desempenho persistem (nunca neutralizados pela
    # projecao): so uma sessao posterior do grupo os substitui.
    muscle_pain = _optional_float(item.get("muscle_pain"))
    joint_pain = bool(item.get("joint_pain"))
    performance_ratio = _optional_float(item.get("performance_ratio"))
    excessive_rep_drop = bool(item.get("excessive_rep_drop"))

    if session_dose > 0 and estimated_hours > 0:
        total_elapsed_hours = hours_since_trained + ahead_hours
        time_recovery = MuscleRecoveryService.calculate_time_recovery(total_elapsed_hours, estimated_hours)
        projected_dose_last_72h = 0.0 if ahead_hours >= DENSITY_WINDOW_HOURS else dose_last_72h
        density_penalty = MuscleRecoveryService.calculate_density_penalty(projected_dose_last_72h)
        adjusted_readiness = MuscleRecoveryService.calculate_adjusted_readiness(time_recovery, density_penalty)
    else:
        # Sem dose registrada para este grupo: tempo/densidade nao limitam.
        adjusted_readiness = 100.0

    readiness_status = MuscleRecoveryService.classify_readiness(
        adjusted_readiness, performance_ratio, excessive_rep_drop, muscle_pain, joint_pain
    )
    negative_signals = MuscleRecoveryService.count_negative_signals(
        performance_ratio, excessive_rep_drop, muscle_pain
    )
    final_pct, signal_penalty = MuscleRecoveryService.reconcile_recovery_pct(
        adjusted_readiness, readiness_status, joint_pain, muscle_pain, negative_signals, performance_ratio
    )

    source_session_at = item.get("last_session_at") or item.get("last_trained_at_ts")
    contract = MuscleRecoveryService.build_readiness_contract(
        readiness_status,
        adjusted_readiness,
        performance_ratio,
        excessive_rep_drop,
        muscle_pain,
        joint_pain,
        source_session_at,
        item.get("rep_drop_details"),
    )

    return {
        **item,
        **contract,
        "projected_recovery_pct": round(final_pct, 1),
        "projected_adjusted_readiness": round(adjusted_readiness, 1),
        "projected_signal_penalty": signal_penalty,
        "projected_readiness_status": readiness_status,
        "projected_recommendation": MuscleRecoveryService.recommendation_for(readiness_status),
        "available_for_plan_date": readiness_status != "NO_DIRECT_WORK",
        "projected_for": target.isoformat(timespec="minutes") if is_projection else None,
    }


def project_recovery_items(items: list[dict[str, Any]], now: datetime, target: datetime) -> list[dict[str, Any]]:
    return [project_recovery_state(item, now, target) for item in items]


@dataclass(frozen=True)
class ReadyForecast:
    """Coluna "Bom para" de um grupo.

    - `ready_at`: primeiro instante (parede do usuario, naive, resolucao de
      `READY_AT_RESOLUTION_MINUTES`) em que a projecao classifica o grupo
      como READY. `None` quando ele ja esta READY ou quando nao ha previsao.
    - `label`: texto pronto quando nao ha instante a mostrar e o grupo ja esta
      pronto (`READY_NOW_LABEL`); `None` = sem previsao (a tela mostra "—")."""

    ready_at: datetime | None
    label: str | None


def ready_forecast(item: dict[str, Any], now: datetime) -> ReadyForecast:
    """Quando o grupo fica "pronto para novo estimulo": o instante em que o
    MESMO estado da tela (`readiness_status` = READY) passa a valer.

    Nao ha formula nova: cada instante candidato e classificado por
    `project_recovery_state` (tempo x densidade x sinais, com
    `classify_readiness`), e a busca binaria acha o primeiro minuto READY.
    A busca vale porque a projecao e monotona no tempo: o tempo de
    recuperacao so cresce, a densidade so cai (zera em `DENSITY_WINDOW_HOURS`)
    e os sinais nao mudam ate a proxima sessao.

    Nao e `now + remaining_hours`: `remaining_hours` vai ate 100% de tempo,
    mas o grupo ja e READY antes disso (prontidao ajustada >=
    `READY_MIN_ADJUSTED_READINESS`), e com sinal ativo ele nao fica READY nem
    com 100% de tempo. As duas contas contradiriam a coluna "Estado".

    `now` e a parede do usuario com que `hours_since_trained` foi medido
    (`MuscleRecoveryService.user_now` / `time_windows.user_now`)."""
    if str(item.get("readiness_status") or "READY") == "READY":
        return ReadyForecast(ready_at=None, label=READY_NOW_LABEL)
    # Sessao sem instante (`hours_since_trained` ausente): o retrato mediu o
    # tempo pelos dias (`days_since_trained * 24`), mas a projecao partiria de
    # zero hora e atrasaria o instante em ate N dias. Sem dado, sem previsao.
    if float(item.get("session_dose") or 0.0) > 0 and item.get("hours_since_trained") is None:
        return ReadyForecast(ready_at=None, label=None)

    def ready_after(steps: int) -> bool:
        target = now + timedelta(minutes=steps * READY_AT_RESOLUTION_MINUTES)
        return project_recovery_state(item, now, target)["projected_readiness_status"] == "READY"

    horizon = math.ceil(READY_SEARCH_HORIZON_HOURS * 60 / READY_AT_RESOLUTION_MINUTES)
    if not ready_after(horizon):
        return ReadyForecast(ready_at=None, label=None)

    # Invariante: READY em `high`; nao READY em `low` (agora o grupo nao esta).
    low, high = 0, horizon
    while high - low > 1:
        middle = (low + high) // 2
        if ready_after(middle):
            high = middle
        else:
            low = middle
    ready_at = now + timedelta(minutes=high * READY_AT_RESOLUTION_MINUTES)
    # Minuto cheio PARA CIMA (a tela mostra hh:mm): mais tarde continua READY,
    # porque a projecao e monotona; para baixo cairia antes do instante READY.
    if ready_at.second or ready_at.microsecond:
        ready_at = ready_at.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return ReadyForecast(ready_at=ready_at, label=None)
