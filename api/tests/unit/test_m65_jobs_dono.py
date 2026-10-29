"""M65: `GET /jobs/{job_id}` so devolve o job para o usuario que o criou.

- `async_jobs.create_job` exige e grava `owner_user_id`;
- dono le; outro usuario (inclusive admin) e job sem dono (criado antes do
  M65) recebem o MESMO 404 de job inexistente, e nao disparam a escrita de
  `worker_lost` (M73) no job alheio;
- todo chamador de `create_job` em `api/app` passa o dono (teste estatico:
  um chamador novo sem dono quebra aqui, antes de chegar em producao).
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import async_jobs, jobs
from app.security import Identity, get_current_identity

API_APP_DIR = Path(__file__).resolve().parents[2] / "app"

NOW = 1_800_000_000
OWNER = 7
OTHER = 8
ADMIN = 1


class FakeTable:
    """So o que `async_jobs` usa. Itens com numeros como `Decimal`, igual ao
    boto3 devolve do DynamoDB (LocalStack em DEV, DynamoDB em producao)."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.updates: list[dict[str, Any]] = []

    def put_item(self, Item):  # noqa: N803 - assinatura do boto3
        self.items[Item["job_id"]] = {
            k: Decimal(v) if isinstance(v, int) and not isinstance(v, bool) else v for k, v in Item.items()
        }

    def get_item(self, Key, ConsistentRead=False):  # noqa: N803
        item = self.items.get(Key["job_id"])
        return {"Item": dict(item)} if item else {}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)
        item = self.items[kwargs["Key"]["job_id"]]
        item["status"] = kwargs["ExpressionAttributeValues"].get(":failed", item.get("status"))


@pytest.fixture
def table(monkeypatch) -> FakeTable:
    fake = FakeTable()
    monkeypatch.setattr(async_jobs, "_table", lambda: fake)
    monkeypatch.setattr(async_jobs.time, "time", lambda: NOW)
    return fake


def _client(user_id: int, role: str = "regular") -> TestClient:
    app = FastAPI()
    app.include_router(jobs.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app)


def _job(table: FakeTable, *, owner: Any = OWNER, age: int = 5, job_type: str = "cardio-agent") -> str:
    job_id = f"job-{owner}-{age}-{job_type}"
    item = {"job_id": job_id, "job_type": job_type, "status": "pending", "created_at": NOW - age}
    if owner is not None:
        item["owner_user_id"] = owner
    table.put_item(Item=item)
    return job_id


# ---------------------------------------------------------------------------
# create_job
# ---------------------------------------------------------------------------


def test_create_job_grava_o_dono(table) -> None:
    job_id = async_jobs.create_job("cardio-agent", request={"x": 1}, owner_user_id=OWNER)
    assert table.items[job_id]["owner_user_id"] == OWNER


def test_create_job_sem_dono_nao_cria_nada(table) -> None:
    with pytest.raises(TypeError):
        async_jobs.create_job("cardio-agent", request={"x": 1})  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        async_jobs.create_job("cardio-agent", owner_user_id=None)  # type: ignore[arg-type]
    assert table.items == {}


# ---------------------------------------------------------------------------
# GET /jobs/{job_id}
# ---------------------------------------------------------------------------


def test_dono_le_o_proprio_job(table) -> None:
    job_id = async_jobs.create_job("cardio-agent", owner_user_id=OWNER)
    resp = _client(OWNER).get(f"/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    assert "owner_user_id" not in resp.json()


def test_outro_usuario_recebe_o_mesmo_404_de_job_inexistente(table) -> None:
    job_id = _job(table)
    alheio = _client(OTHER).get(f"/jobs/{job_id}")
    inexistente = _client(OTHER).get("/jobs/nao-existe")
    assert alheio.status_code == 404
    assert alheio.json() == inexistente.json()


def test_admin_nao_le_job_alheio(table) -> None:
    job_id = _job(table)
    assert _client(ADMIN, role="admin").get(f"/jobs/{job_id}").status_code == 404


def test_outro_usuario_nao_dispara_worker_lost_no_job_alheio(table) -> None:
    job_id = _job(table, age=10_000)  # muito alem do prazo do cardio-agent
    assert _client(OTHER).get(f"/jobs/{job_id}").status_code == 404
    assert table.updates == []
    assert table.items[job_id]["status"] == "pending"

    # O dono, sim, fecha o job orfao (comportamento do M73 preservado).
    resp = _client(OWNER).get(f"/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["error_code"] == async_jobs.WORKER_LOST_ERROR_CODE
    assert len(table.updates) == 1


def test_job_sem_dono_criado_antes_do_m65_e_404_para_todos(table) -> None:
    job_id = _job(table, owner=None, age=10_000)
    for user_id, role in ((OWNER, "regular"), (ADMIN, "admin")):
        assert _client(user_id, role).get(f"/jobs/{job_id}").status_code == 404
    assert table.updates == []


def test_get_job_compara_dono_vindo_do_dynamodb_como_decimal(table) -> None:
    job_id = _job(table, owner=Decimal(OWNER))
    assert async_jobs.get_job(job_id, owner_user_id=OWNER)["status"] == "pending"
    assert async_jobs.get_job(job_id, owner_user_id=OTHER) is None


# ---------------------------------------------------------------------------
# Todo chamador passa o dono
# ---------------------------------------------------------------------------


def _calls(func_name: str) -> list[tuple[str, ast.Call]]:
    found = []
    for py in API_APP_DIR.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name == func_name:
                found.append((f"{py.relative_to(API_APP_DIR)}:{node.lineno}", node))
    return found


@pytest.mark.parametrize("func_name", ["create_job", "dispatch_if_missing"])
def test_todo_chamador_passa_owner_user_id(func_name: str) -> None:
    calls = _calls(func_name)
    assert calls, f"nenhum chamador de {func_name} encontrado - o teste ficou cego"
    sem_dono = [where for where, call in calls if not any(kw.arg == "owner_user_id" for kw in call.keywords)]
    assert sem_dono == [], f"chamadas de {func_name} sem owner_user_id (M65): {sem_dono}"


def test_todos_os_tipos_de_job_tem_chamador_com_dono() -> None:
    """Ficha, periodizacao, cards, descricao de execucao, cardio, insights,
    analise e os tres do Modo Treino: os 10 tipos mapeados no M73."""
    assert len(_calls("create_job")) == len(async_jobs.JOB_TYPE_LAMBDA)
