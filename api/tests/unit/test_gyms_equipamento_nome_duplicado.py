"""Bug #186: nome de equipamento duplicado nao pode sobrescrever o registro existente.

Rodar: cd api && python -m pytest tests/unit/test_gyms_equipamento_nome_duplicado.py
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import gyms, security
from app.security import Identity

DETAIL = "Já existe um equipamento com esse nome."


class _Banco:
    """Tabela `equipment` em memoria com UNIQUE(name); so entende os SQLs de gyms.py."""

    def __init__(self, linhas, corrida=False):
        self.linhas = linhas
        self.corrida = corrida  # SELECT nao ve a linha, mas o INSERT bate no UNIQUE
        self.writes = []
        self._rows = []
        self._next = 100

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def _por_nome(self, nome):
        return [r for r in self.linhas if r["name"] == nome]

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self._rows = []
        if flat.startswith("SELECT id, is_active FROM equipment WHERE name"):
            if not self.corrida:
                self._rows = [dict(r) for r in self._por_nome(params[0])]
        elif flat.startswith("SELECT * FROM equipment WHERE name"):
            self._rows = [dict(r) for r in self._por_nome(params[0])]
        elif flat.startswith("SELECT * FROM equipment WHERE id"):
            self._rows = [dict(r) for r in self.linhas if r["id"] == params[0]]
        elif flat.startswith("INSERT INTO equipment"):
            if self._por_nome(params[0]):  # violacao do UNIQUE: nada e gravado
                raise RuntimeError("Duplicate entry 'x' for key 'uq_equipment_name'")
            self.writes.append(flat)
            self.linhas.append({"id": self._next, "name": params[0], "notes": params[1], "is_active": 1})
        elif flat.startswith("UPDATE equipment SET notes"):
            self.writes.append(flat)
            alvo = next(r for r in self.linhas if r["id"] == params[-1])
            alvo.update(notes=params[0], is_active=1)
        elif flat.startswith("UPDATE equipment SET name"):
            novo = params[0]
            if any(r["name"] == novo and r["id"] != params[-1] for r in self.linhas):
                raise RuntimeError("Duplicate entry 'x' for key 'uq_equipment_name'")
            self.writes.append(flat)
            alvo = next(r for r in self.linhas if r["id"] == params[-1])
            alvo.update(name=novo or alvo["name"], notes=params[1])
        else:
            raise AssertionError(flat)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _client(monkeypatch, banco):
    monkeypatch.setattr(gyms, "_connect", lambda: banco)
    admin = Identity(user_id=1, role="admin", source="master_token")
    monkeypatch.setattr(security, "resolve_identity", lambda token: admin)
    app = FastAPI()
    app.include_router(gyms.router)
    return TestClient(app, raise_server_exceptions=False)


def _linhas():
    return [
        {"id": 1, "name": "Leg press", "notes": "original", "is_active": 1,
         "equipment_type_id": 3, "manufacturer": "A", "model": "M", "location": "L"},
        {"id": 2, "name": "Hack", "notes": "x", "is_active": 1,
         "equipment_type_id": None, "manufacturer": None, "model": None, "location": None},
        {"id": 3, "name": "Antigo", "notes": "velho", "is_active": 0},
    ]


def _post(client, nome, notes="novo"):
    return client.post("/equipment", json={"name": nome, "notes": notes}, headers={"X-API-Token": "t"})


def test_post_nome_de_ativo_da_409_e_nao_altera_o_original(monkeypatch):
    banco = _Banco(_linhas())
    antes = [dict(r) for r in banco.linhas]
    resp = _post(_client(monkeypatch, banco), "Leg press")
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == DETAIL
    assert banco.writes == []
    assert banco.linhas == antes


def test_post_nome_de_inativo_reativa_com_valores_enviados(monkeypatch):
    banco = _Banco(_linhas())
    resp = _post(_client(monkeypatch, banco), "Antigo", notes="reativado")
    assert resp.status_code == 200, resp.text
    assert resp.json()["item"]["is_active"] == 1
    assert resp.json()["item"]["notes"] == "reativado"
    assert not any("INSERT" in w for w in banco.writes)


def test_post_nome_novo_insere(monkeypatch):
    banco = _Banco(_linhas())
    resp = _post(_client(monkeypatch, banco), "Cadeira")
    assert resp.status_code == 200, resp.text
    assert resp.json()["item"]["name"] == "Cadeira"


def test_post_corrida_insert_em_duplicate_key_vira_409(monkeypatch):
    banco = _Banco(_linhas(), corrida=True)
    antes = [dict(r) for r in banco.linhas]
    resp = _post(_client(monkeypatch, banco), "Leg press")
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == DETAIL
    assert banco.linhas == antes


def test_put_renomear_para_nome_de_outro_da_409(monkeypatch):
    banco = _Banco(_linhas())
    antes = [dict(r) for r in banco.linhas]
    resp = _client(monkeypatch, banco).put(
        "/equipment/2", json={"name": "Leg press", "notes": "x"}, headers={"X-API-Token": "t"}
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == DETAIL
    assert banco.linhas == antes


def test_put_renomear_para_nome_livre_funciona(monkeypatch):
    banco = _Banco(_linhas())
    resp = _client(monkeypatch, banco).put(
        "/equipment/2", json={"name": "Hack 2", "notes": "x"}, headers={"X-API-Token": "t"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["item"]["name"] == "Hack 2"
