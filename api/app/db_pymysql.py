"""Camada de acesso a dados sobre PyMySQL puro (MySQL local, ex.: dev/Docker).

Implementacao alternativa a `db_dataapi.py`, usada quando `DB_BACKEND=pymysql`
(ver dispatcher em `db.py`). Expoe exatamente a mesma superficie publica do
modulo Data API (`Connection`, `Cursor`, `get_conn()`, `transaction()`,
`DataApiError`, `is_duplicate_column_error`, `is_duplicate_key_error`,
`is_unknown_key_error`) para que os ~30 call-sites do backend continuem
funcionando sem alteracao, trocando so a variavel de ambiente `DB_BACKEND`.

Diferencas em relacao ao `db_dataapi.py`:

- Conexao TCP real via `pymysql.connect()`, sem traducao de SQL: o paramstyle
  nativo do PyMySQL ja e "format" (`%s` + parametros posicionais), que e o
  mesmo formato usado pelos call-sites - nao ha necessidade de traduzir para
  `:pN` como no Data API.
- `Cursor` usa `pymysql.cursors.DictCursor`, entao `fetchone()`/`fetchall()`
  ja devolvem `dict` (paridade com o `_rows_from_response` do shim Data API,
  que converte os campos da Data API para os mesmos tipos deste modulo).
- Excecoes nativas do PyMySQL (`pymysql.err.OperationalError`,
  `pymysql.err.IntegrityError` etc.) sao capturadas em `execute()` e
  relancadas como `DataApiError(str(exc))`, preservando o texto da mensagem
  nativa do MySQL (`"Duplicate column name"`, `"Duplicate entry"` etc.), para
  que `is_duplicate_column_error`/`is_duplicate_key_error` continuem
  funcionando por substring sem alteracao de logica nos call-sites.
- Configuracao via `MYSQL_HOST`/`MYSQL_PORT`/`MYSQL_USER`/`MYSQL_PASSWORD`/
  `MYSQL_DATABASE` em vez das env vars da Lambda/Aurora.

Paridade com producao (Data API) - este modulo se ajusta ao comportamento
MEDIDO na Aurora, nunca o contrario:

- `rowcount` de UPDATE conta linhas ENCONTRADAS (`CLIENT.FOUND_ROWS`): na
  Aurora real `UPDATE ... SET col = col` em 1 e 2 linhas devolveu
  `numberOfRecordsUpdated` 1 e 2. Sem a flag, o PyMySQL conta so as ALTERADAS
  (0), e um `rowcount == 0 -> 404` respondia 404 so em dev ao regravar os
  mesmos valores.
- `%%` sem parametros vira `%` (o shim sempre desfaz o escape; o PyMySQL so
  desfazia quando recebia uma tupla de argumentos, nao `None`).
- dict/list/tuple como parametro viram texto JSON (como o shim ja fazia em
  producao), em vez de TypeError/expansao em tupla SQL.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import timedelta
from typing import Any, Iterable, Optional, Sequence

import pymysql
import pymysql.cursors
from pymysql.constants import CLIENT

_JSON_BIND_TYPES = (dict, list, tuple)


def _format_timedelta(value: timedelta) -> str:
    """`[-]HH:MM:SS[.ffffff]` com o sinal aplicado ao valor inteiro.

    O `escape_timedelta` do PyMySQL calcula horas/minutos a partir de
    `days`/`seconds` separadamente e grava `timedelta(hours=-1, minutes=-5)`
    como '-2:55:00' (-2h55). O shim Data API envia o valor correto; dev segue."""
    total_us = value.days * 86_400_000_000 + value.seconds * 1_000_000 + value.microseconds
    sign = "-" if total_us < 0 else ""
    seconds, microsecond = divmod(abs(total_us), 1_000_000)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    fraction = f".{microsecond:06d}" if microsecond else ""
    return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}{fraction}"


def _bind_value(value: Any) -> Any:
    if isinstance(value, _JSON_BIND_TYPES):
        # Mesma serializacao de `db_dataapi.json_bind_value` (nao importado
        # para nao acoplar o backend de dev ao boto3).
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, timedelta):
        return _format_timedelta(value)
    return value


def _bind_params(params: Sequence[Any]) -> list[Any]:
    return [_bind_value(value) for value in params]

__all__ = [
    "Connection",
    "Cursor",
    "fetch_rows",
    "get_conn",
    "transaction",
    "DataApiError",
    "is_duplicate_column_error",
    "is_duplicate_key_error",
    "is_unknown_key_error",
]


class DataApiError(Exception):
    """Erro generico da camada de dados (falha de execucao, parametro nao suportado etc).

    Aqui simplesmente encapsula a excecao nativa do PyMySQL
    (`pymysql.err.OperationalError`, `pymysql.err.IntegrityError` etc.),
    preservando `str(exc)` - a mesma mensagem de texto do servidor MySQL que
    o shim Data API tambem preserva - para que os helpers abaixo continuem
    casando por substring independente do backend em uso."""


def is_duplicate_column_error(exc: Exception) -> bool:
    """True se a excecao veio de um ALTER TABLE ADD COLUMN em coluna que ja existe (MySQL 1060)."""
    return "Duplicate column name" in str(exc)


def is_duplicate_key_error(exc: Exception) -> bool:
    """True se a excecao veio de um INSERT/UPDATE violando UNIQUE KEY (MySQL 1062)."""
    return "Duplicate entry" in str(exc)


def is_unknown_key_error(exc: Exception) -> bool:
    """True se a excecao veio de um DROP INDEX/DROP KEY em indice que nao existe (MySQL 1091)."""
    text = str(exc)
    return "check that column/key exists" in text or "Can't DROP" in text


class Cursor:
    """Wrapper fino sobre `pymysql.cursors.DictCursor`, repassando a mesma
    interface DBAPI-like do shim Data API (`.execute()/.fetchone()/
    .fetchall()/.lastrowid/.rowcount/.description`)."""

    def __init__(self, conn: "Connection"):
        self._conn = conn
        self._cursor = conn._raw.cursor()
        self._after_many = False

    def execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> "Cursor":
        if params:
            args: Optional[list[Any]] = _bind_params(params)
        else:
            # Sem parametros o PyMySQL nao aplica %-formatting quando recebe
            # None (e quebra num `%` solto quando recebe `()`): desfaz `%%`
            # aqui, igual a `db_dataapi._translate_sql`.
            sql, args = sql.replace("%%", "%"), None
        self._after_many = False
        try:
            self._cursor.execute(sql, args)
        except pymysql.err.Error as exc:  # noqa: BLE001 - repassa com mensagem nativa do MySQL
            raise DataApiError(str(exc)) from exc
        return self

    def executemany(self, sql: str, seq_of_params: Iterable[Sequence[Any]]) -> "Cursor":
        """Mesmo DML para varias linhas (COD-12), na transacao da conexao.

        O PyMySQL reescreve `INSERT ... VALUES (%s, ...)` num INSERT
        multi-linha (lotes de ate `max_stmt_length`), na ordem recebida; outros
        formatos (ex.: `CAST(%s AS JSON)` no VALUES) viram N `execute()`.
        Contrato igual ao `db_dataapi.Cursor.executemany`: depois dele
        `lastrowid` e None e `rowcount` e -1 (os backends nao concordariam)."""
        all_params = [_bind_params(params) for params in seq_of_params]
        if any(not params for params in all_params):
            raise DataApiError("executemany exige parametros em todas as linhas")
        self._after_many = True
        if not all_params:
            return self
        try:
            self._cursor.executemany(sql, all_params)
        except pymysql.err.Error as exc:  # noqa: BLE001 - repassa com mensagem nativa do MySQL
            raise DataApiError(str(exc)) from exc
        except TypeError as exc:
            # Numero de parametros != placeholders: o PyMySQL estoura no
            # `sql % args` (TypeError); o shim da Data API recusa com
            # DataApiError antes de enviar. Mesmo tipo de falha nos dois.
            raise DataApiError(f"Numero de placeholders %s nao bate com os parametros: {exc}") from exc
        return self

    @property
    def lastrowid(self) -> Optional[int]:
        return None if self._after_many else self._cursor.lastrowid

    @property
    def rowcount(self) -> int:
        return -1 if self._after_many else self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def fetchone(self) -> Optional[dict[str, Any]]:
        return self._cursor.fetchone()

    def fetchall(self) -> list[dict[str, Any]]:
        return self._cursor.fetchall()

    def close(self) -> None:
        self._cursor.close()

    def __enter__(self) -> "Cursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Intencionalmente NAO chama close(): paridade com o shim Data API,
        # cujos call-sites em `with conn.cursor() as cur: ...` so consomem o
        # resultado depois de sair do bloco.
        return None

    def __iter__(self) -> Iterable[dict[str, Any]]:
        return iter(self._cursor)


class Connection:
    """Wrapper fino sobre `pymysql.connect()`, reproduzindo o mesmo contrato
    de `with conn:` do shim Data API: commit implicito ao sair sem excecao,
    rollback ao sair com excecao (paridade com autocommit=True do
    `pymysql.connect(**_db_config())` original do backend on-premise)."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        *,
        autocommit: bool = False,
    ):
        # Nome do schema desta conexao, para quem precisa dele sem gastar um
        # `SELECT DATABASE()` (`load_conventions.current_schema`). Mesmo campo
        # que `db_dataapi.Connection.database` - paridade entre os dois shims.
        self.database = database
        self._raw = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            autocommit=autocommit,
            cursorclass=pymysql.cursors.DictCursor,
            # rowcount = linhas ENCONTRADAS, como a Data API (ver docstring do modulo).
            client_flag=CLIENT.FOUND_ROWS,
        )

    def cursor(self) -> Cursor:
        return Cursor(self)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        # Paridade com o shim Data API: se ninguem deu commit/rollback
        # explicito, aborta a transacao aberta em vez de deixar pendurada.
        try:
            self._raw.rollback()
        finally:
            self._raw.close()

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()


def _config_from_env() -> tuple[str, int, str, str, str]:
    host = os.environ.get("MYSQL_HOST", "127.0.0.1")
    port = int(os.environ.get("MYSQL_PORT", "3306"))
    user = os.environ.get("MYSQL_USER", "root")
    password = os.environ.get("MYSQL_PASSWORD", "")
    database = os.environ.get("MYSQL_DATABASE", "muscle_fit")
    return host, port, user, password, database


def get_conn() -> Connection:
    host, port, user, password, database = _config_from_env()
    return Connection(host, port, user, password, database)


def fetch_rows(sql: str, params: Optional[Sequence[Any]] = None) -> list[dict[str, Any]]:
    """Linhas de UMA leitura (`SELECT`/`WITH`/`SHOW`) fora de transacao explicita.

    Paridade com `db_dataapi.fetch_rows` (PERF-05): conexao em autocommit, um
    statement, sem `SET AUTOCOMMIT = 0`/`ROLLBACK`. Mesmo resultado de
    "BEGIN; SELECT; ROLLBACK" para uma leitura de um statement."""
    _require_read(sql)
    host, port, user, password, database = _config_from_env()
    conn = Connection(host, port, user, password, database, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall() or [])
    finally:
        conn._raw.close()


_READ_PREFIXES = ("SELECT", "WITH", "SHOW", "(")


def _require_read(sql: str) -> None:
    """Mesma recusa de `db_dataapi._require_read`: DML em autocommit gravaria
    na hora."""
    head = sql.lstrip().upper()
    if not head.startswith(_READ_PREFIXES):
        raise DataApiError(f"fetch_rows aceita so leitura (SELECT/WITH/SHOW): {sql.strip()[:60]!r}")


@contextmanager
def transaction():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
