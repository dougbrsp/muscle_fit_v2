"""RBAC do alvo na nutricao (ADR 0011, lote 2D-1), por HTTP: planos, check-ins, dias, dashboard,
refeicoes, sugestao de plano, leitura por IA de texto e de foto.

O resolvedor REAL de permissoes roda sobre um banco falso de concessoes (`access_service._db`). As
concessoes dos papeis (usuario, personal trainer, admin) vem de `DEFAULT_GRANTS_BY_GROUP`; os
cenarios sinteticos montam o conjunto a mao. O banco de nutricao e uma conexao falsa que registra
se foi aberta e as instrucoes executadas: onde a negacao vem antes do banco, "nao conectou" e a
prova; nas rotas por id (a linha e lida antes de decidir), a prova e a ausencia de escrita.

Rodar: cd api && python -m pytest tests/unit/test_nutricao_alvo_permissoes_rbac.py
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import (
    nutrition_checkins,
    nutrition_dashboard,
    nutrition_days,
    nutrition_meal_parse,
    nutrition_meal_photo,
    nutrition_meals,
    nutrition_plan_suggestion,
    nutrition_plans,
)
from app import nutrition_ai_quota
from app.access import DEFAULT_GRANTS_BY_GROUP
from app.access import service as access_service
from app.access.service import FORBIDDEN_DETAIL
from app.security import Identity, get_current_identity

USUARIO, PT, ADMIN_ID, SEM_GRUPO, OUTRO = 7, 8, 1, 9, 99
SO_VER, SEM_EXCLUIR, CHECKIN_SEM_PLANO, DIARIO_PROPRIOS, PLANO_PROPRIOS, TUDO_TODOS, ALUNOS = (
    21, 22, 23, 24, 25, 26, 27,
)
MESTRE = Identity(user_id=ADMIN_ID, role="admin", source="master_token")

RECURSOS = ("nutricao.plano", "nutricao.checkin", "nutricao.diario", "ia.nutricao")
MODULOS = (
    nutrition_plans,
    nutrition_checkins,
    nutrition_days,
    nutrition_dashboard,
    nutrition_meals,
    nutrition_meal_parse,
    nutrition_meal_photo,
    nutrition_plan_suggestion,
)
NOMES_TABELAS = (
    "_require_plan_tables", "_require_tables", "require_diary_tables", "require_food_tables",
    "require_nutrition_tables", "require_plan_energy_columns",
)
NAO_ENCONTRADO = {"plan_not_found", "checkin_not_found"}

REFEICAO = {"local_date": "2026-11-01", "meal_slot": "lunch", "items": []}
PARSE = {"text": "arroz e feijao", "local_date": "2026-11-01"}
FOTO = {"image_base64": "AAAA", "local_date": "2026-11-01"}

# (modulo, metodo, caminho, corpo, recurso, acao)
ROTAS = [
    ("plans", "get", "/nutrition/plans", None, "nutricao.plano", "ver"),
    ("plans", "post", "/nutrition/plans/1/archive", None, "nutricao.plano", "editar"),
    ("plans", "delete", "/nutrition/plans/1", None, "nutricao.plano", "excluir"),
    ("checkins", "get", "/nutrition/checkins", None, "nutricao.checkin", "ver"),
    ("checkins", "put", "/nutrition/checkin/settings", {"weekday": 1}, "nutricao.checkin", "editar"),
    ("checkins", "post", "/nutrition/checkins/1/decline", None, "nutricao.checkin", "editar"),
    ("days", "get", "/nutrition/days/2026-11-01", None, "nutricao.diario", "ver"),
    ("days", "put", "/nutrition/days/2026-11-01/close", None, "nutricao.diario", "editar"),
    ("days", "delete", "/nutrition/days/2026-11-01/close", None, "nutricao.diario", "excluir"),
    ("dashboard", "get", "/nutrition/today", None, "nutricao.diario", "ver"),
    ("meals", "get", "/nutrition/meals/last-time", None, "nutricao.diario", "ver"),
    ("meals", "put", "/nutrition/meals/1", REFEICAO, "nutricao.diario", "editar"),
    ("meals", "delete", "/nutrition/meals/1", None, "nutricao.diario", "excluir"),
    ("suggestion", "get", "/nutrition/plan-suggestion/usage", None, "ia.nutricao", "ver"),
    ("suggestion", "post", "/nutrition/plan-suggestion", {}, "ia.nutricao", "editar"),
    ("parse", "get", "/nutrition/meals/parse/quota", None, "ia.nutricao", "ver"),
    ("parse", "post", "/nutrition/meals/parse", PARSE, "ia.nutricao", "editar"),
    ("photo", "get", "/nutrition/meals/photo/usage", None, "ia.nutricao", "ver"),
    ("photo", "post", "/nutrition/meals/photo", FOTO, "ia.nutricao", "editar"),
]
ESCRITAS = [r for r in ROTAS if r[1] != "get"]
DELETES = [r for r in ROTAS if r[1] == "delete"]
POST_PUT = [r for r in ROTAS if r[1] in ("post", "put")]
UMA_POR_MODULO = [next(r for r in ROTAS if r[0] == m) for m in dict.fromkeys(r[0] for r in ROTAS)]

# Rotas com alvo (`user_id`): (nome, metodo, caminho, corpo_base, onde, recurso, acao)
ALVOS = [
    ("plano", "get", "/nutrition/plans", None, "query", "nutricao.plano", "ver"),
    ("checkin", "get", "/nutrition/checkin", None, "query", "nutricao.checkin", "ver"),
    ("checkin-escrita", "put", "/nutrition/checkin/settings", {"weekday": 1}, "body", "nutricao.checkin", "editar"),
    ("diario", "get", "/nutrition/days/2026-11-01", None, "query", "nutricao.diario", "ver"),
    ("diario-escrita", "post", "/nutrition/meals", REFEICAO, "body", "nutricao.diario", "editar"),
    ("dashboard", "get", "/nutrition/today", None, "query", "nutricao.diario", "ver"),
    ("ia-parse", "post", "/nutrition/meals/parse", PARSE, "body", "ia.nutricao", "editar"),
    ("ia-foto", "post", "/nutrition/meals/photo", FOTO, "body", "ia.nutricao", "editar"),
    ("ia-sugestao", "post", "/nutrition/plan-suggestion", {}, "body", "ia.nutricao", "editar"),
]


def _ids(rotas):
    return [f"{r[1].upper()} {r[2]}" for r in rotas]


def _ids_alvo(rotas):
    return [r[0] for r in rotas]


class FakePermissoes:
    def __init__(self, falha=None):
        self.falha = falha
        self.falha_vinculo = None
        self.consultas = []
        self.vinculos_consultados = []
        self.vinculos = set()  # {(trainer, student)}
        self.por_usuario = {
            USUARIO: self._grupo("usuario"),
            PT: self._grupo("personal_trainer"),
            ADMIN_ID: self._grupo("admin"),
            SO_VER: self._sint([(r, "ver", "todos") for r in RECURSOS]),
            SEM_EXCLUIR: self._sint([(r, a, "todos") for r in RECURSOS for a in ("ver", "editar")]),
            CHECKIN_SEM_PLANO: self._sint([("nutricao.checkin", a, "todos") for a in ("ver", "editar")]),
            DIARIO_PROPRIOS: self._sint(
                [("ia.nutricao", "editar", "todos"), ("nutricao.diario", "editar", "proprios"),
                 ("nutricao.plano", "editar", "todos")]
            ),
            PLANO_PROPRIOS: self._sint(
                [("ia.nutricao", "editar", "todos"), ("nutricao.plano", "editar", "proprios"),
                 ("nutricao.diario", "editar", "todos")]
            ),
            TUDO_TODOS: self._sint(
                [("ia.nutricao", "editar", "todos"), ("nutricao.plano", "editar", "todos"),
                 ("nutricao.diario", "editar", "todos")]
            ),
            ALUNOS: self._sint([(r, a, "alunos") for r in RECURSOS for a in ("ver", "editar", "excluir")]),
        }  # SEM_GRUPO ausente = usuario sem grupo

    @staticmethod
    def _grupo(grupo):
        grants = DEFAULT_GRANTS_BY_GROUP[grupo]
        if not grants:
            return [{"group_key": grupo, "is_system": 1, "resource": None, "action": None, "scope": None}]
        return [{"group_key": grupo, "is_system": 1, "resource": r, "action": a, "scope": s} for r, a, s in grants]

    @staticmethod
    def _sint(triplas):
        return [
            {"group_key": "sint", "is_system": 0, "resource": r, "action": a, "scope": s} for r, a, s in triplas
        ]

    def fetch_rows(self, sql, params=()):
        if len(params) == 2:  # consulta de vinculo (trainer_students)
            self.vinculos_consultados.append(tuple(params))
            if self.falha_vinculo is not None:
                raise self.falha_vinculo
            return [{"x": 1}] if tuple(params) in self.vinculos else []
        self.consultas.append(tuple(params))
        if self.falha is not None:
            raise self.falha
        return list(self.por_usuario.get(params[0], []))


class FakeNutricao:
    """Conexao/cursor falsos: toda leitura por id devolve uma linha cujo dono e `dono`."""

    def __init__(self):
        self.conectou = False
        self.sql: list[str] = []
        self.dono = OUTRO

    def __call__(self):
        self.conectou = True
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))

    def executemany(self, sql, seq):
        self.sql.append(" ".join(sql.split()))

    def fetchone(self):
        return {"id": 1, "user_id": self.dono, "status": "proposed", "username": "u", "full_name": "U",
                "source_plan_id": 1}

    def fetchall(self):
        return []

    @property
    def escritas(self):
        return [s for s in self.sql if s.split(" ", 1)[0].upper() in ("INSERT", "UPDATE", "DELETE")]


@pytest.fixture
def ctx(monkeypatch):
    perms = FakePermissoes()
    nutri = FakeNutricao()
    cota: list[dict] = []
    publicados: list[str] = []
    monkeypatch.setattr(access_service, "_db", perms)

    def _open_usage(**kw):
        cota.append(kw)
        raise HTTPException(status_code=418, detail={"error": "parou_apos_cota"})

    monkeypatch.setattr(nutrition_ai_quota, "open_usage", _open_usage)
    monkeypatch.setattr(nutrition_plan_suggestion, "_incomplete_subject", lambda *a, **k: None)
    for nome in ("publish_draft", "insert_draft"):
        monkeypatch.setattr(nutrition_checkins, nome, lambda *a, _n=nome, **k: publicados.append(_n), raising=False)
    app = FastAPI()
    for modulo in MODULOS:
        monkeypatch.setattr(modulo, "get_conn", nutri)
        for nome in NOMES_TABELAS:
            monkeypatch.setattr(modulo, nome, lambda *a, **k: None, raising=False)
        app.include_router(modulo.router)
    state = {"identity": Identity(user_id=USUARIO, role="regular", source="session")}
    app.dependency_overrides[get_current_identity] = lambda: state["identity"]
    client = TestClient(app, raise_server_exceptions=False)
    return client, state, perms, nutri, cota, publicados


def _como(state, uid, role="regular", source="session"):
    state["identity"] = Identity(user_id=uid, role=role, source=source, device_id=3 if source == "device" else None)


def _chamar(client, metodo, caminho, corpo=None, user_id=None, onde="query"):
    params = {}
    if user_id is not None and onde == "query":
        params["user_id"] = user_id
    if user_id is not None and onde == "body":
        corpo = {**(corpo or {}), "user_id": user_id}
    if corpo is not None:
        return getattr(client, metodo)(caminho, json=corpo, params=params)
    return getattr(client, metodo)(caminho, params=params)


def _erro(resp):
    try:
        detalhe = resp.json().get("detail")
    except ValueError:  # corpo vazio/texto (500 do fake apos a guarda)
        return None
    return detalhe.get("error") if isinstance(detalhe, dict) else None


def _negado(resp):
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == FORBIDDEN_DETAIL


def _liberou(resp):
    """Passou da guarda: nada de 401/403 nem 404 de autorizacao, e o 503 so se for de tabela de
    migracao ausente (negocio, nao o 503 do resolvedor); erros de negocio ou do fake (422, 500)
    sao irrelevantes aqui."""
    if resp.status_code == 503 and not str(_erro(resp)).endswith("_missing"):
        return False
    return resp.status_code not in (401, 403) and _erro(resp) not in NAO_ENCONTRADO


def _chegou_ao_banco(nome, nutri):
    """A leitura de foto rejeita a imagem de teste antes de abrir o banco; nela a prova e so o
    status da guarda."""
    return nutri.conectou or nome == "ia-foto"


# ---------------------------------------------------------------------------
# (a) usuario comum: proprios dados sim, terceiro 403
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("onde_id", ["omitido", "proprio"])
@pytest.mark.parametrize("nome,metodo,caminho,corpo,onde,recurso,acao", ALVOS, ids=_ids_alvo(ALVOS))
def test_usuario_comum_alcanca_os_proprios_dados(ctx, onde_id, nome, metodo, caminho, corpo, onde, recurso, acao):
    client, state, perms, nutri, _, _ = ctx
    _como(state, USUARIO)
    uid = USUARIO if onde_id == "proprio" else None
    resp = _chamar(client, metodo, caminho, corpo, uid, onde)
    assert _liberou(resp), (resp.status_code, resp.text)
    assert _chegou_ao_banco(nome, nutri)
    assert perms.vinculos_consultados == []


@pytest.mark.parametrize("nome,metodo,caminho,corpo,onde,recurso,acao", ALVOS, ids=_ids_alvo(ALVOS))
def test_usuario_comum_com_user_id_de_terceiro_recebe_403_antes_do_banco(
    ctx, nome, metodo, caminho, corpo, onde, recurso, acao
):
    client, state, _, nutri, cota, _ = ctx
    _como(state, USUARIO)
    _negado(_chamar(client, metodo, caminho, corpo, OUTRO, onde))
    assert not nutri.conectou
    assert cota == []


# ---------------------------------------------------------------------------
# (b) personal trainer e admin alcancam terceiro
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("uid,role,vinculo", [(PT, "personal_trainer", True), (ADMIN_ID, "admin", False)], ids=["pt-com-vinculo", "admin"])
@pytest.mark.parametrize("nome,metodo,caminho,corpo,onde,recurso,acao", ALVOS, ids=_ids_alvo(ALVOS))
def test_pt_com_vinculo_e_admin_alcancam_terceiro(ctx, uid, role, vinculo, nome, metodo, caminho, corpo, onde, recurso, acao):
    """Lote 5A: PT so alcanca aluno com vinculo ativo (escopo `alunos`); admin segue em `todos`."""
    client, state, perms, nutri, _, _ = ctx
    if vinculo:
        perms.vinculos.add((uid, OUTRO))
    _como(state, uid, role)
    resp = _chamar(client, metodo, caminho, corpo, OUTRO, onde)
    assert _liberou(resp), (resp.status_code, resp.text)
    assert _chegou_ao_banco(nome, nutri)
    assert ((uid, OUTRO) in perms.vinculos_consultados) is vinculo


@pytest.mark.parametrize("nome,metodo,caminho,corpo,onde,recurso,acao", ALVOS, ids=_ids_alvo(ALVOS))
def test_pt_sem_vinculo_ativo_e_negado_em_terceiro(ctx, nome, metodo, caminho, corpo, onde, recurso, acao):
    client, state, perms, nutri, cota, _ = ctx
    _como(state, PT, "personal_trainer")
    _negado(_chamar(client, metodo, caminho, corpo, OUTRO, onde))
    assert (PT, OUTRO) in perms.vinculos_consultados
    assert not nutri.conectou
    assert cota == []


# ---------------------------------------------------------------------------
# (c) rotas por id de recurso alheio
# ---------------------------------------------------------------------------

POR_ID = [
    ("plano-archive", "post", "/nutrition/plans/5/archive", None, 404, "plan_not_found"),
    ("plano-delete", "delete", "/nutrition/plans/5", None, 404, "plan_not_found"),
    ("checkin-decline", "post", "/nutrition/checkins/5/decline", None, 404, "checkin_not_found"),
    ("checkin-accept", "post", "/nutrition/checkins/5/accept", None, 404, "checkin_not_found"),
    ("refeicao-put", "put", "/nutrition/meals/5", REFEICAO, 403, None),
    ("refeicao-delete", "delete", "/nutrition/meals/5", None, 403, None),
]


@pytest.mark.parametrize("nome,metodo,caminho,corpo,status,erro", POR_ID, ids=[p[0] for p in POR_ID])
def test_recurso_por_id_de_terceiro_para_usuario_comum(ctx, nome, metodo, caminho, corpo, status, erro):
    client, state, _, nutri, _, publicados = ctx
    _como(state, USUARIO)
    nutri.dono = OUTRO
    resp = _chamar(client, metodo, caminho, corpo)
    assert resp.status_code == status, resp.text
    if erro is None:
        assert resp.json()["detail"] == FORBIDDEN_DETAIL
    else:
        assert _erro(resp) == erro
    assert nutri.escritas == []
    assert publicados == []


@pytest.mark.parametrize("nome,metodo,caminho,corpo,status,erro", POR_ID, ids=[p[0] for p in POR_ID])
def test_recurso_por_id_proprio_passa_a_guarda(ctx, nome, metodo, caminho, corpo, status, erro):
    client, state, _, nutri, _, _ = ctx
    _como(state, USUARIO)
    nutri.dono = USUARIO
    resp = _chamar(client, metodo, caminho, corpo)
    assert _liberou(resp), (resp.status_code, resp.text)


@pytest.mark.parametrize("nome,metodo,caminho,corpo,status,erro", POR_ID, ids=[p[0] for p in POR_ID])
def test_recurso_por_id_de_terceiro_pt_com_vinculo_e_admin_passam_a_guarda(ctx, nome, metodo, caminho, corpo, status, erro):
    client, state, perms, nutri, _, _ = ctx
    perms.vinculos.add((PT, OUTRO))
    nutri.dono = OUTRO
    for uid, role in ((PT, "personal_trainer"), (ADMIN_ID, "admin")):
        _como(state, uid, role)
        resp = _chamar(client, metodo, caminho, corpo)
        assert _liberou(resp), (uid, resp.status_code, resp.text)


@pytest.mark.parametrize("nome,metodo,caminho,corpo,status,erro", POR_ID, ids=[p[0] for p in POR_ID])
def test_recurso_por_id_de_terceiro_pt_sem_vinculo_e_negado(ctx, nome, metodo, caminho, corpo, status, erro):
    client, state, _, nutri, _, publicados = ctx
    _como(state, PT, "personal_trainer")
    nutri.dono = OUTRO
    resp = _chamar(client, metodo, caminho, corpo)
    assert resp.status_code == status, resp.text
    if erro is None:
        assert resp.json()["detail"] == FORBIDDEN_DETAIL
    else:
        assert _erro(resp) == erro
    assert nutri.escritas == []
    assert publicados == []


def test_excluir_refeicao_propria_executa_o_delete(ctx):
    client, state, _, nutri, _, _ = ctx
    _como(state, USUARIO)
    nutri.dono = USUARIO
    resp = client.delete("/nutrition/meals/5")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "deleted", "id": 5}
    assert any(s.upper().startswith("DELETE") for s in nutri.sql)


# ---------------------------------------------------------------------------
# (d) user_id 0 e negativo: 400 invalid_target antes de qualquer consulta de nutricao
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("uid,role", [(USUARIO, "regular"), (PT, "personal_trainer"), (ADMIN_ID, "admin")])
@pytest.mark.parametrize("alvo_invalido", [0, -1])
@pytest.mark.parametrize("nome,metodo,caminho,corpo,onde,recurso,acao", ALVOS, ids=_ids_alvo(ALVOS))
def test_user_id_zero_ou_negativo_e_400_invalid_target(
    ctx, uid, role, alvo_invalido, nome, metodo, caminho, corpo, onde, recurso, acao
):
    client, state, _, nutri, cota, _ = ctx
    _como(state, uid, role)
    resp = _chamar(client, metodo, caminho, corpo, alvo_invalido, onde)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["error"] == "invalid_target"
    assert not nutri.conectou
    assert cota == []


# ---------------------------------------------------------------------------
# (e) so `ver` nega escritas; sem `excluir` nega DELETE
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("modulo,metodo,caminho,corpo,recurso,acao", ESCRITAS, ids=_ids(ESCRITAS))
def test_grupo_so_com_ver_e_negado_nas_escritas(ctx, modulo, metodo, caminho, corpo, recurso, acao):
    client, state, _, nutri, cota, _ = ctx
    _como(state, SO_VER)
    _negado(_chamar(client, metodo, caminho, corpo))
    assert not nutri.conectou
    assert cota == []


@pytest.mark.parametrize("modulo,metodo,caminho,corpo,recurso,acao", ROTAS, ids=_ids(ROTAS))
def test_grupo_so_com_ver_le_apenas_o_que_ver_cobre(ctx, modulo, metodo, caminho, corpo, recurso, acao):
    client, state, _, _, _, _ = ctx
    _como(state, SO_VER)
    resp = _chamar(client, metodo, caminho, corpo)
    if acao == "ver":
        assert _liberou(resp), resp.text
    else:
        _negado(resp)


@pytest.mark.parametrize("modulo,metodo,caminho,corpo,recurso,acao", DELETES, ids=_ids(DELETES))
def test_grupo_sem_excluir_e_negado_nos_delete(ctx, modulo, metodo, caminho, corpo, recurso, acao):
    client, state, _, nutri, _, _ = ctx
    _como(state, SEM_EXCLUIR)
    _negado(_chamar(client, metodo, caminho, corpo))
    assert not nutri.conectou


@pytest.mark.parametrize("modulo,metodo,caminho,corpo,recurso,acao", POST_PUT, ids=_ids(POST_PUT))
def test_grupo_sem_excluir_ainda_edita(ctx, modulo, metodo, caminho, corpo, recurso, acao):
    client, state, _, _, _, _ = ctx
    _como(state, SEM_EXCLUIR)
    resp = _chamar(client, metodo, caminho, corpo)
    assert _liberou(resp), resp.text


# ---------------------------------------------------------------------------
# (f) IA com alvo: o recurso do alvo tambem precisa de escopo, sem reservar cota
# ---------------------------------------------------------------------------

IA_ALVO = [
    ("parse", "/nutrition/meals/parse", PARSE, DIARIO_PROPRIOS),
    ("photo", "/nutrition/meals/photo", FOTO, DIARIO_PROPRIOS),
    ("sugestao", "/nutrition/plan-suggestion", {}, PLANO_PROPRIOS),
]


@pytest.mark.parametrize("nome,caminho,corpo,uid", IA_ALVO, ids=[i[0] for i in IA_ALVO])
def test_ia_todos_mas_recurso_do_alvo_proprios_nega_terceiro_sem_cota(ctx, nome, caminho, corpo, uid):
    client, state, _, nutri, cota, _ = ctx
    _como(state, uid)
    _negado(client.post(caminho, json={**corpo, "user_id": OUTRO}))
    assert cota == []
    assert not nutri.conectou


@pytest.mark.parametrize("nome,caminho,corpo,uid", IA_ALVO[:1] + IA_ALVO[2:], ids=["parse", "sugestao"])
def test_ia_com_recurso_do_alvo_proprios_ainda_atende_o_proprio_usuario(ctx, nome, caminho, corpo, uid):
    client, state, _, _, cota, _ = ctx
    _como(state, uid)
    client.post(caminho, json=corpo)
    assert len(cota) == 1 and cota[0]["user_id"] == uid


@pytest.mark.parametrize("nome,caminho,corpo,uid", IA_ALVO[:1] + IA_ALVO[2:], ids=["parse", "sugestao"])
def test_ia_com_todos_nos_dois_recursos_reserva_a_cota_de_quem_pede(ctx, nome, caminho, corpo, uid):
    client, state, _, _, cota, _ = ctx
    _como(state, TUDO_TODOS)
    resp = client.post(caminho, json={**corpo, "user_id": OUTRO})
    assert resp.status_code == 418, resp.text  # parou na cota: a guarda liberou
    assert len(cota) == 1 and cota[0]["user_id"] == TUDO_TODOS


# ---------------------------------------------------------------------------
# (g) accept de check-in exige tambem nutricao.plano/editar sobre o dono
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dono", [OUTRO, CHECKIN_SEM_PLANO], ids=["dono-terceiro", "dono-proprio"])
def test_accept_sem_nutricao_plano_e_403_e_nada_e_publicado(ctx, dono):
    client, state, _, nutri, _, publicados = ctx
    _como(state, CHECKIN_SEM_PLANO)
    nutri.dono = dono
    _negado(client.post("/nutrition/checkins/5/accept"))
    assert nutri.escritas == []
    assert publicados == []


def test_accept_de_checkin_alheio_sem_nutricao_checkin_e_404_antes_do_403_de_plano(ctx):
    client, state, _, nutri, _, publicados = ctx
    _como(state, USUARIO)
    nutri.dono = OUTRO
    resp = client.post("/nutrition/checkins/5/accept")
    assert resp.status_code == 404 and _erro(resp) == "checkin_not_found"
    assert publicados == [] and nutri.escritas == []


# ---------------------------------------------------------------------------
# (h) escopo `alunos`: terceiro so com vinculo ativo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("nome,metodo,caminho,corpo,onde,recurso,acao", ALVOS, ids=_ids_alvo(ALVOS))
def test_alunos_sem_vinculo_ativo_e_403(ctx, nome, metodo, caminho, corpo, onde, recurso, acao):
    client, state, perms, nutri, cota, _ = ctx
    _como(state, ALUNOS)
    _negado(_chamar(client, metodo, caminho, corpo, OUTRO, onde))
    assert (ALUNOS, OUTRO) in perms.vinculos_consultados
    assert not nutri.conectou
    assert cota == []


@pytest.mark.parametrize("nome,metodo,caminho,corpo,onde,recurso,acao", ALVOS, ids=_ids_alvo(ALVOS))
def test_alunos_com_vinculo_ativo_passa(ctx, nome, metodo, caminho, corpo, onde, recurso, acao):
    client, state, perms, nutri, _, _ = ctx
    perms.vinculos.add((ALUNOS, OUTRO))
    _como(state, ALUNOS)
    resp = _chamar(client, metodo, caminho, corpo, OUTRO, onde)
    assert _liberou(resp), (resp.status_code, resp.text)
    assert (ALUNOS, OUTRO) in perms.vinculos_consultados
    assert _chegou_ao_banco(nome, nutri)


def test_alunos_com_vinculo_a_outro_aluno_nao_alcanca_este(ctx):
    client, state, perms, nutri, _, _ = ctx
    perms.vinculos.add((ALUNOS, OUTRO + 1))
    _como(state, ALUNOS)
    _negado(client.get("/nutrition/plans", params={"user_id": OUTRO}))
    assert not nutri.conectou


def test_alunos_no_proprio_dado_nao_consulta_vinculo(ctx):
    client, state, perms, nutri, _, _ = ctx
    _como(state, ALUNOS)
    assert _liberou(client.get("/nutrition/plans", params={"user_id": ALUNOS}))
    assert perms.vinculos_consultados == []


def test_alunos_por_id_sem_vinculo_plano_e_checkin_viram_404(ctx):
    client, state, _, nutri, _, _ = ctx
    _como(state, ALUNOS)
    nutri.dono = OUTRO
    resp = client.post("/nutrition/plans/5/archive")
    assert resp.status_code == 404 and _erro(resp) == "plan_not_found"
    resp = client.post("/nutrition/checkins/5/decline")
    assert resp.status_code == 404 and _erro(resp) == "checkin_not_found"
    assert nutri.escritas == []


# ---------------------------------------------------------------------------
# (i) sem grupo, token mestre, dispositivo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("modulo,metodo,caminho,corpo,recurso,acao", UMA_POR_MODULO, ids=_ids(UMA_POR_MODULO))
def test_usuario_sem_grupo_e_negado_em_cada_modulo(ctx, modulo, metodo, caminho, corpo, recurso, acao):
    client, state, perms, nutri, cota, _ = ctx
    _como(state, SEM_GRUPO)
    _negado(_chamar(client, metodo, caminho, corpo))
    assert perms.consultas == [(SEM_GRUPO,)]
    assert not nutri.conectou
    assert cota == []


def test_cobertura_um_caso_por_modulo_sao_8_modulos(ctx):
    assert len(UMA_POR_MODULO) == 8


@pytest.mark.parametrize("modulo,metodo,caminho,corpo,recurso,acao", ROTAS, ids=_ids(ROTAS))
def test_token_mestre_passa_sem_consultar_o_banco_de_permissoes(ctx, modulo, metodo, caminho, corpo, recurso, acao):
    client, state, perms, _, _, _ = ctx
    state["identity"] = MESTRE
    resp = _chamar(client, metodo, caminho, corpo)
    assert _liberou(resp), resp.text
    assert perms.consultas == [] and perms.vinculos_consultados == []


@pytest.mark.parametrize("nome,metodo,caminho,corpo,onde,recurso,acao", ALVOS, ids=_ids_alvo(ALVOS))
def test_token_mestre_alcanca_terceiro_sem_consultar_permissoes(ctx, nome, metodo, caminho, corpo, onde, recurso, acao):
    client, state, perms, nutri, _, _ = ctx
    state["identity"] = MESTRE
    assert _liberou(_chamar(client, metodo, caminho, corpo, OUTRO, onde))
    assert _chegou_ao_banco(nome, nutri) and perms.consultas == []


@pytest.mark.parametrize("nome,metodo,caminho,corpo,onde,recurso,acao", ALVOS, ids=_ids_alvo(ALVOS))
def test_dispositivo_herda_as_concessoes_do_dono_usuario(ctx, nome, metodo, caminho, corpo, onde, recurso, acao):
    client, state, perms, nutri, _, _ = ctx
    _como(state, USUARIO, source="device")
    assert _liberou(_chamar(client, metodo, caminho, corpo, None, onde))
    assert perms.consultas[0] == (USUARIO,)
    nutri.conectou = False
    _negado(_chamar(client, metodo, caminho, corpo, OUTRO, onde))
    assert not nutri.conectou


@pytest.mark.parametrize("nome,metodo,caminho,corpo,onde,recurso,acao", ALVOS, ids=_ids_alvo(ALVOS))
def test_dispositivo_de_pt_herda_o_acesso_a_aluno_com_vinculo(ctx, nome, metodo, caminho, corpo, onde, recurso, acao):
    client, state, perms, nutri, _, _ = ctx
    perms.vinculos.add((PT, OUTRO))
    _como(state, PT, "personal_trainer", source="device")
    assert _liberou(_chamar(client, metodo, caminho, corpo, OUTRO, onde))
    assert perms.consultas[0] == (PT,)
    nutri.conectou = False
    perms.vinculos.clear()
    _negado(_chamar(client, metodo, caminho, corpo, OUTRO, onde))
    assert not nutri.conectou


def test_dispositivo_sem_grupo_e_negado(ctx):
    client, state, _, nutri, _, _ = ctx
    _como(state, SEM_GRUPO, source="device")
    _negado(client.get("/nutrition/plans"))
    assert not nutri.conectou


# ---------------------------------------------------------------------------
# (j) falha de banco na resolucao: 503, nunca concede
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("modulo,metodo,caminho,corpo,recurso,acao", ROTAS, ids=_ids(ROTAS))
def test_falha_do_banco_na_resolucao_e_503_nunca_concede(ctx, modulo, metodo, caminho, corpo, recurso, acao):
    client, state, perms, nutri, cota, _ = ctx
    perms.falha = RuntimeError("banco fora")
    _como(state, ADMIN_ID, "admin")
    resp = _chamar(client, metodo, caminho, corpo)
    assert resp.status_code == 503, resp.text
    assert not nutri.conectou
    assert cota == []


def test_falha_do_banco_ao_consultar_o_vinculo_e_503(ctx):
    client, state, perms, nutri, _, _ = ctx
    perms.falha_vinculo = RuntimeError("banco fora")
    _como(state, ALUNOS)
    resp = client.get("/nutrition/plans", params={"user_id": OUTRO})
    assert resp.status_code == 503, resp.text
    assert not nutri.conectou
