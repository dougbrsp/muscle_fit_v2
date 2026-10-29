"""M73: job assincrono orfao (Lambda morta) vira `failed`/`worker_lost`.

- `async_jobs.get_job`: job `pending`/`running` alem de timeout da Lambda +
  folga volta `failed` e o estado e persistido com update CONDICIONAL;
- `periodization.logs.sweep_stale_pending_logs`: linhas `pending` antigas de
  `template_generation_logs` viram `failed` com `WORKER_LOST` no relatorio;
- paridade: os timeouts de `async_jobs.LAMBDA_TIMEOUT_SECONDS` sao os de
  `infra/*.tf`, e cada job_type aponta para a Lambda que o chamador invoca.

M81 (entrega atrasada pela fila de throttling ou duplicada):
- `claim_job` grava `started_at` so em job `pending` nunca iniciado;
- `mark_done`/`mark_failed` so gravam em job em andamento (resultado tardio
  e descartado, nunca sobrescreve o `worker_lost`);
- o prazo do `worker_lost` conta de `started_at` quando existe.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from app import async_jobs
from app.security import Identity
from app.periodization import logs as periodization_logs
from tests.support.fake_db import SharedCursorConn

API_APP_DIR = Path(__file__).resolve().parents[2] / "app"
INFRA_DIR = Path(__file__).resolve().parents[3] / "infra"

NOW = 1_800_000_000
OWNER = 7
OWNER_IDENTITY = Identity(user_id=OWNER, role="regular", source="session")


# ---------------------------------------------------------------------------
# Fake da tabela DynamoDB (so o que async_jobs usa)
# ---------------------------------------------------------------------------


class FakeTable:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.updates: list[dict[str, Any]] = []
        # Simula o worker gravando o resultado ENTRE o get_item e o update
        # condicional do get_job (corrida no limite do prazo).
        self.before_conditional_update = None

    def put_item(self, Item):  # noqa: N803 - assinatura do boto3
        self.items[Item["job_id"]] = dict(Item)

    def get_item(self, Key, ConsistentRead=False):  # noqa: N803
        item = self.items.get(Key["job_id"])
        return {"Item": dict(item)} if item else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues, ConditionExpression=None):  # noqa: N803
        self.updates.append(
            {"key": Key, "expr": UpdateExpression, "condition": ConditionExpression, "values": ExpressionAttributeValues}
        )
        if ConditionExpression is not None and self.before_conditional_update:
            hook, self.before_conditional_update = self.before_conditional_update, None
            hook()
        item = self.items.setdefault(Key["job_id"], {"job_id": Key["job_id"]})
        if ConditionExpression is not None and not self._condition_holds(
            ConditionExpression, item, ExpressionAttributeValues
        ):
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "nope"}}, "UpdateItem"
            )
        assignments = UpdateExpression.removeprefix("SET ").split(",")
        for assignment in assignments:
            name, value = (part.strip() for part in assignment.split("="))
            name = ExpressionAttributeNames.get(name, name)
            item[name] = ExpressionAttributeValues[value]

    @staticmethod
    def _condition_holds(condition: str, item: dict[str, Any], values: dict[str, Any]) -> bool:
        """So as duas condicoes que async_jobs usa, comparadas literalmente."""
        if condition == "#status IN (:pending, :running)":
            return item.get("status") in {values[":pending"], values[":running"]}
        if condition == "#status = :pending AND attribute_not_exists(started_at)":
            return item.get("status") == values[":pending"] and "started_at" not in item
        raise AssertionError(f"ConditionExpression nao suportada pelo fake: {condition!r}")


@pytest.fixture
def table(monkeypatch) -> FakeTable:
    fake = FakeTable()
    monkeypatch.setattr(async_jobs, "_table", lambda: fake)
    return fake


def _job(table: FakeTable, job_type: str, *, age: int, status: str = "pending") -> str:
    job_id = f"job-{job_type}-{age}-{status}"
    table.put_item(Item={"job_id": job_id, "job_type": job_type, "status": status, "created_at": NOW - age, "owner_user_id": OWNER})
    return job_id


# ---------------------------------------------------------------------------
# create_job guarda created_at
# ---------------------------------------------------------------------------


def test_create_job_grava_created_at_e_pending(table, monkeypatch) -> None:
    monkeypatch.setattr(async_jobs.time, "time", lambda: NOW)
    job_id = async_jobs.create_job("cardio-agent", request={"x": 1}, owner_user_id=OWNER)
    item = table.items[job_id]
    assert item["status"] == "pending"
    assert item["created_at"] == NOW


# ---------------------------------------------------------------------------
# get_job + worker_lost
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("job_type", "deadline"),
    [
        ("plan-generator", 240),
        ("periodization", 240),
        ("cardio-agent", 180),
        ("insights", 120),
        ("workout-ai-evaluate-set", 120),
        ("nutrition-label-ocr", 240),  # nutricao L6: foto do rotulo na plan-generator (E8)
    ],
)
def test_prazo_e_timeout_da_lambda_mais_folga(job_type: str, deadline: int) -> None:
    assert async_jobs.WORKER_LOST_GRACE_SECONDS == 60
    assert async_jobs.worker_lost_deadline_seconds(job_type) == deadline


def test_job_type_desconhecido_nao_tem_prazo(table) -> None:
    assert async_jobs.worker_lost_deadline_seconds("tipo-novo") is None
    job_id = _job(table, "tipo-novo", age=99_999)
    assert async_jobs.get_job(job_id, now=NOW, owner_user_id=OWNER)["status"] == "pending"
    assert table.updates == []


@pytest.mark.parametrize("status", ["pending", "running"])
def test_job_dentro_do_prazo_continua_em_andamento(table, status: str) -> None:
    job_id = _job(table, "cardio-agent", age=180, status=status)  # exatamente no limite
    job = async_jobs.get_job(job_id, now=NOW, owner_user_id=OWNER)
    assert job["status"] == status
    assert "error" not in job
    assert table.updates == []


@pytest.mark.parametrize("status", ["pending", "running"])
def test_job_alem_do_prazo_vira_failed_worker_lost_e_persiste(table, status: str) -> None:
    job_id = _job(table, "cardio-agent", age=181, status=status)
    job = async_jobs.get_job(job_id, now=NOW, owner_user_id=OWNER)

    assert job["status"] == "failed"
    assert job["error_code"] == "worker_lost"
    assert job["error"] == async_jobs.WORKER_LOST_MESSAGE
    assert job["finished_at"] == NOW

    # Persistido com update condicional.
    assert len(table.updates) == 1
    assert table.updates[0]["condition"] == "#status IN (:pending, :running)"
    stored = table.items[job_id]
    assert stored["status"] == "failed"
    assert stored["error_code"] == "worker_lost"
    assert stored["error"] == async_jobs.WORKER_LOST_MESSAGE

    # Leitura seguinte: ja terminal, nenhum update novo.
    again = async_jobs.get_job(job_id, now=NOW + 10, owner_user_id=OWNER)
    assert again["status"] == "failed" and again["error_code"] == "worker_lost"
    assert len(table.updates) == 1


def test_mensagem_worker_lost_e_amigavel_em_portugues() -> None:
    msg = async_jobs.WORKER_LOST_MESSAGE
    assert "worker" not in msg.lower()
    assert "Tente novamente" in msg


def test_resultado_que_chega_no_limite_nao_e_sobrescrito(table) -> None:
    job_id = _job(table, "plan-generator", age=10_000)
    # O worker grava `done` depois do get_item e antes do update condicional.
    table.before_conditional_update = lambda: async_jobs.mark_done(job_id, {"ok": True})

    job = async_jobs.get_job(job_id, now=NOW, owner_user_id=OWNER)

    assert job["status"] == "done"
    assert job["result"] == {"ok": True}
    assert table.items[job_id]["status"] == "done"
    assert "error_code" not in table.items[job_id]


def test_mark_worker_lost_nao_toca_job_terminal(table) -> None:
    job_id = _job(table, "insights", age=10_000, status="failed")
    table.items[job_id]["error"] = "erro original"
    assert async_jobs.mark_worker_lost(job_id, now=NOW) is False
    assert table.items[job_id]["error"] == "erro original"


def test_job_terminal_antigo_nao_e_reaberto(table) -> None:
    job_id = _job(table, "insights", age=10_000, status="done")
    table.items[job_id]["result"] = json.dumps({"r": 1})
    job = async_jobs.get_job(job_id, now=NOW, owner_user_id=OWNER)
    assert job["status"] == "done" and job["result"] == {"r": 1}
    assert table.updates == []


def test_resultado_atrasado_depois_do_worker_lost_e_descartado(table) -> None:
    """M81 (inverte a decisao aberta do M73): throttling da fila assincrona
    pode atrasar a execucao (a AWS reentrega throttle mesmo com retry 0). Se o
    poll ja fechou o job como worker_lost, o usuario ja tentou de novo - o
    resultado tardio e descartado, nunca sobrescreve o `failed`, e um handler
    que chegue depois nem consegue reivindicar o job."""
    job_id = _job(table, "workouts_analysis", age=10_000)
    assert async_jobs.get_job(job_id, now=NOW, owner_user_id=OWNER)["status"] == "failed"
    assert async_jobs.claim_job(job_id, now=NOW + 1) is False
    assert async_jobs.mark_done(job_id, {"r": 2}) is False
    job = async_jobs.get_job(job_id, now=NOW + 5, owner_user_id=OWNER)
    assert job["status"] == "failed" and job["error_code"] == "worker_lost"
    assert "result" not in job


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda job_id: async_jobs.mark_worker_lost(job_id, now=NOW), id="mark_worker_lost"),
        pytest.param(lambda job_id: async_jobs.claim_job(job_id, now=NOW), id="claim_job"),
        pytest.param(lambda job_id: async_jobs.mark_done(job_id, {}), id="mark_done"),
        pytest.param(lambda job_id: async_jobs.mark_failed(job_id, "x"), id="mark_failed"),
    ],
)
def test_outro_erro_do_dynamodb_propaga(table, monkeypatch, call) -> None:
    job_id = _job(table, "insights", age=10_000)

    def boom(**_kwargs):
        raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "UpdateItem")

    monkeypatch.setattr(table, "update_item", boom)
    with pytest.raises(ClientError):
        call(job_id)


# ---------------------------------------------------------------------------
# M81: claim do job + mark_* condicionais + prazo a partir de started_at
# ---------------------------------------------------------------------------


def test_claim_de_job_pending_grava_started_at_e_devolve_true(table) -> None:
    job_id = _job(table, "cardio-agent", age=5)
    assert async_jobs.claim_job(job_id, now=NOW) is True
    item = table.items[job_id]
    assert item["started_at"] == NOW
    assert item["status"] == "pending"  # nenhum status novo visivel ao frontend
    assert table.updates[-1]["condition"] == "#status = :pending AND attribute_not_exists(started_at)"


def test_segundo_claim_do_mesmo_job_devolve_false_e_preserva_o_primeiro(table) -> None:
    """Invoke assincrono e at-least-once: a segunda entrega nao roda."""
    job_id = _job(table, "insights", age=5)
    assert async_jobs.claim_job(job_id, now=NOW) is True
    assert async_jobs.claim_job(job_id, now=NOW + 30) is False
    assert table.items[job_id]["started_at"] == NOW


def test_claim_depois_do_worker_lost_devolve_false(table) -> None:
    """Entrega atrasada pela fila de throttling: o poll ja fechou o job."""
    job_id = _job(table, "plan-generator", age=10_000)
    assert async_jobs.get_job(job_id, now=NOW, owner_user_id=OWNER)["error_code"] == "worker_lost"
    assert async_jobs.claim_job(job_id, now=NOW + 1) is False
    assert "started_at" not in table.items[job_id]


@pytest.mark.parametrize("status", ["done", "failed"])
def test_claim_de_job_terminal_devolve_false(table, status: str) -> None:
    job_id = _job(table, "insights", age=5, status=status)
    assert async_jobs.claim_job(job_id, now=NOW) is False


@pytest.mark.parametrize(
    "finish",
    [
        pytest.param(lambda job_id: async_jobs.mark_done(job_id, {"r": 1}), id="mark_done"),
        pytest.param(lambda job_id: async_jobs.mark_failed(job_id, "erro tardio"), id="mark_failed"),
    ],
)
def test_mark_depois_do_worker_lost_e_descartado_com_warning(table, finish, caplog) -> None:
    job_id = _job(table, "workouts_analysis", age=10_000)
    assert async_jobs.get_job(job_id, now=NOW, owner_user_id=OWNER)["status"] == "failed"

    with caplog.at_level(logging.WARNING, logger="musclefit.async_jobs"):
        assert finish(job_id) is False

    stored = table.items[job_id]
    assert stored["status"] == "failed"
    assert stored["error_code"] == "worker_lost"
    assert stored["error"] == async_jobs.WORKER_LOST_MESSAGE
    assert "result" not in stored
    assert f"Resultado descartado: job {job_id}" in caplog.text


def test_mark_done_e_mark_failed_em_job_pending_continuam_gravando(table) -> None:
    done_id = _job(table, "insights", age=5)
    assert async_jobs.mark_done(done_id, {"r": 1}) is True
    assert table.items[done_id]["status"] == "done"
    assert json.loads(table.items[done_id]["result"]) == {"r": 1}

    failed_id = _job(table, "cardio-agent", age=5)
    assert async_jobs.mark_failed(failed_id, "erro") is True
    assert table.items[failed_id]["status"] == "failed"
    assert table.items[failed_id]["error"] == "erro"
    assert "error_code" not in table.items[failed_id]


def test_mark_done_depois_de_claim_grava_done(table) -> None:
    """Fluxo normal do handler: claim -> IA -> mark_done."""
    job_id = _job(table, "insights", age=5)
    assert async_jobs.claim_job(job_id, now=NOW) is True
    assert async_jobs.mark_done(job_id, {"r": 1}) is True
    assert table.items[job_id]["status"] == "done"


def test_prazo_conta_de_started_at_quando_presente(table) -> None:
    """Job que esperou na fila (criado ha 500 s) e comecou ha 100 s: a
    plan-generator tem 240 s a partir do inicio real - ainda em andamento."""
    job_id = _job(table, "plan-generator", age=500)
    table.items[job_id]["started_at"] = NOW - 100
    assert async_jobs.worker_lost_reference_seconds(table.items[job_id]) == NOW - 100
    assert async_jobs.get_job(job_id, now=NOW, owner_user_id=OWNER)["status"] == "pending"
    assert table.updates == []

    # Passado o prazo contado do inicio real: perdido.
    assert async_jobs.get_job(job_id, now=NOW - 100 + 241, owner_user_id=OWNER)["error_code"] == "worker_lost"


def test_prazo_sem_started_at_conta_de_created_at(table) -> None:
    job_id = _job(table, "plan-generator", age=500)
    assert async_jobs.worker_lost_reference_seconds(table.items[job_id]) == NOW - 500
    assert async_jobs.get_job(job_id, now=NOW, owner_user_id=OWNER)["error_code"] == "worker_lost"


def test_get_job_nao_expoe_started_at(table) -> None:
    """Contrato de GET /jobs/{id} inalterado: `started_at` e interno."""
    job_id = _job(table, "insights", age=5)
    assert async_jobs.claim_job(job_id, now=NOW) is True
    job = async_jobs.get_job(job_id, now=NOW, owner_user_id=OWNER)
    assert "started_at" not in job
    assert set(job) == {"job_id", "job_type", "status", "created_at", "finished_at"}


# ---------------------------------------------------------------------------
# GET /jobs/{id}: periodizacao fecha tambem a auditoria
# ---------------------------------------------------------------------------


def test_get_jobs_periodizacao_worker_lost_dispara_sweep_dos_logs(table, monkeypatch) -> None:
    from app import jobs

    calls: list[str] = []
    monkeypatch.setattr(jobs, "_sweep_periodization_logs", lambda: calls.append("sweep"))
    monkeypatch.setattr(async_jobs.time, "time", lambda: NOW)

    lost = _job(table, "periodization", age=10_000)
    assert jobs.get_job_status(lost, identity=OWNER_IDENTITY)["error_code"] == "worker_lost"
    assert calls == ["sweep"]

    calls.clear()
    fresh = _job(table, "periodization", age=5)
    assert jobs.get_job_status(fresh, identity=OWNER_IDENTITY)["status"] == "pending"
    other = _job(table, "cardio-agent", age=10_000)
    assert jobs.get_job_status(other, identity=OWNER_IDENTITY)["status"] == "failed"
    assert calls == []


# ---------------------------------------------------------------------------
# template_generation_logs
# ---------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, rowcount: int) -> None:
        self.executed: list[tuple[str, tuple]] = []
        self.rowcount = rowcount

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.executed.append((" ".join(sql.split()), params))


def _conn(rowcount: int = 0) -> SharedCursorConn:
    return SharedCursorConn(FakeCursor(rowcount))


def test_sweep_logs_fecha_pending_antigas_como_failed_worker_lost() -> None:
    conn = _conn(rowcount=2)
    assert periodization_logs.sweep_stale_pending_logs(conn) == 2

    [(sql, params)] = conn.cur.executed
    assert sql == (
        "UPDATE template_generation_logs SET status = %s, validation_report = %s "
        "WHERE status = %s AND created_at < DATE_SUB(NOW(), INTERVAL %s SECOND)"
    )
    status, report, only_status, seconds = params
    assert status == periodization_logs.STATUS_FAILED == "failed"
    assert only_status == periodization_logs.STATUS_PENDING  # nunca toca succeeded/failed
    assert seconds == async_jobs.worker_lost_deadline_seconds("periodization") == 240
    parsed = json.loads(report)
    assert parsed["errors"][0]["code"] == "WORKER_LOST"
    assert parsed["warnings"] == [] and parsed["auto_fixes"] == []


def test_sweep_logs_sem_linhas_devolve_zero() -> None:
    conn = _conn(rowcount=-1)
    assert periodization_logs.sweep_stale_pending_logs(conn) == 0


def test_criar_periodizacao_varre_logs_antes_e_sweep_quebrado_nao_impede(monkeypatch) -> None:
    """O POST chama o sweep em transacao propria e best-effort."""
    source = (API_APP_DIR / "workout_plan_templates.py").read_text(encoding="utf-8")
    body = source.split("def create_periodization_draft(", 1)[1].split("\n@router.", 1)[0]
    sweep_at = body.index("sweep_stale_pending_logs(conn)")
    assert sweep_at < body.index("count_generations_today(conn")
    assert "except Exception" in body[sweep_at : body.index("count_generations_today(conn")]


# ---------------------------------------------------------------------------
# Paridade com infra/*.tf e com os chamadores
# ---------------------------------------------------------------------------


def _tf_lambda_functions() -> dict[str, dict[str, Any]]:
    """resource name -> {function_name, timeout} de todo aws_lambda_function em infra/*.tf."""
    found: dict[str, dict[str, Any]] = {}
    for tf in sorted(INFRA_DIR.glob("*.tf")):
        text = tf.read_text(encoding="utf-8")
        for match in re.finditer(r'resource "aws_lambda_function" "(\w+)" \{', text):
            block = text[match.end() :].split('\nresource "', 1)[0]
            name = re.search(r'^\s*function_name\s*=\s*"([^"]+)"', block, re.MULTILINE)
            timeout = re.search(r"^\s*timeout\s*=\s*(\d+)", block, re.MULTILINE)
            found[match.group(1)] = {
                "function_name": name.group(1) if name else None,
                "timeout": int(timeout.group(1)) if timeout else None,
                "file": tf.name,
            }
    return found


def test_timeouts_batem_com_infra_tf() -> None:
    by_function = {v["function_name"]: v for v in _tf_lambda_functions().values()}
    for function, timeout in async_jobs.LAMBDA_TIMEOUT_SECONDS.items():
        assert function in by_function, f"{function} nao encontrada em infra/*.tf"
        assert by_function[function]["timeout"] == timeout, (
            f"timeout de {function}: {by_function[function]['timeout']}s em infra/"
            f"{by_function[function]['file']}, {timeout}s em async_jobs.LAMBDA_TIMEOUT_SECONDS"
        )


def _env_var_to_function() -> dict[str, str]:
    """Env var da musclefit-api-core -> nome real da funcao (infra/lambda_api_core.tf)."""
    resources = _tf_lambda_functions()
    text = (INFRA_DIR / "lambda_api_core.tf").read_text(encoding="utf-8")
    mapping = {}
    for env, resource in re.findall(
        r"^\s*(\w+_FUNCTION_NAME)\s*=\s*aws_lambda_function\.(\w+)\.function_name", text, re.MULTILINE
    ):
        mapping[env] = resources[resource]["function_name"]
    return mapping


_CONST_RE = re.compile(r'^([A-Z][A-Z0-9_]*)\s*=\s*"([^"]*)"', re.MULTILINE)


def _string_constants() -> dict[str, str]:
    """Constantes string de modulo em api/app (para nomes importados)."""
    consts: dict[str, str] = {}
    for py in API_APP_DIR.rglob("*.py"):
        for name, value in _CONST_RE.findall(py.read_text(encoding="utf-8")):
            consts.setdefault(name, value)
    return consts


def _resolve(token: str, consts: dict[str, str]) -> str:
    token = token.strip()
    if token.startswith('"'):
        return token.strip('"')
    assert token in consts, f"constante {token} nao encontrada em api/app"
    return consts[token]


def test_cada_job_type_aponta_para_a_lambda_que_o_chamador_invoca() -> None:
    env_to_function = _env_var_to_function()
    global_consts = _string_constants()
    seen: dict[str, str] = {}
    for py in API_APP_DIR.rglob("*.py"):
        if py.name == "async_jobs.py":
            continue
        text = py.read_text(encoding="utf-8")
        # Constante do proprio modulo vence (ha `JOB_TYPE` em mais de um arquivo).
        consts = {**global_consts, **dict(_CONST_RE.findall(text))}
        job_types = re.findall(r"async_jobs\.create_job\(\s*([A-Za-z_\"][\w\-\"]*)", text)
        env_vars = re.findall(r"async_jobs\.invoke_async\(\s*([A-Za-z_\"][\w\"]*)", text)
        assert len(job_types) == len(env_vars), f"{py.name}: create_job/invoke_async desemparelhados"
        for job_token, env_token in zip(job_types, env_vars):
            job_type = _resolve(job_token, consts)
            function = env_to_function[_resolve(env_token, consts)]
            assert async_jobs.JOB_TYPE_LAMBDA.get(job_type) == function, (
                f"{py.name}: job_type {job_type!r} roda em {function}, "
                f"mas JOB_TYPE_LAMBDA diz {async_jobs.JOB_TYPE_LAMBDA.get(job_type)!r}"
            )
            seen[job_type] = function
    # Nenhum tipo mapeado sem chamador (mapa morto esconderia divergencia).
    assert set(seen) == set(async_jobs.JOB_TYPE_LAMBDA)
    assert set(async_jobs.JOB_TYPE_LAMBDA.values()) <= set(async_jobs.LAMBDA_TIMEOUT_SECONDS)
