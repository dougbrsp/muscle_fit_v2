"""Testes de scripts/prod_to_dev.py (cópia de produção -> DEV), sem AWS e sem banco.

O script fica fora de `api/` (não é importado pelo app nem entra em imagem);
aqui ele é carregado pelo caminho para rodar no job `backend unit` do CI.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "prod_to_dev.py"
_spec = importlib.util.spec_from_file_location("prod_to_dev", _SCRIPT)
p2d = importlib.util.module_from_spec(_spec)
sys.modules["prod_to_dev"] = p2d
_spec.loader.exec_module(p2d)

UID = 1
OTHER = 2


# ---------------------------------------------------------------------------
# Dublês
# ---------------------------------------------------------------------------


def _table(name, cols, pk=("id",), nullable=(), generated=()):
    return p2d.TableSchema(
        name,
        [p2d.Column(c, c in nullable, c in generated) for c in cols],
        list(pk),
    )


def _schema():
    t = [
        _table("users", ["id", "username", "password_hash", "full_name", "email", "role", "is_active",
                         "intervals_api_key_encrypted", "failed_login_attempts", "locked_until"],
               nullable=("full_name", "email", "intervals_api_key_encrypted", "locked_until")),
        _table("gyms", ["id", "owner_user_id", "name"], nullable=("owner_user_id",)),
        _table("training_types", ["id", "name", "created_by_user_id"]),
        _table("activities", ["id", "user_id", "gym_id", "import_id", "workout_plan_id"],
               nullable=("gym_id", "import_id", "workout_plan_id")),
        _table("exercises", ["id", "activity_id", "name"]),
        _table("exercise_sets", ["id", "exercise_id", "reps"]),
        _table("imports", ["id", "payload_hash"]),
        _table("devices", ["id", "user_id", "token_hash"]),
        _table("user_sessions", ["id", "user_id", "token_hash"]),
        _table("workout_plans", ["id", "user_id", "template_id"], nullable=("template_id",)),
        _table("workout_plan_template_assignments",
               ["id", "template_id", "user_id", "assigned_by_user_id", "unassigned_by_user_id", "active_user_id"],
               nullable=("unassigned_by_user_id", "active_user_id"), generated=("active_user_id",)),
        _table("workout_plan_templates", ["id", "name", "created_by_user_id", "generation_log_id"],
               nullable=("generation_log_id",)),
        _table("workout_plan_template_slots", ["id", "template_id", "name"]),
        _table("schema_migration_files", ["filename", "checksum"], pk=("filename",)),
    ]
    fks = [
        ("gyms", "owner_user_id", "users", "id"),
        ("training_types", "created_by_user_id", "users", "id"),
        ("activities", "user_id", "users", "id"),
        ("activities", "gym_id", "gyms", "id"),
        ("activities", "import_id", "imports", "id"),
        ("activities", "workout_plan_id", "workout_plans", "id"),
        ("exercises", "activity_id", "activities", "id"),
        ("exercise_sets", "exercise_id", "exercises", "id"),
        ("workout_plans", "user_id", "users", "id"),
        ("workout_plans", "template_id", "workout_plan_templates", "id"),
        ("workout_plan_template_assignments", "template_id", "workout_plan_templates", "id"),
        ("workout_plan_template_assignments", "user_id", "users", "id"),
        ("workout_plan_template_assignments", "assigned_by_user_id", "users", "id"),
        ("workout_plan_templates", "created_by_user_id", "users", "id"),
        ("workout_plan_template_slots", "template_id", "workout_plan_templates", "id"),
    ]
    return p2d.Schema({x.name: x for x in t}, fks)


def _prod_data():
    return {
        "users": [
            {"id": UID, "username": "dono", "password_hash": "$2b$12$dono", "full_name": "Dono", "email": "dono@x",
             "role": "admin", "is_active": 1, "intervals_api_key_encrypted": "cifrado", "failed_login_attempts": 3,
             "locked_until": "2026-10-07 10:00:00"},
            {"id": OTHER, "username": "terceiro", "password_hash": "$2b$12$terceiro", "full_name": "Terceiro",
             "email": "terceiro@x", "role": "regular", "is_active": 1, "intervals_api_key_encrypted": None,
             "failed_login_attempts": 0, "locked_until": None},
        ],
        "gyms": [{"id": 10, "owner_user_id": OTHER, "name": "Academia"}, {"id": 11, "owner_user_id": UID, "name": "Minha"}],
        "training_types": [{"id": 5, "name": "Hipertrofia", "created_by_user_id": OTHER}],
        "imports": [{"id": 70, "payload_hash": "a"}, {"id": 71, "payload_hash": "b"}],
        "activities": [
            {"id": 100, "user_id": UID, "gym_id": 10, "import_id": 70, "workout_plan_id": 300},
            {"id": 101, "user_id": OTHER, "gym_id": 10, "import_id": 71, "workout_plan_id": 301},
        ],
        "exercises": [{"id": 200, "activity_id": 100, "name": "Supino"}, {"id": 201, "activity_id": 101, "name": "Remada"}],
        "exercise_sets": [{"id": 900, "exercise_id": 200, "reps": 10}, {"id": 901, "exercise_id": 201, "reps": 8}],
        "devices": [{"id": 1, "user_id": UID, "token_hash": "f" * 64}, {"id": 2, "user_id": OTHER, "token_hash": "e" * 64}],
        "user_sessions": [{"id": 1, "user_id": UID, "token_hash": "s" * 64}],
        "workout_plans": [{"id": 300, "user_id": UID, "template_id": 400}, {"id": 301, "user_id": OTHER, "template_id": 402}],
        "workout_plan_template_assignments": [
            {"id": 1, "template_id": 400, "user_id": UID, "assigned_by_user_id": OTHER, "unassigned_by_user_id": OTHER},
            {"id": 2, "template_id": 402, "user_id": OTHER, "assigned_by_user_id": UID, "unassigned_by_user_id": None},
        ],
        "workout_plan_templates": [
            {"id": 400, "name": "Modelo do admin atribuído", "created_by_user_id": OTHER, "generation_log_id": 77},
            {"id": 401, "name": "Modelo criado pelo dono", "created_by_user_id": UID, "generation_log_id": None},
            {"id": 402, "name": "Modelo de terceiro", "created_by_user_id": OTHER, "generation_log_id": None},
        ],
        "workout_plan_template_slots": [
            {"id": 1, "template_id": 400, "name": "A"},
            {"id": 2, "template_id": 401, "name": "B"},
            {"id": 3, "template_id": 402, "name": "C"},
        ],
    }


class FakeReader:
    """Mesma interface de `DataApiReader.fetch`, sobre tabelas em memória."""

    def __init__(self, data):
        self.data = data
        self.calls = []

    def fetch(self, table, column=None, values=None):
        self.calls.append((table.name, column))
        rows = self.data.get(table.name, [])
        if column is not None:
            wanted = set(values or [])
            rows = [r for r in rows if r.get(column) in wanted]
        cols = table.insertable_columns
        return [{c: r.get(c) for c in cols} for r in rows]


def _prepared():
    schema = _schema()
    return schema, *p2d.prepare(FakeReader(_prod_data()), schema, UID)


# ---------------------------------------------------------------------------
# Classificação
# ---------------------------------------------------------------------------


def test_tabela_de_producao_sem_classificacao_aborta():
    with pytest.raises(p2d.AbortError, match="tabela_nova"):
        p2d.check_classification(["users", "activities", "tabela_nova"])


def test_nenhuma_tabela_classificada_duas_vezes():
    kinds = p2d.classified_tables()
    assert kinds["users"] == "user"
    assert kinds["user_sessions"] == "excluded"
    assert kinds["schema_migration_files"] == "untouched"


# ---------------------------------------------------------------------------
# Filtro por usuário e fechamento de FKs
# ---------------------------------------------------------------------------


def test_so_linhas_do_usuario_nas_tabelas_com_dono_e_nos_filhos():
    _, snap, _ = _prepared()
    assert [r["id"] for r in snap["activities"]] == [100]
    assert [r["id"] for r in snap["exercises"]] == [200]
    assert [r["id"] for r in snap["exercise_sets"]] == [900]
    assert [r["id"] for r in snap["workout_plans"]] == [300]
    assert [r["id"] for r in snap["workout_plan_template_assignments"]] == [1]
    assert [r["id"] for r in snap["devices"]] == [1]


def test_globais_inteiras_e_referenciadas_so_o_que_o_usuario_usa():
    _, snap, _ = _prepared()
    assert {r["id"] for r in snap["gyms"]} == {10, 11}
    assert [r["id"] for r in snap["imports"]] == [70]


def test_modelos_criados_pelo_usuario_ou_que_ele_usa_e_nunca_os_de_terceiros():
    _, snap, _ = _prepared()
    assert {r["id"] for r in snap["workout_plan_templates"]} == {400, 401}
    assert {r["id"] for r in snap["workout_plan_template_slots"]} == {1, 2}


def test_referencia_soft_orfa_vira_null():
    _, snap, report = _prepared()
    modelo = next(r for r in snap["workout_plan_templates"] if r["id"] == 400)
    assert modelo["generation_log_id"] is None  # log 77 é de outro usuário: não veio
    assert report.nulled["workout_plan_templates.generation_log_id"] == 1


def test_fechamento_derruba_orfa_nao_anulavel_em_cascata():
    schema = _schema()
    snap = {
        "activities": [{"id": 100, "user_id": UID, "gym_id": None, "import_id": None, "workout_plan_id": None}],
        "exercises": [{"id": 200, "activity_id": 999, "name": "x"}],  # pai ausente, coluna NOT NULL
        "exercise_sets": [{"id": 900, "exercise_id": 200, "reps": 1}],  # órfã depois que 200 cai
    }
    report = p2d.Report()
    p2d.close_references(snap, schema, report)
    assert snap["exercises"] == [] and snap["exercise_sets"] == []
    assert report.dropped == {"exercises (órfã de activities)": 1, "exercise_sets (órfã de exercises)": 1}


# ---------------------------------------------------------------------------
# Outros usuários e colunas sensíveis
# ---------------------------------------------------------------------------


def test_nenhum_dado_de_terceiro_e_placeholder_anonimizado():
    _, snap, report = _prepared()
    users = {r["id"]: r for r in snap["users"]}
    assert set(users) == {UID, p2d.PLACEHOLDER_USER_ID}
    placeholder = users[p2d.PLACEHOLDER_USER_ID]
    assert placeholder["email"] is None and placeholder["is_active"] == 0
    assert placeholder["password_hash"] == "!"
    assert next(g for g in snap["gyms"] if g["id"] == 10)["owner_user_id"] is None  # anulável -> NULL
    assert snap["training_types"][0]["created_by_user_id"] == p2d.PLACEHOLDER_USER_ID  # NOT NULL
    atribuicao = snap["workout_plan_template_assignments"][0]
    assert atribuicao["assigned_by_user_id"] == p2d.PLACEHOLDER_USER_ID
    assert atribuicao["unassigned_by_user_id"] is None
    assert report.user_refs_placeholder["training_types.created_by_user_id"] == 1
    texto = repr(snap)
    assert "terceiro" not in texto.lower().replace("modelo de terceiro", "")


def test_limpeza_de_credenciais_mantem_so_o_hash_de_senha_do_usuario():
    _, snap, report = _prepared()
    dono = next(r for r in snap["users"] if r["id"] == UID)
    assert dono["password_hash"] == "$2b$12$dono"
    assert dono["intervals_api_key_encrypted"] is None
    assert dono["failed_login_attempts"] == 0 and dono["locked_until"] is None
    assert snap["devices"][0]["token_hash"] != "f" * 64
    assert re.fullmatch(r"[0-9a-f]{64}", snap["devices"][0]["token_hash"])
    assert "user_sessions" not in snap
    assert report.cleaned["devices.token_hash"] == 1


def test_verificacao_final_recusa_id_de_terceiro_que_escapou():
    schema = _schema()
    snap = {"users": [{"id": UID}], "gyms": [{"id": 1, "owner_user_id": OTHER, "name": "x"}]}
    with pytest.raises(p2d.AbortError, match="gyms.owner_user_id"):
        p2d.verify_snapshot(snap, schema, UID)


def test_usuario_inexistente_aborta():
    with pytest.raises(p2d.AbortError, match="não existe"):
        p2d.prepare(FakeReader({"users": []}), _schema(), UID)


# ---------------------------------------------------------------------------
# SQL gerado
# ---------------------------------------------------------------------------


def test_sql_limpa_tabelas_classificadas_preserva_migracoes_e_omite_colunas_geradas():
    schema, snap, _ = _prepared()
    stmts = p2d.render_sql(snap, schema)
    assert "DELETE FROM `user_sessions`" in stmts
    assert "DELETE FROM `activities`" in stmts
    assert not any("schema_migration_files" in s for s in stmts)
    insert = next(s for s in stmts if s.startswith("INSERT INTO `workout_plan_template_assignments`"))
    assert "active_user_id" not in insert
    assert stmts[2] == "SET FOREIGN_KEY_CHECKS = 0" and stmts[-1] == "SET FOREIGN_KEY_CHECKS = 1"


def test_sql_literal_preserva_tipos_e_escapa():
    from decimal import Decimal

    assert p2d.sql_literal(None) == "NULL"
    assert p2d.sql_literal(True) == "1"
    assert p2d.sql_literal(42) == "42"
    assert p2d.sql_literal(Decimal("12.50")) == "12.50"
    assert p2d.sql_literal(b"\x00\xff") == "X'00ff'"
    assert p2d.sql_literal("d'água\n\\") == "'d\\'água\\n\\\\'"
    assert p2d.sql_literal('{"a": 1}') == "'{\"a\": 1}'"


# ---------------------------------------------------------------------------
# Trava de destino
# ---------------------------------------------------------------------------

_DOTENV = {"MYSQL_DATABASE": "fitness", "DB_BACKEND": "pymysql", "MYSQL_ROOT_PASSWORD": "x"}


def _dest(host="127.0.0.1", database="fitness"):
    return p2d.Destination(host=host, port=3306, user="root", password="x", database=database)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "mysql", "::1"])
def test_destino_local_aceito(host):
    p2d.validate_destination(_dest(host), _DOTENV, {})


@pytest.mark.parametrize(
    "dest, dotenv, environ, motivo",
    [
        (_dest("musclefit-db.cluster-abc.sa-east-1.rds.amazonaws.com"), _DOTENV, {}, "AWS"),
        (_dest("10.0.0.5"), _DOTENV, {}, "não é o MySQL local"),
        (_dest("arn:aws:rds:sa-east-1:1:cluster:musclefit-db"), _DOTENV, {}, "AWS"),
        (_dest(), _DOTENV, {"DB_BACKEND": "dataapi"}, "DB_BACKEND=dataapi"),
        (_dest(), {**_DOTENV, "DB_BACKEND": "dataapi"}, {}, "DB_BACKEND=dataapi"),
        (_dest(database="musclefit"), _DOTENV, {}, "não é o do .env"),
        (_dest(database="mysql"), {**_DOTENV, "MYSQL_DATABASE": "mysql"}, {}, "sistema"),
        (_dest(), {}, {}, "MYSQL_DATABASE"),
    ],
)
def test_destino_remoto_ou_de_producao_recusado(dest, dotenv, environ, motivo):
    with pytest.raises(p2d.AbortError, match=motivo):
        p2d.validate_destination(dest, dotenv, environ)


# ---------------------------------------------------------------------------
# Data API: só leitura, paginação e limite de 1 MB
# ---------------------------------------------------------------------------


class _AwsError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.response = {"Error": {"Code": code, "Message": message}}


class FakeDataApi:
    """Responde `SELECT ... WHERE id > N ... LIMIT M` sobre `rows`; acima de
    `max_rows` por resposta simula o limite de 1 MB."""

    def __init__(self, rows, max_rows=None, resuming=0):
        self.rows = rows
        self.max_rows = max_rows
        self.resuming = resuming
        self.sqls = []

    def execute_statement(self, **kw):
        sql = kw["sql"]
        self.sqls.append(sql)
        if self.resuming:
            self.resuming -= 1
            raise _AwsError("DatabaseResumingException", "Aurora DB instance is resuming after being auto-paused")
        after = re.search(r"`id` > (\d+)", sql)
        limit = int(re.search(r"LIMIT (\d+)", sql).group(1))
        rows = [r for r in self.rows if not after or r["id"] > int(after.group(1))][:limit]
        if self.max_rows is not None and len(rows) > self.max_rows:
            raise _AwsError("UnsupportedResultException", "The result exceeds the size limit 1 MB.")
        return {
            "columnMetadata": [{"label": "id"}, {"label": "peso"}, {"label": "ativo"}, {"label": "obs"}],
            "records": [
                [{"longValue": r["id"]}, {"stringValue": r["peso"]}, {"booleanValue": True}, {"isNull": True}]
                for r in rows
            ],
        }


_T = _table("t", ["id", "peso", "ativo", "obs"], nullable=("obs",))


def test_paginacao_por_pk_le_tudo_sem_repetir():
    api = FakeDataApi([{"id": i, "peso": f"{i}.50"} for i in range(1, 26)])
    reader = p2d.DataApiReader(api, "arn:c", "arn:s", "musclefit", page_size=10)
    rows = reader.fetch(_T)
    assert [r["id"] for r in rows] == list(range(1, 26))
    assert rows[0] == {"id": 1, "peso": "1.50", "ativo": 1, "obs": None}  # DECIMAL como texto exato
    assert len(api.sqls) == 3 and "`id` > 20" in api.sqls[-1]


def test_resposta_acima_de_1mb_reduz_a_pagina():
    api = FakeDataApi([{"id": i, "peso": "1.00"} for i in range(1, 21)], max_rows=4)
    reader = p2d.DataApiReader(api, "arn:c", "arn:s", "musclefit", page_size=16)
    rows = reader.fetch(_T)
    assert [r["id"] for r in rows] == list(range(1, 21))
    assert any("LIMIT 4" in s for s in api.sqls)


def test_aurora_em_auto_pause_e_aguardada():
    api = FakeDataApi([{"id": 1, "peso": "1.00"}], resuming=2)
    waits = []
    reader = p2d.DataApiReader(api, "arn:c", "arn:s", "musclefit", sleep=waits.append)
    assert len(reader.fetch(_T)) == 1
    assert waits == [5, 5]


def test_filtro_in_por_ids_em_lotes():
    api = FakeDataApi([])
    reader = p2d.DataApiReader(api, "arn:c", "arn:s", "musclefit")
    reader.fetch(_T, "user_id", range(1, 1201))
    assert len(api.sqls) == 3  # 500 + 500 + 200
    assert "`user_id` IN (1, 2," in api.sqls[0]


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM users",
        "UPDATE users SET role='admin'",
        "SELECT 1; DROP TABLE users",
        "CALL proc()",
        "INSERT INTO t VALUES (1)",
        "SELECT * FROM t INTO OUTFILE '/tmp/x'; DELETE FROM t",
    ],
)
def test_producao_so_aceita_select(sql):
    reader = p2d.DataApiReader(FakeDataApi([]), "arn:c", "arn:s", "musclefit")
    with pytest.raises(p2d.AbortError, match="só SELECT"):
        reader.query(sql)


def test_select_com_palavra_proibida_dentro_de_literal_e_aceito():
    p2d.assert_read_only_sql("SELECT * FROM t WHERE nome = 'DELETE; DROP'")


# ---------------------------------------------------------------------------
# Migrações
# ---------------------------------------------------------------------------


def test_migracao_divergente_aborta():
    prod = {"066_a.py": "x", "067_b.py": "y"}
    p2d.compare_migrations(prod, {"066_a.py": "x", "067_b.py": "y"}, "o MySQL local")
    with pytest.raises(p2d.AbortError, match="só em o MySQL local: 068_c.py"):
        p2d.compare_migrations(prod, {"066_a.py": "x", "067_b.py": "y", "068_c.py": "z"}, "o MySQL local")
    with pytest.raises(p2d.AbortError, match="checksum diferente: 067_b.py"):
        p2d.compare_migrations(prod, {"066_a.py": "x", "067_b.py": "outro"}, "o MySQL local")
    with pytest.raises(p2d.AbortError, match="só em produção: 067_b.py"):
        p2d.compare_migrations(prod, {"066_a.py": None}, "o repositório")


def test_repositorio_tem_as_migracoes_numeradas():
    files = p2d.repo_migration_files()
    assert "001_schema.sql" in files and len(files) >= 67
