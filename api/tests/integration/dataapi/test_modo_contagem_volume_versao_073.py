"""Modo de contagem do volume por Modelo (migracao 073, Lote 2) contra MySQL
real, nos DOIS backends (PyMySQL de dev e shim da RDS Data API).

- POST sem o campo nasce `primary_only` (Modelo e versao);
- PUT trocando o modo e mudanca ESTRUTURAL: abre a versao seguinte, e a
  anterior guarda o modo dela;
- PUT sem o campo (editor anterior ao Lote 3) PRESERVA o modo gravado;
- reverter devolve o modo da versao reativada; duplicar copia o modo;
- modo fora do dominio e 422;
- `GET /{id}/methodology-volume` avalia no modo do Modelo e o expoe
  (`counting_mode`, metrica v5); o preview herda o do Modelo por
  `template_id` e o corpo tem precedencia.

Isolamento: `truncar(raw)`.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import workout_plan_templates as wpt
from app.security import Identity, get_current_identity, require_admin_or_pt
from tests.support.isolamento_v3 import truncar

pytestmark = [pytest.mark.integration, pytest.mark.dataapi]

PEITORAL = 52
PONDERADO = "primary_secondary"
PRIMARIO = "primary_only"


@pytest.fixture
def seed(raw):
    truncar(raw)
    with raw.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin_073', 'x', 'admin')")
        admin = cur.lastrowid
        cur.execute("INSERT INTO muscle_groups (id, name) VALUES (%s, 'Peitoral')", (PEITORAL,))
    raw.commit()
    return {"admin": admin}


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(wpt.router)
    identidade = Identity(user_id=user_id, role="admin", source="session")
    app.dependency_overrides[get_current_identity] = lambda: identidade
    app.dependency_overrides[require_admin_or_pt] = lambda: identidade
    return TestClient(app)


def _modelo(**extra) -> dict:
    return {
        "name": "Modo de contagem",
        "volume_mode": "percent_budget",
        "slots": [
            {
                "sequence": 1,
                "name": "Push",
                "slot_type": "normal",
                "items": [{"item_order": 1, "item_kind": "muscle_group", "muscle_group_id": PEITORAL, "sets_count": 3}],
            }
        ],
        **extra,
    }


def _modos_das_versoes(raw, template_id: int) -> list[tuple[int, str]]:
    raw.commit()  # enxerga o que a API gravou em outra conexao
    with raw.cursor() as cur:
        cur.execute(
            "SELECT version_number, volume_counting_mode FROM workout_plan_template_versions "
            "WHERE template_id = %s ORDER BY version_number",
            (template_id,),
        )
        return [(int(r["version_number"]), str(r["volume_counting_mode"])) for r in cur.fetchall()]


def test_trocar_o_modo_abre_versao_e_editar_sem_o_campo_preserva(seed, db_backend, raw):
    api = _client(seed["admin"])
    criado = api.post("/workout-plan-templates", json=_modelo())
    assert criado.status_code == 200, (db_backend, criado.text)
    template_id = criado.json()["id"]
    assert criado.json()["volume_counting_mode"] == PRIMARIO
    assert _modos_das_versoes(raw, template_id) == [(1, PRIMARIO)]

    trocado = api.put(f"/workout-plan-templates/{template_id}", json=_modelo(volume_counting_mode=PONDERADO))
    assert trocado.status_code == 200, (db_backend, trocado.text)
    assert trocado.json()["version_created"] is True
    assert trocado.json()["volume_counting_mode"] == PONDERADO
    assert _modos_das_versoes(raw, template_id) == [(1, PRIMARIO), (2, PONDERADO)]

    # Editor que nao conhece o campo: so o nome muda, o modo fica.
    renomeado = api.put(f"/workout-plan-templates/{template_id}", json=_modelo(name="Outro nome"))
    assert renomeado.status_code == 200, (db_backend, renomeado.text)
    assert renomeado.json()["version_created"] is False
    assert api.get(f"/workout-plan-templates/{template_id}").json()["volume_counting_mode"] == PONDERADO

    lista = api.get("/workout-plan-templates").json()["items"]
    assert [t["volume_counting_mode"] for t in lista if t["id"] == template_id] == [PONDERADO]

    versoes = api.get(f"/workout-plan-templates/{template_id}/versions").json()["items"]
    v1_id = next(v["id"] for v in versoes if v["number"] == 1)
    detalhe_v1 = api.get(f"/workout-plan-templates/{template_id}/versions/{v1_id}").json()
    assert detalhe_v1["volume_counting_mode"] == PRIMARIO

    revertido = api.post(f"/workout-plan-templates/{template_id}/versions/{v1_id}/revert")
    assert revertido.status_code == 200, (db_backend, revertido.text)
    assert revertido.json()["volume_counting_mode"] == PRIMARIO
    assert _modos_das_versoes(raw, template_id) == [(1, PRIMARIO), (2, PONDERADO), (3, PRIMARIO)]


def test_duplicar_copia_o_modo_e_modo_invalido_e_422(seed, db_backend, raw):
    api = _client(seed["admin"])
    criado = api.post("/workout-plan-templates", json=_modelo(volume_counting_mode=PONDERADO))
    assert criado.status_code == 200, (db_backend, criado.text)
    assert criado.json()["volume_counting_mode"] == PONDERADO

    copia = api.post(f"/workout-plan-templates/{criado.json()['id']}/duplicate")
    assert copia.status_code == 200, (db_backend, copia.text)
    assert copia.json()["volume_counting_mode"] == PONDERADO
    assert _modos_das_versoes(raw, copia.json()["id"]) == [(1, PONDERADO)]

    assert api.post("/workout-plan-templates", json=_modelo(volume_counting_mode="secundario")).status_code == 422


def test_metodologia_avalia_no_modo_do_modelo(seed, db_backend):
    api = _client(seed["admin"])
    template_id = api.post("/workout-plan-templates", json=_modelo(volume_counting_mode=PONDERADO)).json()["id"]

    salvo = api.get(f"/workout-plan-templates/{template_id}/methodology-volume").json()
    assert (salvo["counting_mode"], salvo["metric_version"]) == (PONDERADO, "methodology-volume-v5"), db_backend

    corpo = {"slots": _modelo()["slots"], "volume_mode": "percent_budget"}
    herdado = api.post("/workout-plan-templates/methodology-volume-preview", json={**corpo, "template_id": template_id})
    assert herdado.json()["counting_mode"] == PONDERADO
    explicito = api.post(
        "/workout-plan-templates/methodology-volume-preview",
        json={**corpo, "template_id": template_id, "volume_counting_mode": PRIMARIO},
    )
    assert explicito.json()["counting_mode"] == PRIMARIO
    sem_modelo = api.post("/workout-plan-templates/methodology-volume-preview", json=corpo)
    assert sem_modelo.json()["counting_mode"] == PRIMARIO
