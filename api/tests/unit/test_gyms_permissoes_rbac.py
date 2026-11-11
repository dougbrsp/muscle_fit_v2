"""RBAC de `gyms.py` (ADR 0011, lote 2C-2), por HTTP: `treino.academias`, `admin.catalogos`
e `exercicios.catalogo`, mais a posse da linha (dono da academia ou escopo `todos`).

A identidade real e trocada em `security.resolve_identity`; o resolvedor REAL de permissoes
roda sobre um banco falso de concessoes (linhas no formato real). As concessoes dos papeis
vem de `DEFAULT_GRANTS_BY_GROUP`; so os cenarios sinteticos montam o conjunto a mao.

O banco de negocio e um duble em memoria que PROJETA colunas: o SELECT devolve apenas as
colunas que pede (um handler que leia coluna nao selecionada quebra com 500).

Rodar: cd api && python -m pytest tests/unit/test_gyms_permissoes_rbac.py
"""

from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import gyms, security
from app.access import DEFAULT_GRANTS_BY_GROUP
from app.access import service as access_service
from app.access.service import FORBIDDEN_DETAIL
from app.security import Identity

DONO, TERCEIRO, PT, ADMIN_ID, SEM_GRUPO, SO_EDITAR, CAT_EDITAR = 10, 11, 12, 1, 13, 14, 15

IDENTIDADES = {
    "dono": Identity(user_id=DONO, role="regular", source="session"),
    "terceiro": Identity(user_id=TERCEIRO, role="regular", source="session"),
    "pt": Identity(user_id=PT, role="personal_trainer", source="session"),
    "admin": Identity(user_id=ADMIN_ID, role="admin", source="session"),
    "sem_grupo": Identity(user_id=SEM_GRUPO, role="regular", source="session"),
    "so_editar": Identity(user_id=SO_EDITAR, role="regular", source="session"),
    "cat_editar": Identity(user_id=CAT_EDITAR, role="regular", source="session"),
    "mestre": Identity(user_id=ADMIN_ID, role="admin", source="master_token"),
    "disp_dono": Identity(user_id=DONO, role="regular", source="device", device_id=3),
    "disp_terceiro": Identity(user_id=TERCEIRO, role="regular", source="device", device_id=4),
    "disp_sem_grupo": Identity(user_id=SEM_GRUPO, role="regular", source="device", device_id=5),
}


def _linhas_do_grupo(grupo):
    grants = DEFAULT_GRANTS_BY_GROUP[grupo]
    if not grants:
        return [{"group_key": grupo, "is_system": 1, "resource": None, "action": None, "scope": None}]
    return [
        {"group_key": grupo, "is_system": 1, "resource": r, "action": a, "scope": s}
        for r, a, s in grants
    ]


def _linhas_sinteticas(grants):
    return [{"group_key": "sint", "is_system": 0, "resource": r, "action": a, "scope": s} for r, a, s in grants]


class FakePermissoes:
    def __init__(self, falha=None):
        self.falha = falha
        self.consultas = []
        self.por_usuario = {
            DONO: _linhas_do_grupo("usuario"),
            TERCEIRO: _linhas_do_grupo("usuario"),
            PT: _linhas_do_grupo("personal_trainer"),
            ADMIN_ID: _linhas_do_grupo("admin"),
            SO_EDITAR: _linhas_sinteticas([("treino.academias", "editar", "proprios"), ("treino.academias", "ver", "proprios")]),
            CAT_EDITAR: _linhas_sinteticas(
                [("admin.catalogos", "editar", "todos"), ("treino.academias", "ver", "proprios")]
            ),
        }  # SEM_GRUPO ausente = usuario sem grupo

    def fetch_rows(self, sql, params=()):
        self.consultas.append(tuple(params))
        if self.falha is not None:
            raise self.falha
        return list(self.por_usuario.get(params[0], []))


GYM_BASE = {"logo_path": None, "name": "Academia", "is_active": 1, "opens_at": None, "closes_at": None,
            "usual_weekdays": None, "is_recurring": 1, "notes": None, "created_at": None, "updated_at": None}
TABELAS = {
    "gyms": [
        {**GYM_BASE, "id": 5, "owner_user_id": DONO},
        {**GYM_BASE, "id": 6, "owner_user_id": None},
        {**GYM_BASE, "id": 7, "owner_user_id": TERCEIRO},
    ],
    "gym_equipment": [
        {"id": 77, "gym_id": 5, "mapping_id": 3, "equipment_id": 9, "is_active": 1},
        {"id": 78, "gym_id": 6, "mapping_id": 3, "equipment_id": 9, "is_active": 1},
    ],
    "gym_equipment_catalog": [
        {"id": 88, "gym_id": 5, "equipment_id": 9, "is_active": 1},
        {"id": 89, "gym_id": 6, "equipment_id": 9, "is_active": 1},
    ],
    "equipment": [
        {"id": 9, "name": "Leg press", "notes": None, "is_active": 1, "equipment_type_id": None,
         "manufacturer": None, "model": None, "location": None, "photo_path": None},
    ],
}
_SELECT = re.compile(r"^SELECT (?P<cols>.+?) FROM (?P<tabela>\w+)(?: (?!WHERE|ORDER)\w+)?(?: WHERE (?P<col>\w+) = %s)?")


class _Cursor:
    def __init__(self):
        self.executed = []
        self.rowcount = 1
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.executed.append((flat, params))
        self._rows = []
        if not flat.startswith("SELECT"):
            return
        m = _SELECT.match(flat)
        if not m or m["tabela"] not in TABELAS:
            return
        linhas = TABELAS[m["tabela"]]
        if m["col"]:
            if m["col"] == "name":  # leitura de volta apos INSERT ... ON DUPLICATE KEY
                linhas = linhas[:1]
            else:
                linhas = [r for r in linhas if r.get(m["col"]) == params[0]]
        cols = m["cols"]
        if cols.strip() == "*":
            self._rows = [dict(r) for r in linhas]
        else:
            nomes = [c.strip().split(" AS ")[0].split(".")[-1] for c in cols.split(",")]
            self._rows = [{n: r[n] for n in nomes if n in r} for r in linhas]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def writes(self):
        return [sql for sql, _ in self.executed if sql.startswith(("INSERT", "UPDATE", "DELETE"))]


class _Conn:
    def __init__(self, cur):
        self.cur = cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self.cur


@pytest.fixture
def ctx(monkeypatch):
    cur = _Cursor()
    perms = FakePermissoes()
    monkeypatch.setattr(gyms, "_connect", lambda: _Conn(cur))
    monkeypatch.setattr(gyms, "sync_gym_equipment_for_gym", lambda conn, gym_id: None)
    monkeypatch.setattr(gyms, "_save_photo", lambda *a, **k: "gyms/5.png")
    monkeypatch.setattr(security, "resolve_identity", lambda token: IDENTIDADES[token])
    monkeypatch.setattr(access_service, "_db", perms)
    app = FastAPI()
    app.include_router(gyms.router)
    return TestClient(app, raise_server_exceptions=False), cur, perms


def _h(quem):
    return {"X-API-Token": quem}


def _chamar(client, quem, metodo, caminho, corpo=None):
    kw = {"headers": _h(quem)}
    if corpo is not None:
        kw["json"] = corpo
    return client.request(metodo, caminho, **kw)


def _foto():
    return {"file": ("f.png", b"\x89PNG\r\n", "image/png")}


def _negado(resp):
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == FORBIDDEN_DETAIL


# ---------------------------------------------------------------------------
# Posse da linha em /gyms
# ---------------------------------------------------------------------------


def test_dono_edita_a_propria_academia(ctx):
    client, cur, _ = ctx
    resp = _chamar(client, "dono", "PUT", "/gyms/5", {"notes": "novo", "is_recurring": True})
    assert resp.status_code == 200, resp.text
    assert any(sql.startswith("UPDATE gyms") for sql in cur.writes())


def test_dono_desativa_a_propria_academia(ctx):
    client, cur, _ = ctx
    resp = _chamar(client, "dono", "DELETE", "/gyms/5")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "ok", "id": 5, "is_active": False}
    assert cur.writes() == ["UPDATE gyms SET is_active = 0 WHERE id = %s"]


@pytest.mark.parametrize("quem", ["terceiro", "pt", "disp_terceiro"])
@pytest.mark.parametrize(
    "metodo,corpo", [("PUT", {"notes": "invasao"}), ("DELETE", None)]
)
def test_terceiro_recebe_404_em_put_e_delete_de_gyms(ctx, quem, metodo, corpo):
    client, cur, _ = ctx
    resp = _chamar(client, quem, metodo, "/gyms/5", corpo)
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Academia não encontrada."
    assert cur.writes() == []


@pytest.mark.parametrize("metodo,corpo", [("PUT", {"notes": "x"}), ("DELETE", None)])
def test_academia_inexistente_da_404_e_nao_grava(ctx, metodo, corpo):
    client, cur, _ = ctx
    resp = _chamar(client, "admin", metodo, "/gyms/999", corpo)
    assert resp.status_code == 404
    assert cur.writes() == []


@pytest.mark.parametrize("metodo,corpo", [("PUT", {"notes": "x"}), ("DELETE", None)])
def test_academia_orfa_so_com_escopo_todos(ctx, metodo, corpo):
    client, cur, _ = ctx
    for quem in ("dono", "terceiro", "pt"):
        resp = _chamar(client, quem, metodo, "/gyms/6", corpo)
        assert resp.status_code == 404, (quem, resp.text)
    assert cur.writes() == []
    assert _chamar(client, "admin", metodo, "/gyms/6", corpo).status_code == 200
    assert cur.writes()


def test_pt_se_comporta_como_usuario_comum_em_gyms(ctx):
    client, cur, _ = ctx
    # propria: passa; de terceiro: 404; campo admin-only: 403.
    assert _chamar(client, "pt", "PUT", "/gyms/7", {"notes": "x"}).status_code == 404
    assert cur.writes() == []
    _negado(_chamar(client, "pt", "PUT", "/gyms/7", {"name": "Novo"}))
    assert cur.writes() == []
    # o PT cria academia propria (mesma concessao de usuario) e ve a de terceiros.
    assert _chamar(client, "pt", "GET", "/gyms").status_code == 200


def test_leitura_de_gyms_e_compartilhada(ctx):
    client, _, _ = ctx
    for quem in ("terceiro", "pt", "dono", "disp_terceiro"):
        resp = _chamar(client, quem, "GET", "/gyms")
        assert resp.status_code == 200, resp.text
        assert {g["id"] for g in resp.json()["items"]} >= {5, 6, 7}
    # por id: usuario ve academia de terceiro
    resp = _chamar(client, "terceiro", "GET", "/gyms/5")
    assert resp.status_code == 200
    assert resp.json()["item"]["owner_user_id"] == DONO


@pytest.mark.parametrize("quem", ["dono", "terceiro", "pt", "admin"])
def test_post_gyms_cria_com_o_dono_da_identidade(ctx, quem):
    client, cur, _ = ctx
    resp = _chamar(client, quem, "POST", "/gyms", {"name": "Nova"})
    assert resp.status_code == 200, resp.text
    insert = [(sql, p) for sql, p in cur.executed if sql.startswith("INSERT INTO gyms")]
    assert insert and insert[0][1][0] == IDENTIDADES[quem].user_id


# ---------------------------------------------------------------------------
# Campos admin-only no PUT /gyms/{id}
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "corpo",
    [{"name": "Outro nome"}, {"opens_at": "06:00"}, {"closes_at": "22:00"}, {"opens_at": None}],
    ids=["name", "opens_at", "closes_at", "opens_at_nulo"],
)
def test_dono_sem_admin_catalogos_nao_edita_campos_admin_only(ctx, corpo):
    client, cur, _ = ctx
    _negado(_chamar(client, "dono", "PUT", "/gyms/5", corpo))
    assert cur.executed == [], "403 vem antes de ler a academia"


@pytest.mark.parametrize(
    "corpo",
    [{"notes": "x"}, {"is_active": 0}, {"usual_weekdays": [1, 3]}, {"is_recurring": False}],
    ids=["notes", "is_active", "usual_weekdays", "is_recurring"],
)
def test_dono_edita_os_demais_campos(ctx, corpo):
    client, cur, _ = ctx
    assert _chamar(client, "dono", "PUT", "/gyms/5", corpo).status_code == 200
    assert cur.writes()


@pytest.mark.parametrize(
    "corpo", [{"name": "Outro"}, {"opens_at": "06:00"}, {"closes_at": "22:00"}], ids=["name", "opens_at", "closes_at"]
)
def test_admin_e_mestre_editam_campos_admin_only_mesmo_de_terceiro(ctx, corpo):
    client, cur, _ = ctx
    for quem in ("admin", "mestre"):
        assert _chamar(client, quem, "PUT", "/gyms/5", corpo).status_code == 200
    assert cur.writes()


def test_quem_tem_admin_catalogos_editar_mas_nao_e_dono_nao_edita_gym_de_terceiro(ctx):
    client, cur, _ = ctx
    # passa do campo admin-only (tem admin.catalogos), mas a posse e de treino.academias.
    resp = _chamar(client, "cat_editar", "PUT", "/gyms/5", {"name": "Outro"})
    _negado(resp)  # sem treino.academias/editar
    assert cur.writes() == []


# ---------------------------------------------------------------------------
# Escritas de vinculos (/gym-equipment e /gym-equipment-catalog)
# ---------------------------------------------------------------------------

ESCRITAS_VINCULO = [
    ("POST", "/gym-equipment", {"gym_id": 5, "mapping_id": 3, "equipment_id": 9}),
    ("PUT", "/gym-equipment/77", {"is_active": 0}),
    ("DELETE", "/gym-equipment/77", None),
    ("POST", "/gym-equipment-catalog", {"gym_id": 5, "equipment_id": 9}),
    ("DELETE", "/gym-equipment-catalog/88", None),
]
IDS_VINCULO = [f"{m} {p}" for m, p, _ in ESCRITAS_VINCULO]


@pytest.mark.parametrize("metodo,caminho,corpo", ESCRITAS_VINCULO, ids=IDS_VINCULO)
@pytest.mark.parametrize("quem", ["terceiro", "pt", "disp_terceiro"])
def test_terceiro_recebe_403_nas_escritas_de_vinculo(ctx, quem, metodo, caminho, corpo):
    client, cur, _ = ctx
    resp = _chamar(client, quem, metodo, caminho, corpo)
    assert resp.status_code == 403, resp.text
    assert "permissão para gerenciar equipamentos" in resp.json()["detail"]
    assert cur.writes() == []


@pytest.mark.parametrize("metodo,caminho,corpo", ESCRITAS_VINCULO, ids=IDS_VINCULO)
@pytest.mark.parametrize("quem", ["dono", "disp_dono", "admin", "mestre"])
def test_dono_admin_mestre_e_dispositivo_do_dono_escrevem_vinculos(ctx, quem, metodo, caminho, corpo):
    client, cur, _ = ctx
    resp = _chamar(client, quem, metodo, caminho, corpo)
    assert resp.status_code == 200, resp.text
    assert cur.writes()


@pytest.mark.parametrize(
    "metodo,caminho,corpo",
    [
        ("POST", "/gym-equipment", {"gym_id": 6, "mapping_id": 3, "equipment_id": 9}),
        ("PUT", "/gym-equipment/78", {"is_active": 0}),
        ("DELETE", "/gym-equipment/78", None),
        ("POST", "/gym-equipment-catalog", {"gym_id": 6, "equipment_id": 9}),
        ("DELETE", "/gym-equipment-catalog/89", None),
    ],
)
def test_vinculos_de_academia_orfa_so_com_escopo_todos(ctx, metodo, caminho, corpo):
    client, cur, _ = ctx
    for quem in ("dono", "terceiro", "pt"):
        assert _chamar(client, quem, metodo, caminho, corpo).status_code == 403, quem
    assert cur.writes() == []
    assert _chamar(client, "admin", metodo, caminho, corpo).status_code == 200
    assert cur.writes()


@pytest.mark.parametrize(
    "metodo,caminho,corpo",
    [
        ("POST", "/gym-equipment", {"gym_id": 999, "mapping_id": 3, "equipment_id": 9}),
        ("POST", "/gym-equipment-catalog", {"gym_id": 999, "equipment_id": 9}),
    ],
)
def test_vinculo_em_gym_inexistente_da_404_e_nao_grava(ctx, metodo, caminho, corpo):
    client, cur, _ = ctx
    assert _chamar(client, "dono", metodo, caminho, corpo).status_code == 404
    assert cur.writes() == []


def test_leituras_de_vinculos_seguem_abertas_a_quem_tem_ver(ctx):
    client, _, _ = ctx
    for caminho in ("/gym-equipment", "/gym-equipment-catalog"):
        for quem in ("terceiro", "pt", "dono"):
            assert _chamar(client, quem, "GET", caminho).status_code == 200, (caminho, quem)


# ---------------------------------------------------------------------------
# Equipamento, fotos e logos: admin.catalogos (e exercicios.catalogo para ler)
# ---------------------------------------------------------------------------


def _chamar_catalogo(client, quem, metodo, caminho, corpo):
    if corpo == "foto":
        return client.request(metodo, caminho, headers=_h(quem), files=_foto())
    return _chamar(client, quem, metodo, caminho, corpo)


ESCRITAS_CATALOGO = [
    ("POST", "/equipment", {"name": "Remada"}),
    ("PUT", "/equipment/9", {"notes": "n"}),
    ("DELETE", "/equipment/9", None),
    ("POST", "/equipment/9/photo", "foto"),
    ("DELETE", "/equipment/9/photo", None),
    ("POST", "/gyms/5/logo", "foto"),
    ("DELETE", "/gyms/5/logo", None),
]
IDS_CAT = [f"{m} {p}" for m, p, _ in ESCRITAS_CATALOGO]


@pytest.mark.parametrize("metodo,caminho,corpo", ESCRITAS_CATALOGO, ids=IDS_CAT)
@pytest.mark.parametrize("quem", ["dono", "terceiro", "pt", "so_editar", "disp_dono"])
def test_quem_so_tem_treino_academias_e_negado_em_equipamento_fotos_e_logos(ctx, quem, metodo, caminho, corpo):
    client, cur, _ = ctx
    _negado(_chamar_catalogo(client, quem, metodo, caminho, corpo))
    assert cur.executed == []


@pytest.mark.parametrize("metodo,caminho,corpo", ESCRITAS_CATALOGO, ids=IDS_CAT)
@pytest.mark.parametrize("quem", ["admin", "mestre"])
def test_admin_e_mestre_passam_em_equipamento_fotos_e_logos(ctx, quem, metodo, caminho, corpo):
    client, cur, _ = ctx
    resp = _chamar_catalogo(client, quem, metodo, caminho, corpo)
    assert resp.status_code == 200, resp.text
    assert cur.writes()


def test_equipamento_delete_exige_excluir_editar_nao_basta(ctx):
    client, cur, _ = ctx
    assert _chamar(client, "cat_editar", "POST", "/equipment", {"name": "Remada"}).status_code == 200
    n = len(cur.writes())
    for metodo, caminho in (("DELETE", "/equipment/9"), ("DELETE", "/equipment/9/photo"), ("DELETE", "/gyms/5/logo")):
        _negado(_chamar(client, "cat_editar", metodo, caminho))
    assert len(cur.writes()) == n


@pytest.mark.parametrize("quem", ["dono", "terceiro", "pt", "disp_dono"])
def test_leitura_de_equipamento_e_foto_segue_exercicios_catalogo(ctx, quem):
    client, _, _ = ctx
    resp = _chamar(client, quem, "GET", "/equipment")
    assert resp.status_code == 200 and resp.json()["items"][0]["id"] == 9
    # passou da guarda: 404 de negocio (sem foto), nunca 403
    resp = _chamar(client, quem, "GET", "/equipment/9/photo")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Este equipamento não possui foto."


def test_leitura_do_logo_e_treino_academias_ver(ctx):
    client, _, _ = ctx
    resp = _chamar(client, "terceiro", "GET", "/gyms/5/logo")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Esta academia não possui logo."


# ---------------------------------------------------------------------------
# Sem grupo, token mestre, dispositivo e falha do banco
# ---------------------------------------------------------------------------

TODAS_AS_ROTAS = (
    [("GET", "/gyms", None), ("GET", "/gyms/5", None), ("POST", "/gyms", {"name": "N"}),
     ("PUT", "/gyms/5", {"notes": "x"}), ("DELETE", "/gyms/5", None), ("GET", "/gyms/5/logo", None),
     ("GET", "/equipment", None), ("GET", "/equipment/9/photo", None),
     ("GET", "/gym-equipment", None), ("GET", "/gym-equipment/recommend", None),
     ("GET", "/gym-equipment/recommend-all", None), ("GET", "/gym-equipment-catalog", None)]
    + ESCRITAS_VINCULO
    + ESCRITAS_CATALOGO
)
IDS_TODAS = [f"{m} {p}" for m, p, _ in TODAS_AS_ROTAS]


@pytest.mark.parametrize("metodo,caminho,corpo", TODAS_AS_ROTAS, ids=IDS_TODAS)
@pytest.mark.parametrize("quem", ["sem_grupo", "disp_sem_grupo"])
def test_usuario_sem_grupo_e_negado_em_tudo(ctx, quem, metodo, caminho, corpo):
    client, cur, perms = ctx
    _negado(_chamar_catalogo(client, quem, metodo, caminho, corpo))
    assert perms.consultas, "negado pela consulta, nao por omissao"
    assert cur.executed == []


def test_token_mestre_passa_sem_consultar_permissoes(ctx):
    client, cur, perms = ctx
    assert _chamar(client, "mestre", "DELETE", "/gyms/7").status_code == 200  # de terceiro
    assert _chamar(client, "mestre", "POST", "/equipment", {"name": "X"}).status_code == 200
    assert perms.consultas == []
    assert cur.writes()


def test_dispositivo_herda_as_concessoes_do_dono(ctx):
    client, cur, perms = ctx
    # do dono (usuario): edita a propria academia, nega equipamento
    assert _chamar(client, "disp_dono", "PUT", "/gyms/5", {"notes": "via relogio"}).status_code == 200
    _negado(_chamar(client, "disp_dono", "POST", "/equipment", {"name": "X"}))
    assert perms.consultas[0] == (DONO,)
    # de terceiro: 404 na academia alheia
    assert _chamar(client, "disp_terceiro", "DELETE", "/gyms/5").status_code == 404


@pytest.mark.parametrize("metodo,caminho,corpo", [("GET", "/gyms", None), ("PUT", "/gyms/5", {"notes": "x"})])
def test_falha_do_banco_na_resolucao_e_503_nunca_concede(ctx, metodo, caminho, corpo):
    client, cur, perms = ctx
    perms.falha = RuntimeError("banco fora")
    resp = _chamar(client, "dono", metodo, caminho, corpo)
    assert resp.status_code == 503, resp.text
    assert cur.executed == []


def test_so_editar_nao_desativa_nem_a_propria_academia(ctx):
    client, cur, _ = ctx
    # dono da academia 5 so no teste: academia criada pelo usuario sintetico
    TABELAS["gyms"].append({**GYM_BASE, "id": 50, "owner_user_id": SO_EDITAR})
    try:
        _negado(_chamar(client, "so_editar", "DELETE", "/gyms/50"))
        assert cur.writes() == []
        assert _chamar(client, "so_editar", "PUT", "/gyms/50", {"notes": "x"}).status_code == 200
    finally:
        TABELAS["gyms"].pop()
