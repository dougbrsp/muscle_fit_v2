from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Mapping, Sequence

from ..set_types import sql_is_working, sql_set_confirmed
from .base import IntelligenceServiceBase
from .dashboard.metrics_registry import EXERCISE_PROGRESSION_STATUS_LABELS, ExerciseProgressionStatus

_ES_IS_WORKING = sql_is_working("es.set_type")

logger = logging.getLogger("fitness.intelligence")

# ============================================================================
# Leituras historicas de progressao para o dashboard e para a "Analise por IA"
# de `/dashboard/treinos`.
#
# Decisao de arquivo: o motor DETERMINISTICO de DECISAO (proxima carga) e o
# Motor de Progressao v2, `app/domain/progression_engine.py`, consumido pela
# geracao via `app/generation/deterministic_prescription.py` (o antigo
# `app/progression_service.py::suggest_load_and_reps` foi apagado no M76 Lote
# 1a); este modulo e LEITURA/ANALISE do historico ja registrado.
#
# 2026-10-06 (M74): `classify_progression_trend` (e1RM contra a MEDIA das
# sessoes, sem chave de equipamento) foi REMOVIDA. Progresso por exercicio
# tem uma fonte unica: `intelligence/dashboard/comparability.py`
# (`classify_exercise_progression`), alimentada por `exposure_service`. O
# contexto da IA (`exercise_progression_context`) entrega esse status pronto
# para a IA nao inferir progresso por conta propria.
# ============================================================================

# Teto de exercicios no contexto da IA (os de exposicao mais recente): o
# prompt nao precisa de 60 exercicios para comentar destaques.
MAX_AI_PROGRESSION_EXERCISES = 30

# Recordes recentes do payload `training` (M76 Lote 3b): janela e teto da
# lista - a mesma janela de 60 dias dos antigos "destaques".
RECENT_RECORDS_DAYS = 60
RECENT_RECORDS_LIMIT = 8

_STATUS_ORDER = {
    ExerciseProgressionStatus.PROGRESSING.value: 0,
    ExerciseProgressionStatus.REGRESSING.value: 1,
    ExerciseProgressionStatus.PLATEAU_SUSPECTED.value: 2,
    ExerciseProgressionStatus.STABLE.value: 3,
    ExerciseProgressionStatus.NOT_COMPARABLE.value: 4,
    ExerciseProgressionStatus.INSUFFICIENT_DATA.value: 5,
}


def exercise_progression_context(
    batch_items: Sequence[Mapping[str, Any]],
    equipment_names: Mapping[int, str] | None = None,
    *,
    limit: int = MAX_AI_PROGRESSION_EXERCISES,
) -> list[dict[str, Any]]:
    """Itens do lote de progressao (`exposure_service.classify_batch`) ->
    contexto por exercicio para a IA e para a tela: o STATUS da regra unica
    (`comparability`), as razoes, em que equipamento/convencao a comparacao
    foi feita e os numeros da ultima exposicao e da anterior COMPARAVEL
    (carga de topo, reps das series de topo, e1RM e se ele e confiavel), mais
    as reps emparelhadas que a regra comparou.

    Funcao PURA (sem banco). Ordem: exposicao mais recente primeiro; corta em
    `limit`."""
    from .dashboard.exposure_service import exposure_snapshot, paired_top_reps

    names = equipment_names or {}
    ordered = sorted(
        batch_items,
        key=lambda item: (str(item.get("last_local_date") or ""), -_STATUS_ORDER.get(item.get("status"), 9)),
        reverse=True,
    )
    out: list[dict[str, Any]] = []
    for item in ordered[: max(0, int(limit))]:
        exposures = list(item.get("_exposures") or [])
        comparable = int(item.get("comparable_exposures") or 0)
        # Run comparavel do status (mesmo contexto de fadiga), nao as ultimas N.
        run = list(item.get("_comparable_run") or [])
        latest = exposures[-1] if exposures else None
        previous = run[-2] if len(run) >= 2 else None
        key = (latest or {}).get("comparability_key") or {}
        equipment_id = key.get("equipment_id")
        status = item.get("status")
        try:
            label = EXERCISE_PROGRESSION_STATUS_LABELS[ExerciseProgressionStatus(status)]
        except (ValueError, KeyError):
            label = None
        out.append(
            {
                "exercise_id": item.get("exercise_id"),
                "exercise_name": item.get("name"),
                "status": status,
                "status_label": label,
                "status_reasons": list(item.get("status_reasons") or []),
                # Contexto de fadiga em que o status vale (rotulo do dono).
                "fatigue_context_label": (item.get("fatigue_context") or {}).get("band_label"),
                "comparable_exposures": comparable,
                "exposures_in_window": int(item.get("exposures_in_window") or 0),
                "equipment_id": equipment_id,
                "equipment_name": names.get(int(equipment_id)) if equipment_id is not None else None,
                "load_convention": key.get("load_convention"),
                "latest": exposure_snapshot(latest),
                "previous_comparable": exposure_snapshot(previous),
                "paired_top_reps": paired_top_reps(previous, latest) if previous and latest else None,
            }
        )
    return out


class ProgressionService(IntelligenceServiceBase):
    def get_exercise_progression(self, days: int = 90, user_id: int | None = None) -> list[dict[str, Any]]:
        """Status de progressao por exercicio (regra unica, `comparability`)
        dos ultimos `days` dias, no formato de `exercise_progression_context`.

        UMA consulta em lote (`exposure_service.get_exercises_progression_batch`)
        + UMA leitura dos nomes de equipamento. Sem `user_id` devolve `[]`
        (a regra exige dono - `require_user_id`). Falha de leitura nunca
        derruba o payload: devolve `[]` e registra no log."""
        if user_id is None:
            return []
        from ..db import get_conn
        from .dashboard.exposure_service import get_exercises_progression_batch
        from .dashboard.time_windows import MAX_WINDOW_DAYS, resolve_window, user_timezone, user_today

        span = max(1, min(int(days or 90), MAX_WINDOW_DAYS))
        try:
            conn = get_conn()
            try:
                timezone_name = user_timezone(conn, user_id)
                today = user_today(timezone_name)
                window = resolve_window(
                    "custom",
                    today=today,
                    date_from=today - timedelta(days=span - 1),
                    date_to=today,
                    timezone_name=timezone_name,
                )
                items, _truncated, _limit = get_exercises_progression_batch(conn, user_id=user_id, window=window)
                names = _equipment_names(conn, items)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - contexto opcional nunca derruba o payload
            logger.exception("Falha ao montar o status de progressao por exercicio")
            return []
        return exercise_progression_context(items, names)

    def get_recent_records(
        self, days: int = RECENT_RECORDS_DAYS, limit: int = RECENT_RECORDS_LIMIT, user_id: int | None = None
    ) -> list[dict[str, Any]]:
        """Recordes de carga dos ultimos `days` dias, pela regra UNICA
        (`session_records.record_events`, decisao 3 do M76): carga direcional
        estritamente maior que todas as exposicoes anteriores da MESMA chave
        exercicio + equipamento + convencao; a primeira exposicao nao e
        recorde; sem equipamento resolvido nao ha recorde. Um item por chave
        (o recorde mais recente dela), do mais recente para o mais antigo.

        Substitui `get_recent_progression` (M76 Lote 3b), que listava
        `MAX(best_weight_kg)` por NOME de exercicio misturando equipamentos.

        Quatro consultas, nenhuma por exercicio: exercicios da janela,
        exposicoes (historico inteiro desses exercicios, uma linha cada), series
        das exposicoes recorde (reps so informativas) e nomes. Sem `user_id`
        devolve `[]`; falha de leitura nunca derruba o payload."""
        if user_id is None:
            return []
        from ..db import get_conn
        from ..load_conventions import load_convention_select_expr
        from .dashboard import session_records as sr
        from .dashboard.time_windows import user_timezone, user_today

        try:
            conn = get_conn()
            try:
                today = user_today(user_timezone(conn, user_id))
                start = today - timedelta(days=max(1, int(days)))
                with conn.cursor() as cur:
                    cur.execute(_WINDOW_EXERCISES_SQL, (user_id, start, today))
                    exercise_ids = sorted({int(row["exercise_id"]) for row in cur.fetchall() or [] if row.get("exercise_id") is not None})
                    if not exercise_ids:
                        return []
                    placeholders = ",".join(["%s"] * len(exercise_ids))
                    cur.execute(
                        sr.history_sql(load_convention_select_expr(cur)).format(placeholders=placeholders),
                        (user_id, today, *exercise_ids),
                    )
                    history = sr.parse_history(cur.fetchall() or [])
                    events = select_recent_records(sr.record_events(history), start=start, limit=limit)
                    if not events:
                        return []
                    row_ids = [event.exposure.exercise_row_id for event in events]
                    cur.execute(
                        sr.SETS_SQL.format(placeholders=",".join(["%s"] * len(row_ids))), (user_id, *row_ids)
                    )
                    set_rows = cur.fetchall() or []
                    names = _catalog_names(cur, {event.exposure.exercise_id for event in events})
                    equipment = _equipment_names_by_id(
                        cur, {event.exposure.key.equipment_id for event in events if event.exposure.key.equipment_id is not None}
                    )
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - contexto opcional nunca derruba o payload
            logger.exception("Falha ao montar os recordes recentes")
            return []
        return [record_highlight(event, set_rows, names, equipment) for event in events]

    def get_recent_sessions(self, days: int = 30, user_id: int | None = None) -> list[dict[str, object]]:
        """Sessoes recentes com a composicao COMPLETA de exercicios (resumo
        por exercicio: sets/peso medio/reps medias/RPE medio), usado pela IA
        de `/treinos` para avaliar se a progressao esta bem distribuida entre
        exercicios/sessoes - diferente de `get_recent_records`, que lista so
        os recordes por chave ao longo de 60 dias. Query unica com JOIN (sem N+1 por
        activity_id); agrupamento em sessoes e feito em Python por
        `_group_rows_into_sessions`, testavel sem banco.

        `user_id` opcional (None preserva o comportamento historico sem
        filtro, ver ressalva em VolumeService._fetch_window_sets)."""
        user_filter = "AND a.user_id = %s" if user_id is not None else ""
        params: list[Any] = [self._days_ago(user_id, days)]
        if user_id is not None:
            params.append(user_id)
        rows = self.fetch_all(
            f"""
            SELECT
              a.id AS activity_id,
              a.local_date,
              a.activity_type,
              COALESCE(zm.name, e.original_name) AS exercise_name,
              pg.name AS muscle_primary,
              sg.name AS muscle_secondary,
              COUNT(es.id) AS sets_count,
              ROUND(AVG(es.weight_kg), 2) AS avg_weight_kg,
              ROUND(AVG(es.reps), 2) AS avg_reps,
              ROUND(AVG(es.rpe), 2) AS avg_rpe
            FROM activities a
            JOIN exercises e ON e.activity_id = a.id
            -- Somente series `working` (mesma regra do e1RM de
            -- analytics._build_progression_sessions): aquecimento/ajuste e
            -- aplicacoes de tecnica nao entram em carga/reps/RPE medios.
            LEFT JOIN exercise_sets es ON es.exercise_id = e.id AND {_ES_IS_WORKING} AND {sql_set_confirmed('es.reps_pending')}
            LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
            LEFT JOIN exercise_catalog zm ON zm.id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
            LEFT JOIN muscle_groups pg ON pg.id = (
              SELECT emc_pri.muscle_group_id
              FROM exercise_muscle_contributions emc_pri
              WHERE emc_pri.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                AND emc_pri.role = 'PRIMARY' AND emc_pri.is_active = 1
              ORDER BY emc_pri.id ASC LIMIT 1
            )
            LEFT JOIN muscle_groups sg ON sg.id = (
              SELECT emc_sec.muscle_group_id
              FROM exercise_muscle_contributions emc_sec
              WHERE emc_sec.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                AND emc_sec.role = 'SECONDARY' AND emc_sec.is_active = 1
              ORDER BY emc_sec.id ASC LIMIT 1
            )
            WHERE a.local_date >= %s
              AND (
                LOWER(a.activity_type) LIKE '%%muscul%%'
                OR LOWER(a.activity_type) LIKE '%%força%%'
                OR LOWER(a.activity_type) LIKE '%%strength%%'
              )
              {user_filter}
            GROUP BY a.id, a.local_date, a.activity_type, e.id,
                     COALESCE(zm.name, e.original_name), pg.name, sg.name
            ORDER BY a.local_date DESC, a.id DESC, e.sequence ASC
            """,
            tuple(params),
        )
        return self._group_rows_into_sessions(rows)

    @staticmethod
    def _group_rows_into_sessions(rows: list[dict[str, object]]) -> list[dict[str, object]]:
        """Agrupa linhas flat (uma por exercicio dentro de uma sessao) em
        `[{date, activity_type, exercises: [...]}]`, preservando a ordem de
        chegada das linhas (ja ordenadas por sessao mais recente e por
        `e.sequence` dentro da sessao)."""
        sessions_by_activity: dict[object, dict[str, object]] = {}
        order: list[object] = []

        for row in rows:
            row = dict(row)
            activity_id = row.pop("activity_id")
            local_date = row.pop("local_date")
            activity_type = row.pop("activity_type")

            exercise = {
                "exercise_name": row.pop("exercise_name"),
                "muscle_primary": row.pop("muscle_primary"),
                "muscle_secondary": row.pop("muscle_secondary"),
                "sets_count": row.pop("sets_count"),
                "avg_weight_kg": row.pop("avg_weight_kg"),
                "avg_reps": row.pop("avg_reps"),
                "avg_rpe": row.pop("avg_rpe"),
            }

            if activity_id not in sessions_by_activity:
                sessions_by_activity[activity_id] = {
                    "date": local_date,
                    "activity_type": activity_type,
                    "exercises": [],
                }
                order.append(activity_id)

            sessions_by_activity[activity_id]["exercises"].append(exercise)

        return [sessions_by_activity[activity_id] for activity_id in order]


def _equipment_names(conn: Any, items: Sequence[Mapping[str, Any]]) -> dict[int, str]:
    """Nome do equipamento canonico de cada chave corrente do lote (o lote
    nao traz o nome). UMA consulta com `IN`; vazio sem equipamento."""
    ids: set[int] = set()
    for item in items:
        exposures = item.get("_exposures") or []
        if exposures:
            equipment_id = (exposures[-1].get("comparability_key") or {}).get("equipment_id")
            if equipment_id is not None:
                ids.add(int(equipment_id))
    if not ids:
        return {}
    ordered = sorted(ids)
    placeholders = ", ".join(["%s"] * len(ordered))
    with conn.cursor() as cursor:
        cursor.execute(f"SELECT id, name FROM equipment WHERE id IN ({placeholders})", tuple(ordered))
        rows = cursor.fetchall() or []
    return {int(row["id"]): row["name"] for row in rows if row.get("id") is not None}


# ---------------------------------------------------------------------------
# Recordes recentes (M76 Lote 3b) - consumidores finos de `session_records`
# ---------------------------------------------------------------------------

# Exercicios canonicos com serie de trabalho na janela do usuario.
_WINDOW_EXERCISES_SQL = f"""
SELECT DISTINCT COALESCE(elm.canonical_exercise_id, e.mapping_id) AS exercise_id
FROM activities a
JOIN exercises e ON e.activity_id = a.id
JOIN exercise_sets es ON es.exercise_id = e.id AND {sql_set_confirmed('es.reps_pending')}
LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
WHERE a.user_id = %s
  AND a.local_date BETWEEN %s AND %s
  AND {_ES_IS_WORKING}
"""


def select_recent_records(events: Sequence[Any], *, start: Any, limit: int) -> list[Any]:
    """Recordes (`session_records.RecordEvent`) com data a partir de `start`:
    o mais recente de cada chave, do mais recente para o mais antigo, ate
    `limit`. Funcao PURA."""
    latest: dict[Any, Any] = {}
    for event in events:
        local_date = event.exposure.row.get("local_date")
        if local_date is None or str(local_date)[:10] < str(start)[:10]:
            continue
        latest[event.exposure.key] = event  # `events` vem em ordem cronologica
    ordered = sorted(
        latest.values(),
        key=lambda event: (str(event.exposure.row.get("local_date"))[:10], event.exposure.exercise_row_id),
        reverse=True,
    )
    return ordered[: max(0, int(limit))]


def record_highlight(
    event: Any,
    set_rows: Sequence[Mapping[str, Any]],
    exercise_names: Mapping[int, str],
    equipment_names: Mapping[int, str],
) -> dict[str, Any]:
    """Item de `recent_records` (tela de snapshot da IA e contexto do
    relatorio). Funcao PURA."""
    from .dashboard.session_records import record_set_reps

    key = event.exposure.key
    load = event.load_kg
    previous = event.previous_load_kg
    return {
        "exercise_id": event.exposure.exercise_id,
        "exercise_name": exercise_names.get(event.exposure.exercise_id),
        "equipment_id": key.equipment_id,
        "equipment_name": equipment_names.get(key.equipment_id) if key.equipment_id is not None else None,
        "load_convention": key.load_convention.value,
        "local_date": str(event.exposure.row.get("local_date"))[:10],
        "record_weight_kg": None if load is None else round(float(load), 2),
        "previous_best_weight_kg": None if previous is None else round(float(previous), 2),
        "reps": record_set_reps(event, set_rows),
    }


def _catalog_names(cursor: Any, exercise_ids: set[int]) -> dict[int, str]:
    if not exercise_ids:
        return {}
    ordered = sorted(exercise_ids)
    cursor.execute(
        f"SELECT id, name FROM exercise_catalog WHERE id IN ({', '.join(['%s'] * len(ordered))})", tuple(ordered)
    )
    return {int(row["id"]): row["name"] for row in cursor.fetchall() or [] if row.get("id") is not None}


def _equipment_names_by_id(cursor: Any, equipment_ids: set[int]) -> dict[int, str]:
    if not equipment_ids:
        return {}
    ordered = sorted(int(item) for item in equipment_ids)
    cursor.execute(f"SELECT id, name FROM equipment WHERE id IN ({', '.join(['%s'] * len(ordered))})", tuple(ordered))
    return {int(row["id"]): row["name"] for row in cursor.fetchall() or [] if row.get("id") is not None}
