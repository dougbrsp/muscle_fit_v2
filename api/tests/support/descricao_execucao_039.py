"""Migracao 039 (descricao de execucao do exercicio) nos testes: recorte real
do `information_schema`, estado convergido sobre o recorte e o `ALTER` que a
migracao produz.

O recorte (`schema_dev_2026_09_20_039.json`) foi lido do MySQL 8.0.46 do
ambiente DEV, schema `fitness`, na versao 38 de `schema_migration_files` - a
MESMA versao em que a Aurora de producao esta para estas duas tabelas
(`exercise_catalog` nao e tocada por nenhuma migracao entre a 028 e a 038).
Nenhuma escrita foi feita para produzi-lo: so SELECTs em `information_schema`.
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app import migrate as m

MIGRATION_039 = m.DEFAULT_MIGRATIONS_DIR / "039_descricao_execucao_exercicio.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "schema_dev_2026_09_20_039.json"

CATALOG = "exercise_catalog"

ALTER_CATALOG = (
    f"ALTER TABLE {CATALOG} ADD COLUMN execution_description TEXT NULL, "
    "ADD COLUMN execution_description_model VARCHAR(80) NULL, "
    "ADD COLUMN execution_description_prompt_version VARCHAR(40) NULL, "
    "ADD COLUMN execution_description_generated_at DATETIME NULL, "
    "ADD COLUMN execution_description_reviewed_by BIGINT NULL, "
    "ADD COLUMN execution_description_reviewed_at DATETIME NULL"
)

# (coluna, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA, coll)
AFTER_039 = [
    ("execution_description", "text", "YES", None, "", "", "utf8mb4_0900_ai_ci"),
    ("execution_description_model", "varchar(80)", "YES", None, "", "", "utf8mb4_0900_ai_ci"),
    ("execution_description_prompt_version", "varchar(40)", "YES", None, "", "", "utf8mb4_0900_ai_ci"),
    ("execution_description_generated_at", "datetime", "YES", None, "", "", None),
    ("execution_description_reviewed_by", "bigint", "YES", None, "", "", None),
    ("execution_description_reviewed_at", "datetime", "YES", None, "", "", None),
]


def load_snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def module() -> dict[str, Any]:
    """Carregado UMA vez: `load_python_migration` cria um modulo novo a cada
    chamada, e duas chamadas dariam duas classes `DescricaoExecucaoError`
    diferentes - `pytest.raises` nunca casaria com a excecao levantada."""
    return m.load_python_migration(MIGRATION_039).__globals__


def converged(snapshot: dict[str, Any]) -> dict[str, Any]:
    """O mesmo recorte DEPOIS da 039 (o que uma reexecucao encontraria)."""
    snap = copy.deepcopy(snapshot)
    position = max(c["pos"] for c in snap["columns"] if c["t"] == CATALOG)
    for column, column_type, nullable, default, ckey, extra, coll in AFTER_039:
        position += 1
        snap["columns"].append(
            {
                "t": CATALOG,
                "col": column,
                "type": column_type,
                "nullable": nullable,
                "dflt": default,
                "ckey": ckey,
                "extra": extra,
                "pos": position,
                "coll": coll,
            }
        )
    return snap
