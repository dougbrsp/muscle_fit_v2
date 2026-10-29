"""CRUD `/daily-checkins` (F2.2, migracao 050) contra MySQL real, nos DOIS
backends (PyMySQL de dev e shim da RDS Data API de producao).

O que este arquivo prova:

- POST e UPSERT por `(user_id, local_date)`: repetir o check-in do dia CORRIGE
  em vez de duplicar (ao contrario da 048, onde dois eventos do mesmo tipo no
  mesmo dia sao dois fatos);
- o UPSERT PARCIAL nao apaga o que ja estava: mandar so `soreness` a tarde
  preserva o `energy` respondido de manha - a armadilha mais facil de cometer
  com `ON DUPLICATE KEY UPDATE`;
- escore ausente continua `null`, nunca 0 nem 3 "neutro";
- PUT apaga um escore com `null` EXPLICITO e nao aceita mudar a data;
- GET lista so o proprio usuario, com filtro por data e paginacao;
- IDOR: o usuario B nao le, nao altera nem apaga o check-in do A, e pode ter o
  SEU no mesmo dia;
- validacao: escore 0 ou 6, campo desconhecido e check-in vazio -> 422;
- o payload e identico nos dois backends.

Datas: `hoje_do_usuario()` (fuso do usuario de teste), nunca `date.today()`.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import daily_checkins  # noqa: F401 - importado no topo (use_backend troca o backend de modulos ja importados)
from app.daily_checkins_schema import reset_daily_checkins_table_cache
from app.security import Identity, get_current_identity
from tests.support.dataapi_backend import BACKENDS, use_backend
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()
ONTEM = HOJE - _dt.timedelta(days=1)


@pytest.fixture
def usuarios(raw):
    reset_daily_checkins_table_cache()
    with raw.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        cur.execute("TRUNCATE TABLE daily_checkins")
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
    app.include_router(daily_checkins.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


def _payload(**over):
    base = {"local_date": HOJE.isoformat(), "energy": 4, "soreness": 2, "stress": 3, "mood": 4}
    base.update(over)
    return base


def _limpar(cliente: TestClient) -> None:
    for item in cliente.get("/daily-checkins").json()["items"]:
        assert cliente.delete(f"/daily-checkins/{item['id']}").status_code == 200


def test_ciclo_completo_identico_nos_dois_backends(usuarios, monkeypatch, fake_rds, mysql_schema):
    resultados = {}
    for backend in BACKENDS:
        use_backend(backend, monkeypatch, fake_rds, mysql_schema)
        cliente = _client(usuarios["dono"])
        _limpar(cliente)

        criado = cliente.post("/daily-checkins", json=_payload())
        assert criado.status_code == 200, (backend, criado.text)
        assert criado.json()["status"] == "created"
        item = criado.json()["item"]
        assert item["energy"] == 4 and item["soreness"] == 2
        assert set(item["answered"]) == {"energy", "soreness", "stress", "mood"}

        # UPSERT: o mesmo dia CORRIGE (um id so, `status=updated`).
        de_novo = cliente.post("/daily-checkins", json=_payload(energy=2))
        assert de_novo.json()["status"] == "updated"
        assert de_novo.json()["item"]["id"] == item["id"]
        assert de_novo.json()["item"]["energy"] == 2

        # UPSERT PARCIAL: mandar so `soreness` nao apaga o resto do dia.
        parcial = cliente.post("/daily-checkins", json={"local_date": HOJE.isoformat(), "soreness": 5})
        assert parcial.json()["item"]["soreness"] == 5
        assert parcial.json()["item"]["energy"] == 2, "o escore nao enviado tem de sobreviver"
        assert parcial.json()["item"]["mood"] == 4

        # Check-in parcial de ontem: o que nao foi respondido fica NULO.
        ontem = cliente.post(
            "/daily-checkins", json={"local_date": ONTEM.isoformat(), "soreness": 4}
        ).json()["item"]
        assert ontem["energy"] is None and ontem["stress"] is None and ontem["mood"] is None
        assert ontem["answered"] == ["soreness"]

        lista = cliente.get("/daily-checkins").json()
        assert lista["table_available"] is True and lista["has_more"] is False
        assert [i["local_date"] for i in lista["items"]] == [HOJE.isoformat(), ONTEM.isoformat()]

        so_hoje = cliente.get("/daily-checkins", params={"date_from": HOJE.isoformat()}).json()["items"]
        assert [i["local_date"] for i in so_hoje] == [HOJE.isoformat()]

        pagina = cliente.get("/daily-checkins", params={"limit": 1}).json()
        assert len(pagina["items"]) == 1 and pagina["has_more"] is True

        # PUT: `null` EXPLICITO apaga o escore (volta a "nao respondido").
        editado = cliente.put(f"/daily-checkins/{item['id']}", json={"stress": None, "notes": "dia corrido"})
        assert editado.status_code == 200, editado.text
        assert editado.json()["item"]["stress"] is None
        assert editado.json()["item"]["notes"] == "dia corrido"
        assert editado.json()["item"]["energy"] == 2, "PUT parcial nao mexe no que nao foi enviado"

        resultados[backend] = [
            {k: v for k, v in i.items() if k not in ("id", "created_at", "updated_at")}
            for i in cliente.get("/daily-checkins").json()["items"]
        ]
    assert resultados["pymysql"] == resultados["data-api"]


def test_idor_usuario_b_nao_le_nem_altera_nem_apaga_o_do_a(usuarios, db_backend):
    dono = _client(usuarios["dono"])
    outro = _client(usuarios["outro"])
    _limpar(dono)
    _limpar(outro)

    item = dono.post("/daily-checkins", json=_payload()).json()["item"]

    assert outro.get("/daily-checkins").json()["items"] == [], db_backend
    assert outro.get(f"/daily-checkins/{item['id']}").status_code == 404
    assert outro.put(f"/daily-checkins/{item['id']}", json={"energy": 1}).status_code == 404
    assert outro.delete(f"/daily-checkins/{item['id']}").status_code == 404
    # O registro do A continua intacto.
    assert dono.get(f"/daily-checkins/{item['id']}").json()["energy"] == 4
    # O UNIQUE e por USUARIO: o B pode ter o seu check-in no mesmo dia.
    assert outro.post("/daily-checkins", json=_payload(energy=1)).json()["status"] == "created"
    assert len(dono.get("/daily-checkins").json()["items"]) == 1
    assert dono.get(f"/daily-checkins/{item['id']}").json()["energy"] == 4


def test_validacao_da_escala_e_do_checkin_vazio(usuarios, db_backend):
    dono = _client(usuarios["dono"])
    _limpar(dono)

    # Zero nao e o menor grau: "nao respondi" e ausencia.
    assert dono.post("/daily-checkins", json=_payload(energy=0)).status_code == 422
    assert dono.post("/daily-checkins", json=_payload(mood=6)).status_code == 422
    assert dono.post("/daily-checkins", json=_payload(sleep_quality=4)).status_code == 422
    # Nenhuma resposta: a linha ocuparia o UNIQUE do dia sem dizer nada.
    assert dono.post("/daily-checkins", json={"local_date": HOJE.isoformat()}).status_code == 422
    # Nota sozinha E um check-in.
    assert dono.post(
        "/daily-checkins", json={"local_date": HOJE.isoformat(), "notes": "joelho incomodou"}
    ).status_code == 200, db_backend

    criado = dono.get("/daily-checkins").json()["items"][0]
    # A data nao e editavel: mover o check-in de dia seria afirmar que a
    # pessoa se sentia assim em outro dia.
    assert dono.put(f"/daily-checkins/{criado['id']}", json={"local_date": ONTEM.isoformat()}).status_code == 422
    assert dono.put(f"/daily-checkins/{criado['id']}", json={}).status_code == 400
    assert dono.delete(f"/daily-checkins/{criado['id']}").status_code == 200
    assert dono.delete(f"/daily-checkins/{criado['id']}").status_code == 404
