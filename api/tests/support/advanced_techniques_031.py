"""Recorte da migracao 031 (catalogo `advanced_techniques`) compartilhado entre
o teste dedicado da 031 e o runner consolidado da cadeia
(`tests/integration/dataapi/test_runner_cadeia_completa.py`).

So leitura do catalogo + a lista de codigos esperada: nada aqui aplica
migracao.
"""

from __future__ import annotations

from typing import Any

from app import migrate as m

MIGRATION_031 = m.DEFAULT_MIGRATIONS_DIR / "031_advanced_techniques_catalog.py"

# Os 8 codigos que a 031 insere. O plano da migracao em banco novo tem 9
# statements: 1 CREATE TABLE + 1 INSERT por codigo.
CODES = [
    "rest_pause",
    "drop_set",
    "fst7",
    "gvt",
    "forced_reps",
    "negative_reps",
    "partial_reps",
    "cheat_reps",
]
STATEMENTS_031 = 1 + len(CODES)


def rows(raw: Any) -> dict[str, dict]:
    with raw.cursor() as cur:
        cur.execute("SELECT * FROM advanced_techniques")
        return {r["code"]: r for r in cur.fetchall()}
