"""1RM estimado (e1RM) — dono único da regra (M76, Lote 3a).

Pergunta que este módulo responde: dada uma série (carga, repetições, RPE),
qual é o 1RM estimado, e quando esse número é confiável?

- Epley com RIR: `carga × (1 + (reps + RIR) / 30)`, `RIR = max(0, 10 − RPE)`
  (`epley-rir-v1`, o mesmo número desde a Fase 1 do dashboard).
- Confiável só com até `E1RM_MAX_RELIABLE_REPS` (12) repetições: acima disso
  o Epley superestima e o e1RM não decide nada sozinho (regra única de
  progressão, `dashboard/comparability.py`).
- Série sem RPE (decisão do usuário, 2026-10-06, "meio-termo"):
  `MissingRpePolicy.SKIP` (padrão) — sem RPE não há RIR, e sem RIR não há
  e1RM; `MissingRpePolicy.RIR_ZERO` — assume RIR 0 (Epley puro). Só o
  Desempenho por grupo (`muscle_group_performance`) usa `RIR_ZERO`, para não
  perder as ~12% de séries de trabalho sem RPE.
- Dose de recuperação sem reps/RPE: `DOSE_REPS_WHEN_MISSING` (8) e
  `DOSE_RPE_WHEN_MISSING` (7,0) — o que a dose assume quando a série não
  registrou o dado (não é e1RM, mas é a mesma pergunta "o que fazer sem RPE",
  então mora aqui, num lugar só).

Antes, a fórmula morava em `MuscleRecoveryService.estimated_1rm` e era
reescrita em SQL (`analytics._SQL_RIR`) e inline
(`exercise_performance.paired_reps_epley_ratio`). Agora todos leem daqui; o
`MuscleRecoveryService` só delega.

Módulo PURO: sem banco, sem FastAPI, sem import do resto do app.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Final, Optional

# Teto de confiabilidade do Epley. `metrics_registry.THRESHOLDS
# ["e1rm_max_reliable_reps"]` reexporta este valor (não tem um número seu).
E1RM_MAX_RELIABLE_REPS: Final[int] = 12

# Dose de recuperação quando a série não registrou reps/RPE
# (`VolumeService`: dose por sessão e volume por split).
DOSE_REPS_WHEN_MISSING: Final[int] = 8
DOSE_RPE_WHEN_MISSING: Final[float] = 7.0


class MissingRpePolicy(str, Enum):
    """O que fazer com uma série sem RPE."""

    SKIP = "skip"  # sem RPE -> sem e1RM (padrão)
    RIR_ZERO = "rir_zero"  # sem RPE -> RIR 0 (só Desempenho por grupo)


def _number(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def rir_from_rpe(rpe: float) -> float:
    """RIR = 10 − RPE, nunca negativo (RPE 10 ou acima -> RIR 0)."""
    return max(0.0, 10.0 - float(rpe))


def resolve_rir(rpe: Any, missing_rpe: MissingRpePolicy = MissingRpePolicy.SKIP) -> Optional[float]:
    """RIR da série. Sem RPE: `None` (`SKIP`) ou `0.0` (`RIR_ZERO`)."""
    parsed = _number(rpe)
    if parsed is not None:
        return rir_from_rpe(parsed)
    return 0.0 if missing_rpe is MissingRpePolicy.RIR_ZERO else None


def epley_multiplier(reps: float, rir: float) -> float:
    """Fator de Epley com RIR: `1 + (reps + RIR) / 30`. Aceita reps médias
    (float) — a magnitude por reps emparelhadas na MESMA carga é a razão de
    dois fatores (`exercise_performance.paired_reps_epley_ratio`)."""
    return 1.0 + (float(reps) + float(rir)) / 30.0


def estimated_1rm(
    load_kg: Any,
    reps: Any,
    rpe: Any,
    *,
    missing_rpe: MissingRpePolicy = MissingRpePolicy.SKIP,
) -> Optional[float]:
    """e1RM da série (Epley + RIR). `None` sem carga/reps positivas, ou sem
    RPE na política `SKIP`. Não aplica o teto de reps (ver `reliable_e1rm`)."""
    load = _number(load_kg)
    repetitions = _number(reps)
    if load is None or repetitions is None:
        return None
    repetitions = int(repetitions)
    if load <= 0 or repetitions <= 0:
        return None
    rir = resolve_rir(rpe, missing_rpe)
    if rir is None:
        return None
    return load * epley_multiplier(repetitions, rir)


def is_reliable_reps(reps: Any, max_reps: int = E1RM_MAX_RELIABLE_REPS) -> bool:
    """A série tem até `max_reps` repetições (o Epley é confiável)?"""
    parsed = _number(reps)
    return parsed is not None and 0 < parsed <= int(max_reps)


def reliable_e1rm(
    load_kg: Any,
    reps: Any,
    rpe: Any,
    *,
    missing_rpe: MissingRpePolicy = MissingRpePolicy.SKIP,
    max_reps: int = E1RM_MAX_RELIABLE_REPS,
) -> Optional[float]:
    """e1RM só quando confiável: `estimated_1rm` com até `max_reps` reps,
    senão `None`."""
    if not is_reliable_reps(reps, max_reps):
        return None
    return estimated_1rm(load_kg, reps, rpe, missing_rpe=missing_rpe)


def dose_inputs(reps: Any, rpe: Any) -> tuple[int, float]:
    """(reps, RPE) que a dose de recuperação usa: o registrado, ou
    `DOSE_REPS_WHEN_MISSING` / `DOSE_RPE_WHEN_MISSING` quando falta."""
    reps_value = _number(reps)
    rpe_value = _number(rpe)
    return (
        int(reps_value) if reps_value is not None else DOSE_REPS_WHEN_MISSING,
        rpe_value if rpe_value is not None else DOSE_RPE_WHEN_MISSING,
    )
