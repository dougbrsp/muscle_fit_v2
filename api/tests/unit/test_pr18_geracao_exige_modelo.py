"""PR-18 - nenhum caminho gera ficha sem Modelo de Treino (PLANO_20260912.md
secoes 6, 9 e 15).

Aqui esta o contrato HTTP de `POST /workout-plans/generate`, sem banco:

- `template_id` ausente -> **422 no request sincrono**, antes de qualquer job
  (o erro nao pode aparecer so depois, como job `failed`);
- `plan_type`/`split_day` sairam do input de geracao (um payload antigo com
  eles nao "reativa" o caminho ABC/Full Body: os campos sao ignorados);
- modelo inexistente/nao atribuido -> **404 imediato**, tambem sem criar job;
- modelo valido -> 202 + `job_id`, com o payload publicado para a Lambda.

A geracao de verdade nos dois modos fica em
`tests/integration/test_pr18_geracao_exige_modelo.py`.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import workout_plan_templates_engine, workout_plans
from app.generation import template_request
from app.models import WorkoutPlanGenerateIn
from app.security import Identity, get_current_identity

USER_ID = 7

TEMPLATE = {"id": 55, "name": "Modelo do PT", "volume_mode": "equipment_series", "slots": [{"sequence": 1}]}
SLOT = {"id": 900, "sequence": 1, "name": "A", "items": []}


@pytest.fixture
def client(monkeypatch) -> TestClient:
    """App com so o router de fichas e a identidade fixada. `async_jobs` e
    dublado: este teste e sobre o que acontece ANTES do job existir."""
    created: list[dict] = []
    invoked: list[dict] = []

    monkeypatch.setattr(workout_plans.async_jobs, "create_job", lambda kind, request, *, owner_user_id: created.append(request) or "job-1")
    monkeypatch.setattr(
        workout_plans.async_jobs, "invoke_async", lambda env_var, event: invoked.append(event) or None
    )

    app = FastAPI()
    app.include_router(workout_plans.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=USER_ID, role="user", source="session")
    test_client = TestClient(app, raise_server_exceptions=False)
    test_client.created_jobs = created  # type: ignore[attr-defined]
    test_client.invoked_events = invoked  # type: ignore[attr-defined]
    return test_client


@pytest.fixture
def modelo_valido(monkeypatch):
    """Modelo existente, ativo e atribuido a USER_ID."""
    monkeypatch.setattr(
        workout_plan_templates_engine,
        "get_template_if_assigned",
        lambda template_id, user_id: TEMPLATE if (template_id, user_id) == (55, USER_ID) else None,
    )
    monkeypatch.setattr(
        workout_plan_templates_engine,
        "resolve_next_slot",
        lambda template, user_id, explicit_slot_sequence=None: SLOT,
    )


# ---------------------------------------------------------------------------
# Schema do input
# ---------------------------------------------------------------------------


def test_template_id_e_obrigatorio_no_schema():
    with pytest.raises(ValidationError) as exc:
        WorkoutPlanGenerateIn(gym_id=1)
    assert any(error["loc"] == ("template_id",) for error in exc.value.errors())


def test_plan_type_e_split_day_sairam_do_input_de_geracao():
    payload = WorkoutPlanGenerateIn(template_id=55, plan_type="abc", split_day="B")
    assert not hasattr(payload, "plan_type")
    assert not hasattr(payload, "split_day")
    assert "plan_type" not in payload.model_dump()
    assert "split_day" not in payload.model_dump()


# ---------------------------------------------------------------------------
# POST /workout-plans/generate
# ---------------------------------------------------------------------------


def test_sem_template_id_responde_422_e_nao_cria_job(client):
    response = client.post("/workout-plans/generate", json={"gym_id": 1, "min_rest_seconds": 30})

    assert response.status_code == 422
    assert client.created_jobs == []  # type: ignore[attr-defined]
    assert client.invoked_events == []  # type: ignore[attr-defined]


def test_payload_antigo_de_abc_continua_sendo_422(client):
    """O payload que antes gerava ABC nao gera mais nada: sem `template_id`
    ele e recusado, e `plan_type`/`split_day` viram campos ignorados."""
    response = client.post(
        "/workout-plans/generate",
        json={"plan_type": "abc", "split_day": "B", "min_rest_seconds": 30, "max_rest_seconds": 90},
    )

    assert response.status_code == 422
    assert client.created_jobs == []  # type: ignore[attr-defined]


def test_modelo_inexistente_ou_de_outro_usuario_responde_404_na_hora(client, monkeypatch):
    monkeypatch.setattr(workout_plan_templates_engine, "get_template_if_assigned", lambda template_id, user_id: None)

    response = client.post("/workout-plans/generate", json={"template_id": 999})

    assert response.status_code == 404
    assert response.json()["detail"] == template_request.TEMPLATE_NOT_FOUND_MESSAGE
    assert client.created_jobs == []  # type: ignore[attr-defined]


def test_slot_inexistente_responde_422_na_hora(client, monkeypatch):
    monkeypatch.setattr(
        workout_plan_templates_engine, "get_template_if_assigned", lambda template_id, user_id: TEMPLATE
    )
    monkeypatch.setattr(
        workout_plan_templates_engine,
        "resolve_next_slot",
        lambda template, user_id, explicit_slot_sequence=None: None,
    )

    response = client.post("/workout-plans/generate", json={"template_id": 55, "template_slot_sequence": 99})

    assert response.status_code == 422
    assert response.json()["detail"] == template_request.TEMPLATE_SLOT_NOT_FOUND_MESSAGE
    assert client.created_jobs == []  # type: ignore[attr-defined]


def test_modelo_sem_slots_responde_422_com_mensagem_propria(client, monkeypatch):
    monkeypatch.setattr(
        workout_plan_templates_engine, "get_template_if_assigned", lambda template_id, user_id: TEMPLATE
    )
    monkeypatch.setattr(
        workout_plan_templates_engine,
        "resolve_next_slot",
        lambda template, user_id, explicit_slot_sequence=None: None,
    )

    response = client.post("/workout-plans/generate", json={"template_id": 55})

    assert response.status_code == 422
    assert response.json()["detail"] == template_request.TEMPLATE_WITHOUT_SLOTS_MESSAGE


def test_com_modelo_valido_cria_o_job_com_o_payload_do_modelo(client, modelo_valido):
    response = client.post(
        "/workout-plans/generate",
        json={"template_id": 55, "template_slot_sequence": 1, "gym_id": 3, "plan_date": "2026-09-20"},
    )

    assert response.status_code == 202
    assert response.json()["job_id"] == "job-1"

    published = client.invoked_events[0]  # type: ignore[attr-defined]
    assert published["user_id"] == USER_ID
    assert published["payload"]["template_id"] == 55
    assert published["payload"]["template_slot_sequence"] == 1
    assert "plan_type" not in published["payload"]
    assert "split_day" not in published["payload"]


# ---------------------------------------------------------------------------
# A mesma validacao no lado da Lambda (o evento e montado no codigo, nao pelo
# FastAPI - por isso a guarda explicita de `template_id is None`)
# ---------------------------------------------------------------------------


def test_resolve_template_and_slot_sem_id_e_422_com_mensagem_em_portugues():
    with pytest.raises(HTTPException) as exc:
        template_request.resolve_template_and_slot(None, USER_ID)

    assert exc.value.status_code == 422
    assert exc.value.detail == template_request.TEMPLATE_REQUIRED_MESSAGE
    assert "Modelo de Treino" in exc.value.detail
