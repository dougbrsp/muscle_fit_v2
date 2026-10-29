"""Plano da migracao 028 contra o schema REAL da Aurora de producao
(`information_schema` extraido em 2026-09-14, recorte em
tests/unit/data/aurora_schema_2026_09_14_028.json): 0 statements e nenhuma
leitura de dados.

O dublê responde as mesmas 4 leituras de schema que a 028 faz; qualquer
contagem de linhas ou escrita falharia o teste. O mesmo plano contra MySQL
real, nos dois backends, esta em
tests/integration/dataapi/test_migration_028_paridade_aurora.py.
"""

from __future__ import annotations

import copy

import pytest

from app import migrate as m
from tests.support import paridade_028 as p

SNAPSHOT = p.load_snapshot()


def _plan(snapshot, counts=None):
    db = p.SnapshotDB(snapshot, counts)
    return [" ".join(sql.split()) for sql, _ in p.load_plan()(db)], db


def test_snapshot_e_da_aurora_3_mysql_8():
    assert SNAPSHOT["versao"][0]["mysql"].startswith("8.0")
    assert {t["table_name"] for t in SNAPSHOT["tables"]} == set(p.TABLES)


def test_aurora_de_producao_028_executa_zero_statements():
    statements, db = _plan(SNAPSHOT)
    assert statements == []
    assert len(db.queries) == 4  # so information_schema, sem contagem de linhas

    facade = m.MigrationDB(conn=None)
    facade.query, facade.query_one = db.query, db.query_one
    facade.execute = lambda sql, params=None: pytest.fail(f"escrita na Aurora: {sql}")
    p.load_up()(facade)
    assert facade.statements == []


def test_aurora_tem_as_fks_para_o_catalogo_que_a_028_conhece():
    """A lista da 028 e exatamente a da Aurora (senao o rebuild perderia FK)."""
    aurora = sorted(
        (f["t"], f["col"], f["fk"], f["on_delete"]) for f in SNAPSHOT["foreign_keys"] if f["ref_t"] == "exercise_catalog"
    )
    assert aurora == sorted(p.CATALOG_FKS)


def test_controle_mesmo_dublê_no_formato_dev_ci_planeja_as_correcoes_do_catalogo():
    statements, _ = _plan(p.dev_ci_snapshot(SNAPSHOT), counts={})
    assert statements == p.DEV_CI_PLAN
    # gyms.owner_user_id NOT NULL fica fora (pendencia: banco novo semeia academias sem dono).
    assert not any("gyms" in statement.split()[2] for statement in statements)


def test_dry_run_de_banco_novo_sem_a_023_aplicada_planeja_a_mesma_conversao():
    """No `--dry-run` do runner a 023 so e planejada: a 028 ve gym_equipment/
    equipment_exercise_mappings ainda com BIGINT e FK legada (estado medido no
    baseline 001..021) e precisa planejar, nao falhar."""
    snapshot = p.dev_ci_snapshot(SNAPSHOT)
    legacy = {"gym_equipment": "fk_gym_equipment_mapping", "equipment_exercise_mappings": "fk_eem_mapping"}
    for c in snapshot["columns"]:
        if c["t"] in legacy and c["col"] == "mapping_id":
            c["type"] = "bigint"
    for f in snapshot["foreign_keys"]:
        if f["t"] in legacy and f["col"] == "mapping_id":
            f["fk"], f["ref_t"] = legacy[f["t"]], "exercise_zepp_mappings"

    statements, db = _plan(snapshot, counts={})

    expected = [s.replace("fk_eem_mapping_catalog", "fk_eem_mapping").replace("fk_gym_equipment_mapping_catalog", "fk_gym_equipment_mapping") if "DROP" in s else s for s in p.DEV_CI_PLAN]
    modify_exercises = expected.index("ALTER TABLE exercises MODIFY COLUMN mapping_id BIGINT UNSIGNED NULL")
    expected[modify_exercises:modify_exercises + 1] = [
        "ALTER TABLE equipment_exercise_mappings MODIFY COLUMN mapping_id BIGINT UNSIGNED NOT NULL",
        "ALTER TABLE exercises MODIFY COLUMN mapping_id BIGINT UNSIGNED NULL",
        "ALTER TABLE gym_equipment MODIFY COLUMN mapping_id BIGINT UNSIGNED NOT NULL",
    ]
    assert statements == expected
    # orfaos conferidos antes de trocar o alvo das tres FKs legadas
    assert sum("NOT EXISTS" in sql for sql in db.queries) == 3


@pytest.mark.parametrize(
    ("count_sql", "message"),
    [
        ("FROM exercise_catalog WHERE id = 0", "id = 0"),
        ("FROM exercises c", "exercises.mapping_id sem exercise_catalog"),
    ],
)
def test_dados_que_exigem_decisao_falham_antes_de_qualquer_statement(count_sql, message):
    with pytest.raises(Exception, match=message) as exc:
        _plan(p.dev_ci_snapshot(SNAPSHOT), counts={count_sql: 2})
    assert "Nenhum statement executado" in str(exc.value)


def test_fk_desconhecida_para_o_catalogo_falha():
    snapshot = copy.deepcopy(SNAPSHOT)
    snapshot["foreign_keys"].append(
        {"t": "gyms", "fk": "fk_x", "col": "id", "ref_t": "exercise_catalog", "ref_col": "id", "on_delete": "CASCADE", "on_update": "NO ACTION"}
    )
    with pytest.raises(Exception, match="nao prevista"):
        _plan(snapshot)


def test_coluna_filha_com_tipo_inesperado_falha():
    snapshot = p.dev_ci_snapshot(SNAPSHOT)
    for c in snapshot["columns"]:
        if (c["t"], c["col"]) == ("workout_plan_exercises", "mapping_id"):
            c["type"] = "bigint"  # conversao so prevista nas colunas com FK legada (exercises, gym_equipment, eem)
    with pytest.raises(Exception, match="workout_plan_exercises.mapping_id e bigint"):
        _plan(snapshot, counts={})


def test_plano_de_dev_ci_e_um_statement_por_chamada_sem_estado_de_sessao():
    for statement in p.DEV_CI_PLAN:
        assert not m._FORBIDDEN_SQL_RE.match(statement), statement
        assert ";" not in statement and "FOREIGN_KEY_CHECKS" not in statement.upper(), statement
    m.validate_sql_statements(p.MIGRATION_FILENAME, p.DEV_CI_PLAN)
