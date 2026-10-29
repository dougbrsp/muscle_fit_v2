"""Plano da migração 078 (`nutrition_plans`, nutrição L2b).

- Aurora (recorte real somente leitura `aurora_schema_2026_09_17_035.json`, sem
  a tabela) -> 1 `CREATE TABLE`; o plano só lê `information_schema`.
- Convergido (sintético, com a expressão gerada como o MySQL a reescreve) -> 0.
- Forma divergente (tipo, nulabilidade, expressão gerada, UNIQUE ausente,
  ENGINE) -> erro ANTES do primeiro statement.
- `users` ausente: erro numa execução real, CREATE em DRY-RUN.
- DDL: uma vigente por aluno (coluna gerada + UNIQUE), FK só para `users`.

Rodar: cd api && python -m pytest tests/unit/test_migration_078_plano.py
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "078_nutrition_plans.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_035.json"
T = "nutrition_plans"
# Como o MySQL 8 devolve a expressão em `GENERATION_EXPRESSION`.
EXPRESSAO_DO_MYSQL = "if(((`status` = _utf8mb4'published') and (`superseded_at` is null)),`user_id`,NULL)"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _sql(texto: str) -> str:
    return " ".join(texto.split())


class FakeDB:
    def __init__(self, *, tabela=True, users=True, colunas=None, gen=EXPRESSAO_DO_MYSQL, indices=None,
                 engine="InnoDB", dry_run=False):
        self.tabela = tabela
        self.users = users
        self.colunas = dict(_module()["EXPECTED_COLUMNS"] if colunas is None else colunas)
        self.gen = gen
        self.indices = (
            {"uq_np_current_user": (0, ("current_user_id",)), "idx_np_user_effective": (1, ("user_id", "effective_from"))}
            if indices is None else indices
        )
        self.engine = engine
        self.dry_run = dry_run
        self.queries: list[str] = []

    def query(self, sql, params=None):
        self.queries.append(sql)
        if "information_schema.TABLES" in sql:
            linhas = []
            if self.tabela:
                linhas.append({"TABLE_NAME": T, "ENGINE": self.engine})
            if self.users:
                linhas.append({"TABLE_NAME": "users", "ENGINE": "InnoDB"})
            return linhas
        if "information_schema.COLUMNS" in sql:
            linhas = []
            if self.users:
                linhas.append({"TABLE_NAME": "users", "COLUMN_NAME": "id", "COLUMN_TYPE": "bigint", "IS_NULLABLE": "NO",
                               "COLUMN_KEY": "PRI", "GENERATION_EXPRESSION": ""})
            if self.tabela:
                for nome, (tipo, nulo) in self.colunas.items():
                    linhas.append({"TABLE_NAME": T, "COLUMN_NAME": nome, "COLUMN_TYPE": tipo, "IS_NULLABLE": nulo,
                                   "COLUMN_KEY": "", "GENERATION_EXPRESSION": self.gen if nome == "current_user_id" else ""})
            return linhas
        if "information_schema.STATISTICS" in sql:
            if not self.tabela:
                return []
            return [
                {"TABLE_NAME": T, "INDEX_NAME": nome, "SEQ_IN_INDEX": seq, "COLUMN_NAME": col, "NON_UNIQUE": nu}
                for nome, (nu, cols) in self.indices.items()
                for seq, col in enumerate(cols, 1)
            ]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        raise AssertionError(f"escrita no plano: {sql}")


def test_aurora_hoje_cria_a_tabela_com_um_statement():
    snap = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert T not in {t["table_name"] for t in snap["tables"]}
    db = SafetySnapshotDB(snap)
    statements = _module()["plan"](db)
    assert [(_sql(s), p) for s, p in statements] == [(_sql(_module()["CREATE_SQL"]), None)]
    assert all(q.strip().upper().startswith("SELECT") and "information_schema" in q for q in db.queries)


def test_convergido_nao_faz_nada():
    assert _module()["plan"](FakeDB()) == []


def test_largura_do_tinyint_e_ignorada_menos_a_do_booleano():
    colunas = dict(_module()["EXPECTED_COLUMNS"])
    colunas["meals_per_day"] = ("tinyint(4)", "YES")
    assert _module()["plan"](FakeDB(colunas=colunas)) == []
    colunas["advanced"] = ("tinyint", "NO")
    with pytest.raises(_module()["NutritionPlansError"]):
        _module()["plan"](FakeDB(colunas=colunas))


@pytest.mark.parametrize(
    "ajuste",
    [
        {"colunas": {**_module()["EXPECTED_COLUMNS"], "strategy": ("varchar(32)", "NO")}},
        {"colunas": {**_module()["EXPECTED_COLUMNS"], "protein_g_per_kg": ("decimal(4,2)", "YES")}},
        {"colunas": {k: v for k, v in _module()["EXPECTED_COLUMNS"].items() if k != "superseded_at"}},
        {"gen": "if((`superseded_at` is null),`user_id`,NULL)"},
        {"gen": ""},
        {"indices": {"idx_np_user_effective": (1, ("user_id", "effective_from"))}},
        {"indices": {"uq_np_current_user": (1, ("current_user_id",)), "idx_np_user_effective": (1, ("user_id", "effective_from"))}},
        {"engine": "MyISAM"},
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(ajuste):
    with pytest.raises(_module()["NutritionPlansError"], match="Nenhum statement executado"):
        _module()["up"](FakeDB(**ajuste))


def test_users_ausente_falha_e_dry_run_planeja_o_create():
    with pytest.raises(_module()["NutritionPlansError"]):
        _module()["plan"](FakeDB(tabela=False, users=False))
    assert len(_module()["plan"](FakeDB(tabela=False, users=False, dry_run=True))) == 1


def test_plan_down_e_drop_so_quando_existe():
    assert [s for s, _ in _module()["plan_down"](FakeDB())] == [_module()["DROP_SQL"]]
    assert _module()["plan_down"](FakeDB(tabela=False)) == []


def test_ddl_uma_vigente_por_aluno_e_fk_so_para_users():
    sql = _sql(_module()["CREATE_SQL"])
    assert ("current_user_id BIGINT GENERATED ALWAYS AS "
            "(IF(status = 'published' AND superseded_at IS NULL, user_id, NULL)) VIRTUAL") in sql
    assert "UNIQUE KEY uq_np_current_user (current_user_id)" in sql
    assert "KEY idx_np_user_effective (user_id, effective_from)" in sql
    assert re.findall(r"REFERENCES (\w+)", sql) == ["users"]
    assert "ON DELETE CASCADE" in sql and "exercise_catalog" not in sql
    assert "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4" in sql
    assert ":" not in sql.split("COLLATE")[0], "literal com ':' viraria parâmetro no shim da Data API"


def test_expected_columns_e_o_create_descrevem_as_mesmas_colunas():
    linhas = [linha.strip() for linha in _module()["CREATE_SQL"].splitlines()]
    nomes = [linha.split()[0] for linha in linhas if re.match(r"^[a-z_]+ ", linha) and not linha.startswith(("CREATE", "UNIQUE", "KEY", "CONSTRAINT"))]
    assert set(nomes) == set(_module()["EXPECTED_COLUMNS"])


def test_expressao_normalizada_ignora_o_que_o_mysql_reescreve():
    normaliza = _module()["_normalize_expression"]
    assert normaliza(EXPRESSAO_DO_MYSQL) == normaliza(_module()["CURRENT_EXPRESSION"])
    assert normaliza("if(((`status` = _utf8mb4'draft') and (`superseded_at` is null)),`user_id`,NULL)") != normaliza(
        _module()["CURRENT_EXPRESSION"]
    )
