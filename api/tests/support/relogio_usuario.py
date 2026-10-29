"""Relogio do USUARIO para os testes de integracao do Novo Dashboard.

Os servicos v3 resolvem "hoje" com `user_today(users.timezone)` - o fuso do
usuario (default `America/Sao_Paulo`, UTC-3) - e nunca com o relogio do
processo nem com `CURDATE()` do MySQL. Um teste que insere dados em
`date.today()` (fuso do runner: UTC no GitHub Actions) e le pela rota entra em
desacordo entre 21:00 e 00:00 de Brasilia: o runner ja esta no dia seguinte
e o dado cai fora da janela que o servico calcula (flake do CI de 2026-09-24
00:15 UTC no PR #197).

A fonte da verdade e o fuso do usuario. Todo teste v3 pega "hoje"/"agora"
DAQUI, com o mesmo helper que o servico usa.
"""

from __future__ import annotations

import datetime as _dt

from app.intelligence.dashboard.time_windows import user_now, user_today
from app.timezones import DEFAULT_TIMEZONE

# Fuso do usuario de teste (usuarios criados sem `timezone` caem no default).
TZ_USUARIO = DEFAULT_TIMEZONE


def hoje_do_usuario(timezone_name: str = TZ_USUARIO) -> _dt.date:
    """Data civil "hoje" no fuso do usuario - a mesma que a rota calcula."""
    return user_today(timezone_name)


def agora_do_usuario(timezone_name: str = TZ_USUARIO) -> _dt.datetime:
    """Instante "agora" na parede do usuario (naive, como o servico usa)."""
    return user_now(timezone_name)


__all__ = ["TZ_USUARIO", "agora_do_usuario", "hoje_do_usuario"]
