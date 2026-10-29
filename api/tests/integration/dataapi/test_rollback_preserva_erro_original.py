"""Achado B (hardening): `rollback()`/`close()` do shim `app/db_dataapi.py`
nunca substituem a excecao original levantada dentro de `transaction()` /
`with conn:`.

Cenario REAL (padrao do dublê, medido na Aurora de producao): erro de SQL
(1146, 1062) NAO aborta a transacao da Data API; o rollback funciona e a
excecao original sobe. Cenario HIPOTETICO (`abort_transaction_on_error=True`,
mesmo efeito de uma transacao expirada): o rollback recebe
`TransactionNotFoundException`, e o shim precisa tratar como ja revertida.
Falha real do rollback (ex. throttling) e simulada com `inject_error`.
"""

from __future__ import annotations

import logging

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException

# Importado no topo de proposito: `use_dataapi` so troca o backend de modulos
# `app.*` ja importados (ver tests/support/dataapi_backend.py).
from app import db_dataapi, workout_plan_templates
from app.models import WorkoutPlanTemplateAssignIn
from app.security import Identity
from tests.support.dataapi_backend import use_backend

TABLE = "dataapi_rollback_probe"
LOGGER = "musclefit.db_dataapi"

ABORT_MODE_IDS = ["producao-nao-aborta", "hipotetico-transacao-inexistente"]
ABORT_MODES = pytest.mark.parametrize("abort_on_error", [False, True], ids=ABORT_MODE_IDS)


@pytest.fixture
def probe(raw, dataapi_db):
    with raw.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {TABLE}")
        cur.execute(f"CREATE TABLE {TABLE} (id BIGINT AUTO_INCREMENT PRIMARY KEY, uq VARCHAR(20) NOT NULL UNIQUE) ENGINE=InnoDB")
    yield dataapi_db
    # Rollback falhando de proposito (inject_error) deixa a transacao do dublê
    # aberta segurando metadata lock; sem fechar antes, o DROP TABLE esperaria
    # indefinidamente (o teardown de `fake_rds` so roda depois deste).
    dataapi_db.close()
    with raw.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {TABLE}")


def _count(raw):
    with raw.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM {TABLE}")
        return cur.fetchone()["n"]


def _duplicate(conn, value="dup"):
    cur = conn.cursor()
    cur.execute(f"INSERT INTO {TABLE} (uq) VALUES (%s)", (value,))
    cur.execute(f"INSERT INTO {TABLE} (uq) VALUES (%s)", (value,))


def _gone_warnings(caplog):
    return [r for r in caplog.records if r.name == LOGGER and getattr(r, "event", None) == "dataapi_rollback_transaction_gone"]


# ---------------------------------------------------------------------------
# transaction() / with conn: preservam o DataApiError 1062
# ---------------------------------------------------------------------------


@ABORT_MODES
def test_transaction_duplicate_key_sobe_data_api_error_original(probe, raw, caplog, abort_on_error):
    probe.abort_transaction_on_error = abort_on_error
    caplog.set_level(logging.WARNING, logger=LOGGER)
    with pytest.raises(db_dataapi.DataApiError) as exc:
        with db_dataapi.transaction() as conn:
            _duplicate(conn)
    assert db_dataapi.is_duplicate_key_error(exc.value)
    assert "1062" in str(exc.value)
    assert not getattr(exc.value, "__notes__", None)
    assert conn.transaction_id is None
    assert probe.open_transactions == []
    assert _count(raw) == 0  # o primeiro INSERT foi revertido nos dois modos
    rollbacks = probe.calls_of("RollbackTransaction")
    assert len(rollbacks) == 1  # close() nao tenta de novo com o id morto
    if abort_on_error:
        assert rollbacks[0].error_code == "TransactionNotFoundException"
        (record,) = _gone_warnings(caplog)
        assert record.levelno == logging.WARNING
        assert record.aws_error_code == "TransactionNotFoundException"
        assert rollbacks[0].params["transactionId"] not in record.getMessage()
    else:
        assert rollbacks[0].error_code is None
        assert _gone_warnings(caplog) == []


@ABORT_MODES
def test_with_conn_duplicate_key_sobe_data_api_error_original(probe, raw, abort_on_error):
    probe.abort_transaction_on_error = abort_on_error
    with pytest.raises(db_dataapi.DataApiError) as exc:
        with db_dataapi.get_conn() as conn:
            _duplicate(conn)
    assert db_dataapi.is_duplicate_key_error(exc.value)
    assert conn.transaction_id is None
    assert probe.open_transactions == []
    assert len(probe.calls_of("RollbackTransaction")) == 1
    assert _count(raw) == 0


@ABORT_MODES
def test_erro_capturado_seguido_de_close_nao_levanta(probe, abort_on_error):
    """Codigo que captura o DataApiError e depois fecha a conexao (padrao
    try/finally conn.close()) nao pode ver o close() explodir."""
    probe.abort_transaction_on_error = abort_on_error
    conn = db_dataapi.get_conn()
    try:
        with pytest.raises(db_dataapi.DataApiError):
            _duplicate(conn)
    finally:
        conn.close()
    assert conn.transaction_id is None
    assert probe.open_transactions == []


# ---------------------------------------------------------------------------
# rollback() direto
# ---------------------------------------------------------------------------


def test_rollback_de_transacao_inexistente_conta_como_revertida(probe, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    conn = db_dataapi.get_conn()
    probe.expire_transaction(conn.transaction_id)
    conn.rollback()
    assert conn.transaction_id is None
    assert len(_gone_warnings(caplog)) == 1
    conn.rollback()  # no-op: nao reusa o id morto
    conn.close()
    assert len(probe.calls_of("RollbackTransaction")) == 1


def test_rollback_com_outra_falha_propaga_e_zera_o_id(probe):
    probe.inject_error("RollbackTransaction", "ThrottlingException", "Rate exceeded")
    conn = db_dataapi.get_conn()
    with pytest.raises(ClientError) as exc:
        conn.rollback()
    assert exc.value.response["Error"]["Code"] == "ThrottlingException"
    assert conn.transaction_id is None
    conn.close()  # no-op
    assert len(probe.calls_of("RollbackTransaction")) == 1


# ---------------------------------------------------------------------------
# rollback falha por outro motivo: original preservada + contexto anexado
# ---------------------------------------------------------------------------


@ABORT_MODES
@pytest.mark.parametrize("entrada", ["transaction", "with-conn"])
def test_rollback_throttled_preserva_original_com_contexto(probe, caplog, abort_on_error, entrada):
    probe.abort_transaction_on_error = abort_on_error
    probe.inject_error("RollbackTransaction", "ThrottlingException", "Rate exceeded")
    caplog.set_level(logging.WARNING, logger=LOGGER)
    block = db_dataapi.transaction() if entrada == "transaction" else db_dataapi.get_conn()
    with pytest.raises(db_dataapi.DataApiError) as exc:
        with block as conn:
            _duplicate(conn)
    assert db_dataapi.is_duplicate_key_error(exc.value)
    assert any("ThrottlingException" in note for note in exc.value.__notes__)
    assert conn.transaction_id is None
    (record,) = [r for r in caplog.records if getattr(r, "event", None) == "dataapi_rollback_transaction_failed"]
    assert record.levelno == logging.WARNING
    assert record.rollback_aws_error_code == "ThrottlingException"
    assert record.original_error_type == "DataApiError"
    assert isinstance(record.exc_info[1], ClientError)
    assert len(probe.calls_of("RollbackTransaction")) == 1
    assert probe.client_configs == [None]  # sem UnsupportedResultException: client e retries padrao


@ABORT_MODES
def test_excecao_de_aplicacao_tambem_e_preservada(probe, abort_on_error):
    probe.abort_transaction_on_error = abort_on_error
    probe.inject_error("RollbackTransaction", "InternalServerErrorException", "boom", status=500)
    with pytest.raises(RuntimeError, match="regra de negocio") as exc:
        with db_dataapi.transaction() as conn:
            conn.cursor().execute(f"INSERT INTO {TABLE} (uq) VALUES ('x')")
            raise RuntimeError("regra de negocio")
    assert any("InternalServerErrorException" in note for note in exc.value.__notes__)


# ---------------------------------------------------------------------------
# Fluxo real: POST /workout-plan-templates/{id}/assignments duplicado -> 409
# ---------------------------------------------------------------------------


@pytest.fixture
def assignment_seed(raw):
    with raw.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in ("workout_plan_template_assignments", "workout_plan_templates", "users"):
            cur.execute(f"TRUNCATE TABLE `{table}`")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('pt-achado-b', 'x', 'admin')")
        admin_id = cur.lastrowid
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('aluno-achado-b', 'x', 'regular')")
        student_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_templates (name, created_by_user_id) VALUES ('Modelo Achado B', %s)",
            (admin_id,),
        )
        template_id = cur.lastrowid
    return admin_id, student_id, template_id


@pytest.fixture(params=[
    ("data-api", False),
    ("data-api", True),
    ("pymysql", None),
], ids=["data-api-producao", "data-api-hipotetico-transacao-inexistente", "pymysql"])
def assignment_backend(request, monkeypatch, fake_rds, mysql_schema):
    backend, abort_on_error = request.param
    use_backend(backend, monkeypatch, fake_rds, mysql_schema)
    if abort_on_error is not None:
        fake_rds.abort_transaction_on_error = abort_on_error
    return backend


def test_atribuicao_duplicada_de_modelo_devolve_409(assignment_backend, assignment_seed, fake_rds, raw):
    admin_id, student_id, template_id = assignment_seed
    identity = Identity(user_id=admin_id, role="admin", source="session")
    payload = WorkoutPlanTemplateAssignIn(user_id=student_id)

    first = workout_plan_templates.create_assignment(template_id, payload, identity)
    assert [item["user_id"] for item in first["items"]] == [student_id]

    with pytest.raises(HTTPException) as exc:
        workout_plan_templates.create_assignment(template_id, payload, identity)
    assert exc.value.status_code == 409

    with raw.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM workout_plan_template_assignments")
        assert cur.fetchone()["n"] == 1
    if assignment_backend == "data-api":
        assert fake_rds.open_transactions == []


# ---------------------------------------------------------------------------
# Evidencia de producao (musclefit-api-core, GET /cardio-recommendations?limit=200,
# 2026-09-14 13:43:29 UTC): ExecuteStatement -> UnsupportedResultException (1 MB)
# e RollbackTransaction -> InternalServerErrorException (max retries). A excecao
# que chegava ao ASGI era a do rollback. Os 4 padroes de call-site do backend.
# ---------------------------------------------------------------------------

UNSUPPORTED_MSG = "The result exceeds the size limit 1 MB."
ISE_MSG = "An internal error occurred."
PATTERNS = ["transaction", "with-conn", "try-finally-close", "except-rollback-raise"]


def _run_pattern(pattern: str) -> None:
    sql = f"SELECT id, uq FROM {TABLE}"
    if pattern == "transaction":
        with db_dataapi.transaction() as conn:
            conn.cursor().execute(sql)
    elif pattern == "with-conn":
        with db_dataapi.get_conn() as conn:
            conn.cursor().execute(sql)
    elif pattern == "try-finally-close":  # cardio_recommendations.list_*
        conn = db_dataapi.get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.fetchall()
        finally:
            conn.close()
    else:  # cardio_recommendations.create_*
        conn = db_dataapi.get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


@pytest.mark.parametrize("pattern", PATTERNS)
def test_unsupported_result_com_rollback_falhando_sobe_o_erro_original(probe, caplog, pattern):
    probe.inject_error("ExecuteStatement", "UnsupportedResultException", UNSUPPORTED_MSG)
    probe.inject_error("RollbackTransaction", "InternalServerErrorException", ISE_MSG, status=500)
    caplog.set_level(logging.WARNING, logger=LOGGER)

    with pytest.raises(db_dataapi.DataApiError) as exc:
        _run_pattern(pattern)

    assert "UnsupportedResultException" in str(exc.value)
    assert isinstance(exc.value.__cause__, ClientError)
    assert exc.value.__cause__.response["Error"]["Code"] == "UnsupportedResultException"
    assert any("InternalServerErrorException" in note for note in exc.value.__notes__)

    rollbacks = probe.calls_of("RollbackTransaction")
    assert len(rollbacks) == 1  # close() nao tenta de novo com o id morto
    assert rollbacks[0].error_code == "InternalServerErrorException"
    # rollback apos UnsupportedResultException: client sem retries; o normal (construtor) inalterado
    assert probe.client_configs[0] is None
    assert probe.client_configs[-1].retries == {"total_max_attempts": 1}

    (record,) = [r for r in caplog.records if getattr(r, "event", None) == "dataapi_rollback_transaction_failed"]
    assert record.levelno == logging.WARNING
    assert record.original_aws_error_code == "UnsupportedResultException"
    assert record.rollback_aws_error_code == "InternalServerErrorException"
    assert "SELECT" not in record.getMessage()


@pytest.mark.parametrize("pattern", PATTERNS)
def test_unsupported_result_com_rollback_ok_sobe_o_erro_original(probe, pattern):
    probe.inject_error("ExecuteStatement", "UnsupportedResultException", UNSUPPORTED_MSG)
    with pytest.raises(db_dataapi.DataApiError, match="UnsupportedResultException") as exc:
        _run_pattern(pattern)
    assert not getattr(exc.value, "__notes__", None)
    assert probe.open_transactions == []
    assert len(probe.calls_of("RollbackTransaction")) == 1


def test_close_sem_excecao_em_andamento_propaga_falha_real_do_rollback(probe):
    probe.inject_error("RollbackTransaction", "InternalServerErrorException", ISE_MSG, status=500)
    conn = db_dataapi.get_conn()
    with pytest.raises(ClientError) as exc:
        conn.close()
    assert exc.value.response["Error"]["Code"] == "InternalServerErrorException"
    assert conn.transaction_id is None
    conn.close()  # no-op
    assert probe.client_configs == [None]
