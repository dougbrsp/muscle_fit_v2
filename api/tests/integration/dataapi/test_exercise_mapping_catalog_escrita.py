"""POST/PUT /exercise-mapping-catalog (formulario do catalogo em
/grupos-musculares), nos dois backends
(PyMySQL e o caminho de PRODUCAO: shim `app/db_dataapi.py` + dublê da RDS Data
API), via HTTP com token mestre, num schema proprio com TODAS as migracoes
(001..028) aplicadas pelo runner - o formato da Aurora.

Bug coberto: o SQL dessas rotas usava placeholders nomeados (`%(campo)s`) com
um dict de parametros, mas a camada de dados (`app/db.py`) so aceita `%s`
posicional nos dois backends. PyMySQL: `TypeError` (o dict vira lista de
chaves); Data API: `DataApiError` (0 placeholders `%s` para N parametros).
As duas rotas respondiam 500 em qualquer ambiente.

Tambem cobre a reclassificacao dos exercicios sem vinculo depois do commit
(`reclassify_after_catalog_change`, #89/#91). O `POST /import` legado saiu
no COD-05 (2026-10-09), com 0 chamadas em 14 dias de telemetria.
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import exercise_mapping_catalog as emcat
from app import migrate as m
from app.db_pymysql import Connection as PyMySQLConnection
from tests.support.dataapi_backend import BACKENDS, raw_mysql, use_dataapi
from tests.support.migrations import recreate_schema_with_migrations

MASTER = "catalogo-escrita-master"
TABLES = (
    "exercise_sets",
    "exercises",
    "activities",
    "exercise_aliases",
    "exercise_external_mappings",
    "exercise_muscle_contributions",
    "exercise_catalog",
    "exercise_zepp_mappings",
    "muscle_groups",
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
    database = f"{base[: -len('_test')]}_catalogo_test"
    recreate_schema_with_migrations(database=database, **_kwargs())
    kw = _kwargs()
    m.migrate(conn_factory=lambda: PyMySQLConnection(kw["host"], kw["port"], kw["user"], kw["password"], database))
    return database


@pytest.fixture
def raw(schema):
    with raw_mysql(database=schema, **_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            try:
                for table in TABLES:
                    cur.execute(f"TRUNCATE TABLE `{table}`")
            finally:
                cur.execute("SET FOREIGN_KEY_CHECKS = 1")
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('admin-catalogo', 'x', 'admin')")
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


@pytest.fixture
def client(backend) -> TestClient:
    app = FastAPI()
    app.include_router(emcat.router)
    return TestClient(app, raise_server_exceptions=False)


HEADERS = {"X-API-Token": MASTER}


def _one(raw, sql, params=()):
    with raw.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _all(raw, sql, params=()):
    with raw.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _unlinked_exercise(raw, name: str) -> int:
    with raw.cursor() as cur:
        user_id = _one(raw, "SELECT id FROM users LIMIT 1")["id"]
        cur.execute(
            "INSERT INTO activities (user_id, local_date, name, activity_type) VALUES (%s, CURDATE(), 'Treino', 'Musculacao')",
            (user_id,),
        )
        cur.execute(
            "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count) VALUES (%s, 1, %s, NULL, 1)",
            (cur.lastrowid, name),
        )
        return cur.lastrowid


def _payload(**overrides) -> dict:
    payload = {
        "exercise_name": "Rosca Scott no banco",
        "amazfit_name": "Preacher Curl",
        "equipment": "Banco Scott",
        "body_position": "Sentado",
        "movement": "Flexao de cotovelo",
        "trajectory": "Arco",
        "biomechanical_pattern": "Puxar",
        "primary_joint": "Cotovelo",
        "resistance_vector": "Vertical",
        "primary_group": "Bíceps",
        "secondary_group": "Antebraço",
        "keywords": "rosca, scott",
        "description": "Rosca no banco Scott",
        "mapping_status": "confirmed",
        "source": "manual",
        "is_active": True,
    }
    payload.update(overrides)
    return payload


def _catalog_state(raw, exercise_id: int) -> dict:
    row = _one(
        raw,
        "SELECT name, body_position, movement, trajectory, biomechanical_pattern, primary_joint, resistance_vector, "
        "keywords, description, is_active FROM exercise_catalog WHERE id = %s",
        (exercise_id,),
    )
    external = _all(
        raw,
        "SELECT provider, external_name, mapping_status, source, is_active FROM exercise_external_mappings WHERE exercise_id = %s",
        (exercise_id,),
    )
    contributions = _all(
        raw,
        "SELECT mg.name, emc.role, emc.stimulus_weight, emc.is_active FROM exercise_muscle_contributions emc "
        "JOIN muscle_groups mg ON mg.id = emc.muscle_group_id WHERE emc.exercise_id = %s ORDER BY emc.role, mg.name",
        (exercise_id,),
    )
    return {"catalog": row, "external": external, "contributions": [tuple(c.values()) for c in contributions]}


def test_post_cria_exercicio_novo_e_reclassifica_exercicio_sem_vinculo(backend, client, raw):
    pending = _unlinked_exercise(raw, "Rosca Scott no banco")

    response = client.post("/exercise-mapping-catalog", json=_payload(), headers=HEADERS)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    item = body["item"]
    new_id = item["id"]
    assert isinstance(new_id, int) and new_id > 0
    expected_item = {
        "exercise_name": "Rosca Scott no banco",
        "amazfit_name": "Preacher Curl",
        "equipment": None,  # sem coluna equivalente em exercise_catalog (contrato documentado)
        "body_position": "Sentado",
        "movement": "Flexao de cotovelo",
        "trajectory": "Arco",
        "biomechanical_pattern": "Puxar",
        "primary_joint": "Cotovelo",
        "resistance_vector": "Vertical",
        "primary_group": "Bíceps",
        "secondary_group": "Antebraço",
        "keywords": "rosca, scott",
        "description": "Rosca no banco Scott",
        "mapping_status": "confirmed",
        "source": "manual",
        "is_active": 1,
    }
    assert {key: item[key] for key in expected_item} == expected_item
    assert set(item) == set(expected_item) | {"id", "primary_group_id", "secondary_group_id", "created_at", "updated_at"}

    state = _catalog_state(raw, new_id)
    assert state["catalog"]["name"] == "Rosca Scott no banco" and state["catalog"]["is_active"] == 1
    assert state["external"] == [
        {"provider": "AMAZFIT", "external_name": "Preacher Curl", "mapping_status": "confirmed", "source": "manual", "is_active": 1}
    ]
    assert [(name, role, float(weight), active) for name, role, weight, active in state["contributions"]] == [
        ("Bíceps", "PRIMARY", 1.0, 1),
        ("Antebraço", "SECONDARY", 0.5, 1),
    ]
    # Reclassificacao depois do commit: o exercicio sem vinculo passa a apontar para o item novo.
    assert _one(raw, "SELECT mapping_id FROM exercises WHERE id = %s", (pending,))["mapping_id"] == new_id


def test_post_com_nome_existente_atualiza_o_mesmo_exercicio(backend, client, raw):
    first = client.post("/exercise-mapping-catalog", json=_payload(), headers=HEADERS)
    assert first.status_code == 200, first.text
    second = client.post(
        "/exercise-mapping-catalog", json=_payload(amazfit_name="Scott Curl", secondary_group=None), headers=HEADERS
    )
    assert second.status_code == 200, second.text
    assert second.json()["item"]["id"] == first.json()["item"]["id"]
    assert second.json()["item"]["amazfit_name"] == "Scott Curl"
    assert second.json()["item"]["secondary_group"] is None
    assert _one(raw, "SELECT COUNT(*) AS n FROM exercise_catalog")["n"] == 1


def test_put_atualiza_catalogo_e_reclassifica(backend, client, raw):
    with raw.cursor() as cur:
        cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (501, 'Remada curvada')")
    pending = _unlinked_exercise(raw, "Remada cavalinho")

    response = client.put(
        "/exercise-mapping-catalog/501",
        json=_payload(exercise_name="Remada cavalinho", amazfit_name="T-Bar Row", primary_group="Costas", secondary_group="Bíceps", is_active="sim"),
        headers=HEADERS,
    )

    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert (item["id"], item["exercise_name"], item["amazfit_name"], item["primary_group"], item["secondary_group"]) == (
        501,
        "Remada cavalinho",
        "T-Bar Row",
        "Costas",
        "Bíceps",
    )
    state = _catalog_state(raw, 501)
    assert state["catalog"]["name"] == "Remada cavalinho" and state["catalog"]["description"] == "Rosca no banco Scott"
    assert [(n, r) for n, r, _, _ in state["contributions"]] == [("Costas", "PRIMARY"), ("Bíceps", "SECONDARY")]
    assert _one(raw, "SELECT mapping_id FROM exercises WHERE id = %s", (pending,))["mapping_id"] == 501


def test_put_de_exercicio_inexistente_responde_404(backend, client, raw):
    response = client.put("/exercise-mapping-catalog/999999", json=_payload(), headers=HEADERS)
    assert response.status_code == 404
    assert response.json() == {"detail": "Mapeamento não encontrado."}


def test_reclassificacao_e_chamada_depois_do_commit(backend, client, raw, monkeypatch):
    calls = []

    def spy(source):
        # O item ja esta visivel numa conexao independente: commit feito antes.
        visible = _one(raw, "SELECT COUNT(*) AS n FROM exercise_catalog WHERE name = %s", ("Rosca Scott no banco",))["n"]
        calls.append((source, visible))
        return {"processed": 0, "updated": 0, "unresolved": 0, "written": 0}

    monkeypatch.setattr(emcat, "reclassify_after_catalog_change", spy)

    created = client.post("/exercise-mapping-catalog", json=_payload(), headers=HEADERS)
    assert created.status_code == 200, created.text
    updated = client.put(f"/exercise-mapping-catalog/{created.json()['item']['id']}", json=_payload(), headers=HEADERS)
    assert updated.status_code == 200, updated.text

    assert calls == [("exercise-mapping-catalog.create", 1), ("exercise-mapping-catalog.update", 1)]


# ---------------------------------------------------------------------------
# DELETE /exercise-mapping-catalog/{id} ("Inativar" em /grupos-musculares).
#
# Bug: a rota fazia `UPDATE exercise_zepp_mappings SET is_active = 0` (tabela
# legada) enquanto GET/POST/PUT ja usam exercise_catalog. Um item criado pelo
# POST so existe em exercise_catalog, entao o UPDATE nao encontrava linha
# (rowcount 0) e a rota respondia 404 "Mapeamento nao encontrado.", com o
# item continuando ativo.
#
# Semantica: SOFT delete (exercise_catalog.is_active = 0), o que a UI promete
# ("Inativar ... o historico de treinos ja registrados nao e afetado"). Hard
# delete apagaria em CASCADE aliases, nomes externos, contribuicoes,
# equipment_exercises/gym_equipment/equipment_exercise_mappings e poria
# exercises.mapping_id em NULL (regras de FK da Aurora).
# ---------------------------------------------------------------------------


def _catalog_row(raw, exercise_id: int):
    return _one(raw, "SELECT id, is_active FROM exercise_catalog WHERE id = %s", (exercise_id,))


def test_post_put_delete_get_inativa_o_item_no_catalogo(backend, client, raw):
    created = client.post("/exercise-mapping-catalog", json=_payload(), headers=HEADERS)
    assert created.status_code == 200, created.text
    item_id = created.json()["item"]["id"]

    updated = client.put(
        f"/exercise-mapping-catalog/{item_id}", json=_payload(description="Editado antes de inativar"), headers=HEADERS
    )
    assert updated.status_code == 200, updated.text

    deleted = client.delete(f"/exercise-mapping-catalog/{item_id}", headers=HEADERS)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"status": "ok", "id": item_id, "is_active": False}

    fetched = client.get(f"/exercise-mapping-catalog/{item_id}", headers=HEADERS)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["is_active"] == 0
    assert fetched.json()["description"] == "Editado antes de inativar"

    # Soft delete: a linha continua, so inativa; dependentes preservados (reativavel pelo PUT).
    assert _catalog_row(raw, item_id) == {"id": item_id, "is_active": 0}
    state = _catalog_state(raw, item_id)
    assert [e["external_name"] for e in state["external"] if e["is_active"]] == ["Preacher Curl"]
    assert [(n, r, a) for n, r, _, a in state["contributions"]] == [("Bíceps", "PRIMARY", 1), ("Antebraço", "SECONDARY", 1)]

    active_ids = [i["id"] for i in client.get("/exercise-mapping-catalog", headers=HEADERS).json()["items"]]
    all_ids = [i["id"] for i in client.get("/exercise-mapping-catalog?active_only=false", headers=HEADERS).json()["items"]]
    assert item_id not in active_ids
    assert item_id in all_ids

    reactivated = client.put(f"/exercise-mapping-catalog/{item_id}", json=_payload(is_active=True), headers=HEADERS)
    assert reactivated.status_code == 200, reactivated.text
    assert _catalog_row(raw, item_id) == {"id": item_id, "is_active": 1}


def test_delete_de_item_ja_inativo_e_idempotente(backend, client, raw):
    with raw.cursor() as cur:
        cur.execute("INSERT INTO exercise_catalog (id, name, is_active) VALUES (601, 'Crucifixo inativo', 0)")

    response = client.delete("/exercise-mapping-catalog/601", headers=HEADERS)

    assert response.status_code == 200, response.text
    assert _catalog_row(raw, 601) == {"id": 601, "is_active": 0}


def test_delete_de_item_inexistente_responde_404(backend, client, raw):
    response = client.delete("/exercise-mapping-catalog/999999", headers=HEADERS)
    assert response.status_code == 404
    assert response.json() == {"detail": "Mapeamento não encontrado."}


def test_delete_ignora_o_legado_exercise_zepp_mappings(backend, client, raw):
    # Mesmo id so no legado: nao existe no catalogo canonico -> 404, legado intocado.
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name, is_active) VALUES (777, 'So no legado', 'Legacy', 1)"
        )
    response = client.delete("/exercise-mapping-catalog/777", headers=HEADERS)
    assert response.status_code == 404
    assert _one(raw, "SELECT is_active FROM exercise_zepp_mappings WHERE id = 777")["is_active"] == 1


def test_delete_preserva_historico_e_item_inativo_nao_recebe_vinculo_novo(backend, client, raw):
    created = client.post("/exercise-mapping-catalog", json=_payload(), headers=HEADERS)
    assert created.status_code == 200, created.text
    item_id = created.json()["item"]["id"]

    linked = _unlinked_exercise(raw, "Rosca Scott no banco")
    with raw.cursor() as cur:
        cur.execute("UPDATE exercises SET mapping_id = %s WHERE id = %s", (item_id, linked))
    # Sem vinculo, com o mesmo nome: so a reclassificacao do DELETE o processa.
    pending = _unlinked_exercise(raw, "Rosca Scott no banco")

    deleted = client.delete(f"/exercise-mapping-catalog/{item_id}", headers=HEADERS)
    assert deleted.status_code == 200, deleted.text

    # Historico: o treino ja registrado continua apontando para o item (sem SET NULL/CASCADE).
    assert _one(raw, "SELECT mapping_id FROM exercises WHERE id = %s", (linked,))["mapping_id"] == item_id
    assert _one(raw, "SELECT COUNT(*) AS n FROM exercises")["n"] == 2
    # A reclassificacao usa so o catalogo ativo: o item inativado nao recebe vinculo novo.
    assert _one(raw, "SELECT mapping_id FROM exercises WHERE id = %s", (pending,))["mapping_id"] is None


def test_delete_reclassifica_depois_do_commit(backend, client, raw, monkeypatch):
    with raw.cursor() as cur:
        cur.execute("INSERT INTO exercise_catalog (id, name, is_active) VALUES (602, 'Supino inclinado', 1)")
    calls = []

    def spy(source):
        # A inativacao ja esta visivel numa conexao independente: commit feito antes.
        calls.append((source, _catalog_row(raw, 602)["is_active"]))
        return {"processed": 0, "updated": 0, "unresolved": 0, "written": 0}

    monkeypatch.setattr(emcat, "reclassify_after_catalog_change", spy)

    assert client.delete("/exercise-mapping-catalog/602", headers=HEADERS).status_code == 200
    assert client.delete("/exercise-mapping-catalog/999999", headers=HEADERS).status_code == 404

    assert calls == [("exercise-mapping-catalog.deactivate", 0)]
