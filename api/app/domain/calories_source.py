"""Origem da kcal gasta de um treino (`activities.calories_source`, migração 076).

Dono único do vocabulário e da regra de resolução (plano de nutrição L0,
2026-10-15; decisão do usuário de 2026-10-15: kcal e FC vêm do sync do
Intervals, o relógio Zepp está fora do plano).

Vocabulário:
- `manual`    — o dono do treino digitou a kcal (`PATCH /workout-sessions/{id}/meta`);
                a coluna `calories_source` guarda o código.
- `intervals` — veio do sync do Intervals (`intervals_sync._FILL_ACTIVITY_HR_SQL`
                preenche `activities.calories_kcal` por `COALESCE` e grava a
                mesma kcal em `activity_intervals_link.calories_kcal`). O sync
                não grava a coluna de origem: a leitura INFERE `intervals`
                quando a kcal do treino é igual à do link.
- `None`      — sem kcal, ou kcal presente de origem desconhecida (ex.: valor
                enviado no `finish` antes desta migração).

Duas regras, ambas aqui:
- LEITURA (`resolve_calories_source`, `GET /activities/{id}`): coluna manda;
  senão `intervals` quando a kcal é igual à do link; senão `None`.
- GRAVAÇÃO pela edição manual (`source_on_manual_write`, `PATCH /meta`): o que
  vai para a coluna. Salvar a mesma kcal do link NÃO vira `manual` (a coluna
  fica `NULL` e a leitura volta a inferir `intervals`).

Consumidores (rotas, telas) recebem o código e o rótulo prontos; nenhum decide
a origem por conta própria (guarda `origem_kcal` em
`tests/unit/test_guarda_fontes_da_verdade.py`).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Final

CALORIES_SOURCE_MANUAL: Final[str] = "manual"
CALORIES_SOURCE_INTERVALS: Final[str] = "intervals"
CALORIES_SOURCES: Final[tuple[str, ...]] = (CALORIES_SOURCE_MANUAL, CALORIES_SOURCE_INTERVALS)

CALORIES_SOURCE_LABELS: Final[dict[str, str]] = {
    CALORIES_SOURCE_MANUAL: "Manual",
    CALORIES_SOURCE_INTERVALS: "Intervals",
}

# `activities.calories_kcal` e `activity_intervals_link.calories_kcal` são
# DECIMAL(10,2): a igualdade é comparada nessa precisão.
_KCAL_QUANTUM: Final[Decimal] = Decimal("0.01")


def _as_kcal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(_KCAL_QUANTUM)
    except (InvalidOperation, ValueError):
        return None


def _equals_intervals(calories_kcal: Decimal, intervals_calories_kcal: Any) -> bool:
    """kcal do treino (já quantizada) igual à do link do Intervals, em 0,01."""
    intervals_calories_kcal = _as_kcal(intervals_calories_kcal)
    return intervals_calories_kcal is not None and calories_kcal == intervals_calories_kcal


def resolve_calories_source(
    *,
    calories_kcal: Any,
    stored_source: Any,
    intervals_calories_kcal: Any,
) -> str | None:
    """Origem da kcal do treino na LEITURA.

    - sem kcal -> `None`;
    - coluna com código do vocabulário -> ela manda;
    - kcal igual à do link do Intervals -> `intervals`;
    - senão -> `None` (origem desconhecida)."""
    kcal = _as_kcal(calories_kcal)
    if kcal is None:
        return None
    if isinstance(stored_source, str) and stored_source in CALORIES_SOURCES:
        return stored_source
    if _equals_intervals(kcal, intervals_calories_kcal):
        return CALORIES_SOURCE_INTERVALS
    return None


def source_on_manual_write(kcal: Any, intervals_calories_kcal: Any) -> str | None:
    """Origem a GRAVAR em `activities.calories_source` quando o dono do treino
    edita a kcal (`PATCH /workout-sessions/{id}/meta`).

    - kcal `None` (limpar) -> `None`;
    - kcal igual à do link do Intervals (0,01) -> `None`: editar e salvar sem
      mudar não troca a origem; a leitura volta a inferir `intervals`;
    - qualquer outro valor -> `manual`."""
    value = _as_kcal(kcal)
    if value is None:
        return None
    if _equals_intervals(value, intervals_calories_kcal):
        return None
    return CALORIES_SOURCE_MANUAL


def calories_source_label(source: str | None) -> str | None:
    """Rótulo pronto para a tela; `None` quando não há origem."""
    if source is None:
        return None
    return CALORIES_SOURCE_LABELS.get(source)


__all__ = [
    "CALORIES_SOURCES",
    "CALORIES_SOURCE_INTERVALS",
    "CALORIES_SOURCE_LABELS",
    "CALORIES_SOURCE_MANUAL",
    "calories_source_label",
    "resolve_calories_source",
    "source_on_manual_write",
]
