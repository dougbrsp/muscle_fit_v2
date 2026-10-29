"""Testes unitarios do runner de migracoes (sem banco: fake connection).

Rodar: cd api && python -m pytest tests/unit/test_migrate.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import pytest

from app import migrate as m

REPO_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"


# ---------------------------------------------------------------------------
# Fake DB: interpreta apenas o SQL que o runner e as migracoes emitem.
# ---------------------------------------------------------------------------


class FakeState:
    def __init__(self) -> None:
        self.tables: set[str] = set()
        self.columns: dict[str, set[str]] = {}
        self.indexes: dict[str, set[str]] = {}
        self.fks: dict[str, set[str]] = {}
        self.records: dict[str, dict[str, Any]] = {}
        self.lock: Optional[dict[str, Any]] = None
        self.lock_expired = False
        self.executed: list[str] = []
        self.commits = 0
        self.fail_on: Optional[str] = None


def _norm(sql: str) -> str:
    return " ".join(sql.split())


class FakeCursor:
    def __init__(self, state: FakeState):
        self.s = state
        self._rows: list[dict[str, Any]] = []
        self.rowcount = 0

    def execute(self, sql: str, params: Any = None) -> "FakeCursor":
        s, q, p = self.s, _norm(sql), list(params or [])
        self._rows, self.rowcount = [], 0

        def count(flag: bool) -> None:
            self._rows = [{"n": 1 if flag else 0}]

        if q == "SELECT 1 AS n":
            count(True)
        elif q.startswith(f"CREATE TABLE IF NOT EXISTS {m.CONTROL_TABLE}") or q.startswith(
            f"CREATE TABLE IF NOT EXISTS {m.LOCK_TABLE}"
        ):
            s.tables.add(q.split()[5])
        elif "FROM information_schema.TABLES" in q:
            count(p[0] in s.tables)
        elif "FROM information_schema.COLUMNS" in q:
            count(p[1] in s.columns.get(p[0], set()))
        elif "FROM information_schema.STATISTICS" in q and "INDEX_NAME" in q:
            count(p[1] in s.indexes.get(p[0], set()))
        elif "FROM information_schema.STATISTICS" in q:
            count(False)
        elif "FROM information_schema.TABLE_CONSTRAINTS" in q:
            count(p[1] in s.fks.get(p[0], set()))
        elif "FROM information_schema.KEY_COLUMN_USAGE" in q:
            count(False)
        elif q.startswith(f"SELECT filename, version, kind, checksum FROM {m.CONTROL_TABLE}"):
            self._rows = [dict(r) for r in s.records.values()]
        elif q.startswith(f"INSERT INTO {m.CONTROL_TABLE}"):
            s.records[p[0]] = {"filename": p[0], "version": p[1], "kind": p[2], "checksum": p[3], "execution_ms": p[4]}
        elif q.startswith(f"INSERT IGNORE INTO {m.LOCK_TABLE}"):
            if s.lock is None:
                s.lock = {"owner": p[1], "expires_at": "future"}
                self.rowcount = 1
        elif q.startswith(f"UPDATE {m.LOCK_TABLE} SET owner"):
            if s.lock is not None and s.lock_expired:
                s.lock = {"owner": p[0], "expires_at": "future"}
                s.lock_expired = False
        elif q.startswith(f"UPDATE {m.LOCK_TABLE} SET expires_at"):
            pass
        elif q.startswith(f"SELECT owner, expires_at FROM {m.LOCK_TABLE}"):
            self._rows = [dict(s.lock)] if s.lock else []
        elif q.startswith(f"DELETE FROM {m.LOCK_TABLE}"):
            if s.lock and s.lock["owner"] == p[1]:
                s.lock = None
        else:
            # Statement de migracao: registra e aplica efeitos simples no schema.
            if s.fail_on and s.fail_on in q:
                raise RuntimeError(f"erro simulado em: {q}")
            s.executed.append(q)
            if mt := re.match(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)", q):
                s.tables.add(mt.group(1))
                s.columns.setdefault(mt.group(1), set())
            elif mt := re.match(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", q):
                s.columns.setdefault(mt.group(1), set()).add(mt.group(2))
            elif mt := re.match(r"ALTER TABLE (\w+) ADD INDEX (\w+)", q):
                s.indexes.setdefault(mt.group(1), set()).add(mt.group(2))
            elif mt := re.match(r"ALTER TABLE (\w+) ADD CONSTRAINT (\w+) FOREIGN KEY", q):
                s.fks.setdefault(mt.group(1), set()).add(mt.group(2))
        return self

    def fetchone(self) -> Optional[dict[str, Any]]:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class FakeConn:
    def __init__(self, state: FakeState):
        self.s = state
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.s)

    def commit(self) -> None:
        self.s.commits += 1

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def state() -> FakeState:
    return FakeState()


@pytest.fixture
def factory(state: FakeState):
    return lambda: FakeConn(state)


def _write(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_bytes(content.encode("utf-8"))
    return path


@pytest.fixture
def mig_dir(tmp_path: Path) -> Path:
    _write(tmp_path, "001_schema.sql", "CREATE TABLE baseline_001 (id INT);\n")
    _write(tmp_path, "021_legacy.sql", "CREATE TABLE baseline_021 (id INT);\n")
    _write(
        tmp_path,
        "022_new_table.py",
        "def up(db):\n"
        "    if not db.table_exists('widgets'):\n"
        "        db.execute('CREATE TABLE widgets (id INT)')\n"
        "    if not db.column_exists('widgets', 'color'):\n"
        "        db.execute('ALTER TABLE widgets ADD COLUMN color VARCHAR(20) NULL')\n",
    )
    _write(tmp_path, "023_seed.sql", "-- comentario; com ponto e virgula\nINSERT IGNORE INTO widgets (id) VALUES (1);\n")
    _write(tmp_path, "README.md", "nao e migracao")
    return tmp_path


# ---------------------------------------------------------------------------
# Descoberta / parsing
# ---------------------------------------------------------------------------


def test_discover_orders_by_version_and_ignores_other_files(tmp_path: Path) -> None:
    _write(tmp_path, "010_b.sql", "SELECT 1;")
    _write(tmp_path, "002_a.py", "def up(db):\n    pass\n")
    _write(tmp_path, "100_c.sql", "SELECT 1;")
    _write(tmp_path, "notes.txt", "x")
    found = m.discover_migrations(tmp_path)
    assert [x.filename for x in found] == ["002_a.py", "010_b.sql", "100_c.sql"]
    assert [x.kind for x in found] == ["py", "sql", "sql"]


def test_discover_rejects_duplicate_version(tmp_path: Path) -> None:
    _write(tmp_path, "022_a.sql", "SELECT 1;")
    _write(tmp_path, "022_b.py", "def up(db):\n    pass\n")
    with pytest.raises(m.MigrationError, match="duplicada"):
        m.discover_migrations(tmp_path)


def test_discover_rejects_malformed_name(tmp_path: Path) -> None:
    _write(tmp_path, "022-bad-name.sql", "SELECT 1;")
    with pytest.raises(m.MigrationError, match="nome invalido"):
        m.discover_migrations(tmp_path)


def test_checksum_ignores_line_endings(tmp_path: Path) -> None:
    a = tmp_path / "a.sql"
    b = tmp_path / "b.sql"
    a.write_bytes(b"SELECT 1;\nSELECT 2;\n")
    b.write_bytes(b"SELECT 1;\r\nSELECT 2;\r\n")
    assert m.compute_checksum(a) == m.compute_checksum(b)


def test_split_sql_respects_strings_and_comments() -> None:
    script = (
        "-- cabecalho; ignorado\n"
        "CREATE TABLE t (v VARCHAR(10) DEFAULT 'a;b');\n"
        "/* bloco; */ INSERT INTO t VALUES ('it''s; ok');\n"
        "# outro comentario;\n"
        "UPDATE t SET v = \"x;y\""
    )
    assert m.split_sql_statements(script) == [
        "CREATE TABLE t (v VARCHAR(10) DEFAULT 'a;b')",
        "INSERT INTO t VALUES ('it''s; ok')",
        'UPDATE t SET v = "x;y"',
    ]


def test_repo_migrations_are_discoverable_and_022_is_first_executable() -> None:
    found = m.discover_migrations(REPO_MIGRATIONS_DIR)
    versions = [x.version for x in found]
    assert versions == sorted(versions)
    assert versions[:22] == list(range(1, 23))
    executable = [x for x in found if not x.is_baseline]
    assert executable[0].filename == "022_equipment_types_parity.py"


# ---------------------------------------------------------------------------
# Baseline / execucao
# ---------------------------------------------------------------------------


def test_baseline_is_recorded_without_executing(state: FakeState, factory, mig_dir: Path) -> None:
    report = m.migrate(conn_factory=factory, migrations_dir=mig_dir)

    assert report.baselined == ["001_schema.sql", "021_legacy.sql"]
    assert [a.filename for a in report.applied] == ["022_new_table.py", "023_seed.sql"]
    assert not any("baseline_" in stmt for stmt in state.executed)
    assert state.records["001_schema.sql"]["kind"] == "baseline"
    assert state.records["022_new_table.py"]["kind"] == "applied"
    assert state.lock is None  # lock liberado


def test_second_run_is_noop(state: FakeState, factory, mig_dir: Path) -> None:
    m.migrate(conn_factory=factory, migrations_dir=mig_dir)
    executed_before = list(state.executed)
    report = m.migrate(conn_factory=factory, migrations_dir=mig_dir)
    assert report.applied == [] and report.baselined == []
    assert report.already_applied == 4
    assert state.executed == executed_before


def test_py_migration_helpers_skip_existing_objects(state: FakeState, factory, mig_dir: Path) -> None:
    state.tables.add("widgets")
    state.columns["widgets"] = {"color"}
    report = m.migrate(conn_factory=factory, migrations_dir=mig_dir)
    applied = {a.filename: a for a in report.applied}
    assert applied["022_new_table.py"].statements == []
    assert "022_new_table.py" in state.records


def test_sql_statements_executed_one_by_one(state: FakeState, factory, mig_dir: Path) -> None:
    _write(mig_dir, "024_two.sql", "CREATE TABLE IF NOT EXISTS a (id INT);\nCREATE TABLE IF NOT EXISTS b (id INT);\n")
    m.migrate(conn_factory=factory, migrations_dir=mig_dir)
    assert state.executed[-2:] == ["CREATE TABLE IF NOT EXISTS a (id INT)", "CREATE TABLE IF NOT EXISTS b (id INT)"]


def test_prepare_is_rejected_in_new_sql_migration(state: FakeState, factory, mig_dir: Path) -> None:
    _write(mig_dir, "024_bad.sql", "SET @ddl = 'ALTER TABLE x ADD COLUMN y INT';\nPREPARE s FROM @ddl;\n")
    with pytest.raises(m.MigrationError, match="nao suportado"):
        m.migrate(conn_factory=factory, migrations_dir=mig_dir)
    assert "024_bad.sql" not in state.records
    assert state.lock is None


# ---------------------------------------------------------------------------
# Checksum
# ---------------------------------------------------------------------------


def test_changed_applied_migration_fails_before_executing(state: FakeState, factory, mig_dir: Path) -> None:
    m.migrate(conn_factory=factory, migrations_dir=mig_dir)
    _write(mig_dir, "023_seed.sql", "INSERT IGNORE INTO widgets (id) VALUES (2);\n")
    _write(mig_dir, "024_next.sql", "CREATE TABLE IF NOT EXISTS nxt (id INT);\n")
    executed_before = list(state.executed)

    with pytest.raises(m.MigrationChecksumError, match="023_seed.sql"):
        m.migrate(conn_factory=factory, migrations_dir=mig_dir)

    assert state.executed == executed_before  # 024 nao rodou
    assert "024_next.sql" not in state.records
    assert state.lock is None


def test_changed_baseline_file_only_warns(state: FakeState, factory, mig_dir: Path) -> None:
    m.migrate(conn_factory=factory, migrations_dir=mig_dir)
    _write(mig_dir, "021_legacy.sql", "-- comentario novo\nCREATE TABLE baseline_021 (id INT);\n")
    report = m.migrate(conn_factory=factory, migrations_dir=mig_dir)
    assert any("021_legacy.sql" in w for w in report.warnings)


def test_renamed_applied_migration_is_detected(state: FakeState, factory, mig_dir: Path) -> None:
    m.migrate(conn_factory=factory, migrations_dir=mig_dir)
    (mig_dir / "023_seed.sql").rename(mig_dir / "023_seed_renamed.sql")
    with pytest.raises(m.MigrationChecksumError, match="renomeado"):
        m.migrate(conn_factory=factory, migrations_dir=mig_dir)


# ---------------------------------------------------------------------------
# Dry-run / status / lock / falha
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(state: FakeState, factory, mig_dir: Path) -> None:
    report = m.migrate(conn_factory=factory, migrations_dir=mig_dir, dry_run=True)

    assert report.dry_run is True
    assert report.baselined == ["001_schema.sql", "021_legacy.sql"]
    planned = {a.filename: a.statements for a in report.applied}
    assert planned["022_new_table.py"][0] == "CREATE TABLE widgets (id INT)"
    assert planned["023_seed.sql"] == ["INSERT IGNORE INTO widgets (id) VALUES (1)"]
    assert state.executed == []
    assert state.records == {}
    assert m.CONTROL_TABLE not in state.tables
    assert state.lock is None


def test_status_reports_states(state: FakeState, factory, mig_dir: Path) -> None:
    rows = {r["filename"]: r["state"] for r in m.status(conn_factory=factory, migrations_dir=mig_dir)}
    assert rows == {
        "001_schema.sql": "pending-baseline",
        "021_legacy.sql": "pending-baseline",
        "022_new_table.py": "pending",
        "023_seed.sql": "pending",
    }
    m.migrate(conn_factory=factory, migrations_dir=mig_dir)
    rows = {r["filename"]: r["state"] for r in m.status(conn_factory=factory, migrations_dir=mig_dir)}
    assert rows["001_schema.sql"] == "baseline" and rows["023_seed.sql"] == "applied"


def test_lock_held_by_other_owner_blocks(state: FakeState, factory, mig_dir: Path) -> None:
    state.tables.update({m.CONTROL_TABLE, m.LOCK_TABLE})
    state.lock = {"owner": "outro-processo", "expires_at": "future"}
    with pytest.raises(m.MigrationLockError, match="outro-processo"):
        m.migrate(conn_factory=factory, migrations_dir=mig_dir)
    assert state.executed == []
    assert state.lock["owner"] == "outro-processo"  # nao liberou lock alheio


def test_expired_lock_is_taken_over(state: FakeState, factory, mig_dir: Path) -> None:
    state.tables.update({m.CONTROL_TABLE, m.LOCK_TABLE})
    state.lock = {"owner": "processo-morto", "expires_at": "past"}
    state.lock_expired = True
    report = m.migrate(conn_factory=factory, migrations_dir=mig_dir)
    assert len(report.applied) == 2
    assert state.lock is None


def test_failed_migration_is_not_recorded_and_releases_lock(state: FakeState, factory, mig_dir: Path) -> None:
    state.fail_on = "INSERT IGNORE INTO widgets"
    with pytest.raises(m.MigrationExecutionError, match="023_seed.sql"):
        m.migrate(conn_factory=factory, migrations_dir=mig_dir)
    assert "022_new_table.py" in state.records
    assert "023_seed.sql" not in state.records
    assert state.lock is None


def test_handler_returns_json_report(state: FakeState, factory, mig_dir: Path, monkeypatch) -> None:
    monkeypatch.setattr(m, "_default_conn_factory", factory)
    monkeypatch.setenv("MIGRATIONS_DIR", str(mig_dir))
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "musclefit-migrations")
    monkeypatch.delenv("DB_BACKEND", raising=False)
    result = m.handler({"action": "migrate"}, None)
    assert result["status"] == "ok"
    assert [a["filename"] for a in result["applied"]] == ["022_new_table.py", "023_seed.sql"]


def test_handler_refuses_pymysql_backend_inside_lambda(monkeypatch) -> None:
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "musclefit-migrations")
    monkeypatch.setenv("DB_BACKEND", "pymysql")
    with pytest.raises(m.MigrationError, match="data-api"):
        m.handler({}, None)


# ---------------------------------------------------------------------------
# Migracao 022 real
# ---------------------------------------------------------------------------


def _run_022(state: FakeState, dry_run: bool = False) -> list[str]:
    up = m.load_python_migration(REPO_MIGRATIONS_DIR / "022_equipment_types_parity.py")
    db = m.MigrationDB(FakeConn(state), dry_run=dry_run)
    up(db)
    return db.statements


def test_022_creates_table_columns_index_and_fk_on_fresh_local_schema(state: FakeState) -> None:
    state.tables.add("equipment")
    state.columns["equipment"] = {"id", "name", "notes", "photo_path", "is_active", "created_at", "updated_at"}
    statements = _run_022(state)
    assert statements[0].startswith("CREATE TABLE IF NOT EXISTS equipment_types")
    assert statements[1:5] == [
        "ALTER TABLE equipment ADD COLUMN equipment_type_id BIGINT UNSIGNED NULL",
        "ALTER TABLE equipment ADD COLUMN manufacturer VARCHAR(100) NULL",
        "ALTER TABLE equipment ADD COLUMN model VARCHAR(150) NULL",
        "ALTER TABLE equipment ADD COLUMN location VARCHAR(150) NULL",
    ]
    assert statements[5] == "ALTER TABLE equipment ADD INDEX idx_equipment_type (equipment_type_id)"
    assert statements[6].startswith("ALTER TABLE equipment ADD CONSTRAINT fk_equipment_type FOREIGN KEY")
    assert _run_022(state) == []  # idempotente


def test_022_is_noop_on_aurora_like_schema(state: FakeState) -> None:
    state.tables.update({"equipment", "equipment_types"})
    state.columns["equipment"] = {"equipment_type_id", "manufacturer", "model", "location"}
    state.indexes["equipment"] = {"idx_equipment_type"}
    state.fks["equipment"] = {"fk_equipment_type"}
    assert _run_022(state) == []


# ---------------------------------------------------------------------------
# M39: retry de conexao do runner cabe no timeout da Lambda de migracoes
# ---------------------------------------------------------------------------

_LAMBDA_MIGRATIONS_TF = Path(__file__).resolve().parents[3] / "infra" / "lambda_migrations.tf"

#: Chamadas ao Data API por tentativa de `_connect_with_retry`: BeginTransaction
#: (ao abrir a conexao) + ExecuteStatement (`SELECT 1`), cada uma com o retry
#: proprio de `db_dataapi`.
_DATAAPI_CALLS_PER_CONNECT_ATTEMPT = 2


def _resuming_exc() -> Exception:
    return RuntimeError("An error occurred (DatabaseResumingException) when calling the ExecuteStatement operation")


def _lambda_migrations_timeout() -> int:
    tf = _LAMBDA_MIGRATIONS_TF.read_text(encoding="utf-8")
    bloco = tf.split('resource "aws_lambda_function" "musclefit_migrations"', 1)[1]
    match = re.search(r"^\s*timeout\s*=\s*(\d+)", bloco, re.MULTILINE)
    assert match, "timeout da musclefit-migrations nao encontrado em infra/lambda_migrations.tf"
    return int(match.group(1))


def test_connect_retry_default_is_3_attempts_of_10s() -> None:
    import inspect

    params = inspect.signature(m._connect_with_retry).parameters
    assert m.CONNECT_RETRY_ATTEMPTS == 3
    assert m.CONNECT_RETRY_DELAY_SECONDS == 10.0
    assert params["attempts"].default == m.CONNECT_RETRY_ATTEMPTS
    assert params["delay_seconds"].default == m.CONNECT_RETRY_DELAY_SECONDS


def test_connect_retry_worst_case_fits_lambda_timeout_with_margin() -> None:
    from app import db_dataapi

    per_dataapi_call = sum(db_dataapi._RESUME_RETRY_WAITS)  # 20 s hoje
    per_attempt = _DATAAPI_CALLS_PER_CONNECT_ATTEMPT * per_dataapi_call  # 40 s
    worst = m.CONNECT_RETRY_ATTEMPTS * per_attempt + (m.CONNECT_RETRY_ATTEMPTS - 1) * m.CONNECT_RETRY_DELAY_SECONDS
    timeout = _lambda_migrations_timeout()
    assert worst == 140.0
    # Pelo menos metade do timeout livre para aplicar as migracoes de fato.
    assert worst <= timeout / 2, f"pior caso {worst}s de retry contra timeout {timeout}s"


def test_connect_retry_gives_up_after_default_attempts(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(m.time, "sleep", sleeps.append)
    calls = {"n": 0}

    def always_resuming() -> Any:
        calls["n"] += 1
        raise _resuming_exc()

    with pytest.raises(RuntimeError, match="DatabaseResumingException"):
        m._connect_with_retry(always_resuming)
    assert calls["n"] == 3
    assert sleeps == [10.0, 10.0]


def test_connect_retry_succeeds_when_aurora_wakes_up(factory, monkeypatch) -> None:
    monkeypatch.setattr(m.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def wakes_on_third() -> Any:
        calls["n"] += 1
        if calls["n"] < 3:
            raise _resuming_exc()
        return factory()

    assert m._connect_with_retry(wakes_on_third) is not None
    assert calls["n"] == 3
