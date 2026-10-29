"""Recorte da migracao 029 (`exercise_aliases` sem UNIQUE global por `alias`)
compartilhado entre o teste dedicado da 029 e o runner consolidado da cadeia
(`tests/integration/dataapi/test_runner_cadeia_completa.py`).

So leitura de schema + o plano esperado: nada aqui aplica migracao.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import migrate as m

MIGRATION_029 = m.DEFAULT_MIGRATIONS_DIR / "029_aliases_sem_unique_global.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_14_029.json"
PLAN = ["ALTER TABLE exercise_aliases DROP INDEX uq_exercise_aliases_alias"]


def aliases_indexes(raw: Any) -> list[tuple]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'exercise_aliases'"
        )
        return sorted(
            (r["INDEX_NAME"], int(r["SEQ_IN_INDEX"]), r["COLUMN_NAME"], int(r["NON_UNIQUE"]))
            for r in cur.fetchall()
        )


def aurora_aliases_indexes() -> list[tuple]:
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    return sorted((i["idx"], int(i["seq"]), i["col"], int(i["non_unique"])) for i in snapshot["indexes"])
