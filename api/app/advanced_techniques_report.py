"""Relatorio SOMENTE LEITURA de valores legados de `advanced_technique` fora
do catalogo `advanced_techniques` (PR-06: FK logica, sem FK fisica).

Uso (mesmas variaveis de banco da aplicacao):
    python -m app.advanced_techniques_report

So executa SELECT (um agregado por tabela + o catalogo). Nao altera dados:
a correcao de valor sujo, se houver, e decisao posterior e vira migracao.
A comparacao e feita em Python (sem JOIN entre colunas de collations
possivelmente diferentes).
"""

from __future__ import annotations

import json
from typing import Any

# (tabela, coluna) que guardam um codigo de tecnica.
LEGACY_COLUMNS = (
    ("workout_plan_template_slot_items", "advanced_technique"),
    ("workout_plan_exercises", "advanced_technique"),
)

# FROM de cada tabela. Os itens de Modelo contam so a versao VIGENTE (065):
# versoes anteriores sao historico imutavel - nenhuma correcao as alcanca, e
# conta-las multiplicaria o mesmo item por quantas edicoes o Modelo teve.
FROM_CLAUSES = {
    "workout_plan_template_slot_items": (
        "workout_plan_template_slot_items "
        "JOIN workout_plan_template_slots s ON s.id = workout_plan_template_slot_items.slot_id "
        "JOIN workout_plan_template_versions v ON v.id = s.version_id AND v.current_template_id = s.template_id"
    ),
}


def build_report(conn: Any) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT code, is_active FROM advanced_techniques")
        catalog = {row["code"]: bool(int(row["is_active"] or 0)) for row in cur.fetchall() or []}
        tables = []
        for table, column in LEGACY_COLUMNS:
            source = FROM_CLAUSES.get(table, table)
            cur.execute(
                f"SELECT {table}.{column} AS value, COUNT(*) AS n FROM {source} "
                f"WHERE {table}.{column} IS NOT NULL GROUP BY {table}.{column} ORDER BY {table}.{column}"
            )
            rows = [(row["value"], int(row["n"])) for row in cur.fetchall() or []]
            tables.append(
                {
                    "table": table,
                    "column": column,
                    "rows_with_value": sum(n for _, n in rows),
                    "valid": {value: n for value, n in rows if catalog.get(value) is True},
                    "inactive": {value: n for value, n in rows if catalog.get(value) is False},
                    "invalid": {value: n for value, n in rows if value not in catalog},
                }
            )
    return {
        "catalog_codes": sorted(catalog),
        "tables": tables,
        "total_invalid_rows": sum(sum(t["invalid"].values()) for t in tables),
    }


def main() -> int:
    from .db import get_conn

    conn = get_conn()
    try:
        report = build_report(conn)
    finally:
        conn.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
