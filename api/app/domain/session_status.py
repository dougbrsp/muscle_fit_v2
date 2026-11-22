"""Dono do estado "sessao encerrada" (`activities.session_status`).

Python puro. O literal gravado pelo "Finalizar treino" e o predicado que
decide o que muda numa sessao ja encerrada (reavaliar a progressao) moram so
aqui; rotas, deload e nutricao importam daqui.

Sessao com `session_status` NULL (legada) NAO e encerrada: fica fora do
historico da progressao (`training_history.SESSIONS_SQL` exige 'done').
"""

from __future__ import annotations

from typing import Any, Final

# Valor gravado em `activities.session_status` pelo "Finalizar treino".
SESSION_STATUS_DONE: Final[str] = "done"


def is_closed_session(session_status: Any) -> bool:
    """A sessao esta encerrada (`session_status == 'done'`)?"""
    return session_status == SESSION_STATUS_DONE


def reevaluates_progression(session_status: Any, *, changed: bool = True) -> bool:
    """Uma correcao feita DEPOIS do fato (`changed`) numa sessao encerrada
    obriga a reavaliar a progressao; em andamento ou sem mudanca, nao."""
    return bool(changed) and is_closed_session(session_status)


__all__ = ["SESSION_STATUS_DONE", "is_closed_session", "reevaluates_progression"]
