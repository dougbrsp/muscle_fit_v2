"""Conversoes de valor cru do banco -> tipo Python, compartilhadas pelos
servicos do Novo Dashboard.

Existem porque os dois backends devolvem os MESMOS dados em tipos diferentes:
o PyMySQL entrega `DECIMAL` como `decimal.Decimal` e datas como `date`/
`datetime`, enquanto o shim da RDS Data API entrega numero como `str` e data
como `str` ISO. Cada servico que precisar de um float ou de uma data teria
que tratar os dois casos - e foi assim que `muscle_summary_service` (F1.2)
nasceu com a sua propria copia.

Regra das tres funcoes: valor ausente ou irreconhecivel devolve `None`,
NUNCA um zero ou uma data inventada (plano §8.4 - `null` != `0`).
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Optional

from ...date_helpers import to_date as _to_date


def to_float(value: Any) -> Optional[float]:
    """Numero do banco -> `float`. `Decimal`, `int`, `float`, `bool` e string
    numerica sao aceitos; qualquer outra coisa (inclusive `None`) -> `None`."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


# `DATE`/`DATETIME`/string ISO -> `date`. Corpo em `app/date_helpers.py`
# (COD-06): o mesmo que `template_assignments` usa, sem depender desta camada.
to_date = _to_date


def to_datetime(value: Any) -> Optional[datetime]:
    """`DATETIME`/`DATE`/string ISO -> `datetime` NAIVE (as colunas de data e
    hora do projeto sao naive e ja gravadas na hora local do usuario)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def round_half_up(value: Any, places: int = 2) -> Optional[float]:
    """Arredonda `value` a `places` casas com `ROUND_HALF_UP` decimal e devolve
    `float` - o MESMO numero venha ele como `Decimal` (PyMySQL), `str` (shim
    da RDS Data API) ou `float` (calculado em Python).

    Por que nao `round(x, 2)`: o `round` nativo trabalha sobre o binario do
    float, entao `round(72.645, 2)` da `72.64` (72.645 nao e representavel e
    cai um fio abaixo) enquanto `round(Decimal("72.645"), 2)` da `72.64` por
    arredondamento bancario e `Decimal.quantize(HALF_UP)` da `72.65`. Tres
    regras para o mesmo numero exibido. Aqui o float passa por `str` (o
    `repr` mais curto, "72.645") antes de virar `Decimal`, e todos os tipos
    convergem para a mesma regra: meio centesimo sobe.

    `None`/irreconhecivel -> `None` (regra do modulo: ausencia nao vira zero).
    `NaN`/infinito voltam como `float` sem arredondar."""
    numero = to_float(value)
    if numero is None:
        return None
    if not math.isfinite(numero):
        return numero
    origem = value if isinstance(value, Decimal) else Decimal(str(numero))
    try:
        quantum = Decimal(1).scaleb(-places)
        return float(origem.quantize(quantum, rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return round(numero, places)


__all__ = ["round_half_up", "to_date", "to_datetime", "to_float"]
