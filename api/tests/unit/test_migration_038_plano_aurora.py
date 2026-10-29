"""Plano da migracao 038 (auditoria da periodizacao) contra o
`information_schema` REAL da Aurora de producao (recorte lido em 2026-09-19,
tests/unit/data/aurora_schema_2026_09_19_038.json; ver `_origem`).

Producao esta na versao 37: `template_generation_logs` nao existe e nenhuma das
2 colunas novas de `workout_plan_templates` existe -> 2 statements. Estado
convergido -> 0 statements.
"""

from __future__ import annotations

import copy

import pytest

from app import migrate as m
from tests.support.periodizacao_038 import (
    ALTER_TEMPLATES,
    GENERATION_LOG_INDEX,
    LOGS,
    LOGS_TEMPLATE_INDEX,
    LOGS_USER_INDEX,
    MIGRATION_038,
    TEMPLATES,
    converged,
    load_snapshot,
    module,
    with_logs_table,
    with_template_columns,
)
from tests.support.safety_032_033 import SafetySnapshotDB

SNAPSHOT = load_snapshot()
CREATE_LOGS = " ".join(module()["CREATE_LOGS_SQL"].split())


def _plan(snapshot):
    db = SafetySnapshotDB(snapshot)
    statements = module()["plan"](db)
    return [(" ".join(sql.split()), params) for sql, params in statements], db


def test_recorte_e_da_aurora_sem_a_tabela_nem_as_colunas_novas():
    assert SNAPSHOT["versao"][0]["mysql"].startswith("8.0")
    engines = {t["table_name"]: t["engine"] for t in SNAPSHOT["tables"]}
    assert LOGS not in engines
    for table in (TEMPLATES, "users"):
        assert engines[table] == "InnoDB", table
    presentes = {(c["t"], c["col"]) for c in SNAPSHOT["columns"]}
    for column in ("origin", "generation_log_id"):
        assert (TEMPLATES, column) not in presentes, column
    assert not any((i["t"], i["idx"]) == (TEMPLATES, GENERATION_LOG_INDEX) for i in SNAPSHOT["indexes"])


def test_pks_referenciadas_sao_bigint_signed():
    """`exercise_catalog.id` e BIGINT UNSIGNED (028/036), mas as duas PKs que a
    038 referencia sao SIGNED - por isso as FKs sao BIGINT sem unsigned."""
    ids = {(c["t"], c["col"]): (c["type"], c["ckey"]) for c in SNAPSHOT["columns"] if c["col"] == "id"}
    assert ids[("users", "id")] == ("bigint", "PRI")
    assert ids[(TEMPLATES, "id")] == ("bigint", "PRI")
    assert "user_id BIGINT NOT NULL" in CREATE_LOGS
    assert "saved_template_id BIGINT NULL" in CREATE_LOGS
    assert "UNSIGNED" not in CREATE_LOGS.upper()


def test_producao_hoje_executa_dois_statements():
    statements, db = _plan(SNAPSHOT)
    assert statements == [(CREATE_LOGS, None), (ALTER_TEMPLATES, None)]
    assert all(q.startswith("SELECT") for q in db.queries)


def test_origin_e_a_unica_not_null_e_nao_muda_linha_existente():
    especificacao = {col: (nullable, default) for col, _, _, nullable, default in module()["TEMPLATE_COLUMNS"]}
    assert especificacao["origin"] == ("NO", "manual")
    assert [col for col, (nullable, _) in especificacao.items() if nullable == "NO"] == ["origin"]
    assert "origin ENUM('manual','periodization') NOT NULL DEFAULT 'manual'" in ALTER_TEMPLATES


def test_fk_de_generation_log_id_e_logica_e_a_do_log_e_fisica():
    """FK fisica nos dois sentidos fecharia ciclo (mesma razao da 037)."""
    source = MIGRATION_038.read_text(encoding="utf-8")
    assert "FOREIGN KEY" not in ALTER_TEMPLATES and "CONSTRAINT" not in ALTER_TEMPLATES
    assert f"ADD KEY {GENERATION_LOG_INDEX} (generation_log_id)" in ALTER_TEMPLATES
    assert "FK **LOGICA**" in source or "FK LOGICA" in source
    # do lado do log a FK e fisica, com a regra de ON DELETE de cada lado
    assert "CONSTRAINT fk_tgl_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE" in CREATE_LOGS
    assert f"REFERENCES {TEMPLATES}(id) ON DELETE SET NULL" in CREATE_LOGS


def test_indice_do_limite_diario_cobre_usuario_e_data():
    assert f"KEY {LOGS_USER_INDEX} (user_id, created_at)" in CREATE_LOGS
    assert f"KEY {LOGS_TEMPLATE_INDEX} (saved_template_id)" in CREATE_LOGS


def test_estado_convergido_e_zero_statements():
    assert _plan(converged(SNAPSHOT))[0] == []


@pytest.mark.parametrize(
    "parcial, faltando",
    [(with_logs_table, ALTER_TEMPLATES), (with_template_columns, CREATE_LOGS)],
)
def test_execucao_interrompida_no_meio_retoma_do_ponto(parcial, faltando):
    statements, _ = _plan(parcial(SNAPSHOT))
    assert [sql for sql, _ in statements] == [faltando]


def test_indice_ausente_com_as_colunas_presentes_entra_sozinho():
    snap = copy.deepcopy(converged(SNAPSHOT))
    snap["indexes"] = [i for i in snap["indexes"] if (i["t"], i["idx"]) != (TEMPLATES, GENERATION_LOG_INDEX)]
    statements, _ = _plan(snap)
    assert [sql for sql, _ in statements] == [
        f"ALTER TABLE {TEMPLATES} ADD KEY {GENERATION_LOG_INDEX} (generation_log_id)"
    ]


def _mutate_column(snap, table, col, **changes):
    snap = copy.deepcopy(snap)
    for column in snap["columns"]:
        if (column["t"], column["col"]) == (table, col):
            column.update(changes)
    return snap


@pytest.mark.parametrize(
    "mutate, message",
    [
        (
            lambda s: {**s, "tables": [t for t in s["tables"] if t["table_name"] != "users"]},
            "tabela referenciada users nao existe",
        ),
        (
            lambda s: {
                **s,
                "tables": [{**t, "engine": "MyISAM"} if t["table_name"] == TEMPLATES else t for t in s["tables"]],
            },
            f"{TEMPLATES} usa ENGINE=MyISAM",
        ),
        (
            lambda s: _mutate_column(s, "users", "id", type="bigint unsigned"),
            "users.id e bigint unsigned, esperado bigint",
        ),
        (
            lambda s: _mutate_column(converged(s), TEMPLATES, "origin", type="varchar(20)"),
            "origin e varchar",
        ),
        (
            lambda s: _mutate_column(converged(s), TEMPLATES, "origin", dflt=None),
            "origin tem DEFAULT",
        ),
        (
            lambda s: _mutate_column(converged(s), TEMPLATES, "generation_log_id", nullable="NO"),
            "generation_log_id tem IS_NULLABLE=NO",
        ),
        (
            lambda s: _mutate_column(converged(s), LOGS, "cost_usd", type="decimal(6,2)"),
            f"{LOGS}.cost_usd e decimal",
        ),
        (
            lambda s: {**converged(s), "columns": [c for c in converged(s)["columns"] if c["col"] != "rag_sources"]},
            f"{LOGS}.rag_sources nao existe",
        ),
        (
            lambda s: {
                **converged(s),
                "indexes": [i for i in converged(s)["indexes"] if (i["t"], i["idx"]) != (LOGS, LOGS_USER_INDEX)],
            },
            f"indice {LOGS_USER_INDEX} de {LOGS}",
        ),
        (
            lambda s: {
                **converged(s),
                "foreign_keys": [f for f in converged(s)["foreign_keys"] if f["fk"] != "fk_tgl_user"],
            },
            f"FKs de {LOGS} sao",
        ),
    ],
)
def test_estado_inesperado_falha_antes_de_qualquer_statement(mutate, message):
    with pytest.raises(Exception, match=message) as exc:
        _plan(mutate(SNAPSHOT))
    assert "Nenhum statement executado" in str(exc.value)


def test_largura_de_int_legada_e_espaco_em_enum_nao_sao_divergencia():
    snap = _mutate_column(converged(SNAPSHOT), LOGS, "tokens_in", type="int(11)")
    assert _plan(snap)[0] == []
    snap = _mutate_column(converged(SNAPSHOT), TEMPLATES, "origin", type="enum('manual', 'periodization')")
    assert _plan(snap)[0] == []


def test_plano_e_um_statement_por_chamada_sem_estado_de_sessao():
    sqls = [sql for sql, _ in _plan(SNAPSHOT)[0]]
    assert len(sqls) == 2
    for sql in sqls:
        assert not m._FORBIDDEN_SQL_RE.match(sql)
        assert ";" not in sql
    m.validate_sql_statements(MIGRATION_038.name, sqls)


def test_migracao_e_autocontida_sem_importar_o_app_nem_dataclass():
    source = MIGRATION_038.read_text(encoding="utf-8")
    assert "dataclass" not in source
    assert "from app" not in source and "import app" not in source
