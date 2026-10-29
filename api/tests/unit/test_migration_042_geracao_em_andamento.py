"""Plano da migracao 042 (`exercise_catalog.execution_description_generation_started_at`,
melhoria M4) sobre um recorte de `information_schema` construido no proprio
teste.

Como a 041, nao precisa de um snapshot real: a migracao nao altera nenhuma
coluna existente, so ACRESCENTA uma. O que precisa ser provado e:

- banco com a 039 aplicada e sem a coluna -> exatamente 1 `ALTER TABLE`;
- banco com a coluna no formato esperado  -> 0 statements (convergido);
- banco sem a 039, execucao REAL           -> erro (a coluna nao faria sentido);
- banco sem a 039, DRY-RUN                 -> plano normal: num banco novo a
  039 esta pendente no MESMO lote e, sem executar nada, a coluna dela ainda
  nao existe quando o `plan()` da 042 roda (mesma regra da 033/041);
- TABELA do baseline ausente               -> erro inclusive em dry-run;
- coluna presente em outro formato         -> erro ANTES de qualquer statement.

Rodar: cd api && python -m pytest tests/unit/test_migration_042_geracao_em_andamento.py
"""

from __future__ import annotations

import copy

import pytest

from app import migrate as m

MIGRATION_PATH = m.DEFAULT_MIGRATIONS_DIR / "042_descricao_execucao_geracao_em_andamento.py"
MODULE = m.load_python_migration(MIGRATION_PATH).__globals__

CATALOG = MODULE["CATALOG"]
COLUMN = MODULE["COLUMN"]
ALTER_SQL = MODULE["ALTER_SQL"]
Erro = MODULE["GeracaoEmAndamentoError"]


class DbFalso:
    """So `query()`: o `plan()` desta migracao nunca escreve.

    `dry_run` existe porque a `MigrationDB` real tem esse atributo e a 042 o
    consulta (tolerancia a 039 pendente no mesmo lote)."""

    def __init__(self, snapshot, dry_run: bool = False):
        self.snapshot = snapshot
        self.dry_run = dry_run
        self.escritas: list = []

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": name, "ENGINE": engine} for name, engine in self.snapshot["tables"].items()]
        return [
            {
                "COLUMN_NAME": column,
                "COLUMN_TYPE": info["COLUMN_TYPE"],
                "IS_NULLABLE": info["IS_NULLABLE"],
                "COLUMN_DEFAULT": info.get("COLUMN_DEFAULT"),
            }
            for (table, column), info in self.snapshot["columns"].items()
            if table == CATALOG
        ]

    def execute(self, sql, params=None):
        self.escritas.append((sql, params))


def _apos_039() -> dict:
    """Estado de producao hoje: 039 aplicada, 042 nao."""
    return {
        "tables": {CATALOG: "InnoDB"},
        "columns": {
            (CATALOG, "id"): {"COLUMN_TYPE": "bigint unsigned", "IS_NULLABLE": "NO"},
            (CATALOG, "execution_description"): {"COLUMN_TYPE": "text", "IS_NULLABLE": "YES"},
            (CATALOG, "execution_description_reviewed_at"): {"COLUMN_TYPE": "datetime", "IS_NULLABLE": "YES"},
        },
    }


def _apos_042() -> dict:
    snapshot = _apos_039()
    snapshot["columns"][(CATALOG, COLUMN)] = {"COLUMN_TYPE": "datetime", "IS_NULLABLE": "YES"}
    return snapshot


def test_banco_sem_a_coluna_produz_exatamente_um_alter():
    plano = MODULE["plan"](DbFalso(_apos_039()))
    assert plano == [(ALTER_SQL, None)]
    assert "ADD COLUMN execution_description_generation_started_at DATETIME NULL" in ALTER_SQL


def test_migracao_e_reexecutavel_sem_escrever_nada():
    db = DbFalso(_apos_042())
    assert MODULE["plan"](db) == []
    MODULE["up"](db)
    assert db.escritas == []


def test_up_executa_o_alter_uma_vez():
    db = DbFalso(_apos_039())
    MODULE["up"](db)
    assert db.escritas == [(ALTER_SQL, None)]


def test_sem_a_039_falha_em_vez_de_criar_coluna_solta():
    """Execucao REAL: a 039 ja rodou nesta altura. Se a coluna nao esta la, o
    banco nao e o que a 042 espera e a reserva nao faria sentido."""
    snapshot = _apos_039()
    del snapshot["columns"][(CATALOG, "execution_description")]
    db = DbFalso(snapshot)
    with pytest.raises(Erro, match="039"):
        MODULE["plan"](db)
    assert db.escritas == []


def test_em_dry_run_a_039_pendente_no_mesmo_lote_nao_quebra_o_plano():
    """Banco novo: o runner planeja o lote inteiro sem executar nada, entao a
    039 pendente nao criou `execution_description` quando o `plan()` da 042
    roda. O plano da 042 ainda precisa aparecer - o formato e conferido de
    novo na execucao real. Mesma regra da 033 com `advanced_techniques` e da
    041 com `intervals_activities`."""
    snapshot = _apos_039()
    del snapshot["columns"][(CATALOG, "execution_description")]
    assert MODULE["plan"](DbFalso(snapshot, dry_run=True)) == [(ALTER_SQL, None)]


def test_tabela_do_baseline_ausente_falha_MESMO_em_dry_run():
    """A tolerancia vale SO para a coluna da 039. `exercise_catalog` vem do
    baseline (002/028): se a tabela nao existe, nao ha lote pendente que
    explique isso."""
    snapshot = _apos_039()
    snapshot["tables"] = {}
    snapshot["columns"] = {}
    for dry_run in (False, True):
        db = DbFalso(snapshot, dry_run=dry_run)
        with pytest.raises(Erro, match="nao existe"):
            MODULE["plan"](db)
        assert db.escritas == []


def test_engine_diferente_de_innodb_falha_MESMO_em_dry_run():
    snapshot = _apos_039()
    snapshot["tables"][CATALOG] = "MyISAM"
    for dry_run in (False, True):
        with pytest.raises(Erro, match="ENGINE"):
            MODULE["plan"](DbFalso(snapshot, dry_run=dry_run))


@pytest.mark.parametrize(
    "alteracao, trecho",
    [
        ({"COLUMN_TYPE": "timestamp"}, "esperado datetime"),
        ({"IS_NULLABLE": "NO"}, "IS_NULLABLE"),
        ({"COLUMN_DEFAULT": "CURRENT_TIMESTAMP"}, "DEFAULT"),
    ],
)
def test_coluna_presente_em_outro_formato_falha_antes_de_qualquer_statement(alteracao, trecho):
    snapshot = _apos_042()
    coluna = copy.deepcopy(snapshot["columns"][(CATALOG, COLUMN)])
    coluna.update(alteracao)
    snapshot["columns"][(CATALOG, COLUMN)] = coluna
    db = DbFalso(snapshot)
    with pytest.raises(Erro, match=trecho):
        MODULE["plan"](db)
    assert db.escritas == []
