"""RBAC das rotas de catalogos e administracao (ADR 0011, lote 2C-1), por HTTP.

Um representante por familia: cards de exercicio (`ia.cards_exercicio`), tipos de
treino (`modelos.tipos_treino`), regras de carga (`admin.regras_carga`), seguranca de
treino (`admin.seguranca_treino`), mapping-canonical e `POST /exercise-mappings`
(`admin.catalogos`).

O resolvedor REAL (`access.service.resolve_permissions`) roda sobre um banco falso de
concessoes (linhas no formato real `group_key, is_system, resource, action, scope`)
que conta as consultas. Os handlers que chegariam ao banco de negocio sao
substituidos por dubles (o objetivo aqui e a decisao de acesso).

Provas: sem a concessao = 403 `FORBIDDEN_DETAIL`; com a concessao passa; usuario sem
grupo negado; token mestre passa sem consultar o banco de permissoes; falha do banco
na resolucao = 503 (nunca concede); em DELETE, concessao so de `editar` nao basta.

Observacao: o fake de permissoes e proprio deste arquivo (os testes unitarios nao sao
importaveis entre si).

Rodar: cd api && python -m pytest tests/unit/test_catalogos_admin_permissoes_rbac.py
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import exercise_cards, exercise_mapping_canonical as canonical
from app import main as main_mod
from app import next_set_load_rules_admin as rules_admin
from app import training_safety_admin as safety
from app import training_types
from app.access import service as access_service
from app.access.service import FORBIDDEN_DETAIL
from app.security import Identity, get_current_identity

USUARIO = 7
SEM_GRUPO = 999
MESTRE = Identity(user_id=1, role="admin", source="master_token")
REGRA = {
    "reps_floor": 6,
    "reps_ceiling": 12,
    "rules": [
        {"reps": {"op": "<", "value": "floor"}, "rpe": {"op": ">=", "value": 10}, "action": "DECREASE_LOAD",
         "magnitude": {"kind": "steps", "value": 1}},
    ],
}


class FakePermissoes:
    def __init__(self, concessoes_por_usuario=None, falha=None):
        self.por_usuario = concessoes_por_usuario or {}
        self.falha = falha
        self.consultas = []

    def fetch_rows(self, sql, params=()):
        self.consultas.append(tuple(params))
        if self.falha is not None:
            raise self.falha
        (user_id,) = params
        if user_id not in self.por_usuario:
            return []  # usuario sem grupo
        return [
            {"group_key": "g", "is_system": 0, "resource": r, "action": a, "scope": "todos"}
            for r, a in self.por_usuario[user_id]
        ]


class _Cur:
    rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        return None

    def fetchone(self):
        return None

    def fetchall(self):
        return []


@contextmanager
def _transacao_falsa():
    yield SimpleNamespace(cursor=lambda: _Cur())


@pytest.fixture(autouse=True)
def _dubles_de_negocio(monkeypatch):
    """Handlers que tocariam o banco de negocio viram dubles."""
    monkeypatch.setattr(exercise_cards.refs, "list_references", lambda **kw: {"items": [], "limits": {}})
    monkeypatch.setattr(exercise_cards.refs, "delete_reference", lambda **kw: None)
    monkeypatch.setattr(training_types, "_list_training_types", lambda is_active: [])
    monkeypatch.setattr(training_types, "transaction", _transacao_falsa)
    monkeypatch.setattr(rules_admin, "transaction", _transacao_falsa)
    monkeypatch.setattr(rules_admin.rules, "load_draft", lambda conn: None)
    monkeypatch.setattr(safety, "transaction", _transacao_falsa)
    monkeypatch.setattr(safety, "read_equipment_safety", lambda conn, equipment_id: {"equipment_id": equipment_id})
    monkeypatch.setattr(canonical, "_canonical_mapping_options", lambda: [])
    monkeypatch.setattr(main_mod, "upsert_mapping", lambda dados: {"ok": True})


def _cliente(identidade):
    app = FastAPI()
    for router in (exercise_cards.router, training_types.router, rules_admin.router, safety.router, canonical.router):
        app.include_router(router)
    app.add_api_route(
        "/exercise-mappings",
        _rota_exercise_mappings(),
        methods=["POST"],
        dependencies=_dependencias_de(main_mod.app, "POST", "/exercise-mappings"),
    )
    app.dependency_overrides[get_current_identity] = lambda: identidade
    return TestClient(app, raise_server_exceptions=False)


def _rota_exercise_mappings():
    for rota in main_mod.app.routes:
        if getattr(rota, "path", None) == "/exercise-mappings" and "POST" in rota.methods:
            return rota.endpoint
    raise AssertionError("POST /exercise-mappings nao encontrada em app.main")


def _dependencias_de(app, metodo, caminho):
    for rota in app.routes:
        if getattr(rota, "path", None) == caminho and metodo in rota.methods:
            return list(rota.dependencies)
    raise AssertionError(caminho)


def _sessao(uid):
    return Identity(user_id=uid, role="regular", source="session")


def _usar_permissoes(monkeypatch, concessoes_por_usuario=None, falha=None):
    perms = FakePermissoes(concessoes_por_usuario, falha)
    monkeypatch.setattr(access_service, "_db", perms)
    return perms


# (familia, metodo, caminho, corpo, recurso, acao)
REPRESENTANTES = [
    ("cards", "get", "/exercises/5/card-references", None, "ia.cards_exercicio", "ver"),
    ("cards", "delete", "/exercises/5/card-references/2", None, "ia.cards_exercicio", "excluir"),
    ("tipos_treino", "get", "/training-types", None, "modelos.tipos_treino", "ver"),
    ("tipos_treino", "delete", "/training-types/3", None, "modelos.tipos_treino", "excluir"),
    ("regras_carga", "get", "/next-set-load-rules/draft", None, "admin.regras_carga", "ver"),
    ("regras_carga", "post", "/next-set-load-rules/describe", {"ruleset": REGRA}, "admin.regras_carga", "editar"),
    ("seguranca", "get", "/equipment/4/safety", None, "admin.seguranca_treino", "ver"),
    ("mapping_canonical", "get", "/mapping-canonical/options", None, "exercicios.catalogo", "ver"),
    ("mapping_canonical", "post", "/mapping-canonical/associate", {}, "admin.catalogos", "editar"),
    ("exercise_mappings", "post", "/exercise-mappings", {"original_name": "a", "amazfit_name": "b"}, "admin.catalogos", "editar"),
]
IDS = [f"{r[1].upper()} {r[2]}" for r in REPRESENTANTES]


def _chamar(client, metodo, caminho, corpo):
    if corpo is not None:
        return getattr(client, metodo)(caminho, json=corpo)
    return getattr(client, metodo)(caminho)


def _acesso_liberado(resp, metodo, caminho):
    """Passou da guarda de permissao. `associate` recebe corpo vazio de proposito (nao
    ha duble do handler): 422 de validacao so ocorre depois de a dependencia liberar."""
    if caminho == "/mapping-canonical/associate":
        return resp.status_code == 422
    return resp.status_code in (200, 201, 202, 204)


@pytest.mark.parametrize("familia,metodo,caminho,corpo,recurso,acao", REPRESENTANTES, ids=IDS)
def test_usuario_sem_a_concessao_recebe_403_forbidden(monkeypatch, familia, metodo, caminho, corpo, recurso, acao):
    # Concessao de OUTRO recurso nao serve.
    _usar_permissoes(monkeypatch, {USUARIO: [("conta.dispositivos", "ver"), ("conta.dispositivos", "editar")]})
    resp = _chamar(_cliente(_sessao(USUARIO)), metodo, caminho, corpo)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == FORBIDDEN_DETAIL


@pytest.mark.parametrize("familia,metodo,caminho,corpo,recurso,acao", REPRESENTANTES, ids=IDS)
def test_usuario_com_a_concessao_passa(monkeypatch, familia, metodo, caminho, corpo, recurso, acao):
    perms = _usar_permissoes(monkeypatch, {USUARIO: [(recurso, acao)]})
    resp = _chamar(_cliente(_sessao(USUARIO)), metodo, caminho, corpo)
    assert _acesso_liberado(resp, metodo, caminho), (resp.status_code, resp.text)
    assert perms.consultas == [(USUARIO,)], "a concessao deve vir do resolvedor real, 1 consulta"


@pytest.mark.parametrize("familia,metodo,caminho,corpo,recurso,acao", REPRESENTANTES, ids=IDS)
def test_usuario_sem_grupo_e_negado(monkeypatch, familia, metodo, caminho, corpo, recurso, acao):
    perms = _usar_permissoes(monkeypatch, {})
    resp = _chamar(_cliente(_sessao(SEM_GRUPO)), metodo, caminho, corpo)
    assert resp.status_code == 403
    assert resp.json()["detail"] == FORBIDDEN_DETAIL
    assert perms.consultas, "usuario sem grupo e negado pela consulta, nao por omissao"


@pytest.mark.parametrize("familia,metodo,caminho,corpo,recurso,acao", REPRESENTANTES, ids=IDS)
def test_grupo_vazio_sem_concessoes_e_negado(monkeypatch, familia, metodo, caminho, corpo, recurso, acao):
    _usar_permissoes(monkeypatch, {USUARIO: []})
    assert _chamar(_cliente(_sessao(USUARIO)), metodo, caminho, corpo).status_code == 403


@pytest.mark.parametrize("familia,metodo,caminho,corpo,recurso,acao", REPRESENTANTES, ids=IDS)
def test_token_mestre_passa_sem_consultar_o_banco(monkeypatch, familia, metodo, caminho, corpo, recurso, acao):
    perms = _usar_permissoes(monkeypatch, falha=AssertionError("o banco de permissoes nao pode ser tocado"))
    resp = _chamar(_cliente(MESTRE), metodo, caminho, corpo)
    assert _acesso_liberado(resp, metodo, caminho), (resp.status_code, resp.text)
    assert perms.consultas == []


@pytest.mark.parametrize("familia,metodo,caminho,corpo,recurso,acao", REPRESENTANTES, ids=IDS)
def test_falha_do_banco_na_resolucao_e_503_nunca_concede(monkeypatch, familia, metodo, caminho, corpo, recurso, acao):
    _usar_permissoes(monkeypatch, {USUARIO: [(recurso, acao)]}, falha=RuntimeError("banco fora"))
    resp = _chamar(_cliente(_sessao(USUARIO)), metodo, caminho, corpo)
    assert resp.status_code == 503, resp.text


@pytest.mark.parametrize(
    "metodo,caminho",
    [
        ("delete", "/exercises/5/card-references/2"),
        ("delete", "/training-types/3"),
    ],
)
def test_delete_exige_excluir_ver_e_editar_nao_bastam(monkeypatch, metodo, caminho):
    recurso = "ia.cards_exercicio" if "card" in caminho else "modelos.tipos_treino"
    _usar_permissoes(monkeypatch, {USUARIO: [(recurso, "ver"), (recurso, "editar")]})
    resp = getattr(_cliente(_sessao(USUARIO)), metodo)(caminho)
    assert resp.status_code == 403
    assert resp.json()["detail"] == FORBIDDEN_DETAIL
    _usar_permissoes(monkeypatch, {USUARIO: [(recurso, "excluir")]})
    assert getattr(_cliente(_sessao(USUARIO)), metodo)(caminho).status_code in (200, 204)


def test_post_de_cards_exige_editar_e_ver_nao_basta(monkeypatch):
    _usar_permissoes(monkeypatch, {USUARIO: [("ia.cards_exercicio", "ver"), ("ia.cards_exercicio", "excluir")]})
    resp = _cliente(_sessao(USUARIO)).post("/exercises/5/card-references", json={})
    assert resp.status_code == 403
    assert resp.json()["detail"] == FORBIDDEN_DETAIL


def test_tipos_de_treino_sao_do_recurso_modelos_tipos_treino_e_nao_de_regras_de_carga(monkeypatch):
    _usar_permissoes(monkeypatch, {USUARIO: [("admin.regras_carga", "ver"), ("admin.regras_carga", "editar")]})
    assert _cliente(_sessao(USUARIO)).get("/training-types").status_code == 403
    assert _cliente(_sessao(USUARIO)).get("/next-set-load-rules/draft").status_code == 200
