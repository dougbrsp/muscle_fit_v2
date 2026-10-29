"""Plano da migracao 030 contra o schema REAL da Aurora de producao
(`information_schema` extraido em 2026-09-14, recorte em
tests/unit/data/aurora_schema_2026_09_14_030.json): exatamente os 2 DROP TABLE
das tabelas de backup do onprem, e so leituras de `information_schema`.

O mesmo plano contra MySQL real, nos dois backends, esta em
tests/integration/dataapi/test_migration_030_drop_backups.py.
"""

from __future__ import annotations

import copy

import pytest

from app import migrate as m
from tests.support import drop_backups_030 as d

SNAPSHOT = d.load_snapshot()


def _plan(snapshot):
    db = d.SnapshotDB(snapshot)
    return [" ".join(sql.split()) for sql, _ in d.load_plan()(db)], db


def _without_backups(snapshot):
    snap = copy.deepcopy(snapshot)
    snap["tables"] = [t for t in snap["tables"] if t["table_name"] not in d.BACKUP_TABLES]
    return snap


def test_snapshot_e_da_aurora_3_mysql_8_com_as_duas_tabelas():
    assert SNAPSHOT["versao"][0]["mysql"].startswith("8.0")
    names = {t["table_name"] for t in SNAPSHOT["tables"]}
    assert set(d.BACKUP_TABLES) <= names
    assert len(names) == 48
    assert {t["table_name"]: t["collation"] for t in SNAPSHOT["tables"] if t["table_name"] in d.BACKUP_TABLES} == {
        "exercise_mappings_backup_20260714": "utf8mb4_unicode_ci",
        "exercises_backup_before_mapping_sync": "utf8mb4_0900_ai_ci",
    }
    per_table = {t: sum(c["t"] == t for c in SNAPSHOT["columns"]) for t in d.BACKUP_TABLES}
    assert per_table == {"exercise_mappings_backup_20260714": 21, "exercises_backup_before_mapping_sync": 31}


def test_aurora_nao_tem_fk_nem_view_envolvendo_as_tabelas_de_backup():
    assert not [f for f in SNAPSHOT["foreign_keys"] if f["t"] in d.BACKUP_TABLES or f["ref_t"] in d.BACKUP_TABLES]
    assert SNAPSHOT["view_table_usage"] == []
    assert len(SNAPSHOT["foreign_keys"]) == 57  # todas as FKs do schema, nao so as das tabelas


def test_aurora_de_producao_030_executa_exatamente_os_2_drop_table():
    statements, db = _plan(SNAPSHOT)
    assert statements == d.AURORA_PLAN
    # TABLES + KEY_COLUMN_USAGE + VIEW_TABLE_USAGE; nenhuma contagem de linhas
    assert len(db.queries) == 3
    assert all("information_schema" in q for q in db.queries)

    executed: list[str] = []
    facade = m.MigrationDB(conn=None)
    facade.query, facade.query_one = db.query, db.query_one
    facade.execute = lambda sql, params=None: executed.append(sql)
    d.load_up()(facade)
    assert executed == d.AURORA_PLAN


def test_dev_ci_e_reexecucao_na_aurora_0_statements_e_uma_leitura():
    statements, db = _plan(_without_backups(SNAPSHOT))
    assert statements == []
    assert len(db.queries) == 1 and "information_schema.TABLES" in db.queries[0]


def test_retomada_com_uma_tabela_ja_removida_planeja_so_a_outra():
    snap = copy.deepcopy(SNAPSHOT)
    snap["tables"] = [t for t in snap["tables"] if t["table_name"] != d.BACKUP_TABLES[0]]
    assert _plan(snap)[0] == [f"DROP TABLE {d.BACKUP_TABLES[1]}"]


@pytest.mark.parametrize(
    "fk",
    [
        {"t": "activities", "fk": "fk_x_entrando", "col": "id", "ref_t": "exercises_backup_before_mapping_sync", "ref_col": "id"},
        {"t": "exercise_mappings_backup_20260714", "fk": "fk_x_saindo", "col": "id", "ref_t": "users", "ref_col": "id"},
    ],
    ids=["fk-apontando-para-backup", "fk-saindo-do-backup"],
)
def test_fk_envolvendo_backup_falha_antes_de_qualquer_statement(fk):
    snap = copy.deepcopy(SNAPSHOT)
    snap["foreign_keys"].append({**fk, "on_delete": "NO ACTION", "on_update": "NO ACTION"})
    with pytest.raises(Exception, match=fk["fk"]) as exc:
        _plan(snap)  # o dublê falharia em qualquer escrita
    assert "Nenhum statement executado" in str(exc.value)


def test_view_referenciando_backup_falha_antes_de_qualquer_statement():
    snap = copy.deepcopy(SNAPSHOT)
    snap["view_table_usage"].append({"view": "vw_x", "t": "exercise_mappings_backup_20260714"})
    with pytest.raises(Exception, match="vw_x") as exc:
        _plan(snap)
    assert "Nenhum statement executado" in str(exc.value)


def test_nome_existente_como_view_falha():
    snap = copy.deepcopy(SNAPSHOT)
    for t in snap["tables"]:
        if t["table_name"] == d.BACKUP_TABLES[1]:
            t["table_type"] = "VIEW"
    with pytest.raises(Exception, match="VIEW"):
        _plan(snap)


def test_plano_e_um_statement_por_chamada_sem_if_exists_nem_estado_de_sessao():
    for statement in d.AURORA_PLAN:
        assert not m._FORBIDDEN_SQL_RE.match(statement), statement
        assert ";" not in statement and "IF EXISTS" not in statement.upper(), statement
    m.validate_sql_statements(d.MIGRATION_FILENAME, d.AURORA_PLAN)


def test_migracao_030_e_descoberta_pelo_runner_e_nao_e_baseline():
    migration = next(mig for mig in m.discover_migrations() if mig.filename == d.MIGRATION_FILENAME)
    assert migration.version == 30 and migration.kind == "py" and not migration.is_baseline
