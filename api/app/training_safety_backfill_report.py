"""Relatorio SOMENTE LEITURA do backfill de seguranca (PR-07; checklist da
secao 12 do PLANO_20260912.md, decisao D3): o que o admin precisa cadastrar
ANTES de ligar os motores novos.

1. Equipamentos sem `max_safe_rpe` (tratados como 9: falha nao permitida).
2. Tecnicas avancadas usadas em modelos (`workout_plan_template_slot_items`) e
   fichas (`workout_plan_exercises`) cujo par exercicio x equipamento nao tem a
   tecnica liberada pelo resolvedor (`domain/training_safety.py`): sem
   marcacao, marcada incompativel, ou exige falha num par sem falha permitida.

Uso (mesmas variaveis de banco da aplicacao):
    python -m app.training_safety_backfill_report

So executa SELECT. Funciona tambem ANTES das migracoes 032/033 (colunas e
tabelas ausentes = nada cadastrado; sai `schema_pending`), para medir o
trabalho de cadastro antes do deploy. Os JOINs sao so por id inteiro; codigos
de tecnica sao comparados em Python (sem JOIN entre collations diferentes).
"""

from __future__ import annotations

import json
from typing import Any

from .domain import training_safety as ts

UNMARKED = "unmarked"
MARKED_COMPATIBLE = "compatible"
MARKED_INCOMPATIBLE = "incompatible"

# origem -> SELECT agregado (tecnica, equipamento, exercicio, n)
USAGE_QUERIES = {
    "workout_plan_template_slot_items": (
        "SELECT i.advanced_technique AS technique, i.equipment_id AS equipment_id, i.mapping_id AS exercise_id, "
        "COUNT(*) AS n FROM workout_plan_template_slot_items i "
        # So a versao VIGENTE do Modelo (065): e o que ainda vai virar ficha.
        "JOIN workout_plan_template_slots s ON s.id = i.slot_id "
        "JOIN workout_plan_template_versions v ON v.id = s.version_id AND v.current_template_id = s.template_id "
        "WHERE i.advanced_technique IS NOT NULL "
        "GROUP BY i.advanced_technique, i.equipment_id, i.mapping_id"
    ),
    # preferred_equipment_id aponta para gym_equipment; o equipamento global e ge.equipment_id
    "workout_plan_exercises": (
        "SELECT e.advanced_technique AS technique, ge.equipment_id AS equipment_id, e.mapping_id AS exercise_id, "
        "COUNT(*) AS n FROM workout_plan_exercises e LEFT JOIN gym_equipment ge ON ge.id = e.preferred_equipment_id "
        "WHERE e.advanced_technique IS NOT NULL GROUP BY e.advanced_technique, ge.equipment_id, e.mapping_id"
    ),
}


def _int(value: Any) -> int | None:
    return None if value is None else int(value)


def _schema_state(cur: Any) -> dict[str, bool]:
    cur.execute(
        "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME IN (%s, %s, %s, %s, %s)",
        (
            "equipment",
            "equipment_exercises",
            "equipment_technique_compatibility",
            "equipment_exercise_technique_overrides",
            "advanced_techniques",
        ),
    )
    found = {(row["TABLE_NAME"], row["COLUMN_NAME"]) for row in cur.fetchall() or []}
    tables = {table for table, _ in found}
    return {
        "equipment_columns": ("equipment", "max_safe_rpe") in found,
        "pair_columns": ("equipment_exercises", "max_safe_rpe_override") in found,
        "compatibility_table": "equipment_technique_compatibility" in tables,
        "overrides_table": "equipment_exercise_technique_overrides" in tables,
        "techniques_catalog": "advanced_techniques" in tables,
    }


def _marks(cur: Any, table: str, owner_column: str) -> dict[int, dict[str, int]]:
    cur.execute(f"SELECT {owner_column} AS owner_id, technique_code, is_compatible FROM {table}")
    result: dict[int, dict[str, int]] = {}
    for row in cur.fetchall() or []:
        result.setdefault(int(row["owner_id"]), {})[row["technique_code"]] = int(row["is_compatible"] or 0)
    return result


def build_report(conn: Any) -> dict[str, Any]:
    with conn.cursor() as cur:
        schema = _schema_state(cur)

        rpe_column = "max_safe_rpe" if schema["equipment_columns"] else "NULL"
        cur.execute(f"SELECT id, name, equipment_type_id, is_active, {rpe_column} AS max_safe_rpe FROM equipment ORDER BY name")
        equipment = {int(row["id"]): row for row in cur.fetchall() or []}

        override_column = "max_safe_rpe_override" if schema["pair_columns"] else "NULL"
        cur.execute(f"SELECT id, equipment_id, exercise_id, {override_column} AS max_safe_rpe_override FROM equipment_exercises")
        links = {(int(row["equipment_id"]), int(row["exercise_id"])): row for row in cur.fetchall() or []}

        equipment_marks = _marks(cur, "equipment_technique_compatibility", "equipment_id") if schema["compatibility_table"] else {}
        override_marks = (
            _marks(cur, "equipment_exercise_technique_overrides", "equipment_exercise_id") if schema["overrides_table"] else {}
        )
        requirements: dict[str, str] = {}
        if schema["techniques_catalog"]:
            cur.execute("SELECT code, failure_requirement FROM advanced_techniques WHERE is_active = 1")
            requirements = {row["code"]: row["failure_requirement"] for row in cur.fetchall() or []}

        usages = []
        for source, sql in USAGE_QUERIES.items():
            cur.execute(sql)
            usages += [(source, row) for row in cur.fetchall() or []]

    without_rpe = [
        {
            "equipment_id": equipment_id,
            "name": row["name"],
            "equipment_type_id": _int(row["equipment_type_id"]),
            "is_active": bool(int(row["is_active"] or 0)),
        }
        for equipment_id, row in equipment.items()
        if ts.normalize_max_safe_rpe(row["max_safe_rpe"]) is None
    ]

    pending = []
    ok_rows = 0
    for source, row in usages:
        technique, n = row["technique"], int(row["n"])
        equipment_id, exercise_id = _int(row["equipment_id"]), _int(row["exercise_id"])
        entry = {
            "source": source,
            "technique": technique,
            "equipment_id": equipment_id,
            "equipment_name": equipment[equipment_id]["name"] if equipment_id in equipment else None,
            "exercise_id": exercise_id,
            "rows": n,
        }
        if equipment_id is None or equipment_id not in equipment:
            pending.append({**entry, "marking": UNMARKED, "reason": "NO_EQUIPMENT"})
            continue
        link = links.get((equipment_id, exercise_id)) if exercise_id is not None else None
        link_marks = override_marks.get(int(link["id"]), {}) if link else {}
        default_marks = equipment_marks.get(equipment_id, {})
        safety = ts.build_training_safety(
            exercise_id=exercise_id,
            equipment_id=equipment_id,
            equipment_exercise_id=int(link["id"]) if link else None,
            equipment_max_safe_rpe=equipment[equipment_id]["max_safe_rpe"],
            override_max_safe_rpe=link["max_safe_rpe_override"] if link else None,
            equipment_techniques=default_marks,
            override_techniques=link_marks,
            technique_failure_requirements=requirements,
        )
        validation = ts.validate_technique(technique, safety)
        if validation.valid:
            ok_rows += n
            continue
        mark = link_marks.get(technique, default_marks.get(technique))
        pending.append(
            {
                **entry,
                "equipment_exercise_id": int(link["id"]) if link else None,
                "max_safe_rpe": safety.max_safe_rpe,
                "marking": UNMARKED if mark is None else (MARKED_COMPATIBLE if mark else MARKED_INCOMPATIBLE),
                "reason": validation.reason,
            }
        )

    pending.sort(key=lambda item: (item["source"], item["equipment_name"] or "", item["exercise_id"] or 0, item["technique"]))
    return {
        "schema_pending": not all(schema.values()),
        "schema": schema,
        "equipment_total": len(equipment),
        "equipment_without_max_safe_rpe": without_rpe,
        "equipment_without_max_safe_rpe_active": sum(1 for item in without_rpe if item["is_active"]),
        "technique_usages_ok_rows": ok_rows,
        "technique_usages_pending": pending,
        "technique_usages_pending_rows": sum(item["rows"] for item in pending),
    }


def main() -> int:
    import sys

    from .db import get_conn

    if hasattr(sys.stdout, "reconfigure"):  # redirecionamento no Windows usa cp1252
        sys.stdout.reconfigure(encoding="utf-8")

    conn = get_conn()
    try:
        report = build_report(conn)
    finally:
        conn.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
