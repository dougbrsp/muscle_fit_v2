"""Fixtures de integracao com MySQL real.

- `schema_lifecycle_guard` (sessao, autouse): varre schemas de teste orfaos de
  execucoes anteriores ANTES da suite comecar e derruba TODOS os schemas desta
  execucao ao final dela - inclusive quando algum teste falha, porque o drop
  esta no teardown do yield (pytest executa finalizadores no caminho de
  excecao e tambem na interrupcao por Ctrl-C).
- `mysql_schema` (sessao): DROP/CREATE do schema desta execucao
  (`<TEST_MYSQL_DATABASE sem _test>_run<token>_test`, sufixo `_test`
  obrigatorio - ver tests/conftest.py) e aplica TODAS as migracoes de
  api/db/migrations/ - os `.sql` do baseline (001..021) e, na ordem de versao,
  todas as `.py` (022..NNN) pelo mesmo `up(db)` que roda em producao. Este
  schema reflete PRODUCAO; nao ha lista de migracoes escrita a mao aqui (M25),
  e uma migracao nova entra sozinha. A conexao usa as variaveis TEST_MYSQL_*
  definidas em tests/conftest.py - nunca o `.env` de dev.
- `db_conn`: conexao da propria camada da aplicacao (`app.db.transaction`,
  backend pymysql forcado no conftest raiz) - commit ao final do teste.
- `catalog`: catalogo minimo reutilizavel (grupos musculares, exercicios +
  contribuicoes, equipamentos, academia e vinculos), recriado do zero a cada
  teste.

Sem MySQL acessivel os testes de integracao FALHAM (nao sao pulados): em CI
eles sao obrigatorios, e um skip silencioso esconderia regressao.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pymysql
import pytest

from tests.support import schema_lifecycle
from tests.support.migrations import recreate_schema_with_all_migrations

# Tabelas escritas pelo catalogo/testes. Limpas (TRUNCATE) antes de cada uso
# de `catalog`, inclusive linhas-semente que as migrations possam inserir.
CATALOG_TABLES = (
    "gym_equipment",
    "gym_equipment_catalog",
    "equipment_exercises",
    "equipment_exercise_mappings",
    "exercise_muscle_contributions",
    "exercise_catalog",
    "exercise_zepp_mappings",
    "equipment",
    "gyms",
    "muscle_groups",
    "users",
)


@pytest.fixture(scope="session", autouse=True)
def schema_lifecycle_guard():
    """Varredura de orfaos na entrada + teardown dos schemas desta execucao.

    Autouse e de escopo de sessao: por ser a fixture de maior escopo com
    autouse, o setup roda antes de qualquer fixture de modulo que crie schema,
    e o finalizador roda no fim da sessao - inclusive com testes falhando, e
    tambem na interrupcao por Ctrl-C (o pytest executa os finalizadores).

    A conexao fica aberta durante toda a sessao segurando o lock nomeado desta
    execucao: e o sinal de "estou viva" que impede uma suite rodando em
    paralelo de derrubar os schemas desta. Se o processo morrer sem teardown
    (kill -9), o servidor libera o lock e a proxima execucao varre os restos.

    Em CI (MySQL efemero por job) a varredura nao encontra nada e o teardown
    derruba schemas de um container que morre em seguida: inofensivo la. O
    ganho real e no MySQL de DEV, que e persistente.
    """
    stem = schema_lifecycle.stem_from_env()
    token = schema_lifecycle.token_from_env()

    try:
        conn = schema_lifecycle.connect(**schema_lifecycle.mysql_kwargs_from_env())
    except pymysql.err.OperationalError:
        # Sem MySQL nao ha o que varrer; `mysql_schema` emite a mensagem boa.
        yield None
        return

    try:
        if token:
            schema_lifecycle.acquire_run_lock(conn, token)
        schema_lifecycle.sweep_orphan_schemas(conn, stem=stem, keep_token=token)
        yield None
    finally:
        try:
            if token:
                schema_lifecycle.drop_run_schemas(conn, stem=stem, token=token)
        finally:
            conn.close()


@pytest.fixture(scope="session")
def mysql_schema(schema_lifecycle_guard) -> str:
    """Schema compartilhado da suite, no formato de PRODUCAO.

    Antes do M25 este ponto aplicava so os `.sql` (001..021) e uma LISTA de
    `.py` escrita a mao. A lista era silenciosa: migracao nova nao aparecia
    nela, o schema nascia sem a tabela e o erro chegava tarde e no lugar errado
    (`Table '..._test.context_events' doesn't exist`, uma hora de CI no PR
    #203 por causa da 048; antes disso a 040 e a 043).

    Agora as `.py` sao DESCOBERTAS no diretorio e aplicadas todas, em ordem de
    versao (`tests/support/migrations.py`). Nao ha nada a registrar aqui: uma
    migracao acrescentada ao diretorio entra neste schema sozinha, no mesmo
    caminho de producao.
    """
    host = os.environ["MYSQL_HOST"]
    port = int(os.environ["MYSQL_PORT"])
    database = os.environ["MYSQL_DATABASE"]
    try:
        recreate_schema_with_all_migrations(
            host=host,
            port=port,
            user=os.environ["MYSQL_USER"],
            password=os.environ["MYSQL_PASSWORD"],
            database=database,
        )
    except pymysql.err.OperationalError as exc:
        pytest.fail(
            f"MySQL de teste inacessivel em {host}:{port} ({exc}). "
            "Configure TEST_MYSQL_HOST/TEST_MYSQL_PORT/TEST_MYSQL_USER/TEST_MYSQL_PASSWORD "
            "(ver docs/local-dev.md, secao de testes).",
            pytrace=False,
        )
    return database


@pytest.fixture
def db_conn(mysql_schema):
    from app.db import transaction

    with transaction() as conn:
        yield conn


def truncate_tables(conn, tables=CATALOG_TABLES) -> None:
    with conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        try:
            for table in tables:
                cur.execute(f"TRUNCATE TABLE `{table}`")
        finally:
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")


@dataclass
class Catalog:
    """Ids do catalogo minimo, por nome legivel."""

    user_id: int = 0
    gym_id: int = 0
    other_gym_id: int = 0
    muscle_groups: dict[str, int] = field(default_factory=dict)
    exercises: dict[str, int] = field(default_factory=dict)
    equipment: dict[str, int] = field(default_factory=dict)


MUSCLE_GROUPS = ("Peitoral", "Tríceps", "Ombros", "Costas", "Bíceps", "Quadríceps")

# nome -> (id fixo, [(grupo, role, stimulus_weight)])
EXERCISES = {
    "Supino reto": (1001, [("Peitoral", "PRIMARY", 1.0), ("Tríceps", "SECONDARY", 0.5), ("Ombros", "SECONDARY", 0.3)]),
    "Crucifixo": (1002, [("Peitoral", "PRIMARY", 1.0)]),
    "Remada baixa": (1003, [("Costas", "PRIMARY", 1.0), ("Bíceps", "SECONDARY", 0.5)]),
    "Agachamento": (1004, [("Quadríceps", "PRIMARY", 1.0)]),
}

# equipamento -> exercicios que ele serve (equipment_exercises)
EQUIPMENT = {
    "Banco reto": ["Supino reto"],
    "Polia": ["Crucifixo", "Remada baixa"],
    "Rack": ["Agachamento"],
}

# equipamentos que a academia principal possui (gym_equipment_catalog)
GYM_EQUIPMENT = ("Banco reto", "Polia")


@pytest.fixture
def catalog(db_conn) -> Catalog:
    conn = db_conn
    truncate_tables(conn)
    cat = Catalog()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, 'admin')",
            ("teste-admin", "x"),
        )
        cat.user_id = cur.lastrowid

        for name in MUSCLE_GROUPS:
            cur.execute("INSERT INTO muscle_groups (name) VALUES (%s)", (name,))
            cat.muscle_groups[name] = cur.lastrowid

        for name, (exercise_id, contributions) in EXERCISES.items():
            # exercise_catalog preserva o id de exercise_zepp_mappings. As FKs
            # de `mapping_id` apontam para o catalogo canonico depois da 023,
            # mas `exercise_zepp_mappings` continua sendo a fonte do id: os
            # dois precisam existir com o MESMO id (como em producao).
            cur.execute(
                "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, %s, %s)",
                (exercise_id, name, name),
            )
            cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, %s)", (exercise_id, name))
            for group, role, weight in contributions:
                cur.execute(
                    """
                    INSERT INTO exercise_muscle_contributions
                      (exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (exercise_id, cat.muscle_groups[group], role, weight, weight),
                )
            cat.exercises[name] = exercise_id

        for name, served in EQUIPMENT.items():
            cur.execute("INSERT INTO equipment (name) VALUES (%s)", (name,))
            equipment_id = cur.lastrowid
            cat.equipment[name] = equipment_id
            for exercise_name in served:
                cur.execute(
                    "INSERT INTO equipment_exercises (equipment_id, exercise_id) VALUES (%s, %s)",
                    (equipment_id, cat.exercises[exercise_name]),
                )

        cur.execute("INSERT INTO gyms (name, owner_user_id) VALUES (%s, %s)", ("Academia Teste", cat.user_id))
        cat.gym_id = cur.lastrowid
        for name in GYM_EQUIPMENT:
            cur.execute(
                "INSERT INTO gym_equipment_catalog (gym_id, equipment_id) VALUES (%s, %s)",
                (cat.gym_id, cat.equipment[name]),
            )

        cur.execute("INSERT INTO gyms (name, owner_user_id) VALUES (%s, %s)", ("Outra Academia", cat.user_id))
        cat.other_gym_id = cur.lastrowid
        cur.execute(
            "INSERT INTO gym_equipment_catalog (gym_id, equipment_id) VALUES (%s, %s)",
            (cat.other_gym_id, cat.equipment["Rack"]),
        )
    conn.commit()
    return cat
