"""RBAC do treino (ADR 0011, lote 2F-2), por HTTP: as 55 rotas do contrato `CONTRATO_2F2` de
`test_access_paridade_guards` (11 modulos, com so as 15 de `app.main` da tabela), sem escopo minimo.

Recursos: treino.planos (16), treino.registro (11), treino.modo_treino (7), treino.preferencias (7),
exercicios.catalogo (5, so ver), volume.grupamentos (4, so ver) e 5 POST de IA (editar) que criam job.

O resolvedor REAL de permissoes roda sobre um banco falso de concessoes (`access_service._db`), com
`DEFAULT_GRANTS_BY_GROUP` para os papeis e grupos sinteticos para os cenarios.

Ponto de duble: `_config_from_env` do backend ativo de `app.db` (primeira etapa de `get_conn`,
`transaction` e `fetch_rows`) e `async_jobs._table` (job das rotas de IA). Duble chamado = o guard
deixou a requisicao chegar ao handler. A excecao-sentinela nao e `DataApiError` (nao vira 503).

Rodar: cd api && python -m pytest tests/unit/test_treino_permissoes_rbac.py
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
    CONTRATO_2F2,
    MODULOS_2F2,
    ROTAS_MAIN_2F2,
)

USUARIO, PT, ADMIN_ID, SEM_GRUPO = 7, 8, 1, 9
SINT_SO_VER = 33
SINT_MENOS_BASE = 40  # SINT_MENOS_BASE + indice do recurso
MESTRE = Identity(user_id=ADMIN_ID, role="admin", source="master_token")
PAPEIS = {USUARIO: "regular", PT: "personal_trainer", ADMIN_ID: "admin"}

RECURSOS = sorted({r for r, _ in CONTRATO_2F2.values()})
INDICE = {r: i for i, r in enumerate(RECURSOS)}
IA = [r for r in RECURSOS if r.startswith("ia.")]
ROTAS_IA = [k for k, (r, _) in CONTRATO_2F2.items() if r.startswith("ia.")]

CORPO_VAZIO: dict = {}
_ATIV = {"local_date": "2026-11-10", "name": "Treino"}
_CEIL = {"mapping_id": 1, "gym_equipment_id": 1}
# Corpo minimo valido por rota (o handler/Pydantic valida antes de tocar dados); o padrao e `{}`.
CORPOS: dict[str, dict] = {
    "POST /activities": _ATIV,
    "PUT /activities/{activity_id}": _ATIV,
    "PUT /exercises/logs/{exercise_id}": {"local_date": "2026-11-10", "original_name": "Supino"},
    "POST /insights/generate": {"question": "Como evoluir?"},
    "POST /exercises/evaluate-set": {"exercise_name": "Supino"},
    "POST /workout-plans": {"name": "Plano"},
    "PUT /workout-plans/{plan_id}": {"name": "Plano"},
    "POST /workout-plans/{plan_id}/exercises/{exercise_id}/substitute-equipment": {"gym_equipment_id": 1},
    "POST /workout-plans/generate": {"template_id": 1},
    "PUT /me/equipment-exercise-preferences/{equipment_exercise_id}": {"stars": 3},
    "PUT /me/equipment-load-ceiling": {**_CEIL, "max_load_kg": 100},
    "POST /workout-sessions/{session_id}/exercises": {"original_name": "Supino"},
    "PATCH /workout-sessions/{session_id}/exercises/{exercise_id}/sets/{set_number}": {"reps": 10},
}
# Query obrigatoria por rota.
QUERIES: dict[str, dict] = {
    "GET /exercises/history": {"name": "Supino"},
    "GET /exercises/last-session-sets": {"mapping_id": 1, "exclude_activity_id": 1},
    "GET /me/equipment-load-ceiling": _CEIL,
    "DELETE /me/equipment-load-ceiling": _CEIL,
}


class SentinelaDados(Exception):
    """Levantada pelo duble de acesso a dados; prova que o guard deixou a requisicao chegar ao handler."""


def _grants_usuario():
    return list(DEFAULT_GRANTS_BY_GROUP["usuario"])


class FakePermissoes:
    """Banco de concessoes: `fetch_rows(sql, (user_id,))` -> linhas do grupo do usuario."""

    def __init__(self):
        self.falha = None
        self.consultas = []
        base = _grants_usuario()
        self.por_usuario = {
            USUARIO: self._grupo("usuario"),
            PT: self._grupo("personal_trainer"),
            ADMIN_ID: self._grupo("admin"),
            SINT_SO_VER: self._sint([(r, "ver", "todos") for r in RECURSOS]),
        }  # SEM_GRUPO ausente = usuario sem grupo
        for r in RECURSOS:
            self.por_usuario[SINT_MENOS_BASE + INDICE[r]] = self._sint([g for g in base if g[0] != r])

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
    """(app, metodo, modelo) das rotas vivas do 2F-2: 10 modulos inteiros mais as 15 de `app.main`."""
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    vivas = []
    for nome, app in (("main", main_app), ("engines", engines_app)):
        for route in _iter_api_routes(app):
            modulo = getattr(route.endpoint, "__module__", "")
            for metodo in sorted(route.methods - {"HEAD", "OPTIONS"}):
                if modulo in MODULOS_2F2 or (modulo == "app.main" and f"{metodo} {route.path}" in ROTAS_MAIN_2F2):
                    vivas.append((nome, metodo, route.path))
    return vivas


VIVAS = _rotas_vivas()
CHAVE = {v: f"{v[1]} {v[2]}" for v in VIVAS}
IDS = [f"{n}:{m}:{p}" for n, m, p in VIVAS]


def _ids(rotas):
    return [f"{n}:{m}:{p}" for n, m, p in rotas]


def _recurso(v):
    return CONTRATO_2F2[CHAVE[v]][0]


def _acao(v):
    return CONTRATO_2F2[CHAVE[v]][1]


def _de(recurso):
    return [v for v in VIVAS if _recurso(v) == recurso]


def _amostra(recurso):
    """Rota de amostra do recurso: a primeira GET, ou a primeira rota (IA so tem POST)."""
    rotas = _de(recurso)
    gets = [v for v in rotas if v[1] == "GET"]
    return (gets or rotas)[0]


AMOSTRAS = {r: _amostra(r) for r in RECURSOS}
ESCRITAS = [v for v in VIVAS if v[1] != "GET"]
GETS = [v for v in VIVAS if v[1] == "GET"]
POST_IA = [v for v in VIVAS if _recurso(v).startswith("ia.")]
GET_AMOSTRAS = [AMOSTRAS[r] for r in RECURSOS if not r.startswith("ia.")]


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
    consulta = QUERIES.get(f"{metodo} {modelo}")
    if consulta is not None:
        kwargs["params"] = consulta
    if metodo in ("POST", "PUT", "PATCH"):
        kwargs["json"] = CORPOS.get(f"{metodo} {modelo}", CORPO_VAZIO)
    return clients[nome].request(metodo, caminho, **kwargs)


def _negado(resp, dados):
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == FORBIDDEN_DETAIL
    assert dados.chamadas == 0


def _passou(resp, dados):
    """O guard deixou passar: duble de dados alcancado ou status de sucesso exato (200)."""
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


def test_as_rotas_vivas_sao_exatamente_as_55_do_contrato():
    assert len(VIVAS) == len(set(VIVAS))
    assert {CHAVE[v] for v in VIVAS} == set(CONTRATO_2F2)
    assert len(VIVAS) == len(CONTRATO_2F2) == 55
    assert len(POST_IA) == 5 and {CHAVE[v] for v in POST_IA} == set(ROTAS_IA)
    assert all(v[1] == "POST" for v in POST_IA)
    assert len(RECURSOS) == 10
    assert all(AMOSTRAS[r] for r in RECURSOS)


@pytest.mark.parametrize("rota", VIVAS, ids=IDS)
def test_usuario_sem_grupo_e_negado_antes_de_qualquer_acesso_a_dados(ctx, rota):
    clients, state, perms, dados = ctx
    _como(state, SEM_GRUPO)
    _negado(_chamar(clients, rota), dados)
    assert perms.consultas == [(SEM_GRUPO,)]


# ---------------------------------------------------------------------------
# (b) matriz do usuario menos um recurso
# ---------------------------------------------------------------------------


def test_o_grupo_usuario_cobre_os_10_recursos_e_o_catalogo_so_em_ver():
    por = {(r, a) for r, a, _ in DEFAULT_GRANTS_BY_GROUP["usuario"]}
    for r in RECURSOS:
        assert (r, "ver") in por, r
    assert {a for r, a in por if r == "exercicios.catalogo"} == {"ver"}
    for r, a in set(CONTRATO_2F2.values()):
        assert (r, a) in por, (r, a)


@pytest.mark.parametrize("recurso", RECURSOS)
def test_sem_o_recurso_e_negado_em_todas_as_rotas_dele(ctx, recurso):
    clients, state, _, dados = ctx
    _como(state, SINT_MENOS_BASE + INDICE[recurso])
    rotas = _de(recurso)
    assert rotas
    for rota in rotas:
        _negado(_chamar(clients, rota), dados)


@pytest.mark.parametrize("recurso", RECURSOS)
def test_sem_o_recurso_passa_na_amostra_de_cada_um_dos_outros(ctx, recurso):
    clients, state, _, dados = ctx
    _como(state, SINT_MENOS_BASE + INDICE[recurso])
    for outro in RECURSOS:
        if outro == recurso:
            continue
        dados.chamadas = 0
        _passou(_chamar(clients, AMOSTRAS[outro]), dados)


# ---------------------------------------------------------------------------
# (c) todos os recursos so em ver
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rota", ESCRITAS, ids=_ids(ESCRITAS))
def test_so_ver_e_negado_nas_escritas_sem_criar_job(ctx, rota):
    clients, state, _, dados = ctx
    _como(state, SINT_SO_VER)
    _negado(_chamar(clients, rota), dados)  # dados.chamadas == 0 cobre transaction e async_jobs._table


@pytest.mark.parametrize("rota", GET_AMOSTRAS, ids=_ids(GET_AMOSTRAS))
def test_so_ver_passa_na_get_de_amostra_de_cada_recurso(ctx, rota):
    clients, state, _, dados = ctx
    _como(state, SINT_SO_VER)
    _passou(_chamar(clients, rota), dados)


def test_so_ver_passa_em_todas_as_get(ctx):
    clients, state, _, dados = ctx
    _como(state, SINT_SO_VER)
    assert len(GETS) == 55 - len(ESCRITAS)
    for rota in GETS:
        dados.chamadas = 0
        resp = _chamar(clients, rota)
        assert not (resp.status_code == 403 and resp.json().get("detail") == FORBIDDEN_DETAIL), rota
        _passou(resp, dados)


# ---------------------------------------------------------------------------
# (d) papeis, token mestre, dispositivo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("uid", [USUARIO, PT, ADMIN_ID], ids=["usuario", "pt", "admin"])
@pytest.mark.parametrize("rota", VIVAS, ids=IDS)
def test_usuario_pt_e_admin_passam_pelo_guard_nas_55(ctx, uid, rota):
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
def test_dispositivo_de_dono_usuario_herda_e_passa_nas_55(ctx, rota):
    clients, state, perms, dados = ctx
    _como(state, USUARIO, source="device")
    _passou(_chamar(clients, rota), dados)
    assert perms.consultas[0] == (USUARIO,)


@pytest.mark.parametrize("rota", VIVAS, ids=IDS)
def test_dispositivo_de_dono_sem_grupo_e_negado_nas_55(ctx, rota):
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
