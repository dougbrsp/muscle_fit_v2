"""Limite de FALHAS de login por IP (SEG-07).

Contador em memoria nao serve na Lambda (cada container tem o seu), entao usa
a mesma tabela do reset de senha (`password_reset_attempts`, ver
`password_reset.py::_throttle`), com escopo proprio. Conta so FALHAS; o login
correto nao consome o limite. Transacao propria: o registro precisa sobreviver
ao `HTTPException` que o chamador levanta em seguida.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from .db import transaction

SCOPE_LOGIN_IP = "login_ip_fail"  # VARCHAR(16)
WINDOW_MINUTES = 15
MAX_FAILURES_PER_IP = 20
RATE_LIMITED_DETAIL = "Muitas tentativas de login. Tente novamente em alguns minutos."


def _subject_hash(ip: str) -> str:
    return hashlib.sha256(f"{SCOPE_LOGIN_IP}:{ip}".encode("utf-8")).hexdigest()


def is_ip_blocked(ip: str) -> bool:
    since = datetime.utcnow() - timedelta(minutes=WINDOW_MINUTES)
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM password_reset_attempts "
                "WHERE scope = %s AND subject_hash = %s AND created_at >= %s",
                (SCOPE_LOGIN_IP, _subject_hash(ip), since),
            )
            row = cur.fetchone()
    return int((row or {}).get("n") or 0) >= MAX_FAILURES_PER_IP


def record_failure(ip: str) -> None:
    now = datetime.utcnow()
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM password_reset_attempts WHERE created_at < %s",
                (now - timedelta(minutes=WINDOW_MINUTES * 4),),
            )
            cur.execute(
                "INSERT INTO password_reset_attempts (scope, subject_hash, created_at) VALUES (%s, %s, %s)",
                (SCOPE_LOGIN_IP, _subject_hash(ip), now),
            )
