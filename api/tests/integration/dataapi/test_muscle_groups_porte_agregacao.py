"""CRUD de agregacao em `/muscle-groups` (migracao 055, Fase 4 PR B), nos
dois backends (PyMySQL e o caminho de PRODUCAO: shim `app/db_dataapi.py` +
duble da RDS Data API), num schema com TODAS as migracoes aplicadas pelo
runner.

Os dois backends porque o UPDATE monta a clausula do pai CONDICIONALMENTE (a
sentinela `_AUSENTE`), o que muda a quantidade de placeholders entre uma
chamada e outra - a classe de bug que este arquivo ja pegou uma vez no
`/exercise-mapping-catalog` (placeholder nomeado que so o PyMySQL tolerava).

M76 Lote 4a (E12): o porte (`size_class`) saiu do payload e do CRUD - o motor
le so o macro-grupo (`volume_class_id`). A coluna fica no banco com o DEFAULT
ate a migracao de contract; aqui se prova que a API nao a le, nao a grava e
ignora (sem 422) o campo vindo de cliente antigo.

O que se prova aqui, em ordem de risco:

1. **POST duplicado PRESERVA a agregacao** (o pai fica fora do
   `ON DUPLICATE KEY UPDATE`).
2. **Pai = proprio id e 422**, e ciclo tambem - a FK nao protege contra isso.
3. **Grupo novo nasce sem pai**; o porte gravado e o DEFAULT, e nenhum
   payload o muda.
4. O `GET` da lista traz o pai e nao traz o porte.
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

MASTER = "muscle-groups-porte-master"
HEADERS = {"X-API-Token": MASTER}

TABLES = (
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
    database = f"{base[: -len('_test')]}_mgporte_test"
    recreate_schema_with_migrations(database=database, **_kwargs())
    kw = _kwargs()
    m.migrate(
        conn_factory=lambda: PyMySQLConnection(
            kw["host"], kw["port"], kw["user"], kw["password"], database
        )
    )
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
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES ('admin-porte', 'x', 'admin')"
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


@pytest.fixture
def client(backend) -> TestClient:
    app = FastAPI()
    app.include_router(emcat.muscle_groups_router)
    return TestClient(app, raise_server_exceptions=False)


def _row(raw, name: str) -> dict:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT id, name, size_class, methodology_parent_group_id FROM muscle_groups WHERE name = %s",
            (name,),
        )
        return cur.fetchone()


def _criar(client, name: str, **extras) -> dict:
    resposta = client.post("/muscle-groups", json={"name": name, **extras}, headers=HEADERS)
    assert resposta.status_code == 200, resposta.text
    return resposta.json()["item"]


# ---------------------------------------------------------------------------
# DEFAULT e leitura
# ---------------------------------------------------------------------------


def test_grupo_novo_nasce_sem_pai_e_sem_porte_no_payload(client, raw):
    item = _criar(client, "Serrátil anterior")
    assert "size_class" not in item
    assert item["methodology_parent_group_id"] is None
    assert _row(raw, "Serrátil anterior")["size_class"] == "unclassified"


def test_a_lista_traz_o_pai_e_nao_traz_o_porte(client, raw):
    peitoral = _criar(client, "Peitoral")
    _criar(client, "Peitoral superior", methodology_parent_group_id=peitoral["id"])
    itens = {item["name"]: item for item in client.get("/muscle-groups", headers=HEADERS).json()["items"]}
    assert "size_class" not in itens["Peitoral"]
    assert itens["Peitoral"]["methodology_parent_group_id"] is None
    assert itens["Peitoral superior"]["methodology_parent_group_id"] == peitoral["id"]


@pytest.mark.parametrize("valor", ["large", "enorme", "", None, 3])
def test_size_class_de_cliente_antigo_e_ignorado_no_post_e_no_put(client, raw, valor):
    """Nem gravado nem recusado: o campo nao existe mais na API (E12)."""
    item = _criar(client, "Tríceps", size_class=valor)
    assert "size_class" not in item
    assert _row(raw, "Tríceps")["size_class"] == "unclassified"
    resposta = client.put(f"/muscle-groups/{item['id']}", json={"size_class": valor}, headers=HEADERS)
    assert resposta.status_code == 200, resposta.text
    assert "size_class" not in resposta.json()["item"]
    assert _row(raw, "Tríceps")["size_class"] == "unclassified"


def test_put_nao_toca_o_porte_gravado(client, raw):
    """Grupo classificado pelo seed da 055: editar o nome nao reescreve a
    coluna (que so sai na migracao de contract)."""
    grupo = _criar(client, "Bíceps")
    with raw.cursor() as cur:
        cur.execute("UPDATE muscle_groups SET size_class = 'small' WHERE id = %s", (grupo["id"],))
    resposta = client.put(f"/muscle-groups/{grupo['id']}", json={"name": "Bíceps braquial"}, headers=HEADERS)
    assert resposta.status_code == 200, resposta.text
    assert _row(raw, "Bíceps braquial")["size_class"] == "small"


# ---------------------------------------------------------------------------
# A armadilha do upsert
# ---------------------------------------------------------------------------


def test_post_duplicado_preserva_a_agregacao(client, raw):
    """O POST e upsert por NOME e a UI reenvia o formulario inteiro; o pai fica
    FORA do `ON DUPLICATE KEY UPDATE` para que re-cadastrar nao apague a
    agregacao - nem quando o cliente e antigo e nem manda a coluna."""
    peitoral = _criar(client, "Peitoral")
    fatia = _criar(client, "Peitoral superior", methodology_parent_group_id=peitoral["id"])

    repetido = _criar(client, "Peitoral superior", primary_muscles="Porção clavicular")
    assert repetido["methodology_parent_group_id"] == peitoral["id"]
    assert repetido["primary_muscles"] == "Porção clavicular"
    assert repetido["id"] == fatia["id"]
    assert _row(raw, "Peitoral superior")["methodology_parent_group_id"] == peitoral["id"]


# ---------------------------------------------------------------------------
# PUT
# ---------------------------------------------------------------------------


def test_put_agrega_e_depois_desagrega_com_null(client, raw):
    """Campo AUSENTE preserva; `null` explicito limpa. Sao pedidos diferentes,
    e um COALESCE sozinho nao saberia distingui-los - por isso a sentinela."""
    pai = _criar(client, "Antebraço anterior")
    fatia = _criar(client, "Antebraço posterior")

    client.put(
        f"/muscle-groups/{fatia['id']}",
        json={"methodology_parent_group_id": pai["id"]},
        headers=HEADERS,
    )
    assert _row(raw, "Antebraço posterior")["methodology_parent_group_id"] == pai["id"]

    # Ausente: preserva.
    client.put(f"/muscle-groups/{fatia['id']}", json={"primary_muscles": "Extensores"}, headers=HEADERS)
    assert _row(raw, "Antebraço posterior")["methodology_parent_group_id"] == pai["id"]

    # Explicitamente null: desagrega.
    resposta = client.put(
        f"/muscle-groups/{fatia['id']}",
        json={"methodology_parent_group_id": None},
        headers=HEADERS,
    )
    assert resposta.status_code == 200, resposta.text
    assert _row(raw, "Antebraço posterior")["methodology_parent_group_id"] is None


# ---------------------------------------------------------------------------
# Recusas: 422, nunca 500
# ---------------------------------------------------------------------------


def test_put_com_pai_igual_ao_proprio_id_e_422(client, raw):
    """A FK aceitaria de bom grado - um grupo pode apontar para qualquer id de
    `muscle_groups`, inclusive o seu. Quem recusa e a validacao."""
    grupo = _criar(client, "Core")
    resposta = client.put(
        f"/muscle-groups/{grupo['id']}",
        json={"methodology_parent_group_id": grupo["id"]},
        headers=HEADERS,
    )
    assert resposta.status_code == 422, resposta.text
    assert "próprio grupo" in resposta.json()["detail"]
    assert _row(raw, "Core")["methodology_parent_group_id"] is None


def test_put_que_fecharia_um_ciclo_e_422(client, raw):
    """A -> B ja existe; tentar B -> A fecharia o laco e a soma das fatias
    nunca terminaria."""
    a = _criar(client, "Costas — largura dorsal", size_class="large")
    b = _criar(client, "Costas — miolo/densidade", size_class="large")
    assert (
        client.put(
            f"/muscle-groups/{b['id']}",
            json={"methodology_parent_group_id": a["id"]},
            headers=HEADERS,
        ).status_code
        == 200
    )
    resposta = client.put(
        f"/muscle-groups/{a['id']}",
        json={"methodology_parent_group_id": b["id"]},
        headers=HEADERS,
    )
    assert resposta.status_code == 422, resposta.text
    assert "ciclo" in resposta.json()["detail"]
    assert _row(raw, "Costas — largura dorsal")["methodology_parent_group_id"] is None


def test_pai_inexistente_e_422_e_nao_500(client, raw):
    """Sem a validacao, a FK recusaria e o erro do driver viraria 500."""
    grupo = _criar(client, "Lombar")
    resposta = client.put(
        f"/muscle-groups/{grupo['id']}",
        json={"methodology_parent_group_id": 999999},
        headers=HEADERS,
    )
    assert resposta.status_code == 422, resposta.text


@pytest.mark.parametrize("valor", ["abc", -1, 0, [1]])
def test_pai_com_valor_nao_numerico_ou_nao_positivo_e_422(client, raw, valor):
    grupo = _criar(client, "Adutores")
    resposta = client.put(
        f"/muscle-groups/{grupo['id']}",
        json={"methodology_parent_group_id": valor},
        headers=HEADERS,
    )
    assert resposta.status_code == 422, resposta.text
