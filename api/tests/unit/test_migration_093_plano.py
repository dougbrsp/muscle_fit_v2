"""Plano da migração 093 (objetivo de treino, M97 F0) contra um
`information_schema` sintético:

- banco atual -> 8 ADD COLUMN INSTANT + 2 chaves únicas recriadas com o objetivo;
- convergido -> 0; meio caminho -> só o que falta;
- tipo/nulabilidade/default divergente ou chave em forma desconhecida -> erro
  antes do 1º statement;
- tabela ausente -> erro (DRY-RUN aceita);
- `plan_down` devolve as chaves à forma anterior ANTES de soltar as colunas;
- DDL: nenhuma FK, nenhum backfill, nenhum ':' em literal (Data API);
- registrada em `RESHAPING_MIGRATIONS` (a 036 e a 068 conferem as chaves).

Rodar: cd api && python -m pytest tests/unit/test_migration_093_plano.py
"""

from __future__ import annotations

import functools

import pytest

from app import migrate as m
from tests.support import migrations as support

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "093_training_goal.py"


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _antes() -> dict:
    return {(t, k): list(before) for t, k, before, _after in _mod()["UNIQUE_KEYS"]}


def _depois() -> dict:
    return {(t, k): list(after) for t, k, _before, after in _mod()["UNIQUE_KEYS"]}


def _coluna_ok(table: str) -> tuple:
    for t, _ddl, nullable, default in _mod()["TARGETS"]:
        if t == table:
            return (_mod()["EXPECTED_TYPE"], nullable, default)
    raise AssertionError(table)


class FakeDB:
    def __init__(self, *, tabelas=None, colunas=None, chaves=None, dry_run=False):
        self.tabelas = set(_mod()["TABLES"]) if tabelas is None else set(tabelas)
        self.colunas = colunas or {}  # tabela -> (tipo, nullable, default)
        self.chaves = _antes() if chaves is None else chaves
        self.dry_run = dry_run
        self.executed: list[str] = []

    def query(self, sql, params=None):
        assert ":" not in sql
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": t} for t in params if t in self.tabelas]
        if "information_schema.COLUMNS" in sql:
            assert params[0] == "training_goal"
            return [
                {"TABLE_NAME": t, "COLUMN_TYPE": tipo, "IS_NULLABLE": nul, "COLUMN_DEFAULT": default}
                for t, (tipo, nul, default) in self.colunas.items()
                if t in params[1:]
            ]
        if "information_schema.STATISTICS" in sql:
            return [
                {"TABLE_NAME": t, "INDEX_NAME": k, "COLUMN_NAME": c}
                for (t, k), cols in self.chaves.items()
                if t in self.tabelas
                for c in cols
            ]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        assert params is None
        self.executed.append(sql)


def _convergido(**kw) -> FakeDB:
    return FakeDB(colunas={t: _coluna_ok(t) for t in _mod()["TABLES"]}, chaves=_depois(), **kw)


def test_banco_atual_recebe_colunas_e_chaves_na_ordem():
    db = FakeDB()
    _mod()["up"](db)
    enum = "ENUM('hypertrophy','strength')"
    nn = f"{enum} NOT NULL DEFAULT 'hypertrophy', ALGORITHM=INSTANT"
    assert db.executed == [
        f"ALTER TABLE workout_plan_templates ADD COLUMN training_goal {nn}",
        f"ALTER TABLE workout_plan_template_versions ADD COLUMN training_goal {nn}",
        f"ALTER TABLE workout_plan_template_slot_items ADD COLUMN training_goal {enum} NULL DEFAULT NULL, ALGORITHM=INSTANT",
        f"ALTER TABLE workout_plan_exercises ADD COLUMN training_goal {nn}",
        f"ALTER TABLE exercises ADD COLUMN training_goal {nn}",
        f"ALTER TABLE progression_decisions ADD COLUMN training_goal {nn}",
        f"ALTER TABLE progression_tracks ADD COLUMN training_goal {nn}",
        f"ALTER TABLE progression_group_states ADD COLUMN training_goal {nn}",
        "ALTER TABLE progression_tracks DROP INDEX uk_ptr_user_pair_context, ADD UNIQUE KEY uk_ptr_user_pair_context "
        "(user_id, exercise_id, equipment_id, fatigue_context, training_goal)",
        "ALTER TABLE progression_group_states DROP INDEX uk_pgs_user_group, ADD UNIQUE KEY uk_pgs_user_group "
        "(user_id, muscle_group_id, training_goal)",
    ]


def test_convergido_nao_faz_nada_e_reexecutar_e_idempotente():
    db = _convergido()
    _mod()["up"](db)
    assert db.executed == []


def test_meio_caminho_aplica_so_o_que_falta():
    colunas = {t: _coluna_ok(t) for t in _mod()["TABLES"] if t != "exercises"}
    chaves = _antes()
    chaves[("progression_tracks", "uk_ptr_user_pair_context")] = list(_depois()[("progression_tracks", "uk_ptr_user_pair_context")])
    db = FakeDB(colunas=colunas, chaves=chaves)
    _mod()["up"](db)
    assert db.executed == [
        "ALTER TABLE exercises ADD COLUMN training_goal ENUM('hypertrophy','strength') NOT NULL DEFAULT 'hypertrophy', "
        "ALGORITHM=INSTANT",
        "ALTER TABLE progression_group_states DROP INDEX uk_pgs_user_group, ADD UNIQUE KEY uk_pgs_user_group "
        "(user_id, muscle_group_id, training_goal)",
    ]


@pytest.mark.parametrize(
    "tabela, forma, erro",
    [
        ("exercises", ("varchar(16)", "NO", "hypertrophy"), "esperado enum"),
        ("exercises", ("enum('hypertrophy','strength')", "YES", "hypertrophy"), "IS_NULLABLE=YES"),
        ("workout_plan_template_slot_items", ("enum('hypertrophy','strength')", "YES", "hypertrophy"), "default"),
        ("progression_tracks", ("enum('hypertrophy','strength')", "NO", "strength"), "default"),
    ],
)
def test_coluna_divergente_falha_antes_do_primeiro_statement(tabela, forma, erro):
    db = FakeDB(colunas={tabela: forma})
    with pytest.raises(Exception, match=erro) as exc:
        _mod()["up"](db)
    assert type(exc.value).__name__ == "TrainingGoalError"
    assert db.executed == []


def test_chave_em_forma_desconhecida_ou_ausente_falha():
    chaves = _antes()
    chaves[("progression_group_states", "uk_pgs_user_group")] = ["user_id"]
    db = FakeDB(chaves=chaves)
    with pytest.raises(Exception, match="uk_pgs_user_group de progression_group_states cobre"):
        _mod()["up"](db)
    chaves.pop(("progression_group_states", "uk_pgs_user_group"))
    with pytest.raises(Exception, match="nao existe"):
        _mod()["up"](FakeDB(chaves=chaves))
    assert db.executed == []


def test_tabela_ausente_falha_e_dry_run_aceita():
    with pytest.raises(Exception, match="tabela exercises nao existe"):
        _mod()["up"](FakeDB(tabelas=set(_mod()["TABLES"]) - {"exercises"}))
    db = FakeDB(tabelas=set(), chaves={}, dry_run=True)
    assert len(_mod()["plan"](db)) == len(_mod()["TARGETS"]) + len(_mod()["UNIQUE_KEYS"])


def test_dry_run_em_banco_atrasado_sem_a_chave_da_068_lista_o_add_sem_falhar():
    """Regressão do CI do PR #626: `migrate(dry_run=True)` num banco antes da 068
    (as anteriores só listam DDL) não tem `uk_ptr_user_pair_context`."""
    chaves = _antes()
    chaves.pop(("progression_tracks", "uk_ptr_user_pair_context"))
    plano = [sql for sql, _p in _mod()["plan"](FakeDB(chaves=chaves, dry_run=True))]
    assert plano[-2] == (
        "ALTER TABLE progression_tracks ADD UNIQUE KEY uk_ptr_user_pair_context "
        "(user_id, exercise_id, equipment_id, fatigue_context, training_goal)"
    )
    assert plano[-1].startswith("ALTER TABLE progression_group_states DROP INDEX uk_pgs_user_group, ADD")


def test_down_volta_as_chaves_antes_de_soltar_as_colunas():
    db = _convergido()
    _mod()["down"](db)
    assert db.executed[:2] == [
        "ALTER TABLE progression_tracks DROP INDEX uk_ptr_user_pair_context, ADD UNIQUE KEY uk_ptr_user_pair_context "
        "(user_id, exercise_id, equipment_id, fatigue_context)",
        "ALTER TABLE progression_group_states DROP INDEX uk_pgs_user_group, ADD UNIQUE KEY uk_pgs_user_group "
        "(user_id, muscle_group_id)",
    ]
    assert db.executed[2:] == [f"ALTER TABLE {t} DROP COLUMN training_goal" for t in reversed(_mod()["TABLES"])]
    # Já desfeito: nada.
    vazio = FakeDB()
    _mod()["down"](vazio)
    assert vazio.executed == []


def test_ddl_sem_fk_sem_backfill_e_registrada_como_remodelagem():
    texto = MIGRATION.read_text(encoding="utf-8")
    codigo = texto.split('"""', 2)[2]
    for proibido in ("FOREIGN KEY", "REFERENCES", "exercise_catalog", "UPDATE ", "INSERT "):
        assert proibido not in codigo, proibido
    assert MIGRATION.name in support.RESHAPING_MIGRATIONS
    assert support.RESHAPING_MIGRATIONS[-1] == MIGRATION.name  # desfeita primeiro (ordem inversa)
    # As chaves "antes" são as que a 068 e a 036 conferem por igualdade.
    g068 = m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / "068_progression_track_fatigue_context.py").__globals__
    assert list(_antes()[("progression_tracks", g068["NEW_UK"])]) == g068["NEW_UK_COLUMNS"]
