"""RBAC de analytics e engines (ADR 0011, lote 2E), por HTTP: 56 rotas dos 9 modulos
(`app.analytics`, `app.analytics_dashboard`, `app.dashboard_modules`, `app.dashboard_intelligence`,
`app.engines_extra`, `app.progression`, `app.effort_metrics`, `app.reports`, `app.workouts_analysis`),
servidas por `app.main.app` (api-core) e `app.main_engines.app` (engines).

O resolvedor REAL de permissoes roda sobre um banco falso de concessoes (`access_service._db`),
com `DEFAULT_GRANTS_BY_GROUP` para os papeis e grupos sinteticos para os cenarios.

Ponto de duble escolhido: `_config_from_env` do backend ativo de `app.db` (pymysql sob pytest, Data
API em Lambda; mesmo nome nos dois). Toda abertura de conexao (`get_conn`, `transaction`, `fetch_rows`,
e com isso `async_jobs.create_job`) passa por ele
antes de qualquer consulta, entao ele e o unico ponto comum aos 9 modulos (analytics, dashboards,
reports e effort_metrics importam `get_conn` por nome; os demais usam `services`/`intelligence`/
`transaction`, que nao tem um `get_conn` proprio para sobrescrever). O duble registra a chamada e
levanta uma excecao-sentinela propria do teste (nao e `DataApiError`, entao o `connect_or_503` nao a
converte em 503). "Passou pelo guard" = o duble foi chamado (ou a rota respondeu 200 sem tocar no
banco). "Negado pelo guard" = 403 com corpo `FORBIDDEN_DETAIL` e duble NAO chamado. O banco de
permissoes e outro objeto (`access_service._db`), por isso nao se confunde com o duble.

Rodar: cd api && python -m pytest tests/unit/test_analytics_engines_permissoes_rbac.py
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
    MODULOS_2E,
    RECURSO_2E,
    ROTA_PUT_BODY_MAP_2C1,
)

USUARIO, PT, ADMIN_ID, SEM_GRUPO, SO_VER = 7, 8, 1, 9, 30
FALTA_BASE = 40  # grupos "usuario menos um recurso": FALTA_BASE + indice do recurso
MESTRE = Identity(user_id=ADMIN_ID, role="admin", source="master_token")
PAPEIS = {USUARIO: "regular", PT: "personal_trainer", ADMIN_ID: "admin"}
RECURSOS = tuple(sorted(set(RECURSO_2E.values())))
ACAO_POR_METODO = {"GET": "ver", "POST": "editar"}

# Parametros minimos para o handler ser alcancado (query obrigatoria, caminho e corpo validos).
QUERY_POR_NOME = {
    "name": "supino", "activity_id": "1", "muscle_group_id": "1", "mapping_id": "1",
    "original_mapping_id": "1", "original_sets_count": "3", "new_mapping_id": "2",
}
# Rotas de progressao que validam "exercise_name ou mapping_id" (400) antes de consultar o banco.
QUERY_EXTRA = {
    "/analytics/dashboard-progression": {"exercise_name": "supino"},
    "/analytics/dashboard-progression-trend": {"exercise_name": "supino"},
    "/analytics/dashboard-progression-suggestion": {"exercise_name": "supino"},
}
CORPOS = {
    "POST /analytics/muscle-activation-preview": {"exercises": []},
    "POST /analytics/muscle-activation-breakdown": {"exercises": []},
    "POST /workouts-analysis/generate": {"days": 90},
}


class SentinelaDados(Exception):
    """Levantada pelo duble de acesso a dados; prova que o guard deixou a requisicao chegar ao handler."""


class FakePermissoes:
    """Banco de concessoes: `fetch_rows(sql, (user_id,))` -> linhas do grupo do usuario."""

    def __init__(self):
        self.falha = None
        self.consultas = []
        self.por_usuario = {
            USUARIO: self._grupo("usuario"),
            PT: self._grupo("personal_trainer"),
            ADMIN_ID: self._grupo("admin"),
            SO_VER: self._sint([(r, "ver", "proprios") for r in RECURSOS]),
        }  # SEM_GRUPO ausente = usuario sem grupo
        base = DEFAULT_GRANTS_BY_GROUP["usuario"]
        for i, recurso in enumerate(RECURSOS):
            self.por_usuario[FALTA_BASE + i] = self._sint([g for g in base if g[0] != recurso])

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


# Backend ativo de `app.db` (em pytest a raiz forca `pymysql`; em Lambda e `db_dataapi`). Os dois
# expoem `_config_from_env` como primeira etapa de `get_conn`, `transaction` e `fetch_rows`.
BACKEND = sys.modules[app_db.get_conn.__module__]


def _rotas_vivas() -> tuple[list[tuple[str, str, str]], dict[tuple[str, str, str], dict[str, str]]]:
    """(app, metodo, modelo) das rotas vivas dos 9 modulos em `app.main.app` e `app.main_engines.app`
    (sem o PUT do 2C-1). Os routers incluidos sao expandidos por `_iter_api_routes`."""
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    vivas = []
    queries = {}
    for nome, app in (("main", main_app), ("engines", engines_app)):
        for route in _iter_api_routes(app):
            if getattr(route.endpoint, "__module__", "") not in MODULOS_2E:
                continue
            for metodo in sorted(route.methods - {"HEAD", "OPTIONS"}):
                if f"{metodo} {route.path}" != ROTA_PUT_BODY_MAP_2C1:
                    vivas.append((nome, metodo, route.path))
                    queries[(nome, metodo, route.path)] = {
                        **{q.name: QUERY_POR_NOME[q.name] for q in route.dependant.query_params
                           if q.field_info.is_required()},
                        **QUERY_EXTRA.get(route.path, {}),
                    }
    return vivas, queries


VIVAS, QUERY_OBRIGATORIA = _rotas_vivas()
CHAVE = {v: f"{v[1]} {v[2]}" for v in VIVAS}
IDS = [f"{n}:{m}:{p}" for n, m, p in VIVAS]
POR_RECURSO = {r: [v for v in VIVAS if RECURSO_2E[CHAVE[v]] == r] for r in RECURSOS}
POSTS = [v for v in VIVAS if v[1] == "POST"]
GETS = [v for v in VIVAS if v[1] == "GET"]


def _amostra_por_recurso(excluir=None):
    """Uma rota GET (ou a unica POST, p/ `ia.analise_treinos`) por recurso."""
    return [rotas[0] for r, rotas in sorted(POR_RECURSO.items()) if r != excluir]


def _ids(rotas):
    return [f"{n}:{m}:{p}" for n, m, p in rotas]


@pytest.fixture
def ctx(monkeypatch):
    perms = FakePermissoes()
    monkeypatch.setattr(access_service, "_db", perms)
    dados = Dados()
    monkeypatch.setattr(BACKEND, "_config_from_env", dados)
    # `POST /workouts-analysis/generate` grava o job no DynamoDB, nao no banco relacional: o primeiro
    # acesso a dados dele e `async_jobs._table`.
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
    client = clients[nome]
    if metodo == "POST":
        return client.post(caminho, json=CORPOS[f"{metodo} {modelo}"])
    return client.get(caminho, params=QUERY_OBRIGATORIA[rota])


def _negado(resp, dados):
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == FORBIDDEN_DETAIL
    assert dados.chamadas == 0


def _passou(resp, dados):
    """O guard deixou passar: o duble de dados foi alcancado (ou a rota respondeu 200 sem dados)."""
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


def test_as_rotas_vivas_dos_9_modulos_nos_dois_apps_somam_as_56_da_tabela():
    assert len(VIVAS) == len(set(VIVAS))
    assert {CHAVE[v] for v in VIVAS} == set(RECURSO_2E)
    assert len(RECURSO_2E) == 56
    assert {v[0] for v in VIVAS} == {"main", "engines"}


@pytest.mark.parametrize("rota", VIVAS, ids=IDS)
def test_usuario_sem_grupo_e_negado_antes_de_qualquer_acesso_a_dados(ctx, rota):
    clients, state, perms, dados = ctx
    _como(state, SEM_GRUPO)
    _negado(_chamar(clients, rota), dados)
    assert perms.consultas == [(SEM_GRUPO,)]


# ---------------------------------------------------------------------------
# (b) grupo "usuario menos um recurso"
# ---------------------------------------------------------------------------

NEGADOS_POR_RECURSO = [(i, rota) for i, r in enumerate(RECURSOS) for rota in POR_RECURSO[r]]


@pytest.mark.parametrize("i,rota", NEGADOS_POR_RECURSO, ids=[f"{RECURSOS[i]}|{n}:{m}:{p}" for i, (n, m, p) in NEGADOS_POR_RECURSO])
def test_grupo_sem_um_recurso_e_negado_em_todas_as_rotas_dele(ctx, i, rota):
    clients, state, _, dados = ctx
    _como(state, FALTA_BASE + i)
    _negado(_chamar(clients, rota), dados)


@pytest.mark.parametrize("i", range(len(RECURSOS)), ids=RECURSOS)
def test_grupo_sem_um_recurso_passa_pelo_guard_nos_outros_recursos(ctx, i):
    clients, state, _, dados = ctx
    _como(state, FALTA_BASE + i)
    for rota in _amostra_por_recurso(excluir=RECURSOS[i]):
        antes = dados.chamadas
        resp = _chamar(clients, rota)
        assert resp.status_code != 403 or resp.json()["detail"] != FORBIDDEN_DETAIL, (rota, resp.text)
        assert dados.chamadas > antes or resp.status_code == 200, (rota, resp.status_code, resp.text)


# ---------------------------------------------------------------------------
# (c) tudo so em `ver`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rota", POSTS, ids=_ids(POSTS))
def test_grupo_so_com_ver_e_negado_nos_posts(ctx, rota):
    clients, state, _, dados = ctx
    _como(state, SO_VER)
    _negado(_chamar(clients, rota), dados)


def test_os_posts_sao_exatamente_os_3_de_editar():
    assert {CHAVE[v] for v in POSTS} == {
        "POST /analytics/muscle-activation-breakdown", "POST /analytics/muscle-activation-preview",
        "POST /workouts-analysis/generate",
    }


def test_grupo_so_com_ver_passa_pelo_guard_nos_gets_de_amostra(ctx):
    clients, state, _, dados = ctx
    _como(state, SO_VER)
    amostra = [r for r in _amostra_por_recurso() if r[1] == "GET"]
    assert len(amostra) == len(RECURSOS) - 1  # `ia.analise_treinos` so tem POST
    for rota in amostra:
        antes = dados.chamadas
        resp = _chamar(clients, rota)
        assert dados.chamadas > antes or resp.status_code == 200, (rota, resp.status_code, resp.text)


# ---------------------------------------------------------------------------
# (d) usuario, personal trainer, admin, token mestre, dispositivo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("uid", [USUARIO, PT, ADMIN_ID], ids=["usuario", "pt", "admin"])
@pytest.mark.parametrize("rota", VIVAS, ids=IDS)
def test_papeis_padrao_passam_pelo_guard_nas_56(ctx, uid, rota):
    clients, state, _, dados = ctx
    _como(state, uid)
    _passou(_chamar(clients, rota), dados)


@pytest.mark.parametrize("rota", VIVAS, ids=IDS)
def test_token_mestre_passa_sem_consultar_o_banco_de_permissoes(ctx, rota):
    clients, state, perms, dados = ctx
    state["identity"] = MESTRE
    _passou(_chamar(clients, rota), dados)
    assert perms.consultas == []


@pytest.mark.parametrize("rota", VIVAS, ids=IDS)
def test_dispositivo_do_usuario_herda_as_concessoes_do_dono(ctx, rota):
    clients, state, perms, dados = ctx
    _como(state, USUARIO, source="device")
    _passou(_chamar(clients, rota), dados)
    assert perms.consultas[0] == (USUARIO,)


@pytest.mark.parametrize("rota", VIVAS, ids=IDS)
def test_dispositivo_de_dono_sem_grupo_e_negado(ctx, rota):
    clients, state, perms, dados = ctx
    _como(state, SEM_GRUPO, source="device")
    _negado(_chamar(clients, rota), dados)
    assert perms.consultas == [(SEM_GRUPO,)]


# ---------------------------------------------------------------------------
# (e) falha de banco na resolucao: 503 sem acessar dados
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rota", VIVAS, ids=IDS)
def test_falha_do_banco_na_resolucao_e_503_sem_acessar_dados(ctx, rota):
    clients, state, perms, dados = ctx
    perms.falha = RuntimeError("banco fora")
    _como(state, USUARIO)
    resp = _chamar(clients, rota)
    assert resp.status_code == 503, resp.text
    assert dados.chamadas == 0
