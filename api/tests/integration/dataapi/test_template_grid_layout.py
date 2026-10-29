"""Grade configuravel do editor de Modelo (migracao 062) contra MySQL real,
nos DOIS backends (PyMySQL de dev e shim da RDS Data API).

O que so o banco prova:

- `POST`/`PUT` gravam `grid_rows`/`grid_cols`; `GET /{id}` e `GET` (lista)
  devolvem as duas colunas; NULL = automatico (round-trip de `None`);
- `duplicate` copia a grade;
- o teto de 8 slots vale no POST, e a excecao legada do PUT: Modelo gravado
  com mais de 8 slots continua editavel com a mesma quantidade, mas nao cresce;
- grade que nao comporta os slots e 422 e nao grava nada.

Isolamento: `truncar(raw)` com `isolamento_v3.TABELAS_V3` (as tabelas de
Modelo estao la). Sem relogio: nada aqui deriva de `now()`.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import workout_plan_templates as wpt
from app.security import Identity, get_current_identity, require_admin_or_pt
from tests.support.isolamento_v3 import truncar
from tests.support.template_versions import versao_v1

pytestmark = [pytest.mark.integration, pytest.mark.dataapi]


@pytest.fixture
def seed(raw):
    truncar(raw)
    with raw.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin_grade', 'x', 'admin')")
        admin_id = cur.lastrowid
    raw.commit()
    return {"admin": admin_id}


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(wpt.router)
    identidade = Identity(user_id=user_id, role="admin", source="session")
    app.dependency_overrides[get_current_identity] = lambda: identidade
    app.dependency_overrides[require_admin_or_pt] = lambda: identidade
    return TestClient(app)


def _slots(n: int) -> list[dict]:
    return [{"sequence": seq, "name": f"Dia {seq}", "slot_type": "descanso", "items": []} for seq in range(1, n + 1)]


def _modelo(n: int, **extra) -> dict:
    return {"name": "Grade", "volume_mode": "percent_budget", "slots": _slots(n), **extra}


def _grade(corpo: dict) -> tuple:
    return (corpo.get("grid_rows"), corpo.get("grid_cols"))


def _grade_no_banco(raw, template_id: int) -> tuple:
    with raw.cursor() as cur:
        cur.execute("SELECT grid_rows, grid_cols FROM workout_plan_templates WHERE id = %s", (template_id,))
        row = cur.fetchone()
    return (row["grid_rows"], row["grid_cols"])


def test_round_trip_da_grade_no_post_get_lista_e_put(seed, db_backend, raw):
    api = _client(seed["admin"])
    criado = api.post("/workout-plan-templates", json=_modelo(6, grid_rows=2, grid_cols=3))
    assert criado.status_code == 200, (db_backend, criado.text)
    template_id = criado.json()["id"]
    assert _grade(criado.json()) == (2, 3), db_backend
    assert _grade_no_banco(raw, template_id) == (2, 3)

    assert _grade(api.get(f"/workout-plan-templates/{template_id}").json()) == (2, 3), db_backend
    lista = api.get("/workout-plan-templates").json()["items"]
    assert [_grade(t) for t in lista if t["id"] == template_id] == [(2, 3)], db_backend

    # PUT troca a grade e volta para automatica (NULL).
    trocado = api.put(f"/workout-plan-templates/{template_id}", json=_modelo(6, grid_rows=2, grid_cols=4))
    assert trocado.status_code == 200, (db_backend, trocado.text)
    assert _grade(trocado.json()) == (2, 4)
    automatico = api.put(f"/workout-plan-templates/{template_id}", json=_modelo(6))
    assert automatico.status_code == 200, (db_backend, automatico.text)
    assert _grade(automatico.json()) == (None, None)
    assert _grade_no_banco(raw, template_id) == (None, None)


def test_duplicate_copia_a_grade(seed, db_backend, raw):
    api = _client(seed["admin"])
    template_id = api.post("/workout-plan-templates", json=_modelo(5, grid_rows=2, grid_cols=3)).json()["id"]
    copia = api.post(f"/workout-plan-templates/{template_id}/duplicate")
    assert copia.status_code == 200, (db_backend, copia.text)
    assert copia.json()["id"] != template_id
    assert _grade(copia.json()) == (2, 3), db_backend
    assert len(copia.json()["slots"]) == 5
    # Linhagem (090): a copia grava a origem; o original continua sem origem.
    with raw.cursor() as cur:
        cur.execute(
            "SELECT id, source_template_id FROM workout_plan_templates WHERE id IN (%s, %s)",
            (template_id, copia.json()["id"]),
        )
        origem = {int(r["id"]): r["source_template_id"] for r in cur.fetchall()}
    assert origem == {template_id: None, copia.json()["id"]: template_id}, db_backend


def test_grade_que_nao_comporta_os_slots_e_422_e_nao_grava(seed, db_backend, raw):
    api = _client(seed["admin"])
    template_id = api.post("/workout-plan-templates", json=_modelo(4)).json()["id"]
    recusado = api.put(f"/workout-plan-templates/{template_id}", json=_modelo(4, grid_rows=1, grid_cols=3))
    assert recusado.status_code == 422, (db_backend, recusado.text)
    assert "comporta 3 slots" in recusado.json()["detail"]
    assert _grade_no_banco(raw, template_id) == (None, None)


def test_post_com_mais_de_8_slots_e_422(seed, db_backend):
    api = _client(seed["admin"])
    resposta = api.post("/workout-plan-templates", json=_modelo(9))
    assert resposta.status_code == 422, (db_backend, resposta.text)
    assert "no máximo 8 slots" in resposta.json()["detail"]


def test_put_de_modelo_legado_com_mais_de_8_slots_edita_mas_nao_cresce(seed, db_backend, raw):
    """Modelo gravado antes do teto (aqui semeado direto no banco com 10
    slots): o PUT aceita os mesmos 10, recusa 11."""
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO workout_plan_templates (name, volume_mode, is_active, created_by_user_id) "
            "VALUES ('Legado', 'percent_budget', 1, %s)",
            (seed["admin"],),
        )
        template_id = cur.lastrowid
        for seq in range(1, 11):
            cur.execute(
                "INSERT INTO workout_plan_template_slots (version_id, template_id, sequence, name, slot_type) "
                "VALUES (%s, %s, %s, %s, 'descanso')",
                (versao_v1(cur, template_id), template_id, seq, f"Dia {seq}"),
            )
    raw.commit()

    api = _client(seed["admin"])
    editado = api.put(f"/workout-plan-templates/{template_id}", json=_modelo(10))
    assert editado.status_code == 200, (db_backend, editado.text)
    assert len(editado.json()["slots"]) == 10

    cresceu = api.put(f"/workout-plan-templates/{template_id}", json=_modelo(11))
    assert cresceu.status_code == 422, (db_backend, cresceu.text)
    assert "no máximo 10 slots" in cresceu.json()["detail"]
