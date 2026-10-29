"""Plano da migracao 037 (congelamento da prescricao) contra o
`information_schema` REAL da Aurora de producao (recorte lido em 2026-09-18,
tests/unit/data/aurora_schema_2026_09_18_037.json; ver `_origem`).

Producao esta na versao 36 e nenhuma das 6 colunas existe -> 2 statements
(um ALTER por tabela). Estado convergido -> 0 statements.
"""

from __future__ import annotations

import copy

import pytest

from app import migrate as m
from tests.support.prescricao_037 import (
    ALTER_SETS,
    ALTER_WPE,
    DECISION_INDEX,
    MIGRATION_037,
    SETS,
    WPE,
    load_snapshot,
    module,
    with_columns,
)
from tests.support.safety_032_033 import SafetySnapshotDB

SNAPSHOT = load_snapshot()
NEW_COLUMNS = {
    WPE: ("prescription_json", "progression_decision_id", "engine_version", "requires_user_choice"),
    SETS: ("rpe_target_effective", "safety_event"),
}


def _plan(snapshot):
    db = SafetySnapshotDB(snapshot)
    statements = module()["plan"](db)
    return [(" ".join(sql.split()), params) for sql, params in statements], db


def test_recorte_e_da_aurora_na_versao_36_sem_nenhuma_coluna_nova():
    assert SNAPSHOT["versao"][0]["mysql"].startswith("8.0")
    engines = {t["table_name"]: t["engine"] for t in SNAPSHOT["tables"]}
    for table in (WPE, SETS, "progression_decisions"):
        assert engines[table] == "InnoDB", table
    presentes = {(c["t"], c["col"]) for c in SNAPSHOT["columns"]}
    for table, columns in NEW_COLUMNS.items():
        for column in columns:
            assert (table, column) not in presentes, f"{table}.{column}"
    assert not any((i["t"], i["idx"]) == (WPE, DECISION_INDEX) for i in SNAPSHOT["indexes"])


def test_tipo_de_progression_decisions_id_define_o_tipo_da_coluna_nova():
    """`progression_decisions.id` e BIGINT SIGNED - a armadilha unsigned do
    `exercise_catalog.id` (028/036) nao se aplica aqui."""
    ids = {(c["t"], c["col"]): (c["type"], c["ckey"]) for c in SNAPSHOT["columns"] if c["col"] == "id"}
    assert ids[("progression_decisions", "id")] == ("bigint", "PRI")
    tipo = dict((col, ddl) for col, ddl, *_ in module()["COLUMNS"][WPE])["progression_decision_id"]
    assert tipo == "BIGINT NULL"


def test_producao_hoje_executa_dois_statements_um_por_tabela():
    statements, db = _plan(SNAPSHOT)
    assert statements == [(ALTER_WPE, None), (ALTER_SETS, None)]
    assert all(q.startswith("SELECT") for q in db.queries)


def test_fk_para_progression_decisions_e_logica():
    """FK fisica fecharia ciclo com `fk_pd_wpe` (036) - so indice."""
    source = MIGRATION_037.read_text(encoding="utf-8")
    sqls = [sql for sql, _ in _plan(SNAPSHOT)[0]]
    assert all("FOREIGN KEY" not in sql and "CONSTRAINT" not in sql for sql in sqls)
    assert f"ADD KEY {DECISION_INDEX} (progression_decision_id)" in sqls[0]
    # a FK inversa que a 036 ja criou continua sendo a unica ligacao fisica
    assert any(f["fk"] == "fk_pd_wpe" and f["ref_t"] == WPE for f in SNAPSHOT["foreign_keys"])
    assert "FK LOGICA" in source


def test_requires_user_choice_e_a_unica_not_null_e_nao_muda_linha_existente():
    especificacao = {col: (nullable, default) for col, _, _, nullable, default in module()["COLUMNS"][WPE]}
    assert especificacao["requires_user_choice"] == ("NO", "0")
    assert [col for col, (nullable, _) in especificacao.items() if nullable == "NO"] == ["requires_user_choice"]
    assert "requires_user_choice TINYINT(1) NOT NULL DEFAULT 0" in ALTER_WPE


def test_estado_convergido_e_zero_statements():
    assert _plan(with_columns(SNAPSHOT))[0] == []


@pytest.mark.parametrize("ja_aplicada, faltando", [((WPE,), ALTER_SETS), ((SETS,), ALTER_WPE)])
def test_execucao_interrompida_no_meio_retoma_do_ponto(ja_aplicada, faltando):
    statements, _ = _plan(with_columns(SNAPSHOT, *ja_aplicada))
    assert [sql for sql, _ in statements] == [faltando]


def test_indice_ausente_com_as_colunas_presentes_entra_sozinho():
    """Execucao anterior que criou as colunas mas nao o indice (ambiente
    manipulado a mao) converge sem recriar coluna nenhuma."""
    snap = copy.deepcopy(with_columns(SNAPSHOT))
    snap["indexes"] = [i for i in snap["indexes"] if (i["t"], i["idx"]) != (WPE, DECISION_INDEX)]
    statements, _ = _plan(snap)
    assert [sql for sql, _ in statements] == [f"ALTER TABLE {WPE} ADD KEY {DECISION_INDEX} (progression_decision_id)"]


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
            lambda s: {**s, "tables": [t for t in s["tables"] if t["table_name"] != SETS]},
            f"tabela {SETS} nao existe",
        ),
        (
            lambda s: {
                **s,
                "tables": [{**t, "engine": "MyISAM"} if t["table_name"] == WPE else t for t in s["tables"]],
            },
            f"{WPE} usa ENGINE=MyISAM",
        ),
        (
            lambda s: _mutate_column(with_columns(s), WPE, "prescription_json", type="text"),
            "prescription_json e text, esperado json",
        ),
        (
            lambda s: _mutate_column(with_columns(s), WPE, "progression_decision_id", type="bigint unsigned"),
            "progression_decision_id e bigint unsigned, esperado bigint",
        ),
        (
            lambda s: _mutate_column(with_columns(s), WPE, "requires_user_choice", nullable="YES"),
            "requires_user_choice tem IS_NULLABLE=YES",
        ),
        (
            lambda s: _mutate_column(with_columns(s), WPE, "requires_user_choice", dflt=None),
            "requires_user_choice tem DEFAULT",
        ),
        (
            lambda s: _mutate_column(with_columns(s), SETS, "safety_event", type="varchar(20)"),
            "safety_event e varchar",
        ),
        (
            lambda s: {
                **with_columns(s),
                "indexes": [
                    {**i, "col": "id"} if (i["t"], i["idx"]) == (WPE, DECISION_INDEX) else i
                    for i in with_columns(s)["indexes"]
                ],
            },
            f"indice {DECISION_INDEX} de {WPE}",
        ),
    ],
)
def test_estado_inesperado_falha_antes_de_qualquer_statement(mutate, message):
    with pytest.raises(Exception, match=message) as exc:
        _plan(mutate(SNAPSHOT))
    assert "Nenhum statement executado" in str(exc.value)


def test_largura_de_int_legada_nao_e_divergencia():
    """MySQL < 8.0.19 mostra `tinyint(4)`; nao e outro formato."""
    snap = _mutate_column(with_columns(SNAPSHOT), SETS, "rpe_target_effective", type="tinyint(4)")
    assert _plan(snap)[0] == []


def test_default_como_inteiro_ou_bit_nao_e_divergencia():
    for valor in (0, "b'0'", "'0'"):
        snap = _mutate_column(with_columns(SNAPSHOT), WPE, "requires_user_choice", dflt=valor)
        assert _plan(snap)[0] == [], valor


def test_plano_e_um_statement_por_chamada_sem_estado_de_sessao():
    sqls = [sql for sql, _ in _plan(SNAPSHOT)[0]]
    assert len(sqls) == 2
    for sql in sqls:
        assert not m._FORBIDDEN_SQL_RE.match(sql)
        assert ";" not in sql
    m.validate_sql_statements(MIGRATION_037.name, sqls)


def test_migracao_e_autocontida_sem_importar_o_app_nem_dataclass():
    source = MIGRATION_037.read_text(encoding="utf-8")
    assert "dataclass" not in source
    assert "from app" not in source and "import app" not in source
