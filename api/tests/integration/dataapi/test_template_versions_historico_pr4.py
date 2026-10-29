"""Historico de versoes e reversao do Modelo (PR4) contra MySQL real, nos
DOIS backends (PyMySQL de dev e shim da RDS Data API).

- `GET /{id}/versions`: lista leve, mais nova primeiro, autor, `is_current`,
  `reverted_from_number`;
- `GET /{id}/versions/{vid}`: a arvore DAQUELA versao na forma de `GET /{id}`;
- `POST /{id}/versions/{vid}/revert`: v1 -> v2 -> reverter para v1 grava a
  v3 com o conteudo da v1 e `reverted_from = v1`; cabecalho vivo e intencao
  acompanham; 409 (ja vigente / mesmo conteudo), 404 (outro Modelo, excluido)
  e 422 com a LISTA do que esta invalido hoje;
- `PUT` sinaliza `version_created` (aditivo).

Isolamento: `truncar(raw)`. Sem relogio de Python.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import workout_plan_templates as wpt
from app.security import Identity, get_current_identity, require_admin_or_pt
from tests.support.isolamento_v3 import truncar

pytestmark = [pytest.mark.integration, pytest.mark.dataapi]

PEITORAL, COSTAS = 52, 6


@pytest.fixture
def seed(raw):
    truncar(raw)
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, full_name, password_hash, role) VALUES ('admin_pr4', 'Admin PR4', 'x', 'admin')"
        )
        admin = cur.lastrowid
        for gid, nome in ((PEITORAL, "Peitoral"), (COSTAS, "Costas")):
            cur.execute("INSERT INTO muscle_groups (id, name) VALUES (%s, %s)", (gid, nome))
    raw.commit()
    return {"admin": admin}


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(wpt.router)
    identidade = Identity(user_id=user_id, role="admin", source="session")
    app.dependency_overrides[get_current_identity] = lambda: identidade
    app.dependency_overrides[require_admin_or_pt] = lambda: identidade
    return TestClient(app)


def _modelo(series=3, nome_slot="Push", **extra) -> dict:
    return {
        "name": "Historico",
        "volume_mode": "percent_budget",
        "slots": [
            {
                "sequence": 1,
                "name": nome_slot,
                "slot_type": "normal",
                "items": [{"item_order": 1, "item_kind": "muscle_group", "muscle_group_id": PEITORAL, "sets_count": series}],
            }
        ],
        **extra,
    }


def _criar(api, db_backend) -> int:
    r = api.post("/workout-plan-templates", json=_modelo())
    assert r.status_code == 200, (db_backend, r.text)
    return r.json()["id"]


def _arvore(corpo) -> list:
    return [
        (s["sequence"], s["name"], [(i["muscle_group_id"], i["sets_count"]) for i in s["items"]])
        for s in corpo["slots"]
    ]


def test_v1_v2_reverter_para_v1_gera_v3_com_o_conteudo_da_v1(seed, db_backend):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    v2 = api.put(f"/workout-plan-templates/{template_id}", json=_modelo(series=5, nome_slot="Push B"))
    assert v2.status_code == 200 and v2.json()["version_created"] is True, (db_backend, v2.text)

    lista = api.get(f"/workout-plan-templates/{template_id}/versions").json()["items"]
    assert [(v["number"], v["is_current"]) for v in lista] == [(2, True), (1, False)], db_backend
    assert lista[0]["author_name"] == "Admin PR4"
    v1_id = lista[1]["id"]

    detalhe_v1 = api.get(f"/workout-plan-templates/{template_id}/versions/{v1_id}").json()
    assert _arvore(detalhe_v1) == [(1, "Push", [(PEITORAL, 3)])]
    assert detalhe_v1["version"]["number"] == 1 and detalhe_v1["version"]["is_current"] is False
    # Resumo somente leitura: nomes resolvidos pelo backend e autor da versao.
    assert detalhe_v1["slots"][0]["items"][0]["muscle_group_name"] == "Peitoral"
    assert detalhe_v1["version"]["author_name"] == "Admin PR4"
    assert detalhe_v1["version"]["reverted_from_number"] is None

    revertido = api.post(f"/workout-plan-templates/{template_id}/versions/{v1_id}/revert")
    assert revertido.status_code == 200, (db_backend, revertido.text)
    corpo = revertido.json()
    assert corpo["current_version"]["number"] == 3
    assert corpo["reverted_from_version"] == {"id": v1_id, "number": 1}
    assert _arvore(corpo) == _arvore(detalhe_v1)

    lista = api.get(f"/workout-plan-templates/{template_id}/versions").json()["items"]
    assert [(v["number"], v["reverted_from_number"], v["is_current"]) for v in lista] == [
        (3, 1, True),
        (2, None, False),
        (1, None, False),
    ]
    assert lista[0]["change_summary"] == "Revertido para a v1"
    v3 = api.get(f"/workout-plan-templates/{template_id}/versions/{lista[0]['id']}").json()
    assert v3["version"]["reverted_from_number"] == 1
    # A v1 continua intacta e a v2 tambem.
    assert _arvore(api.get(f"/workout-plan-templates/{template_id}/versions/{v1_id}").json()) == _arvore(detalhe_v1)


def test_put_sem_mudanca_estrutural_sinaliza_versao_mantida(seed, db_backend):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    r = api.put(f"/workout-plan-templates/{template_id}", json=_modelo(name="Outro nome"))
    assert r.status_code == 200
    assert r.json()["version_created"] is False, db_backend


def test_reverter_a_vigente_ou_para_o_mesmo_conteudo_e_409(seed, db_backend):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    v1_id = api.get(f"/workout-plan-templates/{template_id}").json()["current_version"]["id"]
    assert api.post(f"/workout-plan-templates/{template_id}/versions/{v1_id}/revert").status_code == 409

    api.put(f"/workout-plan-templates/{template_id}", json=_modelo(series=5))
    api.post(f"/workout-plan-templates/{template_id}/versions/{v1_id}/revert")  # v3 = v1
    v2_id = api.get(f"/workout-plan-templates/{template_id}/versions").json()["items"][1]["id"]
    assert api.post(f"/workout-plan-templates/{template_id}/versions/{v2_id}/revert").status_code == 200
    # agora a vigente (v4) = v2; reverter de novo para a v2 nao muda nada
    repetido = api.post(f"/workout-plan-templates/{template_id}/versions/{v2_id}/revert")
    assert repetido.status_code == 409, db_backend


def test_versao_de_outro_modelo_ou_modelo_excluido_e_404(seed, db_backend):
    api = _client(seed["admin"])
    a = _criar(api, db_backend)
    b = _criar(api, db_backend)
    api.put(f"/workout-plan-templates/{a}", json=_modelo(series=5))
    v1_de_a = api.get(f"/workout-plan-templates/{a}/versions").json()["items"][1]["id"]
    assert api.post(f"/workout-plan-templates/{b}/versions/{v1_de_a}/revert").status_code == 404
    assert api.get(f"/workout-plan-templates/{b}/versions/{v1_de_a}").status_code == 404

    assert api.delete(f"/workout-plan-templates/{a}").status_code == 200
    assert api.post(f"/workout-plan-templates/{a}/versions/{v1_de_a}/revert").status_code == 404
    assert api.get(f"/workout-plan-templates/{a}/versions").status_code == 404


def test_versao_que_hoje_e_invalida_e_422_com_a_lista(seed, db_backend, raw):
    """A v1 tem um item legado que o save de hoje recusa (`item_kind`
    'exercise'); a reversao nao reativa e devolve a lista."""
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    v1_id = api.get(f"/workout-plan-templates/{template_id}").json()["current_version"]["id"]
    with raw.cursor() as cur:
        cur.execute(
            "UPDATE workout_plan_template_slot_items i JOIN workout_plan_template_slots s ON s.id = i.slot_id "
            "SET i.item_kind = 'exercise' WHERE s.version_id = %s",
            (v1_id,),
        )
    api.put(f"/workout-plan-templates/{template_id}", json=_modelo(series=4))

    r = api.post(f"/workout-plan-templates/{template_id}/versions/{v1_id}/revert")
    assert r.status_code == 422, (db_backend, r.text)
    detalhe = r.json()["detail"]
    assert detalhe["message"] == "A v1 não pode ser reativada hoje:"
    assert detalhe["errors"] and any("exercise" in e for e in detalhe["errors"])
    # Nada foi gravado.
    assert len(api.get(f"/workout-plan-templates/{template_id}/versions").json()["items"]) == 2


def test_reverter_restaura_cabecalho_vivo_e_intencao(seed, db_backend, raw):
    api = _client(seed["admin"])
    template_id = _criar(api, db_backend)
    api.put(
        f"/workout-plan-templates/{template_id}/methodology-intent",
        json={"intent": "specialization", "target_group_ids": [COSTAS]},
    )  # v2 (so intencao)
    api.put(f"/workout-plan-templates/{template_id}", json=_modelo(series=6, grid_rows=1, grid_cols=2))  # v3
    v1_id = api.get(f"/workout-plan-templates/{template_id}/versions").json()["items"][-1]["id"]
    corpo = api.post(f"/workout-plan-templates/{template_id}/versions/{v1_id}/revert").json()
    assert (corpo["grid_rows"], corpo["grid_cols"]) == (None, None), db_backend
    assert corpo["methodology_intent"] == {"intent": "standard", "target_group_ids": []}
    with raw.cursor() as cur:
        cur.execute("SELECT grid_rows FROM workout_plan_templates WHERE id = %s", (template_id,))
        assert cur.fetchone()["grid_rows"] is None
