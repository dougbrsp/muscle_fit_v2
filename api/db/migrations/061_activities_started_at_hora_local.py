"""061 - corrige `activities.started_at`/`ended_at` gravados em UTC pelo
Modo Treino (dados; sem DDL).

Bug (2026-09-15 em diante, atividades 219..235 em produção): o
`POST /workout-sessions/start` fazia `started_at = payload.started_at or
datetime.now()`. O Modo Treino não manda `started_at` e a Lambda roda em UTC,
então `started_at` (e o `ended_at` gravado no finish) ficaram em UTC, fora da
convenção do sistema - DATETIME na hora de PAREDE do usuário, sem fuso
(`app/intervals_hr_match.py`, `time_windows.user_now`). Consequência: o
casamento de FC com o Intervals (tolerância 3600 s) via deltas de
10124-12833 s -> `NO_CANDIDATE` -> "FC por sessão de treino" vazia.
O código foi corrigido no mesmo PR; esta migração corrige as linhas antigas.

--- Critério das candidatas ---------------------------------------------------

- `external_id LIKE 'zepp-session-%'` (prefixo gerado só pelo
  `POST /workout-sessions/start`, `app/workout_sessions.py`);
- `started_at IS NOT NULL` e `local_date >= '2026-09-01'`;
- `ABS(TIMESTAMPDIFF(SECOND, created_at, started_at)) < 120`.

PREMISSA: `created_at` é `TIMESTAMP DEFAULT CURRENT_TIMESTAMP` e a sessão do
banco roda em UTC (Aurora sem `time_zone` configurado; MySQL local idem), então
`created_at` lido é o instante de criação em UTC. Uma sessão gravada certo
(hora local de São Paulo) tem `created_at - started_at` ~ 3 h; uma gravada com
o bug tem os dois praticamente iguais. Antes de 15/09 (195..218) a diferença
era +3 h; de 219 em diante, ~0 s.

--- Conversão ------------------------------------------------------------------

Para cada candidata, no fuso de `users.timezone` (inválido/NULL ->
America/Sao_Paulo):

- `started_at`: UTC -> hora de parede do usuário;
- `ended_at`: idem, só se não for NULL e estiver entre 0 e 24 h depois do
  `started_at` antigo (mesmo relógio; um valor fora disso foi editado à mão e
  não é tocado);
- `local_date`: recalculado (data do novo `started_at`) só se era igual à data
  do `started_at` antigo - um `local_date` diferente foi escolhido pelo
  usuário e é preservado;
- usuário cujo fuso dá a mesma hora (UTC) não gera statement.

Cada linha vira UM `UPDATE ... WHERE id = %s AND started_at = %s`: o guarda
pelo valor antigo impede reescrever uma linha que mudou entre o plano e a
execução. Idempotente: depois de aplicada, `created_at - started_at` ~ 3 h e a
linha deixa de ser candidata (reexecução = 0 statements).

FC não é casada aqui: o job diário `musclefit-intervals-sync` (janela de 30
dias) refaz o casamento sozinho na execução seguinte.

Não há `down` de dados: voltar `started_at` para UTC recriaria o bug.

Banco parcial (DRY-RUN de lote, testes de migração): o plano consulta
`information_schema.COLUMNS` antes de ler. Sem `activities` (ou sem alguma das
colunas do filtro) não há o que corrigir -> 0 statements. Sem
`users.timezone` (044 pendente no mesmo lote) a leitura dispensa o JOIN e
todas as linhas usam America/Sao_Paulo.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; módulo
autocontido (não importa código do app).
"""

from __future__ import annotations

import datetime as _dt
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "America/Sao_Paulo"
EXTERNAL_ID_PATTERN = "zepp-session-%"
MIN_LOCAL_DATE = "2026-09-01"
MAX_CREATED_DELTA_SECONDS = 120
MAX_SESSION_SECONDS = 24 * 3600

_CANDIDATES_FILTER = (
    "WHERE a.external_id LIKE %s "
    "AND a.started_at IS NOT NULL "
    "AND a.local_date >= %s "
    "AND ABS(TIMESTAMPDIFF(SECOND, a.created_at, a.started_at)) < %s "
    "ORDER BY a.id"
)

CANDIDATES_SQL = (
    "SELECT a.id, a.started_at, a.ended_at, a.local_date, u.timezone "
    "FROM activities a "
    "LEFT JOIN users u ON u.id = a.user_id " + _CANDIDATES_FILTER
)

# Sem `users.timezone` (banco anterior à 044): mesmo filtro, sem JOIN; o fuso
# NULL cai em DEFAULT_TIMEZONE em `_zone`.
CANDIDATES_NO_TIMEZONE_SQL = (
    "SELECT a.id, a.started_at, a.ended_at, a.local_date, NULL AS timezone "
    "FROM activities a " + _CANDIDATES_FILTER
)

REQUIRED_ACTIVITY_COLUMNS = frozenset(
    {"id", "user_id", "external_id", "started_at", "ended_at", "local_date", "created_at"}
)

COLUMNS_SQL = (
    "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)"
)

UPDATE_SQL = (
    "UPDATE activities SET started_at = %s, ended_at = %s, local_date = %s "
    "WHERE id = %s AND started_at = %s"
)

_DATETIME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?$")


class StartedAtLocalError(Exception):
    """Valor inesperado; nenhum statement desta execução foi aplicado."""


def _as_datetime(value):
    if value is None or isinstance(value, _dt.datetime):
        return value
    text = value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else str(value)
    match = _DATETIME_RE.match(text.strip())
    if not match:
        raise StartedAtLocalError(f"061: datetime inesperado {text!r}. Nenhum statement executado.")
    parts = match.groups()
    micro = int((parts[6] or "0").ljust(6, "0"))
    return _dt.datetime(*(int(p) for p in parts[:6]), micro)


def _as_date(value):
    if value is None or (isinstance(value, _dt.date) and not isinstance(value, _dt.datetime)):
        return value
    if isinstance(value, _dt.datetime):
        return value.date()
    text = value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else str(value)
    return _dt.date.fromisoformat(text.strip()[:10])


def _zone(name):
    if isinstance(name, (bytes, bytearray)):
        name = name.decode("utf-8")
    if isinstance(name, str) and name.strip():
        try:
            return ZoneInfo(name.strip())
        except (ZoneInfoNotFoundError, ValueError, OSError):
            pass
    return ZoneInfo(DEFAULT_TIMEZONE)


def _utc_to_wall(value: _dt.datetime, zone) -> _dt.datetime:
    return value.replace(tzinfo=_dt.timezone.utc).astimezone(zone).replace(tzinfo=None)


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return str(value)


def _existing_columns(db) -> set[tuple[str, str]]:
    return {
        (_text(row["TABLE_NAME"]).lower(), _text(row["COLUMN_NAME"]).lower())
        for row in db.query(COLUMNS_SQL, ("activities", "users"))
    }


def plan(db) -> list[tuple[str, tuple | None]]:
    """Lê as candidatas e devolve [(sql, params)], um UPDATE por linha. Sem escrita."""
    statements: list[tuple[str, tuple | None]] = []
    columns = _existing_columns(db)
    if not all(("activities", name) in columns for name in REQUIRED_ACTIVITY_COLUMNS):
        return statements  # banco parcial: sem activities completa, nada a corrigir
    candidates_sql = CANDIDATES_SQL if ("users", "timezone") in columns else CANDIDATES_NO_TIMEZONE_SQL
    for row in db.query(candidates_sql, (EXTERNAL_ID_PATTERN, MIN_LOCAL_DATE, MAX_CREATED_DELTA_SECONDS)):
        old_started = _as_datetime(row["started_at"])
        old_ended = _as_datetime(row.get("ended_at"))
        old_local_date = _as_date(row.get("local_date"))
        zone = _zone(row.get("timezone"))

        new_started = _utc_to_wall(old_started, zone)
        if new_started == old_started:
            continue  # fuso do usuário = UTC (no instante): nada a corrigir

        new_ended = old_ended
        if old_ended is not None:
            delta = (old_ended - old_started).total_seconds()
            if 0 <= delta <= MAX_SESSION_SECONDS:
                new_ended = _utc_to_wall(old_ended, zone)

        new_local_date = old_local_date
        if old_local_date == old_started.date():
            new_local_date = new_started.date()

        statements.append((UPDATE_SQL, (new_started, new_ended, new_local_date, row["id"], old_started)))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)
