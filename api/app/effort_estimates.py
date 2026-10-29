"""Leitura do EPOC/PAI de 7 dias ESTIMADO (M99).

Só liga o banco ao motor: lê o tempo em zona das atividades do Intervals na
janela que o motor pede (`cardio_load.activity_read_start`) e devolve a
estimativa pronta de cada dia. Nenhum peso, janela ou arredondamento aqui —
tudo isso é de `domain/cardio_load.py`.

Consumidores: `GET /effort-metrics` (Indicadores físicos), o contexto do
agente de cardio (v1 e v3, e o relatório que reaproveita o v1) e o card de
PAI do dashboard de cardio.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable, Sequence

from . import intervals_store
from .domain import cardio_load

# fetch(sql, params) -> linhas como dicts.
Fetch = Callable[[str, tuple[Any, ...]], Sequence[dict[str, Any]]]


def load_estimates(fetch: Fetch, user_id: int, first_day: date, last_day: date) -> list[cardio_load.EffortEstimate]:
    """Estimativa de cada dia de `first_day` a `last_day` (ordem crescente)."""
    rows = fetch(
        intervals_store.ZONE_SECONDS_SQL,
        (int(user_id), cardio_load.activity_read_start(first_day).isoformat(), last_day.isoformat()),
    )
    activities = intervals_store.zone_seconds_rows([dict(row) for row in rows or []])
    return cardio_load.estimate_days(activities, first_day, last_day)


def load_recent(fetch: Fetch, user_id: int, today: date, days: int) -> list[dict[str, Any]]:
    """Os últimos `days` dias até `today` (inclusive), do mais recente para o
    mais antigo, só os dias com valor."""
    first_day = today - timedelta(days=max(int(days), 1) - 1)
    estimates = load_estimates(fetch, user_id, first_day, today)
    return [e.as_dict() for e in reversed(estimates) if e.epoc_7d_estimated is not None]


def cursor_fetch(cur: Any) -> Fetch:
    """Adapta um cursor (PyMySQL/shim da Data API) para `Fetch`."""

    def fetch(sql: str, params: tuple[Any, ...]) -> Sequence[dict[str, Any]]:
        cur.execute(sql, params)
        return cur.fetchall() or []

    return fetch
