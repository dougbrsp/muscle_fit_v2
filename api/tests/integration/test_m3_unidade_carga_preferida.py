"""M3 - unidade de carga preferida (kg x lb) no MySQL de verdade.

Cobre as duas pontas que o teste unitario nao alcanca:

1. a migracao 040 APLICADA no schema real (tipo/nulabilidade/default da coluna,
   e o fato de uma linha criada ANTES dela continuar valendo 'kg');
2. `GET/PUT /users/me` gravando e devolvendo a preferencia, com a leitura feita
   numa transacao PROPRIA - o que prova a durabilidade, nao so o status HTTP.

E cobre a regra que nao pode cair: por mais que o usuario prefira libras, a
serie continua sendo gravada em QUILOS em `exercise_sets.weight_kg` (D12).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import migrate as m
from app import users
from app.db import transaction
from app.security import Identity, get_current_identity
from tests.support.safety_032_033 import apply as apply_migration

MIGRATION_040 = m.DEFAULT_MIGRATIONS_DIR / "040_preferencia_unidade_carga.py"


@pytest.fixture(scope="module", autouse=True)
def migracao_040(mysql_schema):
    """A fixture `mysql_schema` ja aplica TODAS as migracoes `.py` (M25), a 040
    inclusive. Aqui so provamos que o schema compartilhado esta CONVERGIDO com
    ela: reexecutar a 040 pelo mesmo `up(db)` de producao nao planeja nenhum
    statement.

    O formato exato do ALTER (1 statement, `ENUM('kg','lb') NOT NULL DEFAULT
    'kg'` depois de `timezone`) continua coberto contra o recorte real da
    Aurora em `tests/unit/test_migration_040_plano_aurora.py`
    (`test_producao_hoje_executa_um_unico_statement`)."""
    with transaction() as conn:
        statements = apply_migration(conn, MIGRATION_040)
    assert statements == [], f"schema compartilhado nao convergiu com a 040: {statements}"
    return statements


@pytest.fixture
def client(catalog) -> TestClient:
    app = FastAPI()
    app.include_router(users.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=catalog.user_id, role="regular", source="session"
    )
    return TestClient(app, raise_server_exceptions=False)


def _coluna() -> dict:
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users' "
                "AND COLUMN_NAME = 'preferred_weight_unit'"
            )
            return cur.fetchone()


def _unidade_gravada(user_id: int) -> str:
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT preferred_weight_unit FROM users WHERE id = %s", (user_id,))
            return cur.fetchone()["preferred_weight_unit"]


# ---------------------------------------------------------------------------
# migracao 040
# ---------------------------------------------------------------------------


def test_a_coluna_existe_com_o_formato_esperado():
    info = _coluna()
    assert info is not None, "migracao 040 nao aplicada pelo runner"
    assert info["COLUMN_TYPE"].lower().replace(", ", ",") == "enum('kg','lb')"
    assert info["IS_NULLABLE"] == "NO"
    assert info["COLUMN_DEFAULT"] == "kg"


def test_usuario_criado_sem_informar_nada_fica_em_kg(catalog):
    """O default e o que garante que o deploy nao muda o comportamento de
    ninguem: quem ja usava o sistema continua em quilos."""
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash, role, is_active) VALUES (%s, %s, 'regular', 1)",
                ("m3-sem-preferencia", "x"),
            )
            novo_id = cur.lastrowid
    assert _unidade_gravada(novo_id) == "kg"


def test_reaplicar_a_040_no_schema_ja_migrado_nao_executa_nada():
    with transaction() as conn:
        db = m.MigrationDB(conn, dry_run=True)
        assert m.load_python_migration(MIGRATION_040).__globals__["plan"](db) == []
        assert db.statements == []


# ---------------------------------------------------------------------------
# GET/PUT /users/me
# ---------------------------------------------------------------------------


def test_get_me_sempre_devolve_a_unidade(client, catalog):
    resposta = client.get("/users/me")
    assert resposta.status_code == 200
    assert resposta.json()["item"]["preferred_weight_unit"] == "kg"


def test_put_me_persiste_libras(client, catalog):
    resposta = client.put("/users/me", json={"preferred_weight_unit": "lb"})
    assert resposta.status_code == 200
    assert resposta.json()["item"]["preferred_weight_unit"] == "lb"
    # releitura em transacao propria: prova que foi commitado
    assert _unidade_gravada(catalog.user_id) == "lb"
    assert client.get("/users/me").json()["item"]["preferred_weight_unit"] == "lb"


def test_put_me_sem_o_campo_preserva_a_preferencia(client, catalog):
    client.put("/users/me", json={"preferred_weight_unit": "lb"})
    resposta = client.put("/users/me", json={"full_name": "Doug M3"})
    assert resposta.status_code == 200
    assert resposta.json()["item"]["preferred_weight_unit"] == "lb"
    assert _unidade_gravada(catalog.user_id) == "lb"


@pytest.mark.parametrize("valor", ["libras", "pound", "g", "KGS", "quilos", 1])
def test_unidade_invalida_e_400_e_nao_apaga_a_preferencia(client, catalog, valor):
    """Nunca trocar silenciosamente por 'kg': um typo do cliente viraria
    "minha preferencia sumiu" sem ninguem saber por que."""
    client.put("/users/me", json={"preferred_weight_unit": "lb"})
    resposta = client.put("/users/me", json={"preferred_weight_unit": valor})
    assert resposta.status_code == 400
    assert "preferred_weight_unit" in resposta.json()["detail"]
    assert _unidade_gravada(catalog.user_id) == "lb"


@pytest.mark.parametrize("valor", ["KG", " lb "])
def test_unidade_valida_com_espaco_ou_maiuscula_e_normalizada(client, catalog, valor):
    resposta = client.put("/users/me", json={"preferred_weight_unit": valor})
    assert resposta.status_code == 200
    assert resposta.json()["item"]["preferred_weight_unit"] == valor.strip().lower()


def test_preferir_libras_nao_muda_a_unidade_do_banco(client, catalog):
    """A coluna `exercise_sets.weight_kg` continua em QUILOS (D12): a
    preferencia e so de entrada/exibicao, e nenhum motor a le."""
    client.put("/users/me", json={"preferred_weight_unit": "lb"})
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_sets' AND COLUMN_NAME = 'weight_kg'"
            )
            assert cur.fetchone()["COLUMN_TYPE"].lower() == "decimal(8,2)"
            cur.execute(
                "SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_sets' "
                "AND COLUMN_NAME IN ('weight_lb', 'weight_unit')"
            )
            assert cur.fetchone()["n"] == 0
