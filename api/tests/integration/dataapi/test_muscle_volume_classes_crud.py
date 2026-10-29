"""CRUD de `/muscle-volume-classes` (migração 057, Fase 6 PR 1) e o
`volume_class_id` em `/muscle-groups`, nos dois backends (PyMySQL e o
caminho de PRODUÇÃO: shim `app/db_dataapi.py` + dublê da RDS Data API), num
schema com TODAS as migrações aplicadas pelo runner.

O que se prova, em ordem de risco:

1. **O seed da 057 nasce igual ao motor de hoje** (Grandes/Pequenos/Outros,
   associação por `size_class`) - lido do banco migrado, antes de truncar.
2. **Excluir macro-grupo com grupo associado é 409**, e a FK RESTRICT segura
   no banco.
3. **Fatia é recusada com 422** ("associe o grupo pai").
4. **Faixas inválidas são 422** (mín > máx, negativo, bucket repetido).
5. **Não-admin recebe 403** em toda escrita, e lê normalmente.
6. **Mover grupo entre macro-grupos** tira de um e põe no outro.
7. **POST duplicado de grupo muscular PRESERVA `volume_class_id`** (fora do
   `ON DUPLICATE KEY UPDATE`).
8. **Modo de contagem (073):** o seed copia as 9 faixas e as 3 metas para
   `primary_secondary` marcadas como "não calibradas"; `?counting_mode=` lê e
   escreve só o modo pedido, e gravar séries novas tira a marca.
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import exercise_mapping_catalog as emcat
from app import migrate as m
from app import muscle_volume_classes as mvc
from app import security
from app.db_pymysql import Connection as PyMySQLConnection
from tests.support.dataapi_backend import BACKENDS, raw_mysql, use_dataapi
from tests.support.migrations import recreate_schema_with_migrations

MASTER = "muscle-volume-classes-master"
HEADERS = {"X-API-Token": MASTER}

TABLES = (
    "exercise_muscle_contributions",
    "exercise_catalog",
    "exercise_zepp_mappings",
    "muscle_volume_class_ranges",
    "muscle_volume_class_mode_ranges",
    "muscle_volume_class_weekly_targets",
    "muscle_groups",
    "muscle_volume_classes",
    "users",
)


def _kwargs() -> dict:
    return dict(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
    )


@pytest.fixture(scope="module")
def schema() -> str:
    base = os.environ["MYSQL_DATABASE"]
    database = f"{base[: -len('_test')]}_mvclasses_test"
    recreate_schema_with_migrations(database=database, **_kwargs())
    kw = _kwargs()
    m.migrate(
        conn_factory=lambda: PyMySQLConnection(
            kw["host"], kw["port"], kw["user"], kw["password"], database
        )
    )
    return database


@pytest.fixture(scope="module")
def seed_snapshot(schema) -> dict:
    """O estado que a 057 deixou, lido ANTES do primeiro TRUNCATE."""
    with raw_mysql(database=schema, **_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, sort_order, weekly_sets_min, weekly_sets_max, weekly_source "
                        "FROM muscle_volume_classes ORDER BY sort_order")
            classes = list(cur.fetchall())
            cur.execute("SELECT class_id, frequency_bucket, sets_min, sets_max, exercises_min, exercises_max "
                        "FROM muscle_volume_class_ranges")
            ranges = list(cur.fetchall())
            cur.execute("SELECT class_id, frequency_bucket, sets_min, sets_max, exercises_min, exercises_max, "
                        "copied_from_counting_mode FROM muscle_volume_class_mode_ranges "
                        "WHERE counting_mode = 'primary_secondary'")
            ranges_ps = list(cur.fetchall())
            cur.execute("SELECT class_id, weekly_sets_min, weekly_sets_max, weekly_source, copied_from_counting_mode "
                        "FROM muscle_volume_class_weekly_targets WHERE counting_mode = 'primary_secondary'")
            weekly_ps = list(cur.fetchall())
            cur.execute("SELECT name, size_class, volume_class_id FROM muscle_groups")
            groups = list(cur.fetchall())
    return {"classes": classes, "ranges": ranges, "ranges_ps": ranges_ps, "weekly_ps": weekly_ps, "groups": groups}


@pytest.fixture
def raw(schema, seed_snapshot):
    with raw_mysql(database=schema, **_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            try:
                for table in TABLES:
                    cur.execute(f"TRUNCATE TABLE `{table}`")
            finally:
                cur.execute("SET FOREIGN_KEY_CHECKS = 1")
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES ('admin-mvc', 'x', 'admin')"
            )
        yield conn


@pytest.fixture(params=BACKENDS)
def backend(request, monkeypatch, fake_rds, schema) -> str:
    monkeypatch.setenv("API_TOKEN", MASTER)
    if request.param == "data-api":
        use_dataapi(monkeypatch, fake_rds, schema)
    else:
        monkeypatch.setenv("MYSQL_DATABASE", schema)
    yield request.param
    if request.param == "data-api":
        assert fake_rds.open_transactions == [], "deixou transacao Data API aberta"


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(mvc.router)
    app.include_router(emcat.muscle_groups_router)
    return app


@pytest.fixture
def client(backend) -> TestClient:
    return TestClient(_app(), raise_server_exceptions=False)


@pytest.fixture
def non_admin_client(backend, monkeypatch) -> TestClient:
    identidade = security.Identity(user_id=2, role="regular", source="session")
    app = _app()
    monkeypatch.setattr(security, "resolve_identity", lambda _token: identidade)
    return TestClient(app, raise_server_exceptions=False)


def _grupo(client, name: str, **extras) -> dict:
    resposta = client.post("/muscle-groups", json={"name": name, **extras}, headers=HEADERS)
    assert resposta.status_code == 200, resposta.text
    return resposta.json()["item"]


def _classe(client, name: str, **extras) -> dict:
    resposta = client.post("/muscle-volume-classes", json={"name": name, **extras}, headers=HEADERS)
    assert resposta.status_code == 200, resposta.text
    return resposta.json()["item"]


_FAIXAS_VALIDAS = [
    {"frequency_bucket": "1x", "sets_min": 15, "sets_max": 20, "exercises_min": 4, "exercises_max": 5},
    {"frequency_bucket": "2x", "sets_min": 9, "sets_max": 16},
    {"frequency_bucket": "3x_plus", "sets_min": 6, "sets_max": 12, "exercises_min": 2, "exercises_max": 3},
]


# ---------------------------------------------------------------------------
# Seed da 057 (lido do banco migrado, antes de truncar)
# ---------------------------------------------------------------------------


def test_seed_da_057_no_banco_migrado(seed_snapshot):
    classes = seed_snapshot["classes"]
    assert [(c["name"], c["sort_order"]) for c in classes] == [("Grandes", 1), ("Pequenos", 2), ("Outros", 3)]
    assert [(float(c["weekly_sets_min"]), float(c["weekly_sets_max"])) for c in classes] == [
        (18.0, 32.0), (12.0, 24.0), (12.0, 24.0)
    ]
    assert {c["weekly_source"] for c in classes} == {"derived"}
    assert len(seed_snapshot["ranges"]) == 9
    por_id = {c["id"]: c["name"] for c in classes}
    esperado = {"large": "Grandes", "small": "Pequenos", "abs": "Outros"}
    for grupo in seed_snapshot["groups"]:
        if grupo["size_class"] in esperado:
            assert por_id[grupo["volume_class_id"]] == esperado[grupo["size_class"]], grupo
        else:
            assert grupo["volume_class_id"] is None, grupo


def test_seed_da_073_copia_primary_only_marcando_nao_calibrada(seed_snapshot):
    chave = ("class_id", "frequency_bucket", "sets_min", "sets_max", "exercises_min", "exercises_max")
    copia = sorted(tuple(r[k] for k in chave) for r in seed_snapshot["ranges_ps"])
    assert copia == sorted(tuple(r[k] for k in chave) for r in seed_snapshot["ranges"])
    assert {r["copied_from_counting_mode"] for r in seed_snapshot["ranges_ps"]} == {"primary_only"}
    metas = {w["class_id"]: w for w in seed_snapshot["weekly_ps"]}
    for classe in seed_snapshot["classes"]:
        meta = metas[classe["id"]]
        assert (meta["weekly_sets_min"], meta["weekly_sets_max"], meta["weekly_source"]) == (
            classe["weekly_sets_min"], classe["weekly_sets_max"], classe["weekly_source"]
        )
        assert meta["copied_from_counting_mode"] == "primary_only"


def test_faixas_e_meta_por_modo_sao_independentes(client, raw):
    classe = _classe(client, "Grandes", weekly_sets_min=18, weekly_sets_max=32)
    assert classe["counting_mode"] == "primary_only" and classe["weekly_uncalibrated"] is False
    client.put(f"/muscle-volume-classes/{classe['id']}/ranges", json={"ranges": _FAIXAS_VALIDAS}, headers=HEADERS)
    # Copia "nao calibrada" no modo ponderado, como o seed da 073 deixaria.
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO muscle_volume_class_mode_ranges (class_id, counting_mode, frequency_bucket, sets_min, sets_max, "
            "sets_source, sets_evidence, copied_from_counting_mode) "
            "VALUES (%s, 'primary_secondary', '2x', 9, 16, 'methodology', 'EXG-016', 'primary_only')",
            (classe["id"],),
        )
        cur.execute(
            "INSERT INTO muscle_volume_class_weekly_targets (class_id, counting_mode, weekly_sets_min, "
            "weekly_sets_max, weekly_source, copied_from_counting_mode) "
            "VALUES (%s, 'primary_secondary', 18, 32, 'derived', 'primary_only')",
            (classe["id"],),
        )
    ps = client.get("/muscle-volume-classes", params={"counting_mode": "primary_secondary"}, headers=HEADERS).json()
    item = ps["items"][0]
    assert ps["counting_mode"] == item["counting_mode"] == "primary_secondary"
    assert [r["frequency_bucket"] for r in item["ranges"]] == ["2x"]
    assert item["ranges"][0]["uncalibrated"] is True and item["weekly_uncalibrated"] is True

    # Reenviar a copia igual mantem a marca; series novas a tiram.
    url = f"/muscle-volume-classes/{classe['id']}/ranges"
    params = {"counting_mode": "primary_secondary"}
    r = client.put(url, params=params, json={"ranges": [{"frequency_bucket": "2x", "sets_min": 9, "sets_max": 16}]},
                   headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["item"]["ranges"][0]["uncalibrated"] is True
    r = client.put(url, params=params, json={"ranges": [{"frequency_bucket": "2x", "sets_min": 12, "sets_max": 20}]},
                   headers=HEADERS)
    faixa = r.json()["item"]["ranges"][0]
    assert (faixa["uncalibrated"], faixa["sets_source"], float(faixa["sets_min"])) == (False, "admin", 12.0)
    r = client.put(f"/muscle-volume-classes/{classe['id']}", params=params,
                   json={"weekly_sets_min": 24, "weekly_sets_max": 40}, headers=HEADERS)
    assert r.status_code == 200, r.text
    assert (r.json()["item"]["weekly_uncalibrated"], r.json()["item"]["weekly_source"]) == (False, "admin")

    # primary_only intocado: 3 faixas, meta 18-32.
    po = client.get("/muscle-volume-classes", headers=HEADERS).json()["items"][0]
    assert len(po["ranges"]) == 3 and (float(po["weekly_sets_min"]), float(po["weekly_sets_max"])) == (18.0, 32.0)
    # Regravar primary_only (tabela da 057) nao toca a filha do modo ponderado.
    client.put(url, json={"ranges": _FAIXAS_VALIDAS[:1]}, headers=HEADERS)
    ps = client.get("/muscle-volume-classes", params=params, headers=HEADERS).json()["items"][0]
    assert [(r["frequency_bucket"], float(r["sets_min"])) for r in ps["ranges"]] == [("2x", 12.0)]
    assert client.get("/muscle-volume-classes", params={"counting_mode": "x"}, headers=HEADERS).status_code == 422


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_cria_edita_e_lista_com_faixas_e_grupos(client, raw):
    classe = _classe(client, "Grandes", sort_order=1, weekly_sets_min=18, weekly_sets_max=32)
    assert classe["weekly_source"] == "admin"
    assert classe["ranges"] == [] and classe["muscle_group_ids"] == []

    r = client.put(f"/muscle-volume-classes/{classe['id']}/ranges", json={"ranges": _FAIXAS_VALIDAS}, headers=HEADERS)
    assert r.status_code == 200, r.text
    faixas = r.json()["item"]["ranges"]
    assert [f["frequency_bucket"] for f in faixas] == ["1x", "2x", "3x_plus"]
    assert faixas[1]["exercises_min"] is None and faixas[1]["exercises_source"] is None
    assert {f["sets_source"] for f in faixas} == {"admin"}

    peito = _grupo(client, "Peitoral")
    r = client.put(f"/muscle-volume-classes/{classe['id']}/groups", json={"muscle_group_ids": [peito["id"]]}, headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["item"]["muscle_group_ids"] == [peito["id"]]

    r = client.put(f"/muscle-volume-classes/{classe['id']}", json={"name": "Grandes grupos"}, headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["item"]["name"] == "Grandes grupos"

    itens = client.get("/muscle-volume-classes", headers=HEADERS).json()["items"]
    assert [i["name"] for i in itens] == ["Grandes grupos"]
    assert itens[0]["muscle_group_ids"] == [peito["id"]]


def test_nome_duplicado_e_409(client, raw):
    _classe(client, "Pequenos")
    r = client.post("/muscle-volume-classes", json={"name": "Pequenos"}, headers=HEADERS)
    assert r.status_code == 409, r.text


def test_excluir_com_grupo_associado_e_409_e_sem_grupo_exclui(client, raw):
    classe = _classe(client, "Outros")
    abd = _grupo(client, "Abdômen", volume_class_id=classe["id"])
    assert abd["volume_class_id"] == classe["id"]

    r = client.delete(f"/muscle-volume-classes/{classe['id']}", headers=HEADERS)
    assert r.status_code == 409, r.text

    client.put(f"/muscle-volume-classes/{classe['id']}/groups", json={"muscle_group_ids": []}, headers=HEADERS)
    r = client.delete(f"/muscle-volume-classes/{classe['id']}", headers=HEADERS)
    assert r.status_code == 200, r.text
    assert client.get("/muscle-volume-classes", headers=HEADERS).json()["items"] == []


def test_fk_restrict_segura_a_exclusao_no_banco(client, raw):
    classe = _classe(client, "Grandes")
    _grupo(client, "Quadríceps", volume_class_id=classe["id"])
    with raw.cursor() as cur:
        with pytest.raises(Exception):
            cur.execute("DELETE FROM muscle_volume_classes WHERE id = %s", (classe["id"],))


def test_fatia_e_recusada_com_422(client, raw):
    classe = _classe(client, "Grandes")
    peito = _grupo(client, "Peitoral")
    superior = _grupo(client, "Peitoral superior", methodology_parent_group_id=peito["id"])
    r = client.put(
        f"/muscle-volume-classes/{classe['id']}/groups",
        json={"muscle_group_ids": [peito["id"], superior["id"]]},
        headers=HEADERS,
    )
    assert r.status_code == 422, r.text
    assert "associe o grupo pai" in r.json()["detail"]
    # Nada foi gravado: a transação inteira voltou.
    assert client.get("/muscle-volume-classes", headers=HEADERS).json()["items"][0]["muscle_group_ids"] == []


def test_grupo_inexistente_e_422(client, raw):
    classe = _classe(client, "Grandes")
    r = client.put(f"/muscle-volume-classes/{classe['id']}/groups", json={"muscle_group_ids": [999999]}, headers=HEADERS)
    assert r.status_code == 422, r.text


@pytest.mark.parametrize(
    "faixas",
    [
        [{"frequency_bucket": "1x", "sets_min": 20, "sets_max": 15}],
        [{"frequency_bucket": "1x", "sets_min": -1, "sets_max": 15}],
        [{"frequency_bucket": "1x", "sets_min": 10, "sets_max": 15, "exercises_min": 5, "exercises_max": 3}],
        [{"frequency_bucket": "1x", "sets_min": 10, "sets_max": 15, "exercises_min": 3}],
        [{"frequency_bucket": "4x", "sets_min": 10, "sets_max": 15}],
        [{"frequency_bucket": "1x", "sets_min": 10, "sets_max": 15}, {"frequency_bucket": "1x", "sets_min": 9, "sets_max": 12}],
        [{"frequency_bucket": "2x", "sets_max": 15}],
    ],
)
def test_faixas_invalidas_sao_422_e_nao_gravam(client, raw, faixas):
    classe = _classe(client, "Grandes")
    client.put(f"/muscle-volume-classes/{classe['id']}/ranges", json={"ranges": _FAIXAS_VALIDAS}, headers=HEADERS)
    r = client.put(f"/muscle-volume-classes/{classe['id']}/ranges", json={"ranges": faixas}, headers=HEADERS)
    assert r.status_code == 422, r.text
    atual = client.get("/muscle-volume-classes", headers=HEADERS).json()["items"][0]["ranges"]
    assert len(atual) == 3


def test_meta_semanal_invalida_e_422(client, raw):
    classe = _classe(client, "Grandes")
    r = client.put(
        f"/muscle-volume-classes/{classe['id']}", json={"weekly_sets_min": 30, "weekly_sets_max": 10}, headers=HEADERS
    )
    assert r.status_code == 422, r.text


def test_faixa_reenviada_igual_preserva_a_origem(client, raw):
    """A UI reenvia as três linhas; linha intocada não vira `admin`."""
    classe = _classe(client, "Grandes")
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO muscle_volume_class_ranges (class_id, frequency_bucket, sets_min, sets_max, "
            "exercises_min, exercises_max, sets_source, sets_evidence, exercises_source, exercises_evidence) "
            "VALUES (%s, '1x', 15, 20, 4, 5, 'methodology', 'EXG-015', 'methodology', 'EXG-015')",
            (classe["id"],),
        )
    r = client.put(f"/muscle-volume-classes/{classe['id']}/ranges", json={"ranges": _FAIXAS_VALIDAS}, headers=HEADERS)
    assert r.status_code == 200, r.text
    um_x = r.json()["item"]["ranges"][0]
    assert (um_x["sets_source"], um_x["sets_evidence"]) == ("methodology", "EXG-015")
    assert r.json()["item"]["ranges"][1]["sets_source"] == "admin"


def test_mover_grupo_entre_macro_grupos(client, raw):
    grandes = _classe(client, "Grandes")
    pequenos = _classe(client, "Pequenos")
    gluteo = _grupo(client, "Glúteos", volume_class_id=pequenos["id"])

    r = client.put(f"/muscle-volume-classes/{grandes['id']}/groups", json={"muscle_group_ids": [gluteo["id"]]}, headers=HEADERS)
    assert r.status_code == 200, r.text
    itens = {i["name"]: i for i in client.get("/muscle-volume-classes", headers=HEADERS).json()["items"]}
    assert itens["Grandes"]["muscle_group_ids"] == [gluteo["id"]]
    assert itens["Pequenos"]["muscle_group_ids"] == []


# ---------------------------------------------------------------------------
# `/muscle-groups` com volume_class_id
# ---------------------------------------------------------------------------


def test_muscle_groups_aceita_devolve_e_valida_volume_class_id(client, raw):
    classe = _classe(client, "Grandes")
    item = _grupo(client, "Dorsal", volume_class_id=classe["id"])
    assert item["volume_class_id"] == classe["id"]

    lista = {i["name"]: i for i in client.get("/muscle-groups", headers=HEADERS).json()["items"]}
    assert lista["Dorsal"]["volume_class_id"] == classe["id"]

    r = client.put(f"/muscle-groups/{item['id']}", json={"volume_class_id": 999999}, headers=HEADERS)
    assert r.status_code == 422, r.text

    r = client.put(f"/muscle-groups/{item['id']}", json={"name": "Dorsal"}, headers=HEADERS)
    assert r.status_code == 200 and r.json()["item"]["volume_class_id"] == classe["id"], "ausente = não mexer"

    r = client.put(f"/muscle-groups/{item['id']}", json={"volume_class_id": None}, headers=HEADERS)
    assert r.status_code == 200 and r.json()["item"]["volume_class_id"] is None


def test_put_de_grupo_recusa_macro_grupo_em_fatia(client, raw):
    """Fase 6, PR 2: o `PUT /muscle-groups/{id}` aplica a mesma regra do
    `PUT /muscle-volume-classes/{id}/groups` - fatia usa o macro-grupo do pai."""
    classe = _classe(client, "Grandes")
    pai = _grupo(client, "Peitoral")
    fatia = _grupo(client, "Peitoral superior", methodology_parent_group_id=pai["id"])

    r = client.put(f"/muscle-groups/{fatia['id']}", json={"volume_class_id": classe["id"]}, headers=HEADERS)
    assert r.status_code == 422, r.text
    assert "associe o grupo pai" in r.json()["detail"]

    # desagregar e associar no mesmo PUT vale: o pai efetivo passa a ser null
    r = client.put(
        f"/muscle-groups/{fatia['id']}",
        json={"methodology_parent_group_id": None, "volume_class_id": classe["id"]},
        headers=HEADERS,
    )
    assert r.status_code == 200 and r.json()["item"]["volume_class_id"] == classe["id"], r.text

    # desassociar (null) uma fatia nunca é recusado
    r = client.put(
        f"/muscle-groups/{fatia['id']}",
        json={"methodology_parent_group_id": pai["id"], "volume_class_id": None},
        headers=HEADERS,
    )
    assert r.status_code == 200, r.text


def test_post_duplicado_de_grupo_preserva_o_macro_grupo(client, raw):
    classe = _classe(client, "Pequenos")
    _grupo(client, "Bíceps", volume_class_id=classe["id"])
    reenviado = _grupo(client, "Bíceps", primary_muscles="Bíceps braquial")
    assert reenviado["volume_class_id"] == classe["id"]


# ---------------------------------------------------------------------------
# Permissões
# ---------------------------------------------------------------------------


def test_nao_admin_le_mas_nao_escreve(non_admin_client, raw):
    # A classe nasce direto no banco: o `monkeypatch` de identidade vale para
    # o processo inteiro, então não dá para usar o cliente admin neste teste.
    with raw.cursor() as cur:
        cur.execute("INSERT INTO muscle_volume_classes (name) VALUES ('Grandes')")
        cid = cur.lastrowid
    lista = non_admin_client.get("/muscle-volume-classes")
    assert lista.status_code == 200, lista.text
    assert [i["name"] for i in lista.json()["items"]] == ["Grandes"]
    chamadas = [
        ("post", "/muscle-volume-classes", {"name": "X"}),
        ("put", f"/muscle-volume-classes/{cid}", {"name": "Y"}),
        ("put", f"/muscle-volume-classes/{cid}/ranges", {"ranges": _FAIXAS_VALIDAS}),
        ("put", f"/muscle-volume-classes/{cid}/groups", {"muscle_group_ids": []}),
        ("delete", f"/muscle-volume-classes/{cid}", None),
    ]
    for metodo, url, corpo in chamadas:
        kwargs = {"json": corpo} if corpo is not None else {}
        assert getattr(non_admin_client, metodo)(url, **kwargs).status_code == 403, (metodo, url)
