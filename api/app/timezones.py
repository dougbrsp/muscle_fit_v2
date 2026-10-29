"""Fuso horario do usuario (`users.timezone`, migracao 044).

Unica fonte da regra "nome de fuso valido" e do default. Compartilhado por:

- `app/users.py` (validacao do `PUT /users/me`) e `app/auth.py` (exposicao em
  `GET /auth/me`) na Lambda api-core;
- `app/intelligence/dashboard/time_windows.py` (data "hoje" do usuario) na
  Lambda engines.

Validacao via `zoneinfo` (stdlib): qualquer nome IANA reconhecido pelo banco
de fusos instalado (`tzdata`, fixado em requirements.txt para nao depender do
sistema operacional do container nem do Windows de dev).
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "America/Sao_Paulo"
TIMEZONE_MAX_LENGTH = 64  # VARCHAR(64) em `users.timezone`


def is_valid_timezone(name: Any) -> bool:
    """`True` se `name` e um nome IANA reconhecido pelo `zoneinfo`."""
    if not isinstance(name, str):
        return False
    candidate = name.strip()
    if not candidate or len(candidate) > TIMEZONE_MAX_LENGTH:
        return False
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return True


def normalize_timezone(raw: Any) -> str:
    """Nome de fuso SEMPRE valido para a resposta da API: devolve `raw` se for
    um fuso reconhecido, senao `DEFAULT_TIMEZONE` (linha anterior a migracao
    044, coluna ausente ou valor corrompido). Nunca lanca."""
    if isinstance(raw, str) and is_valid_timezone(raw):
        return raw.strip()
    return DEFAULT_TIMEZONE
