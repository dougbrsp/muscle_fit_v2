"""Opcoes de equipamento do RASCUNHO (`POST /workout-plans/equipment-options`): mesmo nucleo da ficha
salva (`workout_plan_equipment._ranked_equipment_options`), por academia + exercicio."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import workout_plan_equipment as wpe
from app import workout_plans
from app.security import Identity, get_current_identity

pytestmark = pytest.mark.usefixtures("permissoes_por_papel")

URL = "/workout-plans/equipment-options"
LINKS = [
    {"id": 11, "equipment_id": 1, "equipment_name": "Maquina A", "photo_url": None, "canonical_exercise_id": 5},
    {"id": 12, "equipment_id": 2, "equipment_name": "Maquina B", "photo_url": None, "canonical_exercise_id": 5},
]


class _Cur:
    def __init__(self, state):
        self.s = state

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.sql, self.params = sql, params

    def fetchone(self):
        if "FROM gyms" in self.sql:
            return {"id": self.params[0]} if self.params[0] in self.s["gyms"] else None
        if "FROM gym_equipment" in self.sql:
            return self.s["ge"].get(self.params[0])
        return None


@pytest.fixture
def mundo(monkeypatch):
    s = {
        "gyms": {1},
        "links": list(LINKS),
        "coherent": True,
        "ge": {11: {"id": 11, "equipment_id": 1, "gym_id": 1}, 21: {"id": 21, "equipment_id": 1, "gym_id": 2}},
        "ranked_for": [],
    }
    conn = SimpleNamespace(cursor=lambda: _Cur(s))

    @contextmanager
    def tx():
        yield conn

    safety = {
        (5, 1): SimpleNamespace(max_safe_rpe=9, failure_allowed=True),
        (5, 2): SimpleNamespace(max_safe_rpe=8, failure_allowed=False),
    }
    monkeypatch.setattr(wpe, "transaction", tx)
    monkeypatch.setattr(wpe, "_same_exercise_links", lambda c, g, m: list(s["links"]) if (g, m) == (1, 5) else [])
    monkeypatch.setattr(
        wpe, "rank_gym_equipment_rows", lambda cur, links, gym_id, user_id: s["ranked_for"].append(user_id) or links
    )
    monkeypatch.setattr(wpe.ts, "resolve_training_safety_bulk", lambda c, pairs: {p: safety[p] for p in pairs if p in safety})
    monkeypatch.setattr(wpe.equipment_pair, "is_pair_coherent", lambda cur, m, link: s["coherent"])
    monkeypatch.setattr(
        wpe,
        "_load_plan_and_row",
        lambda c, p, e, u: (
            {"gym_id": 1, "user_id": u},
            {"id": 3, "mapping_id": 5, "exercise_name": "X", "preferred_equipment_id": 11, "advanced_technique": None},
        ),
    )
    return s


def _client(role: str = "regular") -> TestClient:
    app = FastAPI()
    app.include_router(workout_plans.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=7, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False)


def test_items_do_rascunho_iguais_aos_da_ficha_salva(mundo):
    saved = wpe.list_equipment_options(10, 3, 7)
    draft = wpe.list_draft_equipment_options(1, 5, 7, current_gym_equipment_id=11)
    assert draft["items"] == saved["items"] and len(draft["items"]) == 2
    assert draft["current_gym_equipment_id"] == saved["current_gym_equipment_id"] == 11
    assert draft["current_max_safe_rpe"] == saved["current_max_safe_rpe"] == 9
    assert [i["is_current"] for i in draft["items"]] == [True, False]
    assert draft["items"][1]["eligible"] is False  # RPE seguro menor que o do atual
    assert mundo["ranked_for"] == [7, 7]  # ranking de quem esta logado


def test_sem_associacao_ou_academia_inativa_lista_vazia(mundo):
    assert wpe.list_draft_equipment_options(1, 999, 7)["items"] == []
    out = wpe.list_draft_equipment_options(2, 5, 7, current_gym_equipment_id=11)
    assert out["items"] == [] and out["current_gym_equipment_id"] is None


def test_vinculo_atual_de_outra_academia_ou_incoerente_e_ignorado(mundo):
    out = wpe.list_draft_equipment_options(1, 5, 7, current_gym_equipment_id=21)
    assert out["current_gym_equipment_id"] is None and out["current_max_safe_rpe"] is None
    assert not any(i["is_current"] for i in out["items"])
    mundo["coherent"] = False
    out = wpe.list_draft_equipment_options(1, 5, 7, current_gym_equipment_id=11)
    assert out["current_gym_equipment_id"] is None


def test_rota_responde_payload(mundo):
    r = _client().post(URL, json={"gym_id": 1, "mapping_id": 5, "current_gym_equipment_id": 11})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"gym_id", "mapping_id", "current_gym_equipment_id", "current_max_safe_rpe", "required_technique", "items"}
    assert body["current_gym_equipment_id"] == 11


@pytest.mark.parametrize("body", [{}, {"gym_id": 0, "mapping_id": 5}, {"gym_id": 1, "mapping_id": 2**63}])
def test_entrada_invalida_e_422(body):
    assert _client().post(URL, json=body).status_code == 422


def test_rota_exige_permissao_treino_planos_ver():
    assert _client(role="sem_grupo").post(URL, json={"gym_id": 1, "mapping_id": 5}).status_code == 403
