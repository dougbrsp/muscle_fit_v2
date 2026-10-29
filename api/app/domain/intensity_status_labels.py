"""Rótulos pt-BR de `intensity_status` e `safety_event` (seção 5.7; Lote 1/P1
da investigação RPE 10, 2026-10-11).

Dono ÚNICO do texto que o usuário lê para cada código de intensidade/evento.
Os CÓDIGOS são os do motor (`progression_engine`); aqui só mora o rótulo e a
classe do evento (segurança x informativo). O frontend recebe tudo por
`frontend/src/lib/motor-constants.generated.json`, gerado por
`api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar` (o teste falha se o
JSON divergir daqui); `frontend/src/lib/intensity-status.ts` só escolhe cor e
monta o banner.

set-eval-v5 (2026-10-12): o status de intensidade avalia SO o esforco (RPE x
alvo efetivo), e o rotulo agora diz isso ("Esforço atingido", nao "Alvo
atingido") - o rotulo antigo era lido pela IA por serie como "alvo geral
atingido" mesmo com 3 reps numa faixa 12-15. O MESMO rotulo vale para a serie
(Modo Treino) e para a sessao (dashboard /progressao): o status e o mesmo
(`_intensity_status_core`). A posicao das reps contra a faixa
(`progression_engine.set_reps_position`) tem rotulo proprio aqui, ao lado.

Python puro, sem I/O.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Final, Mapping, Optional

from .progression_engine import (
    INTENSITY_ABOVE_SAFE_CEILING,
    INTENSITY_BELOW_TARGET,
    INTENSITY_FAILURE_NOT_ALLOWED_REACHED_CEILING,
    INTENSITY_REACHED,
    REPS_ABOVE_TOP,
    REPS_AT_TOP,
    REPS_BELOW_FLOOR,
    REPS_IN_RANGE,
    SAFETY_ALERT_EVENTS,
    SAFETY_EVENT_RPE_ABOVE_CEILING,
    SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET,
)

INTENSITY_STATUS_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        INTENSITY_REACHED: "Esforço atingido",
        INTENSITY_BELOW_TARGET: "Esforço abaixo do alvo",
        INTENSITY_FAILURE_NOT_ALLOWED_REACHED_CEILING: "Esforço atingido (falha não permitida)",
        INTENSITY_ABOVE_SAFE_CEILING: "RPE acima do teto seguro",
    }
)

SAFETY_EVENT_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        SAFETY_EVENT_RPE_ABOVE_CEILING: "RPE acima do teto seguro",
        SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET: "RPE acima do alvo do dia",
    }
)


# Posicao das reps da serie contra a faixa (`progression_engine.set_reps_position`).
REPS_POSITION_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        REPS_BELOW_FLOOR: "Reps abaixo da faixa",
        REPS_IN_RANGE: "Reps dentro da faixa",
        REPS_AT_TOP: "Reps no teto da faixa",
        REPS_ABOVE_TOP: "Reps acima da faixa",
    }
)


def intensity_status_label(status: Optional[str]) -> Optional[str]:
    """Rótulo de um `intensity_status` (None sem status ou código desconhecido)."""
    return INTENSITY_STATUS_LABELS.get(status) if status else None


def safety_event_label(event: Optional[str]) -> Optional[str]:
    """Rótulo de um `safety_event` gravado (None sem evento)."""
    return SAFETY_EVENT_LABELS.get(event) if event else None


def reps_position_label(position: Optional[str]) -> Optional[str]:
    """Rotulo da posicao das reps contra a faixa (None sem faixa/reps)."""
    return REPS_POSITION_LABELS.get(position) if position else None


def export_for_frontend() -> dict[str, Any]:
    """Bloco `intensity` de `motor-constants.generated.json`."""
    return {
        "intensity_status_labels": dict(INTENSITY_STATUS_LABELS),
        "safety_event_labels": dict(SAFETY_EVENT_LABELS),
        "reps_position_labels": dict(REPS_POSITION_LABELS),
        "safety_event_rpe_above_ceiling": SAFETY_EVENT_RPE_ABOVE_CEILING,
        "safety_event_rpe_above_recovery_target": SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET,
        "safety_alert_events": sorted(SAFETY_ALERT_EVENTS),
    }
