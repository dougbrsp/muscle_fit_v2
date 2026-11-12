"""RBAC do corpo, integracoes e cardio (ADR 0011, lote 2F-3), por HTTP: as 42 rotas dos 7 modulos de
`MODULOS_2F3` servidas pelos apps reais.

Contrato (tabela viva `CONTRATO_2F3` de `test_access_paridade_guards`): `corpo.indicadores` (15),
`corpo.checkin` (10), `integracoes.intervals` (11), `cardio` (4), `ia.cardio`/editar (POST generate) e
`modelos.treino`/editar (POST apply). Escopo minimo `todos` so no sync e no apply.

O resolvedor REAL de permissoes roda sobre um banco falso de concessoes (`access_service._db`), com
`DEFAULT_GRANTS_BY_GROUP` para os papeis e grupos sinteticos para os cenarios.

Pontos de duble: `_config_from_env` do backend ativo de `app.db` e `async_jobs._table` (job do generate).
Duble chamado = o guard deixou a requisicao chegar ao handler; a excecao-sentinela nao e `DataApiError`
(nao vira 503). `can_apply` usa `_load_recommendation` substituido por uma linha minima.

Rodar: cd api && python -m pytest tests/unit/test_corpo_integracoes_cardio_permissoes_rbac.py
"""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

from app import async_jobs
from app import cardio_recommendations
from app import db as app_db
from app.access import DEFAULT_GRANTS_BY_GROUP
from app.access import service as access_service
from app.access.service import FORBIDDEN_DETAIL
from app.security import Identity, get_current_identity
from tests.unit.test_access_guarda_cobertura_rotas import _iter_api_routes
from tests.unit.test_access_paridade_guards import (
    CONTRATO_2F3,
    MODULOS_2F3,
    ROTA_APPLY_2F3,
    ROTA_SYNC_2F3,
)

USUARIO, PT, ADMIN_ID, SEM_GRUPO = 7, 8, 1, 9
SINT_II_PROPRIOS, SINT_II_ALUNOS, SINT_II_TODOS = 30, 31, 32
SINT_TREINO_PROPRIOS, SINT_TREINO_ALUNOS, SINT_TREINO_TODOS = 33, 34, 35
SINT_SO_VER = 36
SEM_RECURSO_BASE = 40  # 40 + indice em RECURSOS
MESTRE = Identity(user_id=ADMIN_ID, role="admin", source="master_token")
PAPEIS = {USUARIO: "regular", PT: "personal_trainer", ADMIN_ID: "admin"}
ACOES = ("ver", "editar", "excluir")
RECURSOS = ("corpo.indicadores", "corpo.checkin", "integracoes.intervals", "cardio", "ia.cardio")
ROTA_GENERATE = "POST /cardio-recommendations/generate"

BODY_COMP = {"local_date": "2026-01-01", "weight_kg": 80}
BODY_MEAS = {"local_date": "2026-01-01", "site": "waist", "value_cm": 80}
CORPOS = {
    "POST /body-composition": BODY_COMP,
    "PUT /body-composition/{entry_id}": {"notes": "x"},
    "POST /body-measurements": BODY_MEAS,
    "PUT /body-measurements/{entry_id}": {"notes": "x"},
    "POST /sleep-metrics": {"local_date": "2026-01-01", "duration_hours": 7},
    "PUT /sleep-metrics/{entry_id}": {"notes": "x"},
    "POST /daily-checkins": {"local_date": "2026-01-01", "energy": 3},
    "PUT /daily-checkins/{entry_id}": {"notes": "x"},
    "POST /context-events": {"event_type": "travel", "local_date": "2026-01-01"},
    "PUT /context-events/{entry_id}": {"notes": "x"},
    "POST /integrations/intervals/activities": [],
    "POST /integrations/intervals/wellness": [],
    "PATCH /integrations/intervals/wellness/{wellness_date}": {"resting_hr_bpm": 50},
    "POST /cardio-recommendations": {"reference_date": "2026-01-01", "recommendation_markdown": "x"},
    ROTA_GENERATE: {},
    ROTA_APPLY_2F3: {"dates": ["2026-01-01"]},
}
PATH_VALORES = {"wellness_date": "2026-01-01"}


class SentinelaDados(Exception):
    """Levantada pelo duble de acesso a dados; prova que o guard deixou a requisicao chegar ao handler."""


class FakePermissoes:
    """Banco de concessoes: `fetch_rows(sql, (user_id,))` -> linhas do grupo do usuario."""

    def __init__(self):
        self.falha = None
        self.consultas = []
        base_usuario = DEFAULT_GRANTS_BY_GROUP["usuario"]
        self.por_usuario = {
            USUARIO: self._grupo("usuario"),
            PT: self._grupo("personal_trainer"),
            ADMIN_ID: self._grupo("admin"),
            SINT_II_PROPRIOS: self._sint([("integracoes.intervals", a, "proprios") for a in ACOES]),
            SINT_II_ALUNOS: self._sint([("integracoes.intervals", a, "alunos") for a in ACOES]),
            SINT_II_TODOS: self._sint([("integracoes.intervals", a, "todos") for a in ACOES]),
            SINT_TREINO_PROPRIOS: self._sint([("modelos.treino", a, "proprios") for a in ACOES]),
            SINT_TREINO_ALUNOS: self._sint([("modelos.treino", a, "alunos") for a in ACOES]),
            SINT_TREINO_TODOS: self._sint([("modelos.treino", a, "todos") for a in ACOES]),
            SINT_SO_VER: self._sint([(r, "ver", "todos") for r in (*RECURSOS, "modelos.treino")]),
        }  # SEM_GRUPO ausente = usuario sem grupo
        for i, recurso in enumerate(RECURSOS):
            self.por_usuario[SEM_RECURSO_BASE + i] = self._sint([g for g in base_usuario if g[0] != recurso])

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
    """(app, metodo, modelo) das rotas vivas dos modulos do 2F-3 nos apps reais."""
    from app.main import app as main_app
    from app.main_engines import app as engines_app

    vivas = []
    for nome, app in (("main", main_app), ("engines", engines_app)):
        for route in _iter_api_routes(app):
            if getattr(route.endpoint, "__module__", "") not in MODULOS_2F3:
                continue
            for metodo in sorted(route.methods - {"HEAD", "OPTIONS"}):
                vivas.append((nome, metodo, route.path))
    return vivas


VIVAS = _rotas_vivas()
CHAVE = {v: f"{v[1]} {v[2]}" for v in VIVAS}
IDS = [f"{n}:{m}:{p}" for n, m, p in VIVAS]
ESCOPADAS = {ROTA_SYNC_2F3, ROTA_APPLY_2F3}
OUTRAS_40 = [v for v in VIVAS if CHAVE[v] not in ESCOPADAS]
ESCRITAS = [v for v in VIVAS if v[1] != "GET"]
SYNC = [v for v in VIVAS if CHAVE[v] == ROTA_SYNC_2F3]
APPLY = [v for v in VIVAS if CHAVE[v] == ROTA_APPLY_2F3]
GENERATE = [v for v in VIVAS if CHAVE[v] == ROTA_GENERATE]
DETALHE_CARDIO = [v for v in VIVAS if CHAVE[v] == "GET /cardio-recommendations/{entry_id}"]


def _ids(rotas):
    return [f"{n}:{m}:{p}" for n, m, p in rotas]


def _recurso(rota):
    return CONTRATO_2F3[CHAVE[rota]][0]


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
        caminho = caminho[:ini] + PATH_VALORES.get(caminho[ini + 1:fim], "1") + caminho[fim + 1:]
    kwargs = {}
    corpo = CORPOS.get(f"{metodo} {modelo}")
    if corpo is not None:
        kwargs["json"] = corpo
    return clients[nome].request(metodo, caminho, **kwargs)


def _negado(resp, dados):
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == FORBIDDEN_DETAIL
    assert dados.chamadas == 0


def _passou(resp, dados):
    """O guard deixou passar: o duble de dados foi alcancado ou a resposta foi 200 exato."""
    assert dados.chamadas > 0 or resp.status_code == 200, (resp.status_code, resp.text)
    if dados.chamadas == 0:
        assert resp.status_code == 200


def _varrer(clients, dados, rotas, esperado):
    """Chama cada rota (zerando o duble) e aplica `esperado(resp, dados)`; acumula falhas por rota."""
    falhas = []
    for rota in rotas:
        dados.chamadas = 0
        resp = _chamar(clients, rota)
        try:
            esperado(resp, dados)
        except AssertionError as exc:
            falhas.append(f"{CHAVE[rota]} [{rota[0]}]: {exc}")
    assert not falhas, "\n".join(falhas)


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


def test_as_rotas_vivas_sao_exatamente_as_42_do_contrato():
    assert len(VIVAS) == len(set(VIVAS))
    assert {CHAVE[v] for v in VIVAS} == set(CONTRATO_2F3)
    assert len(CONTRATO_2F3) == 42
    assert len(OUTRAS_40) == len(VIVAS) - len(SYNC) - len(APPLY) and len(SYNC) >= 1 and len(APPLY) >= 1
    assert len(GENERATE) >= 1 and len(DETALHE_CARDIO) >= 1
    assert set(CORPOS) <= set(CONTRATO_2F3)


def test_o_contrato_declara_escopo_todos_so_no_sync_e_no_apply():
    assert {k for k, (_, _, esc) in CONTRATO_2F3.items() if esc} == ESCOPADAS
    assert CONTRATO_2F3[ROTA_SYNC_2F3] == ("integracoes.intervals", "editar", "todos")
    assert CONTRATO_2F3[ROTA_APPLY_2F3] == ("modelos.treino", "editar", "todos")
    assert CONTRATO_2F3[ROTA_GENERATE] == ("ia.cardio", "editar", None)


@pytest.mark.parametrize("rota", VIVAS, ids=IDS)
def test_usuario_sem_grupo_e_negado_antes_de_qualquer_acesso_a_dados(ctx, rota):
    clients, state, perms, dados = ctx
    _como(state, SEM_GRUPO)
    _negado(_chamar(clients, rota), dados)
    assert perms.consultas == [(SEM_GRUPO,)]


# ---------------------------------------------------------------------------
# (b) matriz do usuario menos um recurso
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("i", range(len(RECURSOS)), ids=RECURSOS)
def test_sem_um_recurso_e_negado_nas_rotas_dele_e_passa_nas_outras(ctx, i):
    clients, state, perms, dados = ctx
    recurso = RECURSOS[i]
    _como(state, SEM_RECURSO_BASE + i)
    dele = [v for v in VIVAS if _recurso(v) == recurso]
    assert dele, recurso
    _varrer(clients, dados, dele, lambda resp, d: _negado(resp, d))
    # sync e apply ficam de fora das "outras": o escopo minimo `todos` nega o usuario por outro motivo
    outras = [v for v in OUTRAS_40 if _recurso(v) != recurso]
    assert outras
    _varrer(clients, dados, outras, lambda resp, d: _passou(resp, d))


# ---------------------------------------------------------------------------
# (c) tudo so em ver
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rota", ESCRITAS, ids=_ids(ESCRITAS))
def test_so_ver_em_todos_e_negado_nas_escritas_sem_criar_job(ctx, rota):
    clients, state, _, dados = ctx
    _como(state, SINT_SO_VER)
    _negado(_chamar(clients, rota), dados)  # dados.chamadas == 0 cobre banco e async_jobs._table


def test_so_ver_em_todos_passa_nas_get(ctx):
    clients, state, _, dados = ctx
    _como(state, SINT_SO_VER)
    gets = [v for v in VIVAS if v[1] == "GET"]
    assert gets
    _varrer(clients, dados, gets, lambda resp, d: _passou(resp, d))


# ---------------------------------------------------------------------------
# (d) escopo minimo `todos` no sync e no apply
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("uid", [USUARIO, PT], ids=["usuario", "pt"])
@pytest.mark.parametrize("rota", SYNC, ids=_ids(SYNC))
def test_usuario_e_pt_sao_negados_no_sync(ctx, uid, rota):
    clients, state, _, dados = ctx
    _como(state, uid)
    _negado(_chamar(clients, rota), dados)


@pytest.mark.parametrize("rota", SYNC, ids=_ids(SYNC))
def test_admin_passa_no_sync(ctx, rota):
    clients, state, _, dados = ctx
    _como(state, ADMIN_ID)
    _passou(_chamar(clients, rota), dados)
    assert dados.chamadas > 0


@pytest.mark.parametrize("rota", APPLY, ids=_ids(APPLY))
def test_usuario_e_negado_no_apply(ctx, rota):
    clients, state, _, dados = ctx
    _como(state, USUARIO)
    _negado(_chamar(clients, rota), dados)


@pytest.mark.parametrize("uid", [PT, ADMIN_ID], ids=["pt", "admin"])
@pytest.mark.parametrize("rota", APPLY, ids=_ids(APPLY))
def test_pt_e_admin_passam_no_apply(ctx, uid, rota):
    clients, state, _, dados = ctx
    _como(state, uid)
    _passou(_chamar(clients, rota), dados)
    assert dados.chamadas > 0


@pytest.mark.parametrize("uid", [SINT_II_PROPRIOS, SINT_II_ALUNOS], ids=["proprios", "alunos"])
@pytest.mark.parametrize("rota", SYNC, ids=_ids(SYNC))
def test_sync_com_escopo_abaixo_de_todos_e_negado_mesmo_com_todas_as_acoes(ctx, uid, rota):
    clients, state, _, dados = ctx
    _como(state, uid)
    _negado(_chamar(clients, rota), dados)


@pytest.mark.parametrize("rota", SYNC, ids=_ids(SYNC))
def test_sync_com_escopo_todos_passa(ctx, rota):
    clients, state, _, dados = ctx
    _como(state, SINT_II_TODOS)
    _passou(_chamar(clients, rota), dados)
    assert dados.chamadas > 0


@pytest.mark.parametrize("uid", [SINT_TREINO_PROPRIOS, SINT_TREINO_ALUNOS], ids=["proprios", "alunos"])
@pytest.mark.parametrize("rota", APPLY, ids=_ids(APPLY))
def test_apply_com_escopo_abaixo_de_todos_e_negado_mesmo_com_todas_as_acoes(ctx, uid, rota):
    clients, state, _, dados = ctx
    _como(state, uid)
    _negado(_chamar(clients, rota), dados)


@pytest.mark.parametrize("rota", APPLY, ids=_ids(APPLY))
def test_apply_com_escopo_todos_passa(ctx, rota):
    clients, state, _, dados = ctx
    _como(state, SINT_TREINO_TODOS)
    _passou(_chamar(clients, rota), dados)
    assert dados.chamadas > 0


# ---------------------------------------------------------------------------
# (e) can_apply
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("uid,esperado", [(USUARIO, False), (PT, True), (ADMIN_ID, True)], ids=["usuario", "pt", "admin"])
@pytest.mark.parametrize("rota", DETALHE_CARDIO, ids=_ids(DETALHE_CARDIO))
def test_can_apply_segue_a_concessao_modelos_treino_editar_em_todos(ctx, monkeypatch, uid, esperado, rota):
    clients, state, _, dados = ctx
    linha = {
        "id": 1,
        "recommendation_json": {"summary": "s", "sessions": [], "plan": {"template_id": 1}},
    }
    chamadas = []

    def falso(entry_id, user_id):
        chamadas.append((entry_id, user_id))
        return dict(linha)

    monkeypatch.setattr(cardio_recommendations, "_load_recommendation", falso)
    _como(state, uid)
    resp = _chamar(clients, rota)
    assert resp.status_code == 200, resp.text
    assert chamadas == [(1, uid)]
    assert resp.json()["recommendation_view"]["can_apply"] is esperado


@pytest.mark.parametrize("rota", DETALHE_CARDIO, ids=_ids(DETALHE_CARDIO))
def test_can_apply_e_falso_para_escopo_abaixo_de_todos(ctx, monkeypatch, rota):
    clients, state, _, dados = ctx
    monkeypatch.setattr(
        cardio_recommendations,
        "_load_recommendation",
        lambda entry_id, user_id: {"id": 1, "recommendation_json": {"summary": "s", "sessions": []}},
    )
    perms = ctx[2]
    perms.por_usuario[SINT_TREINO_PROPRIOS] += perms._sint([("cardio", "ver", "proprios")])
    _como(state, SINT_TREINO_PROPRIOS)
    resp = _chamar(clients, rota)
    assert resp.status_code == 200, resp.text
    assert resp.json()["recommendation_view"]["can_apply"] is False


# ---------------------------------------------------------------------------
# (f) papeis, token mestre, dispositivo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("uid", [USUARIO, PT, ADMIN_ID], ids=["usuario", "pt", "admin"])
@pytest.mark.parametrize("rota", OUTRAS_40, ids=_ids(OUTRAS_40))
def test_usuario_pt_e_admin_passam_pelo_guard_nas_outras_40(ctx, uid, rota):
    clients, state, _, dados = ctx
    _como(state, uid)
    _passou(_chamar(clients, rota), dados)


@pytest.mark.parametrize("rota", VIVAS, ids=IDS)
def test_token_mestre_passa_sem_consultar_o_banco_de_permissoes(ctx, rota):
    clients, state, perms, dados = ctx
    state["identity"] = MESTRE
    _passou(_chamar(clients, rota), dados)
    assert perms.consultas == []


@pytest.mark.parametrize("rota", OUTRAS_40, ids=_ids(OUTRAS_40))
def test_dispositivo_de_dono_usuario_herda_e_passa_nas_outras_40(ctx, rota):
    clients, state, perms, dados = ctx
    _como(state, USUARIO, source="device")
    _passou(_chamar(clients, rota), dados)
    assert perms.consultas[0] == (USUARIO,)


@pytest.mark.parametrize("rota", SYNC + APPLY, ids=_ids(SYNC + APPLY))
def test_dispositivo_de_dono_usuario_e_negado_no_sync_e_no_apply(ctx, rota):
    clients, state, perms, dados = ctx
    _como(state, USUARIO, source="device")
    _negado(_chamar(clients, rota), dados)
    assert perms.consultas == [(USUARIO,)]


@pytest.mark.parametrize("rota", SYNC + APPLY, ids=_ids(SYNC + APPLY))
def test_dispositivo_de_dono_pt_herda_o_apply_e_nao_o_sync(ctx, rota):
    clients, state, perms, dados = ctx
    _como(state, PT, source="device")
    resp = _chamar(clients, rota)
    if CHAVE[rota] == ROTA_SYNC_2F3:
        _negado(resp, dados)
    else:
        _passou(resp, dados)
        assert dados.chamadas > 0
    assert perms.consultas[0] == (PT,)


@pytest.mark.parametrize("rota", VIVAS, ids=IDS)
def test_dispositivo_de_dono_sem_grupo_e_negado_nas_42(ctx, rota):
    clients, state, perms, dados = ctx
    _como(state, SEM_GRUPO, source="device")
    _negado(_chamar(clients, rota), dados)
    assert perms.consultas == [(SEM_GRUPO,)]


# ---------------------------------------------------------------------------
# (g) falha de banco nas permissoes: 503 sem acessar dados
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rota", VIVAS, ids=IDS)
def test_falha_do_banco_na_resolucao_e_503_sem_acessar_dados(ctx, rota):
    clients, state, perms, dados = ctx
    perms.falha = RuntimeError("banco fora")
    _como(state, USUARIO)
    resp = _chamar(clients, rota)
    assert resp.status_code == 503, resp.text
    assert dados.chamadas == 0
