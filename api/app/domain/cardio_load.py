"""Esforço cardiovascular estimado: EPOC e PAI acumulados de 7 dias (M99).

Dono ÚNICO de:

- os pesos por zona de FC do Intervals que estimam o EPOC (Firstbeat) e o PAI
  (Zepp) (`EPOC_ZONE_WEIGHTS`, `PAI_ZONE_WEIGHTS`);
- a janela móvel (`ROLLING_WINDOW_DAYS`) e a janela de LEITURA que ela exige
  (`activity_read_start`: quem lê as atividades não faz a conta);
- a fórmula: soma, nas atividades dos dias da janela que termina no dia
  (inclusive), de minutos na zona × peso da zona;
- o arredondamento (inteiro) e a regra de "sem valor": janela sem nenhuma
  atividade com tempo em zona -> None (nada é inventado);
- a versão do modelo (`CARDIO_LOAD_MODEL_VERSION`), que acompanha o valor.

EPOC e PAI são métricas PROPRIETÁRIAS: o app só ESTIMA, calibrado contra os
valores que o usuário anotou do relógio (`manual_effort_metrics`, tabela
histórica). A calibração mostrou que o anotado é o ACUMULADO DE 7 DIAS que o
relógio exibe, não o valor do dia (decisão revista pelo usuário em
27/10/2026). Calibração: `tools/calibrar_cardio_load.py` (só leitura).

As zonas são as 7 do Intervals (Z1–Z4, Z5a, Z5b, Z5c; `icu_hr_zone_times`),
NÃO as 5 do app: o peso da Z5c difere do da Z5a. A Z1 do Intervals é tudo
abaixo do 1º limite (inclui repouso entre séries). Atividade com outra
quantidade de zonas não entra (nada é inventado).

Mapa: `docs/motores/FONTES_DA_VERDADE.md` §3.4 e §70. Guarda:
`api/tests/unit/test_guarda_fontes_da_verdade.py` (`pesos_esforco_paralelos`).

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final, Iterable, Optional, Sequence

from . import hr_zones

CARDIO_LOAD_MODEL_VERSION: Final[str] = "cardio_load_v1_7d"

# Janela móvel: o valor do dia D soma as atividades de D-6 a D (inclusive).
ROLLING_WINDOW_DAYS: Final[int] = 7

# Quantidade de zonas do Intervals que o modelo entende (padrão LTHR).
INTERVALS_ZONE_COUNT: Final[int] = 7

SECONDS_PER_MINUTE: Final[int] = 60

# Pesos POR MINUTO em cada zona do Intervals (Z1, Z2, Z3, Z4, Z5a, Z5b, Z5c),
# >= 0 e não decrescentes. Calibrados em 38 dias (24/07–30/08/2026, user 1)
# por mínimos quadrados não negativos com monotonia, janela de 7 dias:
# leave-one-out MAPE 11,5% (EPOC) e 6,4% (PAI). `tools/calibrar_cardio_load.py`.
EPOC_ZONE_WEIGHTS: Final[tuple[float, ...]] = (0.26, 0.90, 0.90, 3.92, 3.92, 3.92, 16.19)
PAI_ZONE_WEIGHTS: Final[tuple[float, ...]] = (0.02, 0.64, 0.90, 1.11, 1.11, 1.11, 6.03)


@dataclass(frozen=True)
class EffortEstimate:
    local_date: date
    epoc_7d_estimated: Optional[int]
    pai_7d_estimated: Optional[int]
    model_version: str = CARDIO_LOAD_MODEL_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "local_date": self.local_date.isoformat(),
            "epoc_7d_estimated": self.epoc_7d_estimated,
            "pai_7d_estimated": self.pai_7d_estimated,
            "model_version": self.model_version,
        }


def activity_read_start(first_day: date) -> date:
    """Primeiro dia de atividades que precisa ser lido para fechar a janela
    de `first_day`."""
    return first_day - timedelta(days=ROLLING_WINDOW_DAYS - 1)


def activity_zone_seconds(value: Any) -> Optional[tuple[int, ...]]:
    """Segundos nas 7 zonas do Intervals de uma atividade; outra forma,
    quantidade de zonas ou tempo total zero -> None."""
    seconds = hr_zones.parse_intervals_zone_seconds(value)
    if seconds is None or len(seconds) != INTERVALS_ZONE_COUNT or sum(seconds) <= 0:
        return None
    return tuple(seconds)


def weighted_load(zone_seconds: Sequence[int], weights: Sequence[float]) -> float:
    """Σ minutos_z × peso_z (sem arredondar)."""
    return sum(s / SECONDS_PER_MINUTE * w for s, w in zip(zone_seconds, weights))


def _round(value: float) -> int:
    return int(value + 0.5)


def estimate_days(
    activities: Iterable[tuple[date, Any]], first_day: date, last_day: date
) -> list[EffortEstimate]:
    """Estimativa de 7 dias para cada dia de `first_day` a `last_day`.

    `activities`: (dia local da atividade, segundos por zona do Intervals);
    deve cobrir de `activity_read_start(first_day)` a `last_day`."""
    per_day: dict[date, list[float]] = {}
    for day, raw in activities:
        seconds = activity_zone_seconds(raw)
        if seconds is None:
            continue
        acc = per_day.setdefault(day, [0.0, 0.0])
        acc[0] += weighted_load(seconds, EPOC_ZONE_WEIGHTS)
        acc[1] += weighted_load(seconds, PAI_ZONE_WEIGHTS)

    out: list[EffortEstimate] = []
    day = first_day
    while day <= last_day:
        window = [per_day[d] for k in range(ROLLING_WINDOW_DAYS) if (d := day - timedelta(days=k)) in per_day]
        if window:
            out.append(EffortEstimate(day, _round(sum(w[0] for w in window)), _round(sum(w[1] for w in window))))
        else:
            out.append(EffortEstimate(day, None, None))
        day += timedelta(days=1)
    return out
