"""Elegibilidade de troca para o RASCUNHO (`POST /workout-plans/exercise-swap-options`):
mesma regra e mesmo nucleo da ficha salva (`services._swap_eligibility_by_mapping`), por `mapping_id`."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import services, workout_plans
from app.security import Identity, get_current_identity
from tests.unit.test_ficha_troca_exercicio_elegiveis import _Cursor, ficha  # noqa: F401 (fixture)

pytestmark = pytest.mark.usefixtures("permissoes_por_papel")

URL = "/workout-plans/exercise-swap-options"


def _client(role: str = "regular") -> TestClient:
    app = FastAPI()
    app.include_router(workout_plans.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=7, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def test_mesma_elegibilidade_da_ficha_salva(ficha):  # noqa: F811
    ficha(plan_rows=[{"id": 1, "mapping_id": 5}, {"id": 2, "mapping_id": 115}, {"id": 3, "mapping_id": 300}])
    saved = {r["workout_plan_exercise_id"]: r for r in services.list_exercise_swap_options(10, user_id=1)["rows"]}
    draft = {i["mapping_id"]: i for i in services.list_draft_exercise_swap_options([5, 115, 300])["items"]}
    for row_id, mapping_id in ((1, 5), (2, 115), (3, 300)):
        assert draft[mapping_id]["primary_group_id"] == saved[row_id]["primary_group_id"]
        assert draft[mapping_id]["exercise_ids"] == saved[row_id]["exercise_ids"]
    assert draft[5] == {"mapping_id": 5, "primary_group_id": 12, "exercise_ids": [5, 6, 300]}


def test_sem_grupo_primary_nao_restringe(ficha):  # noqa: F811
    ficha()
    assert services.list_draft_exercise_swap_options([300])["items"] == [
        {"mapping_id": 300, "primary_group_id": None, "exercise_ids": None}
    ]


def test_ids_repetidos_saem_uma_vez(ficha):  # noqa: F811
    ficha()
    items = services.list_draft_exercise_swap_options([7, 5, 7, 5])["items"]
    assert [i["mapping_id"] for i in items] == [7, 5]


def test_lista_vazia_nao_toca_o_banco(ficha):  # noqa: F811
    cur = ficha()
    assert services.list_draft_exercise_swap_options([]) == {"items": []}
    assert cur.calls == []


def test_mapping_id_que_nao_resolve_vira_sem_restricao(ficha):  # noqa: F811
    ficha()
    assert services.list_draft_exercise_swap_options([99999])["items"] == [
        {"mapping_id": 99999, "primary_group_id": None, "exercise_ids": None}
    ]


def test_id_legado_resolve_para_o_canonico(ficha):  # noqa: F811
    ficha(legacy={999: 6})
    item = services.list_draft_exercise_swap_options([999])["items"][0]
    assert item["mapping_id"] == 999 and item["primary_group_id"] == 12 and 5 in item["exercise_ids"]


def test_rota_responde_items(ficha):  # noqa: F811
    ficha()
    response = _client().post(URL, json={"mapping_ids": [7]})
    assert response.status_code == 200
    assert response.json() == {"items": [{"mapping_id": 7, "primary_group_id": 6, "exercise_ids": [7, 300]}]}


@pytest.mark.parametrize(
    "body",
    [{}, {"mapping_ids": "5"}, {"mapping_ids": [0]}, {"mapping_ids": [-1]}, {"mapping_ids": ["a"]}, {"mapping_ids": [1] * 201},
     {"mapping_ids": [2**63]}],
)
def test_entrada_invalida_e_422(body):
    assert _client().post(URL, json=body).status_code == 422


def test_maior_id_permitido_e_aceito(ficha):  # noqa: F811
    ficha()
    assert _client().post(URL, json={"mapping_ids": [2**63 - 1]}).status_code == 200


def test_rota_exige_permissao_treino_planos_ver():
    assert _client(role="sem_grupo").post(URL, json={"mapping_ids": [5]}).status_code == 403
