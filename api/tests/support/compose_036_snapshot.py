"""Compoe o recorte da Aurora usado pela migracao 036 a partir dos recortes JA
versionados (nenhuma leitura nova de producao) + a definicao de baseline de
`muscle_groups`.

Uso (regravar o arquivo):
    python -m tests.support.compose_036_snapshot

O resultado e determinístico: rodar de novo produz o mesmo JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "unit" / "data"
OUT = DATA / "aurora_schema_2026_09_17_036.json"

# tabela -> recorte de onde vem colunas/indices/FKs dela
SOURCES = {
    "users": "aurora_schema_2026_09_17_035.json",
    "equipment": "aurora_schema_2026_09_17_034.json",
    "activities": "aurora_schema_2026_09_14_026.json",
    "exercise_catalog": "aurora_schema_2026_09_14_026.json",
    "workout_plan_exercises": "aurora_schema_2026_09_14_026.json",
    "exercises": "aurora_schema_2026_09_14_026.json",
    "exercise_sets": "aurora_schema_2026_09_14_026.json",
}
TABLES_SOURCE = "aurora_schema_2026_09_17_035.json"  # lista COMPLETA de tabelas de producao

# `muscle_groups` nao aparece em nenhum recorte de producao ja versionado e
# este PR nao teve leitura da Aurora. A tabela e de BASELINE
# (api/db/migrations/001_schema.sql linhas 747-754, aplicada em producao via
# mysqldump do mesmo schema e nunca alterada por migracao posterior), entao o
# formato abaixo e o do proprio arquivo de baseline, conferido no MySQL de DEV.
MUSCLE_GROUPS_COLUMNS = [
    ("id", "bigint", "NO", None, "PRI", "auto_increment", None),
    ("name", "varchar(120)", "NO", None, "UNI", "", "utf8mb4_0900_ai_ci"),
    ("primary_muscles", "varchar(255)", "YES", None, "", "", "utf8mb4_0900_ai_ci"),
    ("secondary_muscles", "varchar(255)", "YES", None, "", "", "utf8mb4_0900_ai_ci"),
    ("created_at", "timestamp", "NO", "CURRENT_TIMESTAMP", "", "DEFAULT_GENERATED", None),
    (
        "updated_at",
        "timestamp",
        "NO",
        "CURRENT_TIMESTAMP",
        "",
        "DEFAULT_GENERATED on update CURRENT_TIMESTAMP",
        None,
    ),
]
MUSCLE_GROUPS_INDEXES = [("PRIMARY", 1, "id", 0), ("name", 1, "name", 0)]

ORIGEM = (
    "Recorte COMPOSTO para a migracao 036, sem nenhuma leitura nova de producao "
    "(o agente do PR-10 nao tem acesso a AWS). Fontes, todas ja versionadas neste "
    "repositorio e lidas da Aurora de producao (musclefit, sa-east-1) por SELECT "
    "somente leitura: (a) aurora_schema_2026_09_17_035.json - lista COMPLETA de "
    "tabelas de producao (MAX(schema_migration_files.version)=33) e colunas/indices/"
    "FKs de `users`; (b) aurora_schema_2026_09_17_034.json - `equipment`; "
    "(c) aurora_schema_2026_09_14_026.json - `activities`, `exercise_catalog`, "
    "`workout_plan_exercises`, `exercises` e `exercise_sets`. "
    "`muscle_groups` NAO existe em nenhum recorte de producao: e tabela de BASELINE "
    "(001_schema.sql, linhas 747-754), nunca alterada por migracao posterior, e o "
    "formato aqui e o do arquivo de baseline conferido no MySQL de DEV - a unica "
    "parte deste recorte que nao vem de uma leitura da Aurora. "
    "Nenhuma das tres tabelas da 036 (progression_group_states, progression_tracks, "
    "progression_decisions) existe em producao. `coll` = COLLATION_NAME da coluna. "
    "Gerado por tests/support/compose_036_snapshot.py."
)


def _load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def compose() -> dict:
    base = _load(TABLES_SOURCE)
    snapshot = {
        "_origem": ORIGEM,
        "versao": base["versao"],
        "tables": sorted(base["tables"], key=lambda t: t["table_name"]),
        "views": base.get("views", []),
        "columns": [],
        "indexes": [],
        "foreign_keys": [],
    }
    known = {t["table_name"] for t in snapshot["tables"]}
    for table in sorted(set(SOURCES) | {"muscle_groups"}):
        assert table in known, f"{table} nao esta na lista de tabelas de producao"

    for table, source in sorted(SOURCES.items()):
        data = _load(source)
        snapshot["columns"] += [c for c in data["columns"] if c["t"] == table]
        snapshot["indexes"] += [i for i in data["indexes"] if i["t"] == table]
        snapshot["foreign_keys"] += [f for f in data["foreign_keys"] if f["t"] == table]

    for pos, (col, type_, nullable, dflt, ckey, extra, coll) in enumerate(MUSCLE_GROUPS_COLUMNS, start=1):
        snapshot["columns"].append(
            {
                "t": "muscle_groups",
                "pos": pos,
                "col": col,
                "type": type_,
                "nullable": nullable,
                "dflt": dflt,
                "ckey": ckey,
                "extra": extra,
                "coll": coll,
            }
        )
    for idx, seq, col, non_unique in MUSCLE_GROUPS_INDEXES:
        snapshot["indexes"].append({"t": "muscle_groups", "idx": idx, "seq": seq, "col": col, "non_unique": non_unique})

    snapshot["columns"].sort(key=lambda c: (c["t"], c["pos"]))
    snapshot["indexes"].sort(key=lambda i: (i["t"], i["idx"], i["seq"]))
    snapshot["foreign_keys"].sort(key=lambda f: (f["t"], f["fk"], f["col"]))
    return snapshot


if __name__ == "__main__":  # pragma: no cover - utilitario
    OUT.write_text(json.dumps(compose(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"escrito: {OUT}")
