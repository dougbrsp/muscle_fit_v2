"""RBAC dos Modelos de treino (ADR 0011, lote 2F-1), por HTTP: as 29 rotas de
`app.workout_plan_templates` (prefixo `/workout-plan-templates`) servidas pelos apps reais.

Contrato (tabela viva `CONTRATO_2F1` de `test_access_paridade_guards`):
- 27 de gestao em `modelos.treino` com `min_scope="todos"` (GET ver, POST/PUT/PATCH editar, DELETE excluir),
  sendo `POST /periodization-drafts` em `ia.modelo_periodizacao`/editar, tambem com `min_scope="todos"`;
- `GET /assigned-to-me` e `GET /hr-zones` em `modelos.treino`/ver, sem escopo minimo.

O resolvedor REAL de permissoes roda sobre um banco falso de concessoes (`access_service._db`), com
`DEFAULT_GRANTS_BY_GROUP` para os papeis e grupos sinteticos para os cenarios.

Ponto de duble: `_config_from_env` do backend ativo de `app.db` (primeira etapa de `get_conn`,
`transaction` e `fetch_rows`) e `async_jobs._table` (job da periodizacao). Duble chamado = o guard
deixou a requisicao chegar ao handler. A excecao-sentinela nao e `DataApiError` (nao vira 503).
Excecao conhecida: `GET /hr-zones` rejeita, no proprio handler, identidade que nao e sessao
(`require_session_identity`, 403 com outro texto) antes de tocar o banco; para token mestre e
dispositivo o "passou pelo guard" dessa rota e esse 403 com detalhe diferente de `FORBIDDEN_DETAIL`.

Rodar: cd api && python -m pytest tests/unit/test_modelos_treino_permissoes_rbac.py
"""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

from app import async_jobs
from app import db as app_db
from app.access import DEFAULT_GRANTS_BY_GROUP
from app.access import service as access_service
from app.access.service import FORBIDDEN_DETAIL
from app.security import Identity, get_current_identity
from tests.unit.test_access_guarda_cobertura_rotas import _iter_api_routes
from tests.unit.test_access_paridade_guards import (
    CONTRATO_2F1,
    MODULO_2F1,
    ROTA_DRAFTS_IA,
    ROTAS_2F1_ME,
)

USUARIO, PT, ADMIN_ID, SEM_GRUPO = 7, 8, 1, 9
SINT_PROPRIOS, SINT_ALUNOS, SINT_TODOS = 30, 31, 32
SINT_SO_VER, SINT_SEM_IA = 33, 34
MESTRE = Identity(user_id=ADMIN_ID, role="admin", source="master_token")
PAPEIS = {USUARIO: "regular", PT: "personal_trainer", ADMIN_ID: "admin"}
ACOES = ("ver", "editar", "excluir")
HR_ZONES = "GET /workout-plan-templates/hr-zones"

BODY_TEMPLATE = {"name": "Modelo", "slots": [{"sequence": 1, "name": "A", "items": []}]}
CORPOS = {
    "POST /workout-plan-templates": BODY_TEMPLATE,
    "PUT /workout-plan-templates/{template_id}": BODY_TEMPLATE,
    "PUT /workout-plan-templates/{template_id}/draft": BODY_TEMPLATE,
    "PATCH /workout-plan-templates/{template_id}/status": {"is_active": True},
    "POST /workout-plan-templates/cardio-prescription-check": {},
    "POST /workout-plan-templates/methodology-volume-preview": {"slots": []},
    "POST /workout-plan-templates/preview-activation": {"slots": []},
    "POST /workout-plan-templates/{template_id}/assignments": {"user_id": 5},
    "PUT /workout-plan-templates/{template_id}/methodology-intent": {"intent": "standard"},
    ROTA_DRAFTS_IA: {},
}


class SentinelaDados(Exception):
    """Levantada pelo duble de acesso a dados; prova que o guard deixou a requisicao chegar ao handler."""


class FakePermissoes:
    """Banco de concessoes: `fetch_rows(sql, (user_id,))` -> linhas do grupo do usuario."""

    def __init__(self):
        self.falha = None
        self.consultas = []
        todas = [(r, a, "todos") for r in ("modelos.treino", "ia.modelo_periodizacao") for a in ACOES]
        sem_ia = [g for g in todas if g[0] == "modelos.treino"]
        self.por_usuario = {
            USUARIO: self._grupo("usuario"),
            PT: self._grupo("personal_trainer"),
            ADMIN_ID: self._grupo("admin"),
            SINT_PROPRIOS: self._sint([(r, a, "proprios") for r, a, _ in todas]),
            SINT_ALUNOS: self._sint([(r, a, "alunos") for r, a, _ in todas]),
            SINT_TODOS: self._sint(todas),
            SINT_SO_VER: self._sint([("modelos.treino", "ver", "todos")]),
            SINT_SEM_IA: self._sint(sem_ia),
        }  # SEM_GRUPO ausente = usuario sem grupo

    @staticmethod
    def _sint(triplas):
        return [{"group_key": "sint", "is_system": 0, "resource": r, "action": a, "scope": s} for r, a, s in triplas]

    @staticmethod
    def _grupo(grupo):
        grants = DEFAULT_GRANTS_BY_GROUP[grupo]
        if not grants:
            return [{"group_key": grupo, "is_system": 1, "resource": None, "action": None, "scope": None}]
        return [{"group_key": grupo, "is_system": 1, "resource": r, "action": a, "scope": s} for r, a, s in grants]

    def fetch_rows(self, sql, params=()):
        self.consultas.append(tuple(params))
        if self.falha is not None:
            raise self.falha
        return list(self.por_usuario.get(params[0], []))


class Dados:
    """Duble do acesso a dados: registra cada chamada e levanta a sentinela."""

    def __init__(self):
        self.chamadas = 0

    def __call__(self, *args, **kwargs):
        self.chamadas += 1
        raise SentinelaDados("acesso a dados alcancado")


BACKEND = sys.modules[app_db.get_conn.__module__]


def _rotas_vivas() -> list[tuple[str, str, str]]:
    """(app, metodo, modelo) das rotas vivas de `app.workout_plan_templates` nos apps reais."""
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    vivas = []
    for nome, app in (("main", main_app), ("engines", engines_app)):
        for route in _iter_api_routes(app):
            if getattr(route.endpoint, "__module__", "") != MODULO_2F1:
                continue
            for metodo in sorted(route.methods - {"HEAD", "OPTIONS"}):
                vivas.append((nome, metodo, route.path))
    return vivas


VIVAS = _rotas_vivas()
CHAVE = {v: f"{v[1]} {v[2]}" for v in VIVAS}
IDS = [f"{n}:{m}:{p}" for n, m, p in VIVAS]
GESTAO = [v for v in VIVAS if CHAVE[v] not in ROTAS_2F1_ME]
GESTAO_GET = [v for v in GESTAO if v[1] == "GET"]
GESTAO_ESCRITA = [v for v in GESTAO if v[1] != "GET"]
ME = [v for v in VIVAS if CHAVE[v] in ROTAS_2F1_ME]
SEM_IA = [v for v in VIVAS if CHAVE[v] != ROTA_DRAFTS_IA]
DRAFTS_IA = [v for v in VIVAS if CHAVE[v] == ROTA_DRAFTS_IA]


def _ids(rotas):
    return [f"{n}:{m}:{p}" for n, m, p in rotas]


@pytest.fixture
def ctx(monkeypatch):
    perms = FakePermissoes()
    monkeypatch.setattr(access_service, "_db", perms)
    dados = Dados()
    monkeypatch.setattr(BACKEND, "_config_from_env", dados)
    monkeypatch.setattr(async_jobs, "_table", dados)
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    state = {"identity": Identity(user_id=USUARIO, role="regular", source="session")}
    apps = {"main": main_app, "engines": engines_app}
    for app in apps.values():
        app.dependency_overrides[get_current_identity] = lambda: state["identity"]
    clients = {n: TestClient(a, raise_server_exceptions=False) for n, a in apps.items()}
    yield clients, state, perms, dados
    for app in apps.values():
        app.dependency_overrides.pop(get_current_identity, None)


def _como(state, uid, source="session"):
    state["identity"] = Identity(user_id=uid, role=PAPEIS.get(uid, "regular"), source=source,
                                 device_id=3 if source == "device" else None)


def _chamar(clients, rota):
    nome, metodo, modelo = rota
    caminho = modelo
    while "{" in caminho:
        ini = caminho.index("{")
        fim = caminho.index("}", ini)
        caminho = caminho[:ini] + "1" + caminho[fim + 1:]
    kwargs = {}
    corpo = CORPOS.get(f"{metodo} {modelo}")
    if corpo is not None:
        kwargs["json"] = corpo
    return clients[nome].request(metodo, caminho, **kwargs)


def _negado(resp, dados):
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == FORBIDDEN_DETAIL
    assert dados.chamadas == 0


def _passou(resp, dados, rota=None, source="session"):
    """O guard deixou passar: o duble de dados foi alcancado (ou 200 sem dados); `hr-zones` fora de
    sessao e barrado pelo handler com 403 de outro texto, sem tocar dados."""
    if rota is not None and CHAVE[rota] == HR_ZONES and source != "session":
        assert resp.status_code == 403 and resp.json()["detail"] != FORBIDDEN_DETAIL, resp.text
        assert dados.chamadas == 0
        return
    assert dados.chamadas > 0 or resp.status_code == 200, (resp.status_code, resp.text)


# ---------------------------------------------------------------------------
# (a) enumeracao viva e usuario sem grupo
# ---------------------------------------------------------------------------


def test_o_duble_cobre_a_abertura_de_conexao_do_backend_ativo(monkeypatch):
    dados = Dados()
    monkeypatch.setattr(BACKEND, "_config_from_env", dados)
    assert app_db.get_conn.__module__ == app_db.fetch_rows.__module__ == BACKEND.__name__
    with pytest.raises(SentinelaDados):
        app_db.get_conn()
    with pytest.raises(SentinelaDados):
        app_db.fetch_rows("SELECT 1")
    with pytest.raises(SentinelaDados):
        with app_db.transaction():
            pass
    assert dados.chamadas == 3


def test_as_rotas_vivas_sao_exatamente_as_29_do_contrato():
    assert len(VIVAS) == len(set(VIVAS))
    assert {CHAVE[v] for v in VIVAS} == set(CONTRATO_2F1)
    assert len(CONTRATO_2F1) == 29
    assert len(GESTAO) == 27 and len(GESTAO_GET) == 10 and len(ME) == 2
    assert len(DRAFTS_IA) == len(VIVAS) - len(SEM_IA) >= 1
    assert set(CORPOS) <= set(CONTRATO_2F1)


@pytest.mark.parametrize("rota", VIVAS, ids=IDS)
def test_usuario_sem_grupo_e_negado_antes_de_qualquer_acesso_a_dados(ctx, rota):
    clients, state, perms, dados = ctx
    _como(state, SEM_GRUPO)
    _negado(_chamar(clients, rota), dados)
    assert perms.consultas == [(SEM_GRUPO,)]


# ---------------------------------------------------------------------------
# (b) grupo `usuario`: negado na gestao, passa nas 2 do me_router
# ---------------------------------------------------------------------------


def test_o_grupo_usuario_so_tem_modelos_treino_ver_proprios():
    assert [g for g in DEFAULT_GRANTS_BY_GROUP["usuario"] if g[0] in ("modelos.treino", "ia.modelo_periodizacao")] == [
        ("modelos.treino", "ver", "proprios")
    ]


@pytest.mark.parametrize("rota", GESTAO, ids=_ids(GESTAO))
def test_usuario_e_negado_nas_27_de_gestao_inclusive_nas_get(ctx, rota):
    clients, state, _, dados = ctx
    _como(state, USUARIO)
    _negado(_chamar(clients, rota), dados)


@pytest.mark.parametrize("rota", ME, ids=_ids(ME))
def test_usuario_passa_pelo_guard_nas_2_do_me_router(ctx, rota):
    clients, state, _, dados = ctx
    _como(state, USUARIO)
    _passou(_chamar(clients, rota), dados, rota)
    assert dados.chamadas > 0


# ---------------------------------------------------------------------------
# (c) PT, admin, token mestre, dispositivo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("uid", [PT, ADMIN_ID], ids=["pt", "admin"])
@pytest.mark.parametrize("rota", VIVAS, ids=IDS)
def test_pt_e_admin_passam_pelo_guard_nas_29(ctx, uid, rota):
    clients, state, _, dados = ctx
    _como(state, uid)
    _passou(_chamar(clients, rota), dados, rota)


@pytest.mark.parametrize("rota", VIVAS, ids=IDS)
def test_token_mestre_passa_sem_consultar_o_banco_de_permissoes(ctx, rota):
    clients, state, perms, dados = ctx
    state["identity"] = MESTRE
    _passou(_chamar(clients, rota), dados, rota, source="master_token")
    assert perms.consultas == []


@pytest.mark.parametrize("rota", VIVAS, ids=IDS)
def test_dispositivo_de_dono_pt_herda_e_passa_nas_29(ctx, rota):
    clients, state, perms, dados = ctx
    _como(state, PT, source="device")
    _passou(_chamar(clients, rota), dados, rota, source="device")
    assert perms.consultas[0] == (PT,)


@pytest.mark.parametrize("rota", GESTAO, ids=_ids(GESTAO))
def test_dispositivo_de_dono_usuario_e_negado_na_gestao(ctx, rota):
    clients, state, perms, dados = ctx
    _como(state, USUARIO, source="device")
    _negado(_chamar(clients, rota), dados)
    assert perms.consultas == [(USUARIO,)]


@pytest.mark.parametrize("rota", ME, ids=_ids(ME))
def test_dispositivo_de_dono_usuario_passa_pelo_guard_nas_2_do_me_router(ctx, rota):
    clients, state, perms, dados = ctx
    _como(state, USUARIO, source="device")
    _passou(_chamar(clients, rota), dados, rota, source="device")
    assert perms.consultas[0] == (USUARIO,)


@pytest.mark.parametrize("rota", VIVAS, ids=IDS)
def test_dispositivo_de_dono_sem_grupo_e_negado_nas_29(ctx, rota):
    clients, state, perms, dados = ctx
    _como(state, SEM_GRUPO, source="device")
    _negado(_chamar(clients, rota), dados)
    assert perms.consultas == [(SEM_GRUPO,)]


# ---------------------------------------------------------------------------
# (d) escopo minimo `todos`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rota", GESTAO, ids=_ids(GESTAO))
def test_escopo_proprios_e_negado_nas_27_de_gestao(ctx, rota):
    clients, state, _, dados = ctx
    _como(state, SINT_PROPRIOS)
    _negado(_chamar(clients, rota), dados)


GESTAO_SEM_IA = [v for v in GESTAO if CHAVE[v] != ROTA_DRAFTS_IA]


@pytest.mark.parametrize("rota", GESTAO_SEM_IA, ids=_ids(GESTAO_SEM_IA))
def test_escopo_alunos_passa_pelo_guard_nas_26_de_gestao_de_modelos(ctx, rota):
    """Lote 6A (ADR 0011): o escopo minimo de `modelos.treino` desceu para
    `alunos` (personal trainer). A posse do Modelo (dono/publico) e conferida
    depois, por `authorize_template` (coberto em `test_modelos_dono_visibilidade`)."""
    clients, state, _, dados = ctx
    _como(state, SINT_ALUNOS)
    resp = _chamar(clients, rota)
    assert resp.status_code != 403, resp.text
    _passou(resp, dados, rota)


@pytest.mark.parametrize("rota", DRAFTS_IA, ids=_ids(DRAFTS_IA))
def test_escopo_alunos_segue_negado_na_periodizacao_por_ia(ctx, rota):
    clients, state, _, dados = ctx
    _como(state, SINT_ALUNOS)
    _negado(_chamar(clients, rota), dados)


@pytest.mark.parametrize("uid", [SINT_PROPRIOS, SINT_ALUNOS], ids=["proprios", "alunos"])
@pytest.mark.parametrize("rota", ME, ids=_ids(ME))
def test_escopo_abaixo_de_todos_passa_nas_2_do_me_router(ctx, uid, rota):
    clients, state, _, dados = ctx
    _como(state, uid)
    _passou(_chamar(clients, rota), dados, rota)
    assert dados.chamadas > 0


@pytest.mark.parametrize("rota", VIVAS, ids=IDS)
def test_escopo_todos_passa_nas_29(ctx, rota):
    clients, state, _, dados = ctx
    _como(state, SINT_TODOS)
    _passou(_chamar(clients, rota), dados, rota)


# ---------------------------------------------------------------------------
# (e) acao e recurso
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rota", GESTAO_ESCRITA, ids=_ids(GESTAO_ESCRITA))
def test_so_ver_em_todos_e_negado_nas_escritas(ctx, rota):
    clients, state, _, dados = ctx
    _como(state, SINT_SO_VER)
    _negado(_chamar(clients, rota), dados)


@pytest.mark.parametrize("rota", GESTAO_GET + ME, ids=_ids(GESTAO_GET + ME))
def test_so_ver_em_todos_passa_nas_get(ctx, rota):
    clients, state, _, dados = ctx
    _como(state, SINT_SO_VER)
    _passou(_chamar(clients, rota), dados, rota)
    assert dados.chamadas > 0


@pytest.mark.parametrize("rota", DRAFTS_IA, ids=_ids(DRAFTS_IA))
def test_sem_ia_modelo_periodizacao_e_negado_na_periodizacao_sem_criar_job(ctx, rota):
    clients, state, _, dados = ctx
    _como(state, SINT_SEM_IA)
    _negado(_chamar(clients, rota), dados)  # dados.chamadas == 0 cobre transaction e async_jobs._table


@pytest.mark.parametrize("rota", SEM_IA, ids=_ids(SEM_IA))
def test_sem_ia_modelo_periodizacao_passa_nas_outras_28(ctx, rota):
    clients, state, _, dados = ctx
    _como(state, SINT_SEM_IA)
    _passou(_chamar(clients, rota), dados, rota)


# ---------------------------------------------------------------------------
# (f) falha de banco na resolucao: 503 sem acessar dados
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rota", VIVAS, ids=IDS)
def test_falha_do_banco_na_resolucao_e_503_sem_acessar_dados(ctx, rota):
    clients, state, perms, dados = ctx
    perms.falha = RuntimeError("banco fora")
    _como(state, USUARIO)
    resp = _chamar(clients, rota)
    assert resp.status_code == 503, resp.text
    assert dados.chamadas == 0
