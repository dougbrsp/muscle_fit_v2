"""RBAC dos catalogos de exercicios (ADR 0011, lote 2C-2), por HTTP: `exercise_catalog_admin`
(exercicios, tipos de equipamento, mapeamentos externos), `exercise_mapping_catalog`,
`muscle_volume_classes` e `equipment_exercises`.

Mapa: todo GET = `exercicios.catalogo`/ver (exceto GET `/muscle-volume-classes` =
`volume.grupamentos`/ver); POST/PUT = `admin.catalogos`/editar; DELETE =
`admin.catalogos`/excluir.

O resolvedor REAL de permissoes roda sobre um banco falso de concessoes. As concessoes dos
papeis (usuario, personal trainer, admin) vem de `DEFAULT_GRANTS_BY_GROUP`; so os cenarios
sinteticos (so editar, so excluir, so catalogo) montam o conjunto a mao. O banco de negocio
e um cursor falso vazio que registra as instrucoes: nas negacoes nada pode ser executado.

Rodar: cd api && python -m pytest tests/unit/test_catalogos_exercicios_permissoes_rbac.py
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import equipment_exercises, exercise_catalog_admin, exercise_mapping_catalog, muscle_volume_classes
from app.access import DEFAULT_GRANTS_BY_GROUP
from app.access import service as access_service
from app.access.service import FORBIDDEN_DETAIL
from app.security import Identity, get_current_identity

USUARIO, PT, ADMIN_ID, SEM_GRUPO, SO_EDITAR, SO_EXCLUIR, SO_CATALOGO, SO_VOLUME = 7, 8, 1, 9, 21, 22, 23, 24
MESTRE = Identity(user_id=ADMIN_ID, role="admin", source="master_token")

MODULOS = {
    "exercise_catalog_admin": (exercise_catalog_admin, ["router", "equipment_types_router", "external_mappings_router"]),
    "exercise_mapping_catalog": (
        exercise_mapping_catalog,
        ["router", "muscle_groups_router", "muscle_contributions_router"],
    ),
    "muscle_volume_classes": (muscle_volume_classes, ["router"]),
    "equipment_exercises": (equipment_exercises, ["router"]),
}

# (modulo, metodo, caminho, corpo, recurso, acao)
ROTAS = [
    ("exercise_catalog_admin", "get", "/exercises/1/aliases", None, "exercicios.catalogo", "ver"),
    ("exercise_catalog_admin", "post", "/exercises", {}, "admin.catalogos", "editar"),
    ("exercise_catalog_admin", "delete", "/exercises/aliases/1", None, "admin.catalogos", "excluir"),
    ("exercise_catalog_admin", "get", "/equipment-types", None, "exercicios.catalogo", "ver"),
    ("exercise_catalog_admin", "put", "/equipment-types/1", {}, "admin.catalogos", "editar"),
    ("exercise_catalog_admin", "delete", "/exercises/external-mappings/1", None, "admin.catalogos", "excluir"),
    ("exercise_catalog_admin", "get", "/exercise-external-mappings/providers", None, "exercicios.catalogo", "ver"),
    ("exercise_mapping_catalog", "get", "/exercise-mapping-catalog/groups", None, "exercicios.catalogo", "ver"),
    ("exercise_mapping_catalog", "post", "/exercise-mapping-catalog", {}, "admin.catalogos", "editar"),
    ("exercise_mapping_catalog", "put", "/exercise-mapping-catalog/1", {}, "admin.catalogos", "editar"),
    ("exercise_mapping_catalog", "delete", "/exercise-mapping-catalog/1", None, "admin.catalogos", "excluir"),
    ("muscle_volume_classes", "get", "/muscle-volume-classes", None, "volume.grupamentos", "ver"),
    ("muscle_volume_classes", "post", "/muscle-volume-classes", {}, "admin.catalogos", "editar"),
    ("muscle_volume_classes", "put", "/muscle-volume-classes/1/groups", {}, "admin.catalogos", "editar"),
    ("muscle_volume_classes", "delete", "/muscle-volume-classes/1", None, "admin.catalogos", "excluir"),
    ("equipment_exercises", "get", "/equipment-exercises", None, "exercicios.catalogo", "ver"),
    ("equipment_exercises", "post", "/equipment-exercises", {}, "admin.catalogos", "editar"),
    ("equipment_exercises", "delete", "/equipment-exercises/1", None, "admin.catalogos", "excluir"),
]
ESCRITAS = [r for r in ROTAS if r[1] != "get"]
LEITURAS = [r for r in ROTAS if r[1] == "get"]
DELETES = [r for r in ROTAS if r[1] == "delete"]


def _ids(rotas):
    return [f"{r[1].upper()} {r[2]}" for r in rotas]


class FakePermissoes:
    def __init__(self, falha=None):
        self.falha = falha
        self.consultas = []
        self.por_usuario = {
            USUARIO: self._grupo("usuario"),
            PT: self._grupo("personal_trainer"),
            ADMIN_ID: self._grupo("admin"),
            SO_EDITAR: self._sint([("admin.catalogos", "editar")]),
            SO_EXCLUIR: self._sint([("admin.catalogos", "excluir")]),
            SO_CATALOGO: self._sint([("exercicios.catalogo", "ver")]),
            SO_VOLUME: self._sint([("volume.grupamentos", "ver")]),
        }  # SEM_GRUPO ausente = usuario sem grupo

    @staticmethod
    def _grupo(grupo):
        grants = DEFAULT_GRANTS_BY_GROUP[grupo]
        if not grants:
            return [{"group_key": grupo, "is_system": 1, "resource": None, "action": None, "scope": None}]
        return [
            {"group_key": grupo, "is_system": 1, "resource": r, "action": a, "scope": s} for r, a, s in grants
        ]

    @staticmethod
    def _sint(pares):
        return [{"group_key": "sint", "is_system": 0, "resource": r, "action": a, "scope": "todos"} for r, a in pares]

    def fetch_rows(self, sql, params=()):
        self.consultas.append(tuple(params))
        if self.falha is not None:
            raise self.falha
        return list(self.por_usuario.get(params[0], []))


class _Cur:
    def __init__(self, log):
        self.log = log
        self.rowcount = 0
        self.lastrowid = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.log.append(" ".join(sql.split()))

    def executemany(self, sql, seq):
        self.log.append(" ".join(sql.split()))

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class _Conn:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _Cur(self.log)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def ctx(monkeypatch):
    log: list[str] = []
    perms = FakePermissoes()
    monkeypatch.setattr(access_service, "_db", perms)
    app = FastAPI()
    for modulo, routers in MODULOS.values():
        monkeypatch.setattr(modulo, "_connect", lambda log=log: _Conn(log))
        for nome in routers:
            app.include_router(getattr(modulo, nome))
    state = {"identity": Identity(user_id=USUARIO, role="regular", source="session")}
    app.dependency_overrides[get_current_identity] = lambda: state["identity"]
    return TestClient(app, raise_server_exceptions=False), state, perms, log


def _como(state, uid, role="regular"):
    state["identity"] = Identity(user_id=uid, role=role, source="session")


def _chamar(client, metodo, caminho, corpo):
    if corpo is not None:
        return getattr(client, metodo)(caminho, json=corpo)
    return getattr(client, metodo)(caminho)


def _guarda_liberou(resp):
    """Passou da guarda: nada de 401/403/503; erros de negocio (404/422) sao esperados com
    o banco vazio e corpo vazio."""
    return resp.status_code not in (401, 403, 503) and resp.status_code < 500


def _negado(resp):
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == FORBIDDEN_DETAIL


# ---------------------------------------------------------------------------
# Usuario comum e personal trainer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("uid,role", [(USUARIO, "regular"), (PT, "personal_trainer")], ids=["usuario", "pt"])
@pytest.mark.parametrize("modulo,metodo,caminho,corpo,recurso,acao", LEITURAS, ids=_ids(LEITURAS))
def test_leitura_permitida_a_usuario_comum_e_pt(ctx, uid, role, modulo, metodo, caminho, corpo, recurso, acao):
    client, state, perms, _ = ctx
    _como(state, uid, role)
    resp = _chamar(client, metodo, caminho, corpo)
    assert _guarda_liberou(resp), (resp.status_code, resp.text)
    assert perms.consultas == [(uid,)]


@pytest.mark.parametrize("uid,role", [(USUARIO, "regular"), (PT, "personal_trainer")], ids=["usuario", "pt"])
@pytest.mark.parametrize("modulo,metodo,caminho,corpo,recurso,acao", ESCRITAS, ids=_ids(ESCRITAS))
def test_escrita_negada_a_usuario_comum_e_pt_e_nada_e_gravado(ctx, uid, role, modulo, metodo, caminho, corpo, recurso, acao):
    client, state, _, log = ctx
    _como(state, uid, role)
    _negado(_chamar(client, metodo, caminho, corpo))
    assert log == []


# ---------------------------------------------------------------------------
# Quem tem admin.catalogos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("modulo,metodo,caminho,corpo,recurso,acao", ESCRITAS, ids=_ids(ESCRITAS))
def test_escrita_permitida_com_a_concessao_exata(ctx, modulo, metodo, caminho, corpo, recurso, acao):
    client, state, perms, _ = ctx
    _como(state, SO_EDITAR if acao == "editar" else SO_EXCLUIR)
    resp = _chamar(client, metodo, caminho, corpo)
    assert _guarda_liberou(resp), (resp.status_code, resp.text)


@pytest.mark.parametrize("modulo,metodo,caminho,corpo,recurso,acao", ROTAS, ids=_ids(ROTAS))
def test_admin_passa_em_tudo(ctx, modulo, metodo, caminho, corpo, recurso, acao):
    client, state, _, _ = ctx
    _como(state, ADMIN_ID, "admin")
    assert _guarda_liberou(_chamar(client, metodo, caminho, corpo))


@pytest.mark.parametrize("modulo,metodo,caminho,corpo,recurso,acao", DELETES, ids=_ids(DELETES))
def test_delete_exige_excluir_so_editar_nao_basta(ctx, modulo, metodo, caminho, corpo, recurso, acao):
    client, state, _, log = ctx
    _como(state, SO_EDITAR)
    _negado(_chamar(client, metodo, caminho, corpo))
    assert log == []


@pytest.mark.parametrize(
    "modulo,metodo,caminho,corpo,recurso,acao",
    [r for r in ESCRITAS if r[1] != "delete"],
    ids=_ids([r for r in ESCRITAS if r[1] != "delete"]),
)
def test_post_e_put_exigem_editar_so_excluir_nao_basta(ctx, modulo, metodo, caminho, corpo, recurso, acao):
    client, state, _, log = ctx
    _como(state, SO_EXCLUIR)
    _negado(_chamar(client, metodo, caminho, corpo))
    assert log == []


# ---------------------------------------------------------------------------
# volume.grupamentos x exercicios.catalogo
# ---------------------------------------------------------------------------


def test_get_muscle_volume_classes_segue_volume_grupamentos_e_nega_so_catalogo(ctx):
    client, state, _, log = ctx
    _como(state, SO_CATALOGO)
    _negado(client.get("/muscle-volume-classes"))
    assert log == []
    _como(state, SO_VOLUME)
    assert _guarda_liberou(client.get("/muscle-volume-classes"))


@pytest.mark.parametrize(
    "caminho",
    ["/exercises/1/aliases", "/equipment-types", "/exercise-mapping-catalog/groups", "/equipment-exercises"],
)
def test_leituras_de_exercicios_nao_aceitam_so_volume_grupamentos(ctx, caminho):
    client, state, _, log = ctx
    _como(state, SO_VOLUME)
    _negado(client.get(caminho))
    assert log == []
    _como(state, SO_CATALOGO)
    assert _guarda_liberou(client.get(caminho))


# ---------------------------------------------------------------------------
# Sem grupo, token mestre e falha do banco
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("modulo,metodo,caminho,corpo,recurso,acao", ROTAS, ids=_ids(ROTAS))
def test_usuario_sem_grupo_e_negado(ctx, modulo, metodo, caminho, corpo, recurso, acao):
    client, state, perms, log = ctx
    _como(state, SEM_GRUPO)
    _negado(_chamar(client, metodo, caminho, corpo))
    assert perms.consultas == [(SEM_GRUPO,)]
    assert log == []


@pytest.mark.parametrize("modulo,metodo,caminho,corpo,recurso,acao", ROTAS, ids=_ids(ROTAS))
def test_token_mestre_passa_sem_consultar_o_banco_de_permissoes(ctx, modulo, metodo, caminho, corpo, recurso, acao):
    client, state, perms, _ = ctx
    state["identity"] = MESTRE
    assert _guarda_liberou(_chamar(client, metodo, caminho, corpo))
    assert perms.consultas == []


@pytest.mark.parametrize("modulo,metodo,caminho,corpo,recurso,acao", ROTAS, ids=_ids(ROTAS))
def test_falha_do_banco_na_resolucao_e_503_nunca_concede(ctx, modulo, metodo, caminho, corpo, recurso, acao):
    client, state, perms, log = ctx
    perms.falha = RuntimeError("banco fora")
    _como(state, ADMIN_ID, "admin")
    resp = _chamar(client, metodo, caminho, corpo)
    assert resp.status_code == 503, resp.text
    assert log == []
