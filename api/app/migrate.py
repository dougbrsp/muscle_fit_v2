"""Runner de migracoes versionadas do banco (Aurora via Data API ou MySQL local).

Uso:
    python -m app.migrate              # aplica as migracoes pendentes
    python -m app.migrate --dry-run    # mostra o que seria feito, sem escrever nada
    python -m app.migrate --status     # lista o estado de cada migracao (somente leitura)

Na AWS roda como a Lambda `musclefit-migrations` (handler `app.migrate.handler`),
invocada de forma sincrona por `.github/workflows/deploy-producao.yml` (job
`backend-deploy`, com RUN_DB_MIGRATIONS=true) ou `db-migrations.yml`, ANTES de
atualizar o codigo das demais Lambdas (expand -> deploy do codigo).

O handler tambem expoe UMA operacao de dados que nao e migracao de schema:
`{"action": "replay-progression"}`, o replay das trilhas da progressao
(`app/replay_progression.py`). Ela vive aqui porque esta e a unica Lambda com
escrita no banco fora do caminho HTTP - e portanto a unica forma de rodar o
replay em producao sem acesso manual a AWS.

Formatos suportados em `api/db/migrations/` (nome `NNN_descricao.sql|py`):

- `.sql`: o arquivo e dividido em statements (por `;`, respeitando strings e
  comentarios) e cada statement e executado em uma chamada separada. Nada de
  `PREPARE`/`EXECUTE`/`DELIMITER`/`USE`/`SET @var` - o runner recusa esses
  comandos em migracoes novas, porque cada `execute_statement` do Data API e
  independente e nao garante estado de sessao entre chamadas. Os statements
  precisam ser idempotentes (`CREATE TABLE IF NOT EXISTS`, `INSERT IGNORE`...).
- `.py`: modulo com `def up(db)`. `db` e um `MigrationDB` com `execute()`,
  `query()` e os helpers `table_exists`, `column_exists`, `index_exists`,
  `fk_exists` (checagens em Python via `information_schema`), para ALTERs
  condicionais sem `PREPARE`.

Baseline: os arquivos com versao <= `BASELINE_MAX_VERSION` (001..021) ja estao
aplicados em todos os ambientes (Aurora via mysqldump + guards; local via
`docker-entrypoint-initdb.d`). O runner apenas os REGISTRA (kind=baseline), sem
executar. Versoes maiores sao executadas.

Controle: tabela `schema_migration_files` (nova; a `schema_migrations` legada,
com numeros de versao que nao batem com os arquivos, nao e lida nem escrita).
O checksum (sha256 com fim de linha normalizado para LF) de cada migracao
executada e verificado a cada run: se o arquivo mudou depois de aplicado o
runner falha antes de executar qualquer coisa (a correcao e sempre uma
migracao NOVA). Para arquivos de baseline, divergencia so gera aviso - eles
nunca sao executados pelo runner.

Lock: tabela `schema_migration_lock` com expiracao (lease), e nao `GET_LOCK()`:
`GET_LOCK` e preso a sessao MySQL, e o Data API nao garante que chamadas
consecutivas usem a mesma sessao fora de uma transacao (nem que a sessao seja
encerrada quando a Lambda morre - um lock orfao ficaria preso num pool de
conexoes). O lease em tabela funciona igual nos dois backends e expira sozinho.

Transacoes: DDL no MySQL faz commit implicito, entao uma migracao NAO e
atomica. Se falhar no meio, ela nao e registrada e o lock e liberado; como os
statements sao idempotentes (guards), basta corrigir a causa e rodar de novo.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import logging
import os
import re
import socket
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

from .runtime_guard import exigir_data_api_em_lambda

logger = logging.getLogger("fitness.migrate")

BASELINE_MAX_VERSION = 21
CONTROL_TABLE = "schema_migration_files"
LOCK_TABLE = "schema_migration_lock"
LOCK_NAME = "migrate"
LOCK_TTL_SECONDS = 1800

MIGRATION_FILENAME_RE = re.compile(r"^(?P<version>\d{3})_(?P<name>[A-Za-z0-9_]+)\.(?P<ext>sql|py)$")
_LOOKS_LIKE_MIGRATION_RE = re.compile(r"^\d{3}[^\d]")

# Comandos que dependem de estado de sessao entre chamadas (ou mudam o schema
# corrente) - incompativeis com o Data API, onde cada statement e isolado.
_FORBIDDEN_SQL_RE = re.compile(r"^\s*(PREPARE|EXECUTE|DEALLOCATE|DELIMITER|USE|SET\s+@)\b", re.IGNORECASE)

DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"

ConnFactory = Callable[[], Any]

_CREATE_CONTROL_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {CONTROL_TABLE} (
  filename VARCHAR(255) NOT NULL,
  version INT NOT NULL,
  kind VARCHAR(16) NOT NULL,
  checksum CHAR(64) NOT NULL,
  applied_at DATETIME NOT NULL,
  execution_ms INT NULL,
  PRIMARY KEY (filename),
  KEY idx_schema_migration_files_version (version)
) ENGINE=InnoDB
"""

_CREATE_LOCK_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {LOCK_TABLE} (
  lock_name VARCHAR(64) NOT NULL,
  owner VARCHAR(255) NOT NULL,
  acquired_at DATETIME NOT NULL,
  expires_at DATETIME NOT NULL,
  PRIMARY KEY (lock_name)
) ENGINE=InnoDB
"""


# ---------------------------------------------------------------------------
# Erros
# ---------------------------------------------------------------------------


class MigrationError(Exception):
    """Erro generico do runner."""


class MigrationChecksumError(MigrationError):
    """Migracao ja aplicada teve o arquivo alterado."""


class MigrationLockError(MigrationError):
    """Outro processo esta com o lock de migracao."""


class MigrationExecutionError(MigrationError):
    """Falha ao executar uma migracao."""


# ---------------------------------------------------------------------------
# Descoberta / leitura de arquivos
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Migration:
    version: int
    filename: str
    path: Path
    kind: str  # "sql" | "py"
    checksum: str

    @property
    def is_baseline(self) -> bool:
        return self.version <= BASELINE_MAX_VERSION


def compute_checksum(path: Path) -> str:
    """sha256 do conteudo com CRLF -> LF, para o mesmo arquivo gerar o mesmo
    checksum em checkout Windows e no build Linux da imagem."""
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def discover_migrations(migrations_dir: Optional[Path] = None) -> list[Migration]:
    directory = Path(migrations_dir) if migrations_dir else _migrations_dir_from_env()
    if not directory.is_dir():
        raise MigrationError(f"Diretorio de migracoes nao encontrado: {directory}")

    found: list[Migration] = []
    seen_versions: dict[int, str] = {}
    for entry in sorted(directory.iterdir(), key=lambda p: p.name):
        if not entry.is_file():
            continue
        match = MIGRATION_FILENAME_RE.match(entry.name)
        if not match:
            if _LOOKS_LIKE_MIGRATION_RE.match(entry.name) and not entry.name.endswith((".pyc", ".md")):
                raise MigrationError(
                    f"Arquivo com cara de migracao mas nome invalido: {entry.name} "
                    "(esperado NNN_descricao.sql ou NNN_descricao.py)"
                )
            continue
        version = int(match.group("version"))
        if version in seen_versions:
            raise MigrationError(
                f"Versao de migracao duplicada {version:03d}: {seen_versions[version]} e {entry.name}"
            )
        seen_versions[version] = entry.name
        found.append(
            Migration(
                version=version,
                filename=entry.name,
                path=entry,
                kind=match.group("ext"),
                checksum=compute_checksum(entry),
            )
        )
    found.sort(key=lambda m: m.version)
    return found


def _migrations_dir_from_env() -> Path:
    override = os.environ.get("MIGRATIONS_DIR")
    return Path(override) if override else DEFAULT_MIGRATIONS_DIR


def split_sql_statements(text: str) -> list[str]:
    """Divide um script SQL em statements por `;`, ignorando `;` dentro de
    strings/identificadores e removendo comentarios (`-- `, `#`, `/* */`)."""
    statements: list[str] = []
    buf: list[str] = []
    i, n = 0, len(text)
    quote: Optional[str] = None
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if quote:
            buf.append(ch)
            if ch == "\\" and quote in ("'", '"') and nxt:
                buf.append(nxt)
                i += 2
                continue
            if ch == quote:
                if nxt == quote:  # aspas duplicadas = escape
                    buf.append(nxt)
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "-" and nxt == "-" and (i + 2 >= n or text[i + 2] in " \t\r\n"):
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "#":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            buf.append(" ")
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def validate_sql_statements(filename: str, statements: Iterable[str]) -> None:
    for stmt in statements:
        if _FORBIDDEN_SQL_RE.match(stmt):
            first_line = stmt.splitlines()[0][:80]
            raise MigrationError(
                f"{filename}: comando nao suportado pelo runner/Data API: {first_line!r}. "
                "Use uma migracao .py com os helpers table_exists/column_exists/"
                "index_exists/fk_exists em vez de PREPARE/EXECUTE/SET @var/USE/DELIMITER."
            )


def load_python_migration(path: Path) -> Callable[["MigrationDB"], Any]:
    module_name = f"musclefit_migration_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise MigrationError(f"Nao foi possivel carregar a migracao {path.name}")
    module = importlib.util.module_from_spec(spec)
    # Sem __pycache__ dentro de api/db/migrations/ (o diretorio e montado no
    # init do MySQL e copiado para a imagem da Lambda).
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    up = getattr(module, "up", None)
    if not callable(up):
        raise MigrationError(f"{path.name}: migracao .py precisa definir `def up(db)`")
    return up


# ---------------------------------------------------------------------------
# Acesso ao banco
# ---------------------------------------------------------------------------


def _default_conn_factory() -> Any:
    # Import tardio: `app.db` escolhe Data API ou PyMySQL por DB_BACKEND, e
    # importar boto3/pymysql so quando de fato for conectar deixa os testes
    # unitarios (fake db) livres de dependencias de rede.
    from . import db

    return db.get_conn()


def _count(row: Any) -> int:
    if row is None:
        return 0
    value = row["n"] if isinstance(row, dict) else row[0]
    return int(value or 0)


def _is_resuming_error(exc: Exception) -> bool:
    text = str(exc)
    return "DatabaseResumingException" in text or "is resuming after being auto-paused" in text


class MigrationDB:
    """Fachada passada para `up(db)` das migracoes `.py`.

    Em dry-run, `execute()` apenas registra o statement (leituras via `query()`
    e os helpers continuam indo ao banco, para o plano refletir o schema real)."""

    def __init__(self, conn: Any, dry_run: bool = False):
        self._conn = conn
        self.dry_run = dry_run
        self.statements: list[str] = []

    def query(self, sql: str, params: Optional[Sequence[Any]] = None) -> list[dict[str, Any]]:
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return list(cur.fetchall() or [])

    def query_one(self, sql: str, params: Optional[Sequence[Any]] = None) -> Optional[dict[str, Any]]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> int:
        normalized = " ".join(sql.split())
        self.statements.append(normalized)
        if self.dry_run:
            logger.info("[dry-run] %s", normalized)
            return 0
        logger.info("executando: %s", normalized)
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return cur.rowcount

    def table_exists(self, table: str) -> bool:
        return table_exists(self, table)

    def column_exists(self, table: str, column: str) -> bool:
        return column_exists(self, table, column)

    def index_exists(self, table: str, index: str) -> bool:
        return index_exists(self, table, index)

    def fk_exists(self, table: str, constraint: str) -> bool:
        return fk_exists(self, table, constraint)


def table_exists(db: MigrationDB, table: str) -> bool:
    return _count(
        db.query_one(
            "SELECT COUNT(*) AS n FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (table,),
        )
    ) > 0


def column_exists(db: MigrationDB, table: str, column: str) -> bool:
    return _count(
        db.query_one(
            "SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
            (table, column),
        )
    ) > 0


def index_exists(db: MigrationDB, table: str, index: str) -> bool:
    return _count(
        db.query_one(
            "SELECT COUNT(*) AS n FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s",
            (table, index),
        )
    ) > 0


def fk_exists(db: MigrationDB, table: str, constraint: str) -> bool:
    return _count(
        db.query_one(
            "SELECT COUNT(*) AS n FROM information_schema.TABLE_CONSTRAINTS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND CONSTRAINT_NAME = %s "
            "AND CONSTRAINT_TYPE = 'FOREIGN KEY'",
            (table, constraint),
        )
    ) > 0


# ---------------------------------------------------------------------------
# Relatorio
# ---------------------------------------------------------------------------


@dataclass
class AppliedMigration:
    filename: str
    version: int
    execution_ms: int
    statements: list[str] = field(default_factory=list)


@dataclass
class MigrationReport:
    status: str = "ok"
    dry_run: bool = False
    baselined: list[str] = field(default_factory=list)
    applied: list[AppliedMigration] = field(default_factory=list)
    already_applied: int = 0
    warnings: list[str] = field(default_factory=list)
    lock_owner: Optional[str] = None
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Controle + lock
# ---------------------------------------------------------------------------


def _load_records(conn: Any) -> dict[str, dict[str, Any]]:
    db = MigrationDB(conn)
    if not table_exists(db, CONTROL_TABLE):
        return {}
    rows = db.query(f"SELECT filename, version, kind, checksum FROM {CONTROL_TABLE}")
    return {row["filename"]: row for row in rows}


def _insert_record(conn: Any, migration: Migration, kind: str, execution_ms: Optional[int]) -> None:
    cur = conn.cursor()
    cur.execute(
        f"INSERT INTO {CONTROL_TABLE} (filename, version, kind, checksum, applied_at, execution_ms) "
        "VALUES (%s, %s, %s, %s, UTC_TIMESTAMP(), %s)",
        (migration.filename, migration.version, kind, migration.checksum, execution_ms),
    )
    conn.commit()


def _check_consistency(
    migrations: Sequence[Migration], records: dict[str, dict[str, Any]], report: MigrationReport
) -> None:
    errors: list[str] = []
    by_filename = {m.filename: m for m in migrations}
    recorded_versions = {int(r["version"]): name for name, r in records.items()}

    for name, record in records.items():
        migration = by_filename.get(name)
        if migration is None:
            report.warnings.append(f"{name}: registrada em {CONTROL_TABLE}, mas o arquivo nao existe mais")
            continue
        if record["checksum"] != migration.checksum:
            msg = (
                f"{name}: checksum divergente (registrado {str(record['checksum'])[:12]}..., "
                f"arquivo {migration.checksum[:12]}...)"
            )
            if record.get("kind") == "baseline":
                report.warnings.append(msg + " - arquivo de baseline, nunca executado pelo runner; ignorado")
            else:
                errors.append(msg + " - migracao aplicada nao pode ser editada; crie uma migracao nova")

    for migration in migrations:
        if migration.filename in records:
            continue
        other = recorded_versions.get(migration.version)
        if other is not None and other != migration.filename:
            errors.append(
                f"{migration.filename}: versao {migration.version:03d} ja registrada como {other} "
                "(arquivo renomeado?)"
            )

    if errors:
        raise MigrationChecksumError("; ".join(errors))


def _new_lock_owner() -> str:
    request_id = os.environ.get("AWS_LAMBDA_LOG_STREAM_NAME") or socket.gethostname()
    return f"{request_id}:{os.getpid()}:{uuid.uuid4().hex[:8]}"[:255]


def _acquire_lock(conn: Any, owner: str, ttl_seconds: int = LOCK_TTL_SECONDS) -> None:
    cur = conn.cursor()
    cur.execute(
        f"INSERT IGNORE INTO {LOCK_TABLE} (lock_name, owner, acquired_at, expires_at) "
        "VALUES (%s, %s, UTC_TIMESTAMP(), UTC_TIMESTAMP() + INTERVAL %s SECOND)",
        (LOCK_NAME, owner, ttl_seconds),
    )
    conn.commit()
    # Lease expirado (processo anterior morreu sem liberar): toma o lock.
    cur.execute(
        f"UPDATE {LOCK_TABLE} SET owner = %s, acquired_at = UTC_TIMESTAMP(), "
        "expires_at = UTC_TIMESTAMP() + INTERVAL %s SECOND "
        "WHERE lock_name = %s AND expires_at < UTC_TIMESTAMP()",
        (owner, ttl_seconds, LOCK_NAME),
    )
    conn.commit()
    cur.execute(f"SELECT owner, expires_at FROM {LOCK_TABLE} WHERE lock_name = %s", (LOCK_NAME,))
    row = cur.fetchone()
    if not row or row["owner"] != owner:
        holder = row["owner"] if row else "?"
        until = row["expires_at"] if row else "?"
        raise MigrationLockError(
            f"Lock de migracao em uso por {holder} (expira em {until} UTC). "
            "Outra execucao esta em andamento; tente de novo depois."
        )


def _refresh_lock(conn: Any, owner: str, ttl_seconds: int = LOCK_TTL_SECONDS) -> None:
    cur = conn.cursor()
    cur.execute(
        f"UPDATE {LOCK_TABLE} SET expires_at = UTC_TIMESTAMP() + INTERVAL %s SECOND "
        "WHERE lock_name = %s AND owner = %s",
        (ttl_seconds, LOCK_NAME, owner),
    )
    conn.commit()


def _release_lock(conn: Any, owner: str) -> None:
    try:
        cur = conn.cursor()
        cur.execute(f"DELETE FROM {LOCK_TABLE} WHERE lock_name = %s AND owner = %s", (LOCK_NAME, owner))
        conn.commit()
    except Exception:  # noqa: BLE001 - o lease expira sozinho; nao mascarar o erro original
        logger.warning("Nao foi possivel liberar o lock de migracao (expira sozinho).", exc_info=True)


#: Tentativas de conexao do runner enquanto o Aurora retoma do auto-pause (M39).
#: Este retry fica POR CIMA do retry de `db_dataapi` (`_RESUME_RETRY_WAITS`:
#: 5 tentativas, 20 s de espera por chamada). Cada tentativa daqui faz ate duas
#: chamadas ao Data API (BeginTransaction ao abrir a conexao + ExecuteStatement
#: do `SELECT 1`), ou seja ate ~40 s. Pior caso:
#:   attempts x 40 s + (attempts - 1) x delay = 3 x 40 + 2 x 10 = ~140 s
#: contra `timeout = 300` da Lambda `musclefit-migrations`
#: (infra/lambda_migrations.tf). Com o antigo default 6 eram 6 x 40 + 5 x 10 =
#: 290 s, sem folga para aplicar as migracoes. Ha teste unitario travando isso
#: (tests/unit/test_migrate.py).
CONNECT_RETRY_ATTEMPTS = 3
CONNECT_RETRY_DELAY_SECONDS = 10.0


def _connect_with_retry(
    conn_factory: ConnFactory,
    attempts: int = CONNECT_RETRY_ATTEMPTS,
    delay_seconds: float = CONNECT_RETRY_DELAY_SECONDS,
) -> Any:
    """Aurora Serverless com auto-pause responde DatabaseResumingException na
    primeira chamada; espera o cluster voltar em vez de falhar o deploy.

    Os defaults cabem no `timeout` da Lambda de migracoes somados ao retry
    proprio de `db_dataapi` - ver `CONNECT_RETRY_ATTEMPTS`."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            conn = conn_factory()
            conn.cursor().execute("SELECT 1 AS n")
            return conn
        except Exception as exc:  # noqa: BLE001
            if not _is_resuming_error(exc) or attempt == attempts:
                raise
            last_exc = exc
            logger.warning("Aurora retomando apos auto-pause (tentativa %s/%s); aguardando...", attempt, attempts)
            time.sleep(delay_seconds)
    raise MigrationError(f"Banco indisponivel: {last_exc}")


# ---------------------------------------------------------------------------
# Execucao
# ---------------------------------------------------------------------------


def _run_one(conn: Any, migration: Migration, dry_run: bool) -> list[str]:
    db = MigrationDB(conn, dry_run=dry_run)
    if migration.kind == "sql":
        statements = split_sql_statements(migration.path.read_text(encoding="utf-8"))
        validate_sql_statements(migration.filename, statements)
        for stmt in statements:
            db.execute(stmt)
    else:
        up = load_python_migration(migration.path)
        up(db)
    if not dry_run:
        conn.commit()
    return db.statements


def migrate(
    conn_factory: Optional[ConnFactory] = None,
    migrations_dir: Optional[Path] = None,
    dry_run: bool = False,
) -> MigrationReport:
    started = time.monotonic()
    migrations = discover_migrations(migrations_dir)
    report = MigrationReport(dry_run=dry_run)
    conn = _connect_with_retry(conn_factory or _default_conn_factory)
    owner: Optional[str] = None
    try:
        if not dry_run:
            cur = conn.cursor()
            cur.execute(_CREATE_CONTROL_TABLE_SQL)
            cur.execute(_CREATE_LOCK_TABLE_SQL)
            conn.commit()
            owner = _new_lock_owner()
            _acquire_lock(conn, owner)
            report.lock_owner = owner

        records = _load_records(conn)
        _check_consistency(migrations, records, report)

        for migration in migrations:
            if migration.filename in records:
                report.already_applied += 1
                continue
            if migration.is_baseline:
                if not dry_run:
                    _insert_record(conn, migration, "baseline", None)
                report.baselined.append(migration.filename)
                continue

            if owner:
                _refresh_lock(conn, owner)
            logger.info("%s migracao %s", "Planejando" if dry_run else "Aplicando", migration.filename)
            t0 = time.monotonic()
            try:
                statements = _run_one(conn, migration, dry_run)
            except MigrationError:
                raise
            except Exception as exc:  # noqa: BLE001
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                raise MigrationExecutionError(f"{migration.filename}: {exc}") from exc
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            if not dry_run:
                _insert_record(conn, migration, "applied", elapsed_ms)
            report.applied.append(
                AppliedMigration(
                    filename=migration.filename,
                    version=migration.version,
                    execution_ms=elapsed_ms,
                    statements=statements,
                )
            )
    finally:
        if owner:
            _release_lock(conn, owner)
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    for warning in report.warnings:
        logger.warning(warning)
    report.duration_ms = int((time.monotonic() - started) * 1000)
    return report


def status(conn_factory: Optional[ConnFactory] = None, migrations_dir: Optional[Path] = None) -> list[dict[str, Any]]:
    """Estado de cada migracao, sem escrever nada no banco."""
    migrations = discover_migrations(migrations_dir)
    conn = _connect_with_retry(conn_factory or _default_conn_factory)
    try:
        records = _load_records(conn)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    rows: list[dict[str, Any]] = []
    for migration in migrations:
        record = records.get(migration.filename)
        if record is None:
            state = "pending-baseline" if migration.is_baseline else "pending"
        elif record["checksum"] != migration.checksum:
            state = f"{record['kind']}-checksum-mismatch"
        else:
            state = str(record["kind"])
        rows.append({"filename": migration.filename, "version": migration.version, "state": state})
    known = {m.filename for m in migrations}
    for name, record in sorted(records.items()):
        if name not in known:
            rows.append({"filename": name, "version": int(record["version"]), "state": "missing-file"})
    return rows


# ---------------------------------------------------------------------------
# Lambda + CLI
# ---------------------------------------------------------------------------


def _guard_backend_in_lambda() -> None:
    # Regra anti-vazamento: dentro de uma Lambda real o backend TEM que ser o
    # Data API. DB_BACKEND=pymysql ali significaria config de dev vazada.
    exigir_data_api_em_lambda(MigrationError)


# Margem entre o fim do orcamento do replay e o timeout da Lambda: tempo para
# o rollback + a resposta chegarem antes de a funcao ser morta. Ser morto pelo
# timeout tambem NAO deixa escrita parcial (sem commit, a Data API descarta a
# transacao), mas devolve "Task timed out" em vez de um erro explicando o que
# faltou - por isso o orcamento.
REPLAY_TIME_RESERVE_SECONDS = 25.0


def _replay_time_budget(context: Any) -> float:
    """Orcamento do replay a partir do tempo que a Lambda ainda tem."""
    remaining = getattr(context, "get_remaining_time_in_millis", None)
    if not callable(remaining):
        from .replay_progression import DEFAULT_TIME_BUDGET_SECONDS

        return DEFAULT_TIME_BUDGET_SECONDS
    return max(5.0, float(remaining()) / 1000.0 - REPLAY_TIME_RESERVE_SECONDS)


def _replay_user_ids(raw: Any) -> Optional[list[int]]:
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)) or not raw:
        raise MigrationError(f"user_ids invalido: {raw!r} (esperado lista nao vazia de inteiros)")
    try:
        return [int(value) for value in raw]
    except (TypeError, ValueError) as exc:
        raise MigrationError(f"user_ids invalido: {raw!r} (esperado lista de inteiros)") from exc


def _replay_progression(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Acao `replay-progression`: passo 1 da ativacao dos motores
    determinísticos (ver `app/replay_progression.py` e
    `docs/refatoracao-motores/ATIVACAO_MOTORES_DETERMINISTICOS.md`).

    `dry_run` default TRUE: um evento sem o campo nunca escreve por acidente.
    `cleanup` (retencao D15) NAO e exposto aqui de proposito - apagar decisao
    antiga e outra operacao, com outro risco, e nao faz parte da ativacao."""
    from .replay_progression import replay

    dry_run = event.get("dry_run", True)
    if not isinstance(dry_run, bool):
        raise MigrationError(f"dry_run invalido: {dry_run!r} (esperado true ou false)")
    report = replay(
        _replay_user_ids(event.get("user_ids")),
        dry_run=dry_run,
        time_budget_seconds=_replay_time_budget(context),
    )
    return report.to_dict()


def handler(event: Any, context: Any) -> dict[str, Any]:
    """Handler da Lambda `musclefit-migrations`.

    Evento:
    - {"action": "migrate" | "dry-run" | "status"} (default: migrate) - runner
      de migracoes de schema;
    - {"action": "replay-progression", "dry_run": true|false, "user_ids": [..]}
      - replay das trilhas da progressao (`app/replay_progression.py`), que nao
      toca em schema nenhum. Fica nesta Lambda porque e a unica com acesso de
      escrita ao banco fora do caminho HTTP, e por isso a unica forma de rodar
      uma operacao de dados sem acesso manual a AWS.

    Erros sao relancados: a invocacao termina com FunctionError e o
    job backend-deploy de deploy-producao.yml falha antes de atualizar as
    demais Lambdas."""
    from .logging_config import configure_logging

    configure_logging()
    logging.getLogger().setLevel(logging.INFO)
    _guard_backend_in_lambda()
    payload: dict[str, Any] = event if isinstance(event, dict) else {}
    action = payload.get("action", "migrate")
    if action == "status":
        result = {"status": "ok", "migrations": status()}
    elif action in ("migrate", "dry-run"):
        result = migrate(dry_run=action == "dry-run").to_dict()
    elif action == "replay-progression":
        result = _replay_progression(payload, context)
    else:
        raise MigrationError(f"action invalida: {action!r}")
    logger.info("resultado: %s", json.dumps(result, default=str))
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.migrate", description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="mostra o plano sem escrever no banco")
    group.add_argument("--status", action="store_true", help="lista o estado das migracoes (somente leitura)")
    parser.add_argument("--migrations-dir", type=Path, default=None, help="default: api/db/migrations ou $MIGRATIONS_DIR")
    parser.add_argument("--json", action="store_true", help="saida em JSON")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        if args.status:
            rows = status(migrations_dir=args.migrations_dir)
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                for row in rows:
                    print(f"{row['filename']:<60} {row['state']}")
            return 1 if any("mismatch" in r["state"] for r in rows) else 0

        report = migrate(migrations_dir=args.migrations_dir, dry_run=args.dry_run)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2, default=str))
        else:
            prefix = "[dry-run] " if report.dry_run else ""
            print(f"{prefix}baseline registradas: {len(report.baselined)}")
            print(f"{prefix}ja aplicadas: {report.already_applied}")
            print(f"{prefix}{'a aplicar' if report.dry_run else 'aplicadas'}: {len(report.applied)}")
            for item in report.applied:
                print(f"  - {item.filename} ({item.execution_ms} ms, {len(item.statements)} statement(s))")
                if report.dry_run:
                    for stmt in item.statements:
                        print(f"      {stmt}")
            for warning in report.warnings:
                print(f"AVISO: {warning}")
        return 0
    except MigrationError as exc:
        logger.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
