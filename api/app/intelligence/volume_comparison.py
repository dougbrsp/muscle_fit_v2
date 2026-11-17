"""Variacao e direcao de volume entre periodos: dono unico (#188, etapa 3).

Antes o frontend calculava a diferenca e a seta entre dois periodos. A regra
mora aqui; as rotas devolvem o valor pronto e a tela so desenha. Os valores
das tolerancias sao os que o cliente usava (nenhum valor mudou ao mover):

- `VOLUME_STABLE_TOLERANCE` = 0.0001: `getTrendDirection` (epsilon padrao) de
  `VolumeGrupamentosClient.tsx`, usado para volume, series, series
  equivalentes e ultima carga;
- `RPE_STABLE_TOLERANCE` = 0.05: `TrendValue ... epsilon={0.05}` das colunas
  de RPE medio na mesma tela;
- `EFFECTIVE_SETS_STABLE_TOLERANCE` = 0.01: `sessionDelta` de
  `lib/dashboard-muscles.ts` (series efetivas sao fracionarias; 3.0000001
  contra 3.0 e a mesma coisa).

- `TREND_NO_TOLERANCE` = 0: `getTrendBadge` da aba de tendencia (atual x
  anterior) rotulava "Aumentou"/"Diminuiu" com qualquer diferenca.

`|delta| <= tolerancia` e "estavel" (limite inclusivo, como no cliente).

Janelas atual x anterior da aba de tendencia (`granularity_windows`): porte
fiel de `buildWindow` do cliente, agora com o "hoje" local do usuario no
servidor.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Literal, Optional

VOLUME_STABLE_TOLERANCE = 0.0001
RPE_STABLE_TOLERANCE = 0.05
EFFECTIVE_SETS_STABLE_TOLERANCE = 0.01
TREND_NO_TOLERANCE = 0.0

# Granularidades da aba de tendencia de /volume-grupamentos.
GRANULARITY_D7 = "d7"
GRANULARITY_WEEK = "week"
GRANULARITY_MONTH = "month"
PERIOD_GRANULARITIES: tuple[str, ...] = (GRANULARITY_D7, GRANULARITY_WEEK, GRANULARITY_MONTH)
# Tamanho de cada janela "D-7" (hoje e os 6 dias anteriores).
D7_WINDOW_DAYS = 7


def week_start_of(day: date) -> date:
    """Segunda-feira da semana de `day` (a semana comeca na segunda)."""
    return day - timedelta(days=day.weekday())


def granularity_windows(today: date, granularity: str) -> dict[str, dict[str, date]]:
    """`{current: {from, to}, previous: {from, to}}` (limites inclusivos).

    - d7: ultimos 7 dias x os 7 anteriores;
    - week: segunda corrente ate hoje x o mesmo numero de dias da semana anterior;
    - month: dia 1 ate hoje x o mesmo numero de dias do mes anterior, truncado
      ao tamanho do mes anterior."""
    if granularity == GRANULARITY_D7:
        current_from = today - timedelta(days=D7_WINDOW_DAYS - 1)
        previous_to = current_from - timedelta(days=1)
        previous_from = previous_to - timedelta(days=D7_WINDOW_DAYS - 1)
    elif granularity == GRANULARITY_WEEK:
        current_from = week_start_of(today)
        span = (today - current_from).days + 1
        previous_to = current_from - timedelta(days=1)
        previous_from = previous_to - timedelta(days=span - 1)
    elif granularity == GRANULARITY_MONTH:
        current_from = today.replace(day=1)
        span = today.day
        previous_month_end = current_from - timedelta(days=1)
        previous_from = previous_month_end.replace(day=1)
        previous_to = previous_from + timedelta(days=min(span, previous_month_end.day) - 1)
    else:
        raise ValueError(f"granularidade invalida: {granularity!r}")
    return {
        "current": {"from": current_from, "to": today},
        "previous": {"from": previous_from, "to": previous_to},
    }

# Casas do delta publicado (so apresentacao; a direcao usa a diferenca crua).
DELTA_DECIMALS = 2

Direction = Literal["up", "down", "stable"]


def period_delta(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    """`current - previous`, arredondado a `DELTA_DECIMALS`; `None` se faltar um."""
    if current is None or previous is None:
        return None
    return round(float(current) - float(previous), DELTA_DECIMALS)


def period_direction(
    current: Optional[float], previous: Optional[float], tolerance: float
) -> Optional[Direction]:
    """`up`/`down`/`stable` de `current` contra `previous`; `None` se faltar um."""
    if current is None or previous is None:
        return None
    delta = float(current) - float(previous)
    if abs(delta) <= tolerance:
        return "stable"
    return "up" if delta > 0 else "down"


# Tendencia por grupo muscular (`/analytics/muscle-groups-mapped-trend`, #188):
# antes o cliente calculava (`pressureRows` de `lib/muscle-group-trend.ts`) e
# pintava por `variation > 5` / `variation < -5` (`MuscleGroupTrend.tsx`).
# Mesmo valor e comparacoes estritas ao mover.
GROUP_TREND_VARIATION_THRESHOLD_PCT = 5.0
TREND_TONE_UP = "up"
TREND_TONE_DOWN = "down"
TREND_TONE_STABLE = "stable"
# Casas de `variation_pct` publicado (a tela mostra 1 casa; o tom usa o valor cru).
VARIATION_PCT_DECIMALS = 4


def variation_pct(latest: float, previous: float) -> Optional[float]:
    """`(latest - previous) / previous * 100`; `None` com `previous <= 0` (sem base)."""
    if previous is None or float(previous) <= 0:
        return None
    return (float(latest or 0) - float(previous)) / float(previous) * 100


def variation_tone(variation: Optional[float]) -> Optional[str]:
    """Tom da variacao: estritamente acima do limiar sobe, abaixo de -limiar desce."""
    if variation is None:
        return None
    if variation > GROUP_TREND_VARIATION_THRESHOLD_PCT:
        return TREND_TONE_UP
    if variation < -GROUP_TREND_VARIATION_THRESHOLD_PCT:
        return TREND_TONE_DOWN
    return TREND_TONE_STABLE


def _collation_key(text: str) -> tuple[str, str]:
    """Aproxima `localeCompare(..., 'pt-BR')` do cliente: sem acento e sem caixa primeiro."""
    base = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return base.casefold(), text


def pressure_rows(trend_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Ranking "pressao no periodo mais recente" (antes `pressureRows` do cliente).

    Ultimo e penultimo periodo distintos (ordem crescente); por grupo: `latest`,
    `previous` (0 se ausente), `total` (soma dos `value` publicados na janela),
    `variation_pct` e `tone` deste dono. Ordem: `latest` desc,
    `total` desc, nome (a ordem de opcoes do cliente)."""
    periods = sorted({r["period"] for r in trend_rows})
    latest_period = periods[-1] if periods else None
    previous_period = periods[-2] if len(periods) > 1 else None
    index = {(r["period"], r["group"]): float(r.get("value") or 0) for r in trend_rows}
    totals: dict[str, float] = defaultdict(float)
    for r in trend_rows:
        totals[r["group"]] += float(r.get("value") or 0)
    rows = []
    for group, total in totals.items():
        latest = index.get((latest_period, group), 0.0)
        previous = index.get((previous_period, group), 0.0)
        variation = variation_pct(latest, previous)
        rows.append(
            {
                "group": group,
                "latest": latest,
                "previous": previous,
                "total": round(total, 2),
                "variation_pct": round(variation, VARIATION_PCT_DECIMALS) if variation is not None else None,
                "tone": variation_tone(variation),
            }
        )
    rows.sort(key=lambda r: (-r["latest"], -totals[r["group"]], _collation_key(r["group"])))
    return {"latest_period": latest_period, "previous_period": previous_period, "pressure_rows": rows}
