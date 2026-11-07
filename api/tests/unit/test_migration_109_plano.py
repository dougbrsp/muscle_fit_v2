"""Plano da migração 109 (`exercise_card_references`, fotos de referência dos cards V3).

- Sem a tabela -> 1 `CREATE TABLE`; convergida -> 0 (reexecução); forma divergente -> erro antes do 1º statement.
- DDL: sem FOREIGN KEY (regra da 028), `exercise_id BIGINT UNSIGNED`, sem DML, `plan_down` = `DROP TABLE`.

Rodar: cd api && python -m pytest tests/unit/test_migration_109_plano.py
"""

from __future__ import annotations

import functools
import re

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "109_exercise_card_references.py"
TABLE = "exercise_card_references"
CATALOG = "exercise_catalog"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


class FakeDB:
    def __init__(self, *, tabela=True, catalogo=True, catalogo_tipo="bigint(20) unsigned", catalogo_key="PRI",
                 catalogo_engine="InnoDB", engine="InnoDB", colunas=None, indices=None):
        self.tabela = tabela
        self.catalogo = catalogo
        self.catalogo_tipo = catalogo_tipo
        self.catalogo_key = catalogo_key
        self.catalogo_engine = catalogo_engine
        self.engine = engine
        self.colunas = dict(_module()["EXPECTED_COLUMNS"] if colunas is None else colunas)
        self.indices = dict(_module()["EXPECTED_INDEXES"] if indices is None else indices)
        self.executados: list[str] = []
        self.leituras: list[str] = []

    def query(self, sql, params=None):
        self.leituras.append(sql)
        if "information_schema.TABLES" in sql:
            linhas = []
            if self.catalogo:
                linhas.append({"TABLE_NAME": CATALOG, "ENGINE": self.catalogo_engine})
            if self.tabela:
                linhas.append({"TABLE_NAME": TABLE, "ENGINE": self.engine})
            return linhas
        if "information_schema.COLUMNS" in sql:
            linhas = []
            if self.catalogo:
                linhas.append({"TABLE_NAME": CATALOG, "COLUMN_NAME": "id", "COLUMN_TYPE": self.catalogo_tipo,
                               "IS_NULLABLE": "NO", "COLUMN_KEY": self.catalogo_key})
            if self.tabela:
                linhas += [
                    {"TABLE_NAME": TABLE, "COLUMN_NAME": n, "COLUMN_TYPE": t, "IS_NULLABLE": nu, "COLUMN_KEY": ""}
                    for n, (t, nu) in self.colunas.items()
                ]
            return linhas
        if "information_schema.STATISTICS" in sql:
            return [
                {"TABLE_NAME": TABLE, "INDEX_NAME": nome, "COLUMN_NAME": c, "NON_UNIQUE": 0 if unico else 1}
                for nome, (cols, unico) in self.indices.items()
                for c in cols
            ]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        self.executados.append(sql)


def _sql(plano) -> list[str]:
    return [" ".join(s.split()) for s, _ in plano]


def test_sem_a_tabela_cria_uma_vez():
    plano = _sql(_module()["plan"](FakeDB(tabela=False)))
    assert len(plano) == 1
    assert plano[0].startswith(f"CREATE TABLE IF NOT EXISTS {TABLE}")
    assert "exercise_id BIGINT UNSIGNED NOT NULL" in plano[0]
    assert "KEY idx_ecr_exercise_active (exercise_id, deleted_at)" in plano[0]
    assert "deleted_at DATETIME NULL" in plano[0] and "note VARCHAR(160) NULL" in plano[0]


def test_ddl_sem_fk_sem_enum_sem_dml():
    sql = _module()["CREATE_SQL"].upper().replace(TABLE.upper(), "T")  # o nome da tabela contem "REFERENCES"
    for proibido in ("FOREIGN KEY", "REFERENCES", "ENUM", "CHECK", "INSERT INTO", "UPDATE ", "DELETE FROM"):
        assert proibido not in sql, proibido
    assert "ENGINE=INNODB" in sql


def test_reexecucao_nao_faz_nada():
    db = FakeDB()
    assert _module()["plan"](db) == []
    _module()["up"](db)
    assert db.executados == []


def test_up_executa_o_unico_statement_e_so_ele():
    db = FakeDB(tabela=False)
    _module()["up"](db)
    assert len(db.executados) == 1 and db.executados[0].strip().startswith("CREATE TABLE IF NOT EXISTS")


def test_tipo_da_coluna_divergente_falha_antes_de_qualquer_statement():
    colunas = dict(_module()["EXPECTED_COLUMNS"], exercise_id=("bigint", "NO"))
    db = FakeDB(colunas=colunas)
    with pytest.raises(Exception, match="109"):
        _module()["up"](db)
    assert db.executados == []


def test_nulabilidade_divergente_falha():
    colunas = dict(_module()["EXPECTED_COLUMNS"], deleted_at=("datetime", "NO"))
    with pytest.raises(Exception, match="109"):
        _module()["plan"](FakeDB(colunas=colunas))


def test_coluna_ausente_falha():
    colunas = {n: v for n, v in _module()["EXPECTED_COLUMNS"].items() if n != "note"}
    with pytest.raises(Exception, match="109"):
        _module()["plan"](FakeDB(colunas=colunas))


def test_indice_ausente_ou_diferente_falha():
    with pytest.raises(Exception, match="109"):
        _module()["plan"](FakeDB(indices={}))
    errado = {"idx_ecr_exercise_active": (("exercise_id",), False)}
    with pytest.raises(Exception, match="109"):
        _module()["plan"](FakeDB(indices=errado))
    unico = {"idx_ecr_exercise_active": (("exercise_id", "deleted_at"), True)}
    with pytest.raises(Exception, match="109"):
        _module()["plan"](FakeDB(indices=unico))


def test_engine_diferente_de_innodb_falha():
    with pytest.raises(Exception, match="109"):
        _module()["plan"](FakeDB(engine="MyISAM"))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"catalogo": False},
        {"catalogo_tipo": "bigint"},
        {"catalogo_tipo": "int unsigned"},
        {"catalogo_key": ""},
        {"catalogo_engine": "MyISAM"},
    ],
    ids=["sem-catalogo", "bigint-com-sinal", "int", "sem-pk", "catalogo-myisam"],
)
def test_catalogo_fora_do_formato_falha_mesmo_sem_a_tabela(kwargs):
    db = FakeDB(tabela=False, **kwargs)
    with pytest.raises(Exception, match="109"):
        _module()["up"](db)
    assert db.executados == []


@pytest.mark.parametrize("tipo", ["bigint unsigned", "bigint(20) unsigned", "BIGINT(20) UNSIGNED"])
def test_largura_de_exibicao_do_mysql_5_e_aceita(tipo):
    assert _module()["plan"](FakeDB(catalogo_tipo=tipo)) == []


def test_down_e_so_o_drop_table():
    assert _sql(_module()["plan_down"](FakeDB())) == [f"DROP TABLE IF EXISTS {TABLE}"]
    assert _module()["plan_down"](FakeDB(tabela=False)) == []
    db = FakeDB()
    _module()["down"](db)
    assert db.executados == [f"DROP TABLE IF EXISTS {TABLE}"]


def test_so_le_information_schema_e_um_statement_por_chamada():
    db = FakeDB(tabela=False)
    _module()["plan"](db)
    assert db.leituras and all("information_schema" in sql for sql in db.leituras)
    for sql, _ in _module()["plan"](db):
        assert ";" not in sql.strip().rstrip(";")  # sem multi-statement (Data API)


def test_migracao_autocontida_nao_importa_o_app():
    texto = MIGRATION.read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+app\b", texto, re.MULTILINE)
