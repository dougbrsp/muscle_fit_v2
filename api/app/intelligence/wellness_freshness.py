"""Idade maxima do wellness do Intervals.icu (M52).

Entre 03/09 e 01/10/2026 a Lambda diaria `musclefit-intervals-sync` ficou
quebrada e, por 4 semanas, a recuperacao muscular aplicou a TODOS os grupos o
TSB de 03/09 - sem aviso nenhum. A causa: os indicadores liam "a ultima linha
de `intervals_wellness`" sem olhar a data dela.

A regra, em UM lugar so (recuperacao, prompt de geracao de ficha e o aviso da
tela leem daqui):

- `WELLNESS_MAX_AGE_DAYS` = 3: um wellness de ate 3 dias atras (hoje, ontem,
  anteontem e o dia anterior) ainda vale; com 4 dias ou mais ele e "velho".
  Tres dias absorvem uma falha pontual do sync diario e um fim de semana sem
  abrir o relogio, sem deixar um numero de semanas atras dirigir o calculo.
- "Hoje" e a data civil NO FUSO DO USUARIO (`users.timezone`, via
  `time_windows.user_today`) - nunca o relogio da Lambda (UTC) nem o
  `CURDATE()` do banco, que viram o dia 3h antes de Brasilia.
- Dado velho = AUSENCIA de dado: fator global NEUTRO na recuperacao e secao
  OMITIDA no prompt. A tela recebe a data do ultimo wellness e a flag `stale`
  para avisar por que o ajuste nao foi aplicado.

Modulo PURO (sem banco, sem rede): o chamador ja tem a linha em maos.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping, Optional

from ..date_helpers import to_date

# Idade maxima, em dias, do wellness usado por qualquer indicador.
WELLNESS_MAX_AGE_DAYS = 3

# Coluna com o fuso do DONO da linha, que `get_latest_intervals_wellness`
# traz no mesmo SELECT (JOIN em `users`) para nao gastar um statement a mais.
USER_TIMEZONE_KEY = "user_timezone"


def _as_date(value: Any) -> Optional[date]:
    """`date_helpers.to_date` (COD-06) com UMA diferenca, mantida: o texto
    passa por `strip()` antes de ser lido (`" 2026-10-01"` vale aqui e e
    `None` em `to_date`). `""` -> `None` nas duas."""
    if value in (None, ""):
        return None
    if isinstance(value, (date, datetime)):
        return to_date(value)
    return to_date(str(value).strip())


def wellness_age_days(wellness_date: Any, today: date) -> Optional[int]:
    """Dias entre `wellness_date` e `today` (0 = hoje). `None` se a data nao
    for legivel. Data no futuro conta como 0 (nunca idade negativa)."""
    parsed = _as_date(wellness_date)
    if parsed is None:
        return None
    return max(0, (today - parsed).days)


def is_stale(wellness_date: Any, today: date, max_age_days: int = WELLNESS_MAX_AGE_DAYS) -> bool:
    """`True` quando o wellness passou da idade maxima. Data ilegivel conta
    como velha: sem saber de quando e o dado, o conservador e nao aplica-lo."""
    age = wellness_age_days(wellness_date, today)
    return age is None or age > max_age_days


def user_today_for_row(row: Mapping[str, Any] | None) -> date:
    """"Hoje" no fuso do dono da linha (`user_timezone`), com o fallback
    padrao (`normalize_timezone` -> America/Sao_Paulo) para fuso ausente."""
    from ..timezones import normalize_timezone
    from .dashboard.time_windows import user_today

    timezone_name = normalize_timezone((row or {}).get(USER_TIMEZONE_KEY))
    return user_today(timezone_name)


def is_row_stale(row: Mapping[str, Any] | None, today: date | None = None) -> bool:
    """A linha mais recente de `intervals_wellness` esta velha demais para ser
    usada? `row=None` (sem dado) devolve `False`: ausencia nao e "velho"."""
    if not row:
        return False
    return is_stale(row.get("wellness_date"), today or user_today_for_row(row))


__all__ = [
    "USER_TIMEZONE_KEY",
    "WELLNESS_MAX_AGE_DAYS",
    "is_row_stale",
    "is_stale",
    "user_today_for_row",
    "wellness_age_days",
]
