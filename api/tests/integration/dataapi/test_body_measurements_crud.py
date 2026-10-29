"""CRUD `/body-measurements` (F1.4, migracao 047) contra MySQL real, nos
DOIS backends (PyMySQL de dev e shim da RDS Data API de producao).

O que este arquivo prova:

- POST cria e, repetido na mesma `(local_date, site)`, ATUALIZA (upsert) em
  vez de duplicar;
- GET lista so o proprio usuario, com filtros `site`/`date_from`/`date_to`
  e paginacao;
- PUT/DELETE so alcancam o proprio registro; o usuario B recebe 404 para o
  id do A (IDOR) e nao consegue apaga-lo;
- validacao: `site` fora do vocabulario e `value_cm` fora de [10, 300]
  respondem 422;
- o payload e identico nos dois backends.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import body_measurements  # noqa: F401 - importado no topo (use_backend troca o backend de modulos ja importados)
from app.body_measurements_schema import reset_body_measurements_table_cache
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()


@pytest.fixture
def usuarios(raw):
    reset_body_measurements_table_cache()
    with raw.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        cur.execute("TRUNCATE TABLE body_measurements")
        cur.execute("TRUNCATE TABLE users")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        ids = {}
        for nome in ("dono", "outro"):
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', 'regular')", (nome,))
            ids[nome] = cur.lastrowid
    raw.commit()
    return ids


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(body_measurements.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


def _payload(**over):
    base = {"local_date": HOJE.isoformat(), "site": "waist", "value_cm": 88.5, "protocol_ok": True, "notes": "jejum"}
    base.update(over)
    return base


def test_ciclo_completo_identico_nos_dois_backends(usuarios, monkeypatch, fake_rds, mysql_schema):
    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        cliente = _client(usuarios["dono"])
        # limpa o que o backend anterior escreveu
        for item in cliente.get("/body-measurements").json()["items"]:
            assert cliente.delete(f"/body-measurements/{item['id']}").status_code == 200

        criado = cliente.post("/body-measurements", json=_payload())
        assert criado.status_code == 200, (backend, criado.text)
        assert criado.json()["status"] == "created"
        item = criado.json()["item"]
        assert item["value_cm"] == 88.5 and item["protocol_ok"] is True and item["site_label"] == "Cintura"

        # upsert: mesma data e local -> atualiza, nao duplica
        de_novo = cliente.post("/body-measurements", json=_payload(value_cm=88.0, protocol_ok=False))
        assert de_novo.json()["status"] == "updated"
        assert de_novo.json()["item"]["id"] == item["id"]
        assert de_novo.json()["item"]["value_cm"] == 88.0
        assert de_novo.json()["item"]["protocol_ok"] is False

        ontem = (HOJE - _dt.timedelta(days=1)).isoformat()
        assert cliente.post("/body-measurements", json=_payload(local_date=ontem, site="hip", value_cm=98)).status_code == 200

        lista = cliente.get("/body-measurements").json()
        assert lista["has_more"] is False and lista["limitations"] == []
        assert [(i["local_date"], i["site"]) for i in lista["items"]] == [(HOJE.isoformat(), "waist"), (ontem, "hip")]
        assert [i["site"] for i in cliente.get("/body-measurements", params={"site": "hip"}).json()["items"]] == ["hip"]
        assert cliente.get("/body-measurements", params={"date_from": HOJE.isoformat()}).json()["items"][0]["site"] == "waist"
        pagina = cliente.get("/body-measurements", params={"limit": 1}).json()
        assert len(pagina["items"]) == 1 and pagina["has_more"] is True

        editado = cliente.put(f"/body-measurements/{item['id']}", json={"value_cm": 87.5, "notes": None})
        assert editado.status_code == 200, editado.text
        assert editado.json()["item"]["value_cm"] == 87.5
        assert cliente.get(f"/body-measurements/{item['id']}").json()["notes"] is None

        resultados[backend] = [
            {k: v for k, v in i.items() if k not in ("id", "created_at", "updated_at")}
            for i in cliente.get("/body-measurements").json()["items"]
        ]
    assert resultados["pymysql"] == resultados["data-api"]


def test_idor_usuario_b_nao_le_nem_altera_nem_apaga_o_do_a(usuarios, db_backend):
    dono = _client(usuarios["dono"])
    outro = _client(usuarios["outro"])
    for item in dono.get("/body-measurements").json()["items"]:
        dono.delete(f"/body-measurements/{item['id']}")

    item = dono.post("/body-measurements", json=_payload()).json()["item"]

    assert outro.get("/body-measurements").json()["items"] == [], db_backend
    assert outro.get(f"/body-measurements/{item['id']}").status_code == 404
    assert outro.put(f"/body-measurements/{item['id']}", json={"value_cm": 50}).status_code == 404
    assert outro.delete(f"/body-measurements/{item['id']}").status_code == 404
    # O registro do A continua intacto.
    assert dono.get(f"/body-measurements/{item['id']}").json()["value_cm"] == 88.5
    # O B pode ter a SUA cintura no mesmo dia (UNIQUE inclui user_id).
    assert outro.post("/body-measurements", json=_payload(value_cm=70)).json()["status"] == "created"
    assert len(dono.get("/body-measurements").json()["items"]) == 1


def test_validacao_e_colisao_de_unique(usuarios, db_backend):
    dono = _client(usuarios["dono"])
    for item in dono.get("/body-measurements").json()["items"]:
        dono.delete(f"/body-measurements/{item['id']}")

    assert dono.post("/body-measurements", json=_payload(site="belly")).status_code == 422
    assert dono.post("/body-measurements", json=_payload(value_cm=5)).status_code == 422
    assert dono.post("/body-measurements", json=_payload(value_cm=301)).status_code == 422
    assert dono.post("/body-measurements", json=_payload(protocol_ok="talvez")).status_code == 422

    a = dono.post("/body-measurements", json=_payload()).json()["item"]
    ontem = (HOJE - _dt.timedelta(days=1)).isoformat()
    b = dono.post("/body-measurements", json=_payload(local_date=ontem)).json()["item"]
    # Mover B para a data de A colide com o UNIQUE -> 409, nao 500.
    assert dono.put(f"/body-measurements/{b['id']}", json={"local_date": HOJE.isoformat()}).status_code == 409, db_backend
    assert dono.put(f"/body-measurements/{a['id']}", json={}).status_code == 400
    assert dono.delete(f"/body-measurements/{a['id']}").status_code == 200
    assert dono.delete(f"/body-measurements/{a['id']}").status_code == 404
