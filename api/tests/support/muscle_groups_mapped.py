"""Cenarios compartilhados pelos testes da classificacao CANONICA de grupos
musculares (`app/muscle_groups_mapped.py`, rotas
`/analytics/muscle-groups-mapped-*`), usada pelas telas Grupos musculares,
Dashboard e Insights - nos dois backends (PyMySQL e shim RDS Data API).

Classificacao: exercises.mapping_id -> exercise_legacy_id_map ->
exercise_catalog -> exercise_muscle_contributions (PRIMARY/SECONDARY).
"""

from __future__ import annotations

import datetime as _dt
import os
from contextlib import contextmanager
from typing import Any, Iterator

from tests.support.dataapi_backend import raw_mysql

SEED_TABLES = (
    "exercise_sets",
    "exercises",
    "activities",
    "exercise_external_mappings",
    "exercise_legacy_id_map",
    "exercise_muscle_contributions",
    "exercise_aliases",
    "exercise_catalog",
    "exercise_zepp_mappings",
    "muscle_groups",
    "users",
)

@contextmanager
def raw_connection(database: str) -> Iterator[Any]:
    """Conexao neutra (autocommit) ao MySQL de teste, para semear cenarios."""
    with raw_mysql(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=database,
    ) as conn:
        yield conn


PULLDOWN_ID = 3001  # Costas (PRIMARY) + Biceps (SECONDARY), nome AMAZFIT "Lat Pulldown"
SUPINO_ID = 3002  # Peitoral (PRIMARY) + Triceps (SECONDARY), sem nome externo
NO_GROUP_ID = 3003  # no catalogo, sem contribuicao muscular -> nao mapeado
LEGACY_ID = 9003  # id antigo consolidado em PULLDOWN_ID
UNMAPPED_NAME = "Rosca livre XYZ"  # sem mapping_id e sem casar com o catalogo


def _reset(cur) -> None:
    for table in SEED_TABLES:
        cur.execute(f"TRUNCATE TABLE `{table}`")


def _catalog_exercise(cur, exercise_id: int, name: str, *, in_catalog: bool = True) -> None:
    cur.execute(
        "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, %s, %s)",
        (exercise_id, name, name),
    )
    if in_catalog:
        cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, %s)", (exercise_id, name))


def _contribution(cur, exercise_id: int, group_id: int, role: str, weight: float) -> None:
    cur.execute(
        "INSERT INTO exercise_muscle_contributions (exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight) "
        "VALUES (%s, %s, %s, %s, %s)",
        (exercise_id, group_id, role, weight, weight),
    )


def seed_canonical_history(conn) -> dict[str, Any]:
    """Semeia por conexao neutra (autocommit). Datas relativas a hoje, com
    folga de dias para nao depender do fuso do servidor (CURDATE())."""
    today = _dt.date.today()
    ids: dict[str, Any] = {"date_a": today - _dt.timedelta(days=5), "date_b": today - _dt.timedelta(days=2)}
    with conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        try:
            _reset(cur)
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('atleta-canon', 'x', 'regular')")
            ids["user"] = cur.lastrowid
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('outro-canon', 'x', 'regular')")
            ids["other_user"] = cur.lastrowid

            groups = {}
            for name in ("Costas", "Peitoral", "Bíceps", "Tríceps"):
                cur.execute("INSERT INTO muscle_groups (name) VALUES (%s)", (name,))
                groups[name] = cur.lastrowid

            _catalog_exercise(cur, PULLDOWN_ID, "Puxada alta")
            _catalog_exercise(cur, SUPINO_ID, "Supino reto")
            _catalog_exercise(cur, NO_GROUP_ID, "Alongamento")
            _catalog_exercise(cur, LEGACY_ID, "Voador antigo", in_catalog=False)
            cur.execute(
                "INSERT INTO exercise_legacy_id_map (old_exercise_id, canonical_exercise_id) VALUES (%s, %s)",
                (LEGACY_ID, PULLDOWN_ID),
            )
            _contribution(cur, PULLDOWN_ID, groups["Costas"], "PRIMARY", 1.0)
            _contribution(cur, PULLDOWN_ID, groups["Bíceps"], "SECONDARY", 0.5)
            _contribution(cur, SUPINO_ID, groups["Peitoral"], "PRIMARY", 1.0)
            _contribution(cur, SUPINO_ID, groups["Tríceps"], "SECONDARY", 0.5)
            # Nome exibido = 1o mapeamento AMAZFIT ATIVO (menor id).
            for external_name, is_active in (("Chest Press", 0), ("Lat Pulldown", 1), ("Chest Fly", 1)):
                cur.execute(
                    "INSERT INTO exercise_external_mappings (exercise_id, provider, external_name, is_active) "
                    "VALUES (%s, 'AMAZFIT', %s, %s)",
                    (PULLDOWN_ID, external_name, is_active),
                )

            def activity(user_id, local_date, name):
                cur.execute(
                    "INSERT INTO activities (user_id, local_date, name, activity_type) VALUES (%s, %s, %s, 'Musculacao')",
                    (user_id, local_date, name),
                )
                return cur.lastrowid

            def exercise(activity_id, sequence, original_name, mapping_id, sets):
                cur.execute(
                    "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (activity_id, sequence, original_name, mapping_id, len(sets)),
                )
                exercise_id = cur.lastrowid
                for set_number, (reps, weight, rpe, set_type) in enumerate(sets, 1):
                    cur.execute(
                        "INSERT INTO exercise_sets (exercise_id, set_number, reps, weight_kg, rpe, set_type) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (exercise_id, set_number, reps, weight, rpe, set_type),
                    )
                return exercise_id

            ids["activity_a"] = a = activity(ids["user"], ids["date_a"], "Treino A")
            exercise(a, 1, "Supino maquina", PULLDOWN_ID, [
                (15, "20.00", None, "warmup"),  # aquecimento: fora de tudo
                (10, "40.00", "7.50", "working"),
                (8, "42.50", "8.50", "working"),
            ])
            # Mesmo exercicio canonico/nome exibido no mesmo dia: vira UMA linha
            # agregada com a anterior. Serie sem carga (peso corporal).
            exercise(a, 2, "Barra fixa", PULLDOWN_ID, [(12, "0.00", "9.00", "working")])
            exercise(a, 3, "Supino reto", SUPINO_ID, [
                (10, "60.00", "8.00", "working"),
                (9, "60.00", "8.50", "working"),
                (8, "60.00", None, "working"),
            ])
            exercise(a, 4, UNMAPPED_NAME, None, [(10, "12.00", "8.00", "working"), (10, "12.00", None, "working")])
            exercise(a, 5, "Alongamento", NO_GROUP_ID, [(10, "0.00", None, "working")])

            b = activity(ids["user"], ids["date_b"], "Treino B")
            exercise(b, 1, "Voador antigo", LEGACY_ID, [(10, "35.50", "9.00", "working")])
            exercise(b, 2, "Supino reto", SUPINO_ID, [])  # sem series: nao conta

            # Fora do filtro: outro usuario; e fora da janela de 30 dias.
            other = activity(ids["other_user"], today - _dt.timedelta(days=1), "Treino alheio")
            exercise(other, 1, "Supino reto", SUPINO_ID, [(5, "100.00", "10.00", "working")])
            old = activity(ids["user"], today - _dt.timedelta(days=60), "Treino antigo")
            exercise(old, 1, "Supino reto", SUPINO_ID, [(5, "50.00", "7.00", "working")])
        finally:
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    return ids


def table_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Recorte estavel do agregado (independe da data de hoje)."""
    return {
        "total_sets": result["total_sets"],
        "mapped_sets": result["mapped_sets"],
        "unmapped_sets": result["unmapped_sets"],
        "mapped_ratio": result["mapped_ratio"],
        "unmapped": result["unmapped"],
        "table": [
            {
                key: row[key]
                for key in ("group", "sets", "value", "volume_kg", "avg_rpe", "rpe_sets", "exercises")
            }
            for row in result["table_rows"]
        ],
    }


def expected_summary(days: int, include_secondary: bool) -> dict[str, Any]:
    """Esperado para `metric=volume`. Os pesos sao os `stimulus_weight` do seed
    (PRIMARY 1,0; SECONDARY 0,5). Series sem contribuicao cadastrada (o
    exercicio sem vinculo e o "Alongamento") contam em "Outros" - a politica
    unica de `VolumeService` (M76 Lote 3c) - e seguem listadas em `unmapped`."""
    costas = {"group": "Costas", "sets": 4, "value": 1107.0, "volume_kg": 1095.0, "avg_rpe": 8.5, "rpe_sets": 4, "exercises": ["Lat Pulldown"]}
    peitoral = {"group": "Peitoral", "sets": 3, "value": 1620.0, "volume_kg": 1620.0, "avg_rpe": 8.25, "rpe_sets": 2, "exercises": ["Supino reto"]}
    triceps = {"group": "Tríceps", "sets": 3, "value": 810.0, "volume_kg": 810.0, "avg_rpe": 8.25, "rpe_sets": 2, "exercises": ["Supino reto"]}
    biceps = {"group": "Bíceps", "sets": 4, "value": 553.5, "volume_kg": 547.5, "avg_rpe": 8.5, "rpe_sets": 4, "exercises": ["Lat Pulldown"]}
    outros = {"group": "Outros", "sets": 3, "value": 250.0, "volume_kg": 240.0, "avg_rpe": 8.0, "rpe_sets": 1, "exercises": ["Alongamento", UNMAPPED_NAME]}
    total_sets = 10
    if days >= 60:
        # + serie antiga de supino (5 x 50 kg, RPE 7).
        peitoral = {**peitoral, "sets": 4, "value": 1870.0, "volume_kg": 1870.0, "avg_rpe": 7.83, "rpe_sets": 3}
        triceps = {**triceps, "sets": 4, "value": 935.0, "volume_kg": 935.0, "avg_rpe": 7.83, "rpe_sets": 3}
        total_sets = 11
    table = [peitoral, costas, outros]
    if include_secondary:
        table = [peitoral, costas, triceps, biceps, outros]
    mapped_sets = total_sets - 3
    return {
        "total_sets": total_sets,
        "mapped_sets": mapped_sets,
        "unmapped_sets": 3,
        "mapped_ratio": round(mapped_sets / total_sets, 4),
        "unmapped": [{"exercise": UNMAPPED_NAME, "sets": 2}, {"exercise": "Alongamento", "sets": 1}],
        "table": table,
    }


HEAVY_NAME_LENGTH = 120


def seed_heavy_history(conn, *, days: int = 365, exercises_per_day: int = 12, sets_per_exercise: int = 3) -> dict[str, Any]:
    """Volume alto por 1 ano (um treino por dia, nomes longos) para exercitar
    o limite de 1 MiB do resultado da RDS Data API."""
    today = _dt.date.today()
    with conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        try:
            _reset(cur)
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('atleta-volume', 'x', 'regular')")
            user_id = cur.lastrowid
            group_ids = []
            for name in ("Costas", "Peitoral", "Bíceps", "Tríceps", "Quadríceps", "Ombros"):
                cur.execute("INSERT INTO muscle_groups (name) VALUES (%s)", (name,))
                group_ids.append(cur.lastrowid)
            exercise_ids = []
            for index in range(exercises_per_day):
                exercise_id = 5000 + index
                name = f"Exercicio canonico {index:02d} " + "x" * HEAVY_NAME_LENGTH
                _catalog_exercise(cur, exercise_id, name[:HEAVY_NAME_LENGTH])
                _contribution(cur, exercise_id, group_ids[index % len(group_ids)], "PRIMARY", 1.0)
                _contribution(cur, exercise_id, group_ids[(index + 1) % len(group_ids)], "SECONDARY", 0.5)
                exercise_ids.append((exercise_id, name[:HEAVY_NAME_LENGTH]))

            set_rows = []
            for offset in range(days):
                cur.execute(
                    "INSERT INTO activities (user_id, local_date, name, activity_type) VALUES (%s, %s, 'Treino', 'Musculacao')",
                    (user_id, today - _dt.timedelta(days=offset)),
                )
                activity_id = cur.lastrowid
                for sequence, (exercise_id, name) in enumerate(exercise_ids, 1):
                    cur.execute(
                        "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (activity_id, sequence, name, exercise_id, sets_per_exercise),
                    )
                    row_id = cur.lastrowid
                    for set_number in range(1, sets_per_exercise + 1):
                        set_rows.append((row_id, set_number, 10, "52.50", "8.50", "working"))
            cur.executemany(
                "INSERT INTO exercise_sets (exercise_id, set_number, reps, weight_kg, rpe, set_type) VALUES (%s, %s, %s, %s, %s, %s)",
                set_rows,
            )
        finally:
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    return {"user": user_id, "sets": len(set_rows)}
