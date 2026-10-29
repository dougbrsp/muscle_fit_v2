"""Plano da migração 077 (sexo canônico em `users`, nutrição L2a) contra um banco
sintético: produção (1 'M', 1 'F', 2 NULL) -> 2 UPDATEs e 2 linhas; reexecução
-> 0; NULL e valor desconhecido ficam; forma divergente -> erro antes do
primeiro statement; tabela ausente -> erro (DRY-RUN: 0); `plan_down` no-op.

Os aliases embutidos na migração (cópia congelada, para o checksum) são
conferidos contra o dono (`anthropometrics.normalize_sex`) nos dois sentidos."""

from __future__ import annotations

import functools
import logging

import pytest

from app import migrate as m
from app.domain.nutrition import anthropometrics as an

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "077_users_sex_canonical.py"
T = "users"
FORMA_DA_001 = {"COLUMN_TYPE": "varchar(20)", "IS_NULLABLE": "YES", "CHARACTER_SET_NAME": "utf8mb4"}


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


class FakeDB:
    """`users.sex` em memória: o SELECT agrupa por valor EXATO (como o
    `COLLATE utf8mb4_0900_bin`) e o UPDATE troca os valores exatos do `IN`."""

    def __init__(self, values=(), *, column=None, table=True, dry_run=False):
        self.values: list[str | None] = list(values)
        self.column = dict(FORMA_DA_001) if column is None else column
        self.table = table
        self.dry_run = dry_run
        self.executed: list[tuple[str, tuple | None]] = []

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": T}] if self.table else []
        if "information_schema.COLUMNS" in sql:
            assert params == (T, "sex")
            return [{"COLUMN_NAME": "sex", **self.column}] if self.column else []
        if sql == _module()["DISTINCT_SQL"]:
            contagem: dict[str, int] = {}
            for value in self.values:
                if value is not None:
                    contagem[value] = contagem.get(value, 0) + 1
            return [{"raw_value": raw, "n": n} for raw, n in sorted(contagem.items())]
        raise AssertionError(f"consulta inesperada: {sql}")

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        assert sql.startswith(f"UPDATE {T} SET sex = %s WHERE sex COLLATE utf8mb4_0900_bin IN (")
        alvo, *origens = params
        afetadas = 0
        for i, value in enumerate(self.values):
            if value in origens:
                self.values[i] = alvo
                afetadas += 1
        return afetadas


def _sqls(statements):
    return [sql for sql, _ in statements]


# ---------------------------------------------------------------------------
# Produção (medida em 2026-10-15) e reexecução
# ---------------------------------------------------------------------------


def test_producao_toca_exatamente_2_linhas_e_preserva_os_null():
    db = FakeDB(["M", "F", None, None])
    statements = _module()["plan"](db)
    assert [params for _sql, params in statements] == [("male", "M"), ("female", "F")]
    assert _sqls(statements) == [_module()["update_sql"](1)] * 2

    _module()["up"](db)
    assert db.values == ["male", "female", None, None]
    assert len(db.executed) == 2


def test_reexecucao_e_zero_statements():
    db = FakeDB(["M", "F", None, None])
    _module()["up"](db)
    assert _module()["plan"](db) == []
    db.executed.clear()
    _module()["up"](db)
    assert db.executed == []


def test_dev_com_um_m_vira_male():
    db = FakeDB(["M"])
    _module()["up"](db)
    assert db.values == ["male"]


def test_variacoes_aceitas_pelo_motor_convergem_num_update_por_codigo():
    valores = ["M", "m", "Masculino", " homem ", "MALE", "F", "Mulher", "feminina", "não-binário", "Outro", "male"]
    db = FakeDB(valores)
    statements = _module()["plan"](db)
    assert [params[0] for _sql, params in statements] == ["male", "female", "other"]
    # 'male' já canônico não entra no IN; 'MALE' (outra caixa) entra.
    assert "male" not in statements[0][1][1:] and "MALE" in statements[0][1][1:]
    _module()["up"](db)
    assert db.values == [an.sex_code(v) for v in valores]
    assert set(db.values) == {"male", "female", "other"}
    assert _module()["plan"](db) == []


def test_desconhecido_nao_muda_e_o_plano_lista_o_que_sobrou(caplog):
    db = FakeDB(["M", "abc", "abc", None])
    with caplog.at_level(logging.WARNING, logger="fitness.migrate"):
        statements = _module()["plan"](db)
    assert [params for _sql, params in statements] == [("male", "M")]
    assert "'abc' (2 linha(s))" in caplog.text and "077" in caplog.text
    _module()["up"](db)
    assert db.values == ["male", "abc", "abc", None]
    assert _module()["plan"](db) == []


def test_tabela_sem_sexo_preenchido_nao_faz_nada():
    assert _module()["plan"](FakeDB([None, None])) == []
    assert _module()["plan"](FakeDB([])) == []


# ---------------------------------------------------------------------------
# Forma, tabela ausente, down
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "column",
    [
        None,  # coluna ausente
        {**FORMA_DA_001, "COLUMN_TYPE": "varchar(10)"},
        {**FORMA_DA_001, "IS_NULLABLE": "NO"},
        {**FORMA_DA_001, "COLUMN_TYPE": "enum('M','F')"},
        {**FORMA_DA_001, "CHARACTER_SET_NAME": "latin1"},
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(column):
    db = FakeDB(["M"], column=column if column is not None else {})
    with pytest.raises(_module()["UsersSexCanonicalError"]):
        _module()["up"](db)
    assert db.executed == []


def test_tabela_ausente_falha_na_execucao_real_e_e_zero_no_dry_run():
    with pytest.raises(_module()["UsersSexCanonicalError"]):
        _module()["plan"](FakeDB(table=False))
    assert _module()["plan"](FakeDB(table=False, dry_run=True)) == []


def test_down_nao_reverte_dados():
    db = FakeDB(["male", "female"])
    assert _module()["plan_down"](db) == []
    _module()["down"](db)
    assert db.executed == [] and db.values == ["male", "female"]


def test_so_dml_em_users_sex_com_comparacao_binaria():
    sqls = [_module()["DISTINCT_SQL"], _module()["update_sql"](3)]
    assert all("COLLATE utf8mb4_0900_bin" in sql for sql in sqls)
    assert _module()["update_sql"](3).endswith("IN (%s, %s, %s)")
    fonte = MIGRATION.read_text(encoding="utf-8").upper()
    for proibido in ("ALTER TABLE", "DROP ", "DELETE ", "INSERT ", "EXERCISE_CATALOG"):
        assert proibido not in fonte


# ---------------------------------------------------------------------------
# Paridade dos aliases embutidos com o dono
# ---------------------------------------------------------------------------


def test_codigos_canonicos_sao_os_do_motor():
    assert _module()["CANONICAL_CODES"] == tuple(sexo.value for sexo in an.Sex)


@pytest.mark.parametrize("alias", sorted(_module()["SEX_ALIASES"]))
def test_cada_alias_embutido_da_o_mesmo_codigo_no_motor(alias):
    assert an.sex_code(alias) == _module()["SEX_ALIASES"][alias]


def test_todo_alias_do_motor_esta_embutido():
    do_motor = {alias: sexo.value for alias, sexo in an._SEX_ALIASES.items()}
    do_motor.update({sexo.value: sexo.value for sexo in an.Sex})
    assert _module()["SEX_ALIASES"] == do_motor


@pytest.mark.parametrize("raw", ["  Não-Binário ", "FEMININO", "masc_", "Homem", "non_binary", "x", "ÁBC"])
def test_a_dobra_embutida_e_a_do_motor(raw):
    assert _module()["_fold"](raw) == an.fold_text(raw)
    assert _module()["canonical_code"](raw) == an.sex_code(raw)
