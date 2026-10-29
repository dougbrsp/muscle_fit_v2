"""Copia LITERAL de `VolumeService.get_top_exercises_for_muscle_group` como
ela era ANTES do M19 (uma passada sobre as series da janela e DUAS consultas
POR GRUPO muscular).

Serve de ORACULO do teste de equivalencia do M19
(`tests/integration/dataapi/test_top_exercicios_por_grupo_performance.py`):
a correcao e exclusivamente de desempenho, entao o ranking devolvido pelo
caminho novo (`get_top_exercises_by_muscle_group`, uma passada e duas
consultas para TODOS os grupos) precisa ser identico campo a campo ao que
esta funcao produz, grupo a grupo. (O consumidor original, o alerta de
estagnacao de `/analytics/dashboard-alerts`, saiu no M76 Lote 2c; o ranking
segue vivo no drill-down muscular.)

Mesmo papel de `legacy_volume_queries.py` no PR do drill-down. Nada aqui e
importado pela aplicacao.
"""

from __future__ import annotations

from typing import Any


def top_exercises_for_muscle_group_legacy(
    service, muscle_group_id: int, days: int, user_id: int | None = None, limit: int = 5
) -> list[dict[str, Any]]:
    raw_sets = service._fetch_window_sets(days, user_id=user_id)
    mapping_ids_all = {row["mapping_id"] for row in raw_sets if row.get("mapping_id")}
    contributions_by_mapping = service._fetch_contributions_by_mapping(mapping_ids_all)

    totals: dict[Any, dict[str, Any]] = {}
    for row in raw_sets:
        contributions = service._resolve_contributions(
            row.get("mapping_id"),
            contributions_by_mapping,
            row.get("primary_group"),
            row.get("secondary_group"),
            row.get("primary_group_id"),
            row.get("secondary_group_id"),
            row.get("secondary_participation"),
        )
        for contrib in contributions:
            if contrib["muscle_group_id"] != muscle_group_id:
                continue
            key = row.get("mapping_id") or ("exercise", row["exercise_id"])
            bucket = totals.setdefault(
                key,
                {
                    "mapping_id": row.get("mapping_id"),
                    "sets_count": 0,
                    "stimulus_equivalent_sets": 0.0,
                    "last_trained_at": None,
                },
            )
            bucket["sets_count"] += 1
            bucket["stimulus_equivalent_sets"] += contrib["stimulus_weight"]
            trained_at = row.get("trained_at")
            if trained_at and (
                bucket["last_trained_at"] is None or str(trained_at) > str(bucket["last_trained_at"])
            ):
                bucket["last_trained_at"] = trained_at

    mapping_ids = {b["mapping_id"] for b in totals.values() if b.get("mapping_id")}
    names_by_mapping: dict[int, str] = {}
    if mapping_ids:
        placeholders = ",".join(["%s"] * len(mapping_ids))
        name_rows = service.fetch_all(
            f"""
            SELECT ec.id, COALESCE(ec.name, (
                SELECT exm.external_name
                FROM exercise_external_mappings exm
                WHERE exm.exercise_id = ec.id AND exm.provider = 'AMAZFIT' AND exm.is_active = 1
                ORDER BY exm.id ASC LIMIT 1
            ), 'Exercício sem nome') AS name
            FROM exercise_catalog ec
            WHERE ec.id IN ({placeholders})
            """,
            tuple(mapping_ids),
        )
        names_by_mapping = {row["id"]: row["name"] for row in name_rows}

    results: list[dict[str, Any]] = []
    for bucket in totals.values():
        mapping_id = bucket["mapping_id"]
        results.append(
            {
                "mapping_id": mapping_id,
                "exercise_name": names_by_mapping.get(mapping_id, "Exercício não mapeado")
                if mapping_id
                else "Exercício não mapeado",
                "sets_count": bucket["sets_count"],
                "stimulus_equivalent_sets": round(bucket["stimulus_equivalent_sets"], 2),
                "last_trained_at": bucket["last_trained_at"],
            }
        )
    results.sort(key=lambda r: r["stimulus_equivalent_sets"], reverse=True)
    return results[:limit]
