"""Literais SQL com acento (antes duplamente codificados - mojibake) nos dois
backends (PyMySQL e shim RDS Data API + dublê), sobre MySQL 8 com tabelas
`utf8mb4` / `utf8mb4_0900_ai_ci` (mesma collation das migrations/Aurora).

- Filtro de "atividade de musculacao" (`LIKE '%força%'`), usado por
  `analytics.compute_training_trend`/`compute_rolling_weekly_trend` e por
  `intelligence/{cardio_context,progression,volume}_service.py`: com o
  literal duplamente codificado, NENHUM `activity_type` com "força" casava.

Comportamento do MySQL 8 registrado aqui: `LIKE` segue a collation
(`_ai_ci` = sem acento e sem caixa, entao '%força%' casa 'Forca' tambem).
O `MUSCLE_GROUP_CASE` (REGEXP por nome) saiu no COD-05 (2026-10-09).
"""

from __future__ import annotations

import pytest

# Importados no topo: `use_dataapi` so troca o backend de modulos `app.*` ja importados.
from app import analytics  # noqa: F401
from app.intelligence import cardio_context_service
from app.intelligence.base import IntelligenceServiceBase

ACTIVITY_TYPES = {
    "Treino de Força": True,
    "FORÇA": True,
    "Forca (sem acento)": True,  # LIKE com _ai_ci ignora acento
    "musculacao": True,
    "Strength Training": True,
    "Corrida": False,
    "Caminhada": False,
}


@pytest.fixture
def activities(raw):
    with raw.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in ("exercise_sets", "exercises", "activities", "users"):
            cur.execute(f"TRUNCATE TABLE `{table}`")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('atleta-acento', 'x', 'regular')")
        user_id = cur.lastrowid
        for activity_type in ACTIVITY_TYPES:
            cur.execute(
                "INSERT INTO activities (user_id, local_date, name, activity_type) VALUES (%s, CURDATE(), %s, %s)",
                (user_id, activity_type, activity_type),
            )
        cur.execute(
            "SELECT TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'activities'"
        )
        collation = cur.fetchone()["TABLE_COLLATION"]
    return {"user_id": user_id, "collation": collation}


def test_tabela_activities_usa_utf8mb4_0900_ai_ci(activities):
    assert activities["collation"] == "utf8mb4_0900_ai_ci"


def test_filtro_de_forca_classifica_activity_type_com_acento(db_backend, activities):
    rows = IntelligenceServiceBase().fetch_all(
        f"""
        SELECT a.activity_type
        FROM activities a
        WHERE a.user_id = %s AND ({cardio_context_service._STRENGTH_ACTIVITY_FILTER})
        ORDER BY a.id
        """,
        (activities["user_id"],),
    )
    assert [row["activity_type"] for row in rows] == [name for name, strength in ACTIVITY_TYPES.items() if strength]
