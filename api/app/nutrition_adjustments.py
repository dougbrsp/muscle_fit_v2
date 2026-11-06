"""Fechamento manual do dia e correção manual de atividades (Lote G da nutrição adaptativa): persistência.

Só SQL por usuário (migração 108). Nenhuma regra mora aqui: o que "dia fechado" significa para a completude e o que cada
correção faz com a atividade são do motor (`domain/nutrition/adaptive_energy` e `activity_energy`, §99 do mapa
`docs/motores/FONTES_DA_VERDADE.md`); aqui só se guarda a marca e se devolve a linha crua.

Leitura tolerante: sem as tabelas (código novo sobre o schema antigo) ou com falha de leitura não há fechamentos nem
correções, e o histórico segue como antes (log só com o tipo da exceção). As escritas exigem a 108 (503 citando a migração).

Reversível: reabrir o dia e desfazer a correção são `DELETE` da linha (idempotentes: apagar o que não existe é no-op).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

from .nutrition_schema import ACTIVITY_CORRECTIONS_TABLE, DAY_CLOSURES_TABLE, has_adjustment_tables

logger = logging.getLogger(__name__)

# Dias fechados do aluno no intervalo (1 SELECT).
CLOSURES_SQL = (
    f"SELECT day, closed_at FROM {DAY_CLOSURES_TABLE} WHERE user_id = %s AND day BETWEEN %s AND %s ORDER BY day"
)
CLOSURE_ONE_SQL = f"SELECT day, closed_at FROM {DAY_CLOSURES_TABLE} WHERE user_id = %s AND day = %s"
# Fechar é idempotente: refechar mantém o primeiro `closed_at`.
CLOSE_SQL = (
    f"INSERT INTO {DAY_CLOSURES_TABLE} (user_id, day, closed_at) VALUES (%s, %s, UTC_TIMESTAMP()) "
    "ON DUPLICATE KEY UPDATE closed_at = closed_at"
)
REOPEN_SQL = f"DELETE FROM {DAY_CLOSURES_TABLE} WHERE user_id = %s AND day = %s"

# Correções do aluno (poucas linhas: uma por atividade corrigida; sem janela para o histórico inteiro ler a mesma coisa).
CORRECTIONS_SQL = (
    f"SELECT activity_id, action, energy_kind FROM {ACTIVITY_CORRECTIONS_TABLE} WHERE user_id = %s ORDER BY activity_id"
)
UPSERT_CORRECTION_SQL = (
    f"INSERT INTO {ACTIVITY_CORRECTIONS_TABLE} (user_id, activity_id, action, energy_kind, created_at, updated_at) "
    "VALUES (%s, %s, %s, %s, UTC_TIMESTAMP(), UTC_TIMESTAMP()) "
    "ON DUPLICATE KEY UPDATE action = VALUES(action), energy_kind = VALUES(energy_kind), updated_at = UTC_TIMESTAMP()"
)
CLEAR_CORRECTION_SQL = f"DELETE FROM {ACTIVITY_CORRECTIONS_TABLE} WHERE user_id = %s AND activity_id = %s"

# A atividade é do aluno? Devolve o dia local dela (para devolver o dia atualizado). Escopo SEMPRE pelo `user_id`.
ACTIVITY_OWNER_SQL = "SELECT id, activity_date FROM intervals_activities WHERE user_id = %s AND id = %s"


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if hasattr(value, "isoformat") else (None if value is None else str(value))


def load_closures(
    cur: Any, user_id: int, first_day: date, last_day: date, available: Optional[bool] = None
) -> dict[date, Optional[str]]:
    """Dias fechados à mão no intervalo: dia -> `closed_at` (ISO, UTC). Vazio sem a tabela da 108 ou em falha de leitura.
    `available` = a detecção das tabelas já feita por quem chama (uma vez por carga); `None` detecta aqui."""
    try:
        if not (has_adjustment_tables(cur) if available is None else available):
            return {}
        cur.execute(CLOSURES_SQL, (int(user_id), first_day, last_day))
        linhas = cur.fetchall() or []
    except Exception as erro:  # noqa: BLE001 - refinamento: sem ele vale a regra dos 70% da TMB
        logger.warning("nutrition.day_closures_read_failed error=%s", type(erro).__name__)
        return {}
    fechados: dict[date, Optional[str]] = {}
    for linha in linhas:
        dia = _as_date(linha.get("day"))
        if dia is not None:
            fechados[dia] = _iso(linha.get("closed_at"))
    return fechados


def load_corrections(cur: Any, user_id: int, available: Optional[bool] = None) -> tuple[dict[str, Any], ...]:
    """Correções do aluno como linhas cruas (`activity_id`, `action`, `energy_kind`); o motor as interpreta. Vazio sem a
    tabela da 108 ou em falha de leitura."""
    try:
        if not (has_adjustment_tables(cur) if available is None else available):
            return ()
        cur.execute(CORRECTIONS_SQL, (int(user_id),))
        linhas = cur.fetchall() or []
    except Exception as erro:  # noqa: BLE001
        logger.warning("nutrition.activity_corrections_read_failed error=%s", type(erro).__name__)
        return ()
    return tuple(
        {"activity_id": str(l["activity_id"]), "action": l.get("action"), "energy_kind": l.get("energy_kind")}
        for l in linhas
        if l.get("activity_id") is not None
    )


def read_closure(cur: Any, user_id: int, day: date) -> dict[str, Any]:
    """`closure` público do dia: `{closed, closed_at}`. Sem a tabela: `{closed: false, closed_at: null}`."""
    fechados = load_closures(cur, user_id, day, day)
    return {"closed": day in fechados, "closed_at": fechados.get(day)}


def close_day(cur: Any, user_id: int, day: date) -> None:
    cur.execute(CLOSE_SQL, (int(user_id), day))


def reopen_day(cur: Any, user_id: int, day: date) -> None:
    cur.execute(REOPEN_SQL, (int(user_id), day))


ACTIVITY_ID_MAX_DIGITS = 18  # cabe em BIGINT com folga; maior que isso nunca é id real (e nunca estoura o VARCHAR(64))


def canonical_activity_id(activity_id: Any) -> Optional[str]:
    """Chave canônica da correção: `str(int(id))` (sem zeros à esquerda, sinal ou espaços); inválido ou grande demais = `None`."""
    texto = str(activity_id).strip()
    if not texto.isascii() or not texto.isdigit() or len(texto.lstrip("0") or "0") > ACTIVITY_ID_MAX_DIGITS:
        return None
    return str(int(texto))


def activity_day(cur: Any, user_id: int, activity_id: str) -> Optional[date]:
    """Dia local da atividade do Intervals DO ALUNO; `None` se não existe, é de outro usuário ou o id é inválido (404)."""
    canonico = canonical_activity_id(activity_id)
    if canonico is None:
        return None
    chave = int(canonico)
    cur.execute(ACTIVITY_OWNER_SQL, (int(user_id), chave))
    return _as_date((cur.fetchone() or {}).get("activity_date"))


def set_correction(cur: Any, user_id: int, activity_id: str, action: str, energy_kind: Optional[str]) -> None:
    cur.execute(UPSERT_CORRECTION_SQL, (int(user_id), str(activity_id), action, energy_kind))


def clear_correction(cur: Any, user_id: int, activity_id: str) -> None:
    cur.execute(CLEAR_CORRECTION_SQL, (int(user_id), str(activity_id)))


__all__ = [
    "activity_day",
    "canonical_activity_id",
    "clear_correction",
    "close_day",
    "load_closures",
    "load_corrections",
    "read_closure",
    "reopen_day",
    "set_correction",
]
