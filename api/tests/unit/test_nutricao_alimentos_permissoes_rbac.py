"""RBAC dos alimentos na nutricao (ADR 0011, lote 2D-2), por HTTP: catalogo (`nutrition_foods`),
fontes externas, preferidos e foto de rotulo (16 rotas).

O resolvedor REAL de permissoes roda sobre um banco falso de concessoes (`access_service._db`). As
concessoes dos papeis (usuario, personal trainer, admin) vem de `DEFAULT_GRANTS_BY_GROUP`; os
cenarios sinteticos montam o conjunto a mao. O banco de alimentos e o `FakeFoodsDB` (com um cursor
de preferidos neste arquivo); as fontes externas (OFF, USDA) e a cota/despacho da foto de rotulo
sao dubles. Onde a negacao vem do guard de entrada, a prova e "nenhuma consulta ao banco de
alimentos, nenhuma cota reservada"; onde vem do handler, e a ausencia de escrita.

Rodar: cd api && python -m pytest tests/unit/test_nutricao_alimentos_permissoes_rbac.py
"""

from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import async_jobs
from app import external_food_sources as ext
from app import nutrition_ai_quota
from app import nutrition_external_foods as external_mod
from app import nutrition_favorites as favorites_mod
from app import nutrition_foods as foods_mod
from app import nutrition_label_photo as label_mod
from app import nutrition_label_storage as storage
from app.access import DEFAULT_GRANTS_BY_GROUP
from app.access import service as access_service
from app.access.service import FORBIDDEN_DETAIL
from app.domain.nutrition import catalog
from app.domain.nutrition.ai_usage import AiFeature, QuotaStatus
from app.security import Identity, get_current_identity
from tests.support import nutrition_external_payloads as payloads
from tests.support.nutrition_foods_fake import AGORA, FakeConn, FakeCursor, FakeFoodsDB

USUARIO, PT, ADMIN_ID, SEM_GRUPO, OUTRO = 7, 8, 1, 9, 99
CAT_VER, SO_VER, SEM_EXCLUIR, SEM_IA, IA_SO_VER = 21, 22, 23, 24, 25
MESTRE = Identity(user_id=ADMIN_ID, role="admin", source="master_token")
TODOS_OS_USUARIOS = (USUARIO, PT, ADMIN_ID, CAT_VER, SO_VER, SEM_EXCLUIR, SEM_IA, IA_SO_VER, SEM_GRUPO)
PAPEIS = {USUARIO: "regular", PT: "personal_trainer", ADMIN_ID: "admin"}

TACO, PUBLICO, PRIVADO_OUTRO = 1, 2, 3  # ids do cenario
MOCA = payloads.OFF_MOCA_CODE
ARROZ = {"name": "Arroz caseiro", "energy_kcal": 128, "carbohydrates_g": 28.1, "protein_g": 2.5, "total_fat_g": 0.2}
JPEG = b"\xff\xd8\xff\xe0" + b"j" * 2048
FOTO = {"image_base64": base64.b64encode(JPEG).decode("ascii").rstrip("=")}

MODULOS = (foods_mod, external_mod, favorites_mod, label_mod)

# (id, metodo, modelo do caminho, caminho concreto, corpo, recurso, acao, status de sucesso)
# `{proprio}` = alimento privado de quem chama.
ROTAS = [
    ("search", "get", "/nutrition/foods/search", "/nutrition/foods/search?q=arroz", None, "nutricao.alimentos", "ver", 200),
    ("get-food", "get", "/nutrition/foods/{food_id}", "/nutrition/foods/1", None, "nutricao.alimentos", "ver", 200),
    ("create-food", "post", "/nutrition/foods", "/nutrition/foods", ARROZ, "nutricao.alimentos", "editar", 201),
    ("update-food", "put", "/nutrition/foods/{food_id}", "/nutrition/foods/{proprio}", ARROZ, "nutricao.alimentos", "editar", 200),
    ("portion", "post", "/nutrition/foods/{food_id}/portions", "/nutrition/foods/1/portions",
     {"label": "1 colher", "amount": 25}, "nutricao.alimentos", "editar", 201),
    ("promote", "post", "/nutrition/foods/{food_id}/promote", "/nutrition/foods/3/promote", None, "admin.catalogos", "editar", 200),
    ("barcode", "get", "/nutrition/foods/barcode/{ean}", f"/nutrition/foods/barcode/{MOCA}", None, "nutricao.alimentos", "ver", 200),
    ("usda-search", "get", "/nutrition/foods/usda/search", "/nutrition/foods/usda/search?q=rice", None,
     "nutricao.alimentos", "ver", 200),
    ("usda-import", "post", "/nutrition/foods/usda/{fdc_id}/import", "/nutrition/foods/usda/173161/import", None,
     "nutricao.alimentos", "editar", 201),
    ("fav-list", "get", "/nutrition/favorites", "/nutrition/favorites", None, "nutricao.alimentos", "ver", 200),
    ("fav-add", "post", "/nutrition/favorites", "/nutrition/favorites", {"food_id": 2}, "nutricao.alimentos", "editar", 201),
    ("fav-reorder", "post", "/nutrition/favorites/reorder", "/nutrition/favorites/reorder", {"food_ids": [1]},
     "nutricao.alimentos", "editar", 200),
    ("fav-update", "put", "/nutrition/favorites/{food_id}", "/nutrition/favorites/1", {}, "nutricao.alimentos", "editar", 200),
    ("fav-delete", "delete", "/nutrition/favorites/{food_id}", "/nutrition/favorites/1", None, "nutricao.alimentos", "excluir", 200),
    ("label-usage", "get", "/nutrition/foods/label-photo/usage", "/nutrition/foods/label-photo/usage", None,
     "ia.nutricao", "ver", 200),
    ("label-post", "post", "/nutrition/foods/label-photo", "/nutrition/foods/label-photo", FOTO, "ia.nutricao", "editar", 202),
]
SEM_PROMOTE = [r for r in ROTAS if r[0] != "promote"]
ESCRITAS_ALIMENTOS = [r for r in ROTAS if r[1] != "get" and r[5] == "nutricao.alimentos"]
ESCRITAS = [r for r in ROTAS if r[1] != "get"]
DELETES = [r for r in ROTAS if r[1] == "delete"]
ROTAS_IA = [r for r in ROTAS if r[5] == "ia.nutricao"]


def _ids(rotas):
    return [r[0] for r in rotas]


def _sint(triplas):
    return [{"group_key": "sint", "is_system": 0, "resource": r, "action": a, "scope": s} for r, a, s in triplas]


class FakePermissoes:
    """Banco de concessoes: `fetch_rows(sql, (user_id,))` -> linhas do grupo do usuario."""

    def __init__(self):
        self.falha = None
        self.consultas = []
        alim = [("nutricao.alimentos", a, "proprios") for a in ("ver", "editar", "excluir")]
        self.por_usuario = {
            USUARIO: self._grupo("usuario"),
            PT: self._grupo("personal_trainer"),
            ADMIN_ID: self._grupo("admin"),
            CAT_VER: _sint(alim + [("admin.catalogos", "ver", "todos")]),
            SO_VER: _sint([("nutricao.alimentos", "ver", "proprios"), ("ia.nutricao", "ver", "proprios")]),
            SEM_EXCLUIR: _sint([("nutricao.alimentos", a, "proprios") for a in ("ver", "editar")]
                               + [("ia.nutricao", a, "proprios") for a in ("ver", "editar", "excluir")]),
            SEM_IA: _sint(alim),
            IA_SO_VER: _sint(alim + [("ia.nutricao", "ver", "proprios")]),
        }  # SEM_GRUPO ausente = usuario sem grupo

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


class FakeFavCursor(FakeCursor):
    """Preferidos (`user_favorite_foods`) sobre o `FakeFoodsDB`; o resto cai no cursor base."""

    def execute(self, sql, params=None):  # noqa: C901 - despacho por statement
        db = self.db
        p = tuple(params or ())
        escrita = {
            favorites_mod.INSERT_FAVORITE_SQL, favorites_mod.UPDATE_DEFAULT_SQL,
            favorites_mod.UPDATE_POSITION_SQL, favorites_mod.DELETE_FAVORITE_SQL,
        }
        if sql in escrita or sql in {
            favorites_mod.FAVORITES_OF_USER_SQL, favorites_mod.FAVORITE_BY_FOOD_SQL,
            favorites_mod.COUNT_AND_MAX_POSITION_SQL, favorites_mod.FOOD_SQL, favorites_mod.PORTION_SQL,
        } or sql == favorites_mod.foods_by_ids_sql(len(p)) or sql == favorites_mod.portions_by_ids_sql(len(p)):
            db.queries.append((sql, p))
            self._rows, self.rowcount = [], 0
        if sql == favorites_mod.FAVORITES_OF_USER_SQL:
            linhas = sorted((f for f in db.favorites if f["user_id"] == p[0]), key=lambda f: (f["position"], f["id"]))
            self._rows, self.rowcount = [dict(f) for f in linhas], len(linhas)
        elif sql == favorites_mod.FAVORITE_BY_FOOD_SQL:
            self._rows = [dict(f) for f in db.favorites if f["user_id"] == p[0] and f["food_id"] == p[1]]
        elif sql == favorites_mod.COUNT_AND_MAX_POSITION_SQL:
            minhas = [f for f in db.favorites if f["user_id"] == p[0]]
            self._rows = [{"n": len(minhas), "max_position": max((f["position"] for f in minhas), default=None)}]
        elif sql == favorites_mod.INSERT_FAVORITE_SQL:
            usuario, food_id, qtd, unidade, porcao, posicao = p
            db.next_fav_id += 1
            db.favorites.append({
                "id": db.next_fav_id, "user_id": usuario, "food_id": food_id, "default_quantity": qtd,
                "default_unit": unidade, "default_portion_id": porcao, "position": posicao,
                "created_at": AGORA, "updated_at": AGORA,
            })
            self.rowcount = 1
        elif sql == favorites_mod.UPDATE_DEFAULT_SQL:
            qtd, unidade, porcao, usuario, food_id = p
            for f in db.favorites:
                if f["user_id"] == usuario and f["food_id"] == food_id:
                    f.update(default_quantity=qtd, default_unit=unidade, default_portion_id=porcao)
                    self.rowcount += 1
        elif sql == favorites_mod.UPDATE_POSITION_SQL:
            posicao, usuario, food_id = p
            for f in db.favorites:
                if f["user_id"] == usuario and f["food_id"] == food_id:
                    f["position"] = posicao
                    self.rowcount += 1
        elif sql == favorites_mod.DELETE_FAVORITE_SQL:
            antes = len(db.favorites)
            db.favorites = [f for f in db.favorites if not (f["user_id"] == p[0] and f["food_id"] == p[1])]
            self.rowcount = antes - len(db.favorites)
        elif sql == favorites_mod.FOOD_SQL:
            linha = db.foods.get(int(p[0]))
            self._rows = [{k: v for k, v in linha.items() if not k.startswith("_")}] if linha else []
        elif sql == favorites_mod.foods_by_ids_sql(len(p)):
            self._rows = [{k: v for k, v in db.foods[i].items() if not k.startswith("_")} for i in p if i in db.foods]
        elif sql == favorites_mod.PORTION_SQL:
            linha = db.portions.get(int(p[0]))
            self._rows = [dict(linha)] if linha else []
        elif sql == favorites_mod.portions_by_ids_sql(len(p)):
            self._rows = [dict(db.portions[i]) for i in p if i in db.portions]
        else:
            super().execute(sql, params)


class FakeFavConn(FakeConn):
    def cursor(self):
        return FakeFavCursor(self.db)


class FakeBanco(FakeFoodsDB):
    def __init__(self):
        super().__init__()
        self.next_fav_id = 0
        self.proprio: dict[int, int] = {}

    def conn(self):  # type: ignore[override]
        return FakeFavConn(self)

    @property
    def escritas(self):
        return [q for q, _p in self.queries if q.split(" ", 1)[0].upper() in ("INSERT", "UPDATE", "DELETE")]


class Quota:
    """Dubles da cota e do despacho da foto de rotulo; `eventos` registra o que foi chamado."""

    def __init__(self, monkeypatch):
        self.eventos: list[tuple] = []
        monkeypatch.setattr(nutrition_ai_quota, "open_usage", lambda **kw: self._reg("open_usage", kw) or 55)
        monkeypatch.setattr(nutrition_ai_quota, "finish_usage", lambda *a, **kw: self._reg("finish_usage", a) or True)
        monkeypatch.setattr(nutrition_ai_quota, "link_job", lambda *a, **kw: self._reg("link_job", a) or True)
        monkeypatch.setattr(
            nutrition_ai_quota, "usage_today",
            lambda conn, **kw: self._reg("usage_today", kw) or QuotaStatus(feature=AiFeature.LABEL_PHOTO, limit=5, used=1),
        )
        monkeypatch.setattr(label_mod, "get_conn", lambda: self)
        monkeypatch.setattr(storage, "put_label_photo", lambda uid, dados: self._reg("put", uid) or "chave.jpg")
        monkeypatch.setattr(async_jobs, "create_job", lambda *a, **kw: self._reg("create_job", a) or "job-1")
        monkeypatch.setattr(async_jobs, "invoke_async", lambda *a, **kw: self._reg("invoke", a))

    def _reg(self, nome, dado):
        self.eventos.append((nome, dado))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def ctx(monkeypatch):
    perms = FakePermissoes()
    monkeypatch.setattr(access_service, "_db", perms)
    db = FakeBanco()
    db.add_food("Arroz, tipo 1, cozido", energy_kcal=128.26, carbohydrates_g=28.06, protein_g=2.52,
                total_fat_g=0.23, serving_size=100)
    db.add_food("Pao caseiro publico", source="user", owner=None, energy_kcal=250, carbohydrates_g=50,
                protein_g=8, total_fat_g=2)
    db.add_food("Doce secreto", source="user", owner=OUTRO, energy_kcal=400, carbohydrates_g=80, protein_g=2,
                total_fat_g=10)
    for uid in TODOS_OS_USUARIOS:
        db.proprio[uid] = db.add_food(f"Pasta de {uid}", source="user", owner=uid, energy_kcal=300,
                                      carbohydrates_g=20, protein_g=10, total_fat_g=20)
        db.next_fav_id += 1
        db.favorites.append({"id": db.next_fav_id, "user_id": uid, "food_id": TACO, "default_quantity": None,
                             "default_unit": None, "default_portion_id": None, "position": 0,
                             "created_at": AGORA, "updated_at": AGORA})
    monkeypatch.setattr(foods_mod, "get_conn", db.conn)
    for modulo in (foods_mod, external_mod, favorites_mod):
        monkeypatch.setattr(modulo, "require_food_tables", lambda cur: None)
    monkeypatch.setattr(favorites_mod, "require_favorites_table", lambda cur: None)
    monkeypatch.setattr(foods_mod, "has_favorites_table", lambda cur: True)
    monkeypatch.setattr(external_mod, "_off_day_start", lambda conn, user_id: AGORA)
    monkeypatch.setattr(ext, "off_product", lambda code, *, fields: payloads.load(payloads.OFF_MOCA))
    monkeypatch.setattr(ext, "usda_search", lambda body: payloads.load(payloads.USDA_SEARCH))
    monkeypatch.setattr(ext, "usda_food", lambda fdc_id: payloads.load(payloads.USDA_SR_LEGACY))
    monkeypatch.setenv(ext.USDA_API_KEY_ENV, "chave-de-teste")
    quota = Quota(monkeypatch)
    app = FastAPI()
    for modulo in MODULOS:
        app.include_router(modulo.router)
    state = {"identity": Identity(user_id=USUARIO, role="regular", source="session")}
    app.dependency_overrides[get_current_identity] = lambda: state["identity"]
    client = TestClient(app, raise_server_exceptions=False)
    return client, state, perms, db, quota


def _como(state, uid, source="session"):
    state["identity"] = Identity(user_id=uid, role=PAPEIS.get(uid, "regular"), source=source,
                                 device_id=3 if source == "device" else None)


def _chamar(client, db, rota, uid=None):
    _id, metodo, _modelo, caminho, corpo, *_ = rota
    caminho = caminho.replace("{proprio}", str(db.proprio.get(uid, 0)))
    if corpo is not None:
        return getattr(client, metodo)(caminho, json=corpo)
    return getattr(client, metodo)(caminho)


def _negado(resp):
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == FORBIDDEN_DETAIL


def _negado_no_handler(resp, codigo="food_forbidden"):
    assert resp.status_code == 403, resp.text
    detalhe = resp.json()["detail"]
    assert detalhe["error"] == codigo and detalhe != FORBIDDEN_DETAIL


def _nomes(resp):
    return [i["name"] for i in resp.json()["items"]]


# ---------------------------------------------------------------------------
# (a) usuario comum e personal trainer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("uid", [USUARIO, PT], ids=["usuario", "pt"])
@pytest.mark.parametrize("rota", SEM_PROMOTE, ids=_ids(SEM_PROMOTE))
def test_usuario_e_pt_usam_alimentos_preferidos_externas_e_foto_com_status_de_sucesso(ctx, uid, rota):
    client, state, _, db, _ = ctx
    _como(state, uid)
    resp = _chamar(client, db, rota, uid)
    assert resp.status_code == rota[7], resp.text


@pytest.mark.parametrize("uid", [USUARIO, PT], ids=["usuario", "pt"])
def test_cadastro_manual_nasce_privado_e_o_proprio_pode_edita_lo(ctx, uid):
    client, state, _, db, _ = ctx
    _como(state, uid)
    resp = client.post("/nutrition/foods", json=ARROZ)
    assert resp.status_code == 201, resp.text
    corpo = resp.json()
    assert corpo["is_public"] is False and corpo["is_own"] is True
    assert db.foods[corpo["id"]]["owner_user_id"] == uid
    assert corpo["permissions"] == {"can_edit": True, "can_add_portion": True, "can_promote": False}
    assert client.put(f"/nutrition/foods/{corpo['id']}", json={**ARROZ, "name": "Arroz da casa"}).status_code == 200


@pytest.mark.parametrize("uid", [USUARIO, PT], ids=["usuario", "pt"])
def test_cadastro_pela_foto_ou_com_ean_continua_privado_sem_admin_catalogos(ctx, uid):
    client, state, _, db, _ = ctx
    _como(state, uid)
    foto = client.post("/nutrition/foods", json={**ARROZ, "source": "label_photo"})
    ean = client.post("/nutrition/foods", json={**ARROZ, "name": "Arroz com ean", "barcode": "7891000100103"})
    for resp in (foto, ean):
        assert resp.status_code == 201, resp.text
        assert resp.json()["is_public"] is False
        assert db.foods[resp.json()["id"]]["owner_user_id"] == uid


@pytest.mark.parametrize("uid", [USUARIO, PT], ids=["usuario", "pt"])
def test_porcao_em_alimento_publico_nasce_privada_do_usuario(ctx, uid):
    client, state, _, db, _ = ctx
    _como(state, uid)
    resp = client.post("/nutrition/foods/1/portions", json={"label": "1 colher", "amount": 25})
    assert resp.status_code == 201, resp.text
    assert resp.json()["is_public"] is False and resp.json()["is_own"] is True
    assert db.portions[resp.json()["id"]]["owner_user_id"] == uid


@pytest.mark.parametrize("uid", [USUARIO, PT], ids=["usuario", "pt"])
def test_private_all_e_403_do_handler_e_nao_consulta_alimentos(ctx, uid):
    client, state, _, db, _ = ctx
    _como(state, uid)
    resp = client.get("/nutrition/foods/search", params={"q": "pasta", "scope": "private_all"})
    _negado_no_handler(resp)
    assert db.queries == []


@pytest.mark.parametrize("uid", [USUARIO, PT], ids=["usuario", "pt"])
def test_editar_alimento_publico_e_403_do_handler_sem_escrita(ctx, uid):
    client, state, _, db, _ = ctx
    _como(state, uid)
    _negado_no_handler(client.put(f"/nutrition/foods/{PUBLICO}", json=ARROZ))
    assert db.escritas == []
    assert db.foods[PUBLICO]["name"] == "Pao caseiro publico"


@pytest.mark.parametrize("uid", [USUARIO, PT], ids=["usuario", "pt"])
def test_promote_e_403_do_guard_e_nao_toca_no_banco(ctx, uid):
    client, state, _, db, _ = ctx
    _como(state, uid)
    _negado(client.post(f"/nutrition/foods/{db.proprio[uid]}/promote"))
    assert db.queries == []
    assert db.foods[db.proprio[uid]]["owner_user_id"] == uid


@pytest.mark.parametrize("uid", [USUARIO, PT], ids=["usuario", "pt"])
def test_privado_de_terceiro_e_404_e_nao_aparece_na_busca(ctx, uid):
    client, state, _, db, _ = ctx
    _como(state, uid)
    assert client.get(f"/nutrition/foods/{PRIVADO_OUTRO}").status_code == 404
    assert "Doce secreto" not in _nomes(client.get("/nutrition/foods/search", params={"q": "doce"}))
    assert client.put(f"/nutrition/foods/{PRIVADO_OUTRO}", json=ARROZ).status_code == 404


def test_preferidos_do_usuario_ciclo_completo_so_na_propria_lista(ctx):
    client, state, _, db, _ = ctx
    _como(state, USUARIO)
    assert client.post("/nutrition/favorites", json={"food_id": PUBLICO}).status_code == 201
    assert [i["food"]["id"] for i in client.get("/nutrition/favorites").json()["items"]] == [TACO, PUBLICO]
    assert client.post("/nutrition/favorites/reorder", json={"food_ids": [PUBLICO, TACO]}).status_code == 200
    assert client.put(f"/nutrition/favorites/{PUBLICO}", json={}).status_code == 200
    assert client.delete(f"/nutrition/favorites/{PUBLICO}").status_code == 200
    assert {f["user_id"] for f in db.favorites if f["food_id"] == PUBLICO} == set()
    assert any(f["user_id"] == PT for f in db.favorites)  # a lista de outro usuario ficou intacta


# ---------------------------------------------------------------------------
# (b) admin
# ---------------------------------------------------------------------------


def test_admin_lista_privados_de_todos_e_ve_privado_de_terceiro(ctx):
    client, state, _, db, _ = ctx
    _como(state, ADMIN_ID)
    resp = client.get("/nutrition/foods/search", params={"q": "doce", "scope": "private_all"})
    assert resp.status_code == 200, resp.text
    assert _nomes(resp) == ["Doce secreto"] and resp.json()["items"][0]["permissions"]["can_promote"] is True
    assert client.get(f"/nutrition/foods/{PRIVADO_OUTRO}").status_code == 200


def test_admin_edita_alimento_publico_e_promove_levando_as_porcoes_do_dono(ctx):
    client, state, _, db, _ = ctx
    db.add_portion(PRIVADO_OUTRO, "1 concha", 120, owner=OUTRO)
    _como(state, ADMIN_ID)
    assert client.put(f"/nutrition/foods/{PUBLICO}", json={**ARROZ, "name": "Pao editado"}).status_code == 200
    assert db.foods[PUBLICO]["name"] == "Pao editado"
    resp = client.post(f"/nutrition/foods/{PRIVADO_OUTRO}/promote")
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_public"] is True
    assert db.foods[PRIVADO_OUTRO]["owner_user_id"] is None
    assert all(p["owner_user_id"] is None for p in db.portions.values())
    assert client.post(f"/nutrition/foods/{PRIVADO_OUTRO}/promote").status_code == 409  # ja e publico


def test_admin_cria_porcao_publica_em_alimento_publico_e_nao_em_privado_de_terceiro(ctx):
    client, state, _, db, _ = ctx
    _como(state, ADMIN_ID)
    publica = client.post("/nutrition/foods/1/portions", json={"label": "1 colher", "amount": 25})
    assert publica.status_code == 201 and publica.json()["is_public"] is True
    assert db.portions[publica.json()["id"]]["owner_user_id"] is None
    _negado_no_handler(client.post(f"/nutrition/foods/{PRIVADO_OUTRO}/portions", json={"label": "x", "amount": 1}))


@pytest.mark.parametrize(
    ("extra", "publico"),
    [({}, False), ({"source": "label_photo"}, True), ({"barcode": "7891000100103"}, True)],
    ids=["manual", "foto-de-rotulo", "com-ean"],
)
def test_alimento_do_admin_nasce_conforme_catalog_is_born_public(ctx, extra, publico):
    client, state, _, db, _ = ctx
    _como(state, ADMIN_ID)
    resp = client.post("/nutrition/foods", json={**ARROZ, **extra})
    assert resp.status_code == 201, resp.text
    esperado = catalog.is_born_public(True, extra.get("source"), extra.get("barcode"))
    assert esperado is publico and resp.json()["is_public"] is publico
    assert (db.foods[resp.json()["id"]]["owner_user_id"] is None) is publico


# ---------------------------------------------------------------------------
# (c) grupo sintetico: alimentos completos + admin.catalogos so `ver`
# ---------------------------------------------------------------------------


def test_catalogos_ver_lista_e_ve_privados_de_terceiros(ctx):
    client, state, _, _, _ = ctx
    _como(state, CAT_VER)
    resp = client.get("/nutrition/foods/search", params={"q": "doce", "scope": "private_all"})
    assert resp.status_code == 200 and _nomes(resp) == ["Doce secreto"]
    assert resp.json()["items"][0]["permissions"]["can_promote"] is False  # promover exige `editar`
    assert client.get(f"/nutrition/foods/{PRIVADO_OUTRO}").status_code == 200


def test_catalogos_ver_nao_edita_publico_nem_privado_de_terceiro_e_nao_promove(ctx):
    client, state, _, db, _ = ctx
    _como(state, CAT_VER)
    _negado_no_handler(client.put(f"/nutrition/foods/{PUBLICO}", json=ARROZ))
    _negado_no_handler(client.put(f"/nutrition/foods/{PRIVADO_OUTRO}", json=ARROZ))
    _negado(client.post(f"/nutrition/foods/{PRIVADO_OUTRO}/promote"))
    assert db.escritas == []
    assert db.foods[PRIVADO_OUTRO]["owner_user_id"] == OUTRO


def test_catalogos_ver_porcao_e_alimento_nascem_privados(ctx):
    client, state, _, db, _ = ctx
    _como(state, CAT_VER)
    porcao = client.post("/nutrition/foods/1/portions", json={"label": "1 colher", "amount": 25})
    assert porcao.status_code == 201 and porcao.json()["is_public"] is False
    foto = client.post("/nutrition/foods", json={**ARROZ, "source": "label_photo"})
    assert foto.status_code == 201 and foto.json()["is_public"] is False
    assert db.foods[foto.json()["id"]]["owner_user_id"] == CAT_VER


# ---------------------------------------------------------------------------
# (d) so `ver` nega escritas; sem `excluir` nega o DELETE
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rota", ESCRITAS, ids=_ids(ESCRITAS))
def test_grupo_so_com_ver_e_negado_em_toda_escrita(ctx, rota):
    client, state, _, db, quota = ctx
    _como(state, SO_VER)
    _negado(_chamar(client, db, rota, SO_VER))
    assert db.queries == [] and quota.eventos == []


@pytest.mark.parametrize("rota", [r for r in ROTAS if r[1] == "get"], ids=_ids([r for r in ROTAS if r[1] == "get"]))
def test_grupo_so_com_ver_le_o_que_ver_cobre(ctx, rota):
    client, state, _, db, _ = ctx
    _como(state, SO_VER)
    assert _chamar(client, db, rota, SO_VER).status_code == rota[7]


@pytest.mark.parametrize("rota", DELETES, ids=_ids(DELETES))
def test_grupo_sem_excluir_e_negado_no_delete_de_preferido(ctx, rota):
    client, state, _, db, _ = ctx
    _como(state, SEM_EXCLUIR)
    _negado(_chamar(client, db, rota, SEM_EXCLUIR))
    assert db.queries == []
    assert any(f["user_id"] == SEM_EXCLUIR and f["food_id"] == TACO for f in db.favorites)


@pytest.mark.parametrize("rota", [r for r in ESCRITAS_ALIMENTOS if r[1] != "delete"], ids=_ids(
    [r for r in ESCRITAS_ALIMENTOS if r[1] != "delete"]))
def test_grupo_sem_excluir_ainda_edita(ctx, rota):
    client, state, _, db, _ = ctx
    _como(state, SEM_EXCLUIR)
    assert _chamar(client, db, rota, SEM_EXCLUIR).status_code == rota[7]


# ---------------------------------------------------------------------------
# (e) ia.nutricao na foto de rotulo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rota", ROTAS_IA, ids=_ids(ROTAS_IA))
def test_sem_ia_nutricao_e_negado_sem_reservar_cota(ctx, rota):
    client, state, _, db, quota = ctx
    _como(state, SEM_IA)
    _negado(_chamar(client, db, rota, SEM_IA))
    assert quota.eventos == []


def test_ia_nutricao_so_ver_le_o_uso_mas_nao_envia_a_foto(ctx):
    client, state, _, _, quota = ctx
    _como(state, IA_SO_VER)
    assert client.get("/nutrition/foods/label-photo/usage").status_code == 200
    quota.eventos.clear()
    _negado(client.post("/nutrition/foods/label-photo", json=FOTO))
    assert quota.eventos == []


def test_foto_com_ia_nutricao_editar_reserva_a_cota_do_proprio_usuario(ctx):
    client, state, _, _, quota = ctx
    _como(state, USUARIO)
    resp = client.post("/nutrition/foods/label-photo", json=FOTO)
    assert resp.status_code == 202 and resp.json()["job_id"] == "job-1"
    assert [e[0] for e in quota.eventos][:2] == ["open_usage", "put"]
    assert quota.eventos[0][1]["user_id"] == USUARIO


# ---------------------------------------------------------------------------
# (f) sem grupo, token mestre, dispositivo; enumeracao das rotas vivas
# ---------------------------------------------------------------------------


def _rotas_vivas():
    """Rotas vivas em `app.main.app` cujo endpoint e de um dos 4 modulos. O FastAPI recente guarda
    routers incluidos como `_IncludedRouter`; `effective_route_contexts()` os expande."""
    from app.main import app

    modulos = {m.__name__ for m in MODULOS}
    vivas = []
    for route in app.routes:
        expand = getattr(route, "effective_route_contexts", None)
        for candidato in (expand() if callable(expand) else [route]):
            endpoint = getattr(candidato, "endpoint", None)
            if endpoint is not None and endpoint.__module__ in modulos:
                vivas.extend((m.lower(), candidato.path) for m in sorted(candidato.methods - {"HEAD", "OPTIONS"}))
    return vivas


def test_as_rotas_vivas_dos_4_modulos_sao_16_e_batem_com_a_tabela():
    vivas = _rotas_vivas()
    assert len(vivas) == 16 and len(set(vivas)) == 16
    assert set(vivas) == {(r[1], r[2]) for r in ROTAS}


@pytest.mark.parametrize("metodo,modelo", _rotas_vivas(), ids=lambda v: v)
def test_usuario_sem_grupo_e_negado_nas_16_rotas_vivas(ctx, metodo, modelo):
    client, state, perms, db, quota = ctx
    rota = next(r for r in ROTAS if (r[1], r[2]) == (metodo, modelo))
    _como(state, SEM_GRUPO)
    _negado(_chamar(client, db, rota, SEM_GRUPO))
    assert perms.consultas == [(SEM_GRUPO,)]
    assert db.queries == [] and quota.eventos == []


@pytest.mark.parametrize("rota", ROTAS, ids=_ids(ROTAS))
def test_token_mestre_passa_sem_consultar_o_banco_de_permissoes(ctx, rota):
    client, state, perms, db, _ = ctx
    state["identity"] = MESTRE
    resp = _chamar(client, db, rota, ADMIN_ID)
    assert resp.status_code == rota[7], resp.text
    assert perms.consultas == []


@pytest.mark.parametrize("rota", SEM_PROMOTE, ids=_ids(SEM_PROMOTE))
def test_dispositivo_do_usuario_herda_as_concessoes_do_dono(ctx, rota):
    client, state, perms, db, _ = ctx
    _como(state, USUARIO, source="device")
    resp = _chamar(client, db, rota, USUARIO)
    assert resp.status_code == rota[7], resp.text
    assert perms.consultas[0] == (USUARIO,)


def test_dispositivo_do_usuario_continua_sem_promote_e_dispositivo_de_admin_promove(ctx):
    client, state, perms, db, _ = ctx
    _como(state, USUARIO, source="device")
    _negado(client.post(f"/nutrition/foods/{PRIVADO_OUTRO}/promote"))
    _como(state, ADMIN_ID, source="device")
    assert client.post(f"/nutrition/foods/{PRIVADO_OUTRO}/promote").status_code == 200
    assert perms.consultas[-1] == (ADMIN_ID,)


def test_dispositivo_de_dono_sem_grupo_e_negado(ctx):
    client, state, _, db, _ = ctx
    _como(state, SEM_GRUPO, source="device")
    _negado(client.get("/nutrition/foods/search", params={"q": "arroz"}))
    assert db.queries == []


# ---------------------------------------------------------------------------
# (g) falha de banco na resolucao: 503, nunca concede
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rota", ROTAS, ids=_ids(ROTAS))
def test_falha_do_banco_na_resolucao_e_503_nunca_concede(ctx, rota):
    client, state, perms, db, quota = ctx
    perms.falha = RuntimeError("banco fora")
    _como(state, ADMIN_ID)
    resp = _chamar(client, db, rota, ADMIN_ID)
    assert resp.status_code == 503, resp.text
    assert db.queries == [] and quota.eventos == []
