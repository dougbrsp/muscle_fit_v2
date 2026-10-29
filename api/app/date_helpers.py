"""Conversoes de data repetidas em varios modulos (COD-06 da auditoria
auditoria de 2026-10-08, arquivo privado).

Extracao mecanica, no mesmo espirito de `db_helpers.py` (COD-02): cada funcao
aqui e o corpo que ja existia, IDENTICO, em mais de um modulo. Os modulos de
origem continuam expondo o nome antigo (`_as_date`, `_iso`, `to_date`), entao
chamadas internas, imports cruzados e `monkeypatch` dos testes seguem iguais.

Variantes com QUALQUER diferenca de comportamento ficaram nos modulos de
origem (ver o PR do COD-06 e `tests/unit/test_cod06_date_helpers_paridade.py`):
- `insights_core._as_date`: string ISO -> `date`, string invalida -> HTTP 400,
  o resto passa igual;
- `muscle_groups_mapped.as_date`: aceita `dd/mm/aaaa` e cai em `date.today()`;
- `intervals_store.to_date`: devolve a STRING ISO, nao `date`;
- `domain/progression_engine._as_date`: so objetos (string -> `None`); e do
  motor de progressao, que continua dono (o avaliador importa de la);
- `equipment_recommender._iso`: so `date`/`datetime` viram ISO (um `time`
  passa igual); `data_quality_service._iso`: `None` -> `None`, resto -> `str`;
  `exercise_card_service.iso`: UTC com `Z`.

Sem regra de negocio e sem relogio: "hoje do usuario" e do motor de fuso
(`intelligence/dashboard/time_windows.py`, docs/motores/FONTES_DA_VERDADE.md).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional


def to_date(value: Any) -> Optional[date]:
    """`DATE`/`DATETIME`/string ISO -> `date`.

    `None` -> `None`; `datetime` -> a data dele; `date` passa igual; qualquer
    outra coisa vira `str` e e lida pelos 10 primeiros caracteres (`AAAA-MM-DD`,
    com ou sem hora depois). Irreconhecivel -> `None`, nunca uma data inventada.
    Os dois backends devolvem a mesma coluna em tipos diferentes (PyMySQL:
    objeto; shim da RDS Data API: string ISO)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def isoformat_or_value(value: Any) -> Any:
    """Qualquer objeto com `isoformat()` (`date`, `datetime`, `time`) -> ISO
    8601; o resto (inclusive `None`) passa igual."""
    return value.isoformat() if hasattr(value, "isoformat") else value


__all__ = ["isoformat_or_value", "to_date"]
