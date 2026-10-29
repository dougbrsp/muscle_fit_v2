"""CRUD `/context-events` (F2.1, migracao 048) contra MySQL real, nos DOIS
backends (PyMySQL de dev e shim da RDS Data API de producao).

O que este arquivo prova:

- POST cria e NAO faz upsert: o mesmo tipo no mesmo dia pode acontecer duas
  vezes e as duas ficam (ao contrario da 047, onde a chave e unica);
- GET lista so o proprio usuario, com filtro por tipo e por INTERVALO (evento
  com duracao aparece em qualquer janela que toque qualquer um dos seus dias);
- PUT/DELETE so alcancam o proprio registro; o usuario B recebe 404 para o id
  do A (IDOR) e nao consegue apaga-lo;
- validacao: tipo fora do vocabulario, `intensity` 0 ou 6, e
  `end_local_date` antes de `local_date` respondem 422 - inclusive quando o
  PUT mexe em um lado so do intervalo;
- escrita sempre grava `source='manual'`: derivado nao entra por aqui;
- o payload e identico nos dois backends.

Datas: `hoje_do_usuario()` (fuso do usuario de teste), nunca `date.today()`.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import context_events  # noqa: F401 - importado no topo (use_backend troca o backend de modulos ja importados)
from app.context_events_schema import reset_context_events_table_cache
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()
ONTEM = HOJE - _dt.timedelta(days=1)


@pytest.fixture
def usuarios(raw):
    reset_context_events_table_cache()
    with raw.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        cur.execute("TRUNCATE TABLE context_events")
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
    app.include_router(context_events.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


def _payload(**over):
    base = {"event_type": "alcohol", "local_date": HOJE.isoformat(), "intensity": 3, "notes": "duas taças"}
    base.update(over)
    return base


def _limpar(cliente: TestClient) -> None:
    for item in cliente.get("/context-events").json()["items"]:
        assert cliente.delete(f"/context-events/{item['id']}").status_code == 200


def test_ciclo_completo_identico_nos_dois_backends(usuarios, monkeypatch, fake_rds, mysql_schema):
    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        cliente = _client(usuarios["dono"])
        _limpar(cliente)

        criado = cliente.post("/context-events", json=_payload())
        assert criado.status_code == 200, (backend, criado.text)
        assert criado.json()["status"] == "created"
        item = criado.json()["item"]
        assert item["event_type_label"] == "Álcool"
        assert item["source"] == "manual" and item["derived"] is False and item["intensity"] == 3

        # NAO e upsert: o mesmo tipo no mesmo dia pode acontecer duas vezes.
        outro_igual = cliente.post("/context-events", json=_payload(intensity=1))
        assert outro_igual.json()["item"]["id"] != item["id"]

        viagem = cliente.post(
            "/context-events",
            json={
                "event_type": "travel",
                "local_date": (HOJE - _dt.timedelta(days=10)).isoformat(),
                "end_local_date": ONTEM.isoformat(),
            },
        ).json()["item"]
        assert viagem["intensity"] is None, "sem gradacao continua nulo, nunca 0"

        lista = cliente.get("/context-events").json()
        assert lista["has_more"] is False and lista["limitations"] == []
        assert len(lista["items"]) == 3
        assert [i["event_type"] for i in cliente.get("/context-events", params={"event_type": "travel"}).json()["items"]] == ["travel"]

        # Filtro por INTERVALO: a viagem terminou ontem e aparece numa janela
        # que comeca ontem, mesmo tendo comecado 10 dias antes.
        do_ontem = cliente.get("/context-events", params={"date_from": ONTEM.isoformat()}).json()["items"]
        assert "travel" in {i["event_type"] for i in do_ontem}

        pagina = cliente.get("/context-events", params={"limit": 1}).json()
        assert len(pagina["items"]) == 1 and pagina["has_more"] is True

        editado = cliente.put(f"/context-events/{item['id']}", json={"notes": None, "intensity": 5})
        assert editado.status_code == 200, editado.text
        assert editado.json()["item"]["notes"] is None and editado.json()["item"]["intensity"] == 5

        resultados[backend] = [
            {k: v for k, v in i.items() if k not in ("id", "created_at", "updated_at")}
            for i in cliente.get("/context-events").json()["items"]
        ]
    assert resultados["pymysql"] == resultados["data-api"]


def test_idor_usuario_b_nao_le_nem_altera_nem_apaga_o_do_a(usuarios, db_backend):
    dono = _client(usuarios["dono"])
    outro = _client(usuarios["outro"])
    _limpar(dono)
    _limpar(outro)

    item = dono.post("/context-events", json=_payload()).json()["item"]

    assert outro.get("/context-events").json()["items"] == [], db_backend
    assert outro.get(f"/context-events/{item['id']}").status_code == 404
    assert outro.put(f"/context-events/{item['id']}", json={"intensity": 1}).status_code == 404
    assert outro.delete(f"/context-events/{item['id']}").status_code == 404
    # O registro do A continua intacto.
    assert dono.get(f"/context-events/{item['id']}").json()["intensity"] == 3
    # O B pode ter o SEU evento no mesmo dia.
    assert outro.post("/context-events", json=_payload(intensity=1)).json()["status"] == "created"
    assert len(dono.get("/context-events").json()["items"]) == 1


def test_validacao_do_vocabulario_da_intensidade_e_do_intervalo(usuarios, db_backend):
    dono = _client(usuarios["dono"])
    _limpar(dono)

    assert dono.post("/context-events", json=_payload(event_type="ressaca")).status_code == 422
    # Zero nao e o menor grau: "sem gradacao" e ausencia.
    assert dono.post("/context-events", json=_payload(intensity=0)).status_code == 422
    assert dono.post("/context-events", json=_payload(intensity=6)).status_code == 422
    assert dono.post("/context-events", json=_payload(peso=80)).status_code == 422
    assert dono.post(
        "/context-events",
        json=_payload(event_type="travel", local_date=HOJE.isoformat(), end_local_date=ONTEM.isoformat()),
    ).status_code == 422

    viagem = dono.post(
        "/context-events",
        json={"event_type": "travel", "local_date": ONTEM.isoformat(), "end_local_date": HOJE.isoformat()},
    ).json()["item"]
    # PUT parcial: mudar so o INICIO tem de ser checado contra o fim gravado.
    depois = (HOJE + _dt.timedelta(days=2)).isoformat()
    assert dono.put(f"/context-events/{viagem['id']}", json={"local_date": depois}).status_code == 422, db_backend
    assert dono.put(f"/context-events/{viagem['id']}", json={}).status_code == 400
    assert dono.delete(f"/context-events/{viagem['id']}").status_code == 200
    assert dono.delete(f"/context-events/{viagem['id']}").status_code == 404
