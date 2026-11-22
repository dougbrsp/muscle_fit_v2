from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Annotated, Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query

from .db import get_conn
from .db_helpers import connect_or_503, DB_UNAVAILABLE_RDS, jsonable as _jsonable
from . import intervals_activity_kind
from .domain import effective_intensity
from .load_conventions import exercise_tonnage_joins_sql, sql_tonnage_excluded_sets, sql_tonnage_sum
from .models import MuscleActivationPreviewIn
from .observability import log_event
from .access import require_permission
from .security import Identity
from .domain.set_measures import sql_is_rep_based
from .set_types import sql_not_preparatory, sql_set_confirmed
from .training_goal_query import goal_query, optional_goal_param, optional_goal_query

logger = logging.getLogger(__name__)

# Aquecimento/serie de ajuste (`warmup`/`feeler`) nao entram em totais,
# volume, series nem RPE dos agregados abaixo. Aplicado no ON do LEFT JOIN
# para preservar atividades/exercicios sem nenhuma serie restante.
_ES_NOT_PREPARATORY = sql_not_preparatory("es.set_type")

router = APIRouter(prefix="/analytics", tags=["analytics"])

# M63 - `volume_kg` GLOBAL ciente da convencao de carga: series de par
# equipamento<->exercicio `assisted`/`bodyweight` ficam FORA de reps x kg (o
# numero gravado nao e a carga movida), pela MESMA regra da tonelagem do
# dashboard e do relatorio (M58, `load_conventions.sql_counts_in_tonnage`).
# Series, reps e RPE continuam contando TUDO. Os JOINs sao LEFT e por chave
# unica: nao duplicam serie nem somem com atividade sem exercicio.
_TONNAGE_JOINS = exercise_tonnage_joins_sql()


def _global_tonnage_columns() -> tuple[str, str]:
    """`(volume_kg, tonnage_excluded_sets)` em SQL para as agregacoes globais
    (`/summary`, `/training-trend`, serie semanal movel). A deteccao da
    coluna da 045 e cacheada por schema (`load_conventions`): em regime nao
    custa statement."""
    convention = _load_convention_expr()
    return sql_tonnage_sum(convention), sql_tonnage_excluded_sets(convention)


def _connect():
    return connect_or_503(get_conn, DB_UNAVAILABLE_RDS)


def _rows_jsonable(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{key: _jsonable(value) for key, value in row.items()} for row in rows]


def _fetch_all(sql: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
    with _connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, params or ())
            rows = cursor.fetchall()
            return _rows_jsonable(rows)


def _fetch_one(sql: str, params: Optional[tuple] = None) -> Dict[str, Any]:
    rows = _fetch_all(sql, params)
    return rows[0] if rows else {}


def _safe_days(days: int) -> int:
    if days < 1:
        return 1
    if days > 3650:
        return 3650
    return days


def _user_today(user_id: Optional[int]) -> date:
    """"Hoje" do usuario (`time_windows.user_today`, fuso de `users.timezone`),
    a mesma data das rotas v3 - e a ancora das janelas em dias deste modulo,
    passada ao SQL como parametro no lugar de `CURDATE()` (dia UTC do Aurora;
    M76 Lote 2b; antes so as rotas de progressao por grupo usavam, numa copia
    mais abaixo). Sem `user_id` (chamador legado sem identidade), o fuso
    default. Nunca falha: fuso ausente/invalido ou banco indisponivel caem no
    default de `normalize_timezone`."""
    from .intelligence.dashboard.time_windows import user_timezone, user_today
    from .timezones import DEFAULT_TIMEZONE

    if user_id is None:
        return user_today(DEFAULT_TIMEZONE)
    try:
        with _connect() as conn:
            timezone_name = user_timezone(conn, user_id)
    except Exception:  # noqa: BLE001 - mesmo contrato de `user_timezone`: o fuso nunca derruba a rota
        timezone_name = DEFAULT_TIMEZONE
    return user_today(timezone_name)


def _days_ago(user_id: Optional[int], days: int) -> date:
    """Primeiro dia de uma janela de `days` dias contada do "hoje" do usuario:
    a mesma conta de `DATE_SUB(CURDATE(), INTERVAL n DAY)`, no fuso certo."""
    return _user_today(user_id) - timedelta(days=int(days))


def _period_expr(period: str) -> str:
    if period == "day":
        return "DATE_FORMAT(a.local_date, '%%Y-%%m-%%d')"
    if period == "week":
        return "DATE_FORMAT(a.local_date, '%%x-W%%v')"
    if period == "month":
        return "DATE_FORMAT(a.local_date, '%%Y-%%m')"
    if period == "year":
        return "DATE_FORMAT(a.local_date, '%%Y')"

    raise HTTPException(
        status_code=400,
        detail="period inválido. Use: day, week, month ou year.",
    )


@router.get("/health")
def analytics_health(identity: Identity = Depends(require_permission("dashboards", "ver"))):
    db_status = _fetch_one("SELECT 1 AS ok")

    return {
        "status": "ok",
        "namespace": "/analytics",
        "database": bool(db_status.get("ok")),
        "compatibility": {
            "existing_fitness_api_endpoints": "unchanged",
            "openclaw_mcp": "unchanged",
        },
    }


# M98 C4: o que e cardio e do dono `intervals_activity_kind`, que classifica por
# tipo + nome em Python. Para o volume lido nao crescer com a janela (limite de
# 1 MB da Data API), o SQL ja agrega por (tipo, nome) - a classificacao e
# constante dentro de cada grupo - e so a CLASSE e decidida em Python.
_ACTIVITY_KIND_SQL = """
SELECT a.activity_type, a.name, COUNT(DISTINCT a.id) AS activities
FROM activities a
WHERE a.local_date >= %s
  AND a.user_id = %s
GROUP BY a.activity_type, a.name
"""


def _cardio_periods_sql(period: str) -> str:
    """Agregado por (periodo, tipo, nome): o rotulo do periodo e o mesmo de
    `_period_expr`; a media da FC sai como soma + contagem para a media do
    periodo ser exata depois de juntar os grupos de cardio."""
    return f"""
SELECT {_period_expr(period)} AS period, a.activity_type, a.name,
       MIN(a.local_date) AS period_start, MAX(a.local_date) AS period_end,
       COUNT(*) AS activities,
       COALESCE(SUM(a.duration_seconds), 0) AS duration_seconds,
       COALESCE(SUM(a.calories_kcal), 0) AS calories_kcal,
       SUM(a.avg_hr_bpm) AS avg_hr_sum, COUNT(a.avg_hr_bpm) AS avg_hr_count,
       MAX(a.max_hr_bpm) AS max_hr_bpm
FROM activities a
WHERE a.local_date >= %s
  AND a.user_id = %s
GROUP BY period, a.activity_type, a.name
"""


def _is_cardio_row(row: Dict[str, Any]) -> bool:
    return intervals_activity_kind.is_cardio(row.get("activity_type"), row.get("name"))


def count_cardio_activities(rows: List[Dict[str, Any]]) -> int:
    """Atividades que o dono classifica como cardio (linhas por tipo + nome)."""
    return sum(int(row.get("activities") or 0) for row in rows if _is_cardio_row(row))


def _insert_after(data: Dict[str, Any], after: str, key: str, value: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name, current in data.items():
        out[name] = current
        if name == after:
            out[key] = value
    if key not in out:
        out[key] = value
    return out


def _as_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summarize_cardio_periods(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Junta, por periodo, os grupos (tipo, nome) que o dono classifica como
    CARDIO, com a mesma forma e semantica do SQL anterior: contagem,
    primeiro/ultimo dia, soma de duracao e calorias (ausente = 0), media da FC
    media com 2 casas (so as medidas, como o `AVG`) e a maior FC maxima. Ordem:
    pelo primeiro dia do periodo."""
    buckets: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not _is_cardio_row(row):
            continue
        start, end = str(row.get("period_start"))[:10], str(row.get("period_end"))[:10]
        bucket = buckets.setdefault(
            str(row.get("period")),
            {"start": start, "end": end, "activities": 0, "duration": 0.0, "calories": 0.0,
             "hr_sum": 0.0, "hr_count": 0, "max_hr": None},
        )
        bucket["start"] = min(bucket["start"], start)
        bucket["end"] = max(bucket["end"], end)
        bucket["activities"] += int(row.get("activities") or 0)
        bucket["duration"] += _as_float(row.get("duration_seconds")) or 0.0
        bucket["calories"] += _as_float(row.get("calories_kcal")) or 0.0
        bucket["hr_sum"] += _as_float(row.get("avg_hr_sum")) or 0.0
        bucket["hr_count"] += int(row.get("avg_hr_count") or 0)
        max_hr = _as_float(row.get("max_hr_bpm"))
        if max_hr is not None:
            bucket["max_hr"] = max_hr if bucket["max_hr"] is None else max(bucket["max_hr"], max_hr)
    return [
        {
            "period": key,
            "period_start": bucket["start"],
            "period_end": bucket["end"],
            "activities": bucket["activities"],
            "duration_seconds": bucket["duration"],
            "calories_kcal": bucket["calories"],
            "avg_hr_bpm": round(bucket["hr_sum"] / bucket["hr_count"], 2) if bucket["hr_count"] else None,
            "max_hr_bpm": bucket["max_hr"],
        }
        for key, bucket in sorted(buckets.items(), key=lambda pair: (pair[1]["start"], pair[0]))
    ]


@router.get("/summary")
def analytics_summary(
    days: int = Query(default=90, ge=1, le=3650),
    identity: Identity = Depends(require_permission("dashboards", "ver")),
):
    days = _safe_days(days)
    tonnage_sum, tonnage_excluded = _global_tonnage_columns()
    since = _days_ago(identity.user_id, days)

    totals = _fetch_one(
        f"""
        SELECT
            COUNT(DISTINCT a.id) AS activities,
            COUNT(DISTINCT CASE WHEN LOWER(a.activity_type) LIKE '%%muscul%%' THEN a.id END) AS strength_activities,
            COUNT(es.id) AS sets_count,
            COALESCE(SUM(COALESCE(es.reps, 0)), 0) AS reps_total,
            {tonnage_sum} AS volume_kg,
            {tonnage_excluded} AS tonnage_excluded_sets,
            ROUND(AVG(es.rpe), 2) AS avg_rpe,
            SUM(CASE WHEN es.rpe >= {_high_rpe_min()} THEN 1 ELSE 0 END) AS high_rpe_sets
        FROM activities a
        LEFT JOIN exercises e ON e.activity_id = a.id
        LEFT JOIN exercise_sets es ON es.exercise_id = e.id AND {_ES_NOT_PREPARATORY} AND {sql_set_confirmed('es.reps_pending')}
        {_TONNAGE_JOINS}
        WHERE a.local_date >= %s
          AND a.user_id = %s
        """,
        (since, identity.user_id),
    )

    latest = _fetch_all(
        """
        SELECT
            a.id,
            a.local_date,
            a.activity_type,
            a.name,
            a.duration_seconds,
            a.calories_kcal,
            a.avg_hr_bpm,
            a.max_hr_bpm,
            a.avg_rpe
        FROM activities a
        WHERE a.local_date >= %s
          AND a.user_id = %s
        ORDER BY a.local_date DESC, a.id DESC
        LIMIT 10
        """,
        (since, identity.user_id),
    )

    # O que e cardio e do dono `intervals_activity_kind` (M98 C4): classificado
    # em Python sobre tipo + nome, nunca por `LIKE` em SQL.
    cardio_total = count_cardio_activities(
        _fetch_all(_ACTIVITY_KIND_SQL, (since, identity.user_id))
    )
    totals = _insert_after(totals, "strength_activities", "cardio_activities", cardio_total)

    return {
        "days": days,
        "totals": totals,
        "latest_activities": latest,
    }


def compute_training_trend(period: str, days: int, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Query compartilhada do endpoint `/analytics/training-trend`.

    Extraida para reuso (ex.: `weekly_trend` no payload de
    `training-evolution`) sem duplicar SQL. NAO altera o comportamento do
    endpoint existente - `training_trend` abaixo apenas chama esta funcao.

    `user_id` e opcional (None preserva o comportamento historico sem
    filtro, usado por `dashboard_aggregator_service`, que ainda nao propaga
    identidade - lacuna residual documentada, fora do escopo desta
    correcao); `/analytics/training-trend` abaixo sempre passa
    `identity.user_id`."""
    period_sql = _period_expr(period)
    tonnage_sum, tonnage_excluded = _global_tonnage_columns()
    user_filter = " AND a.user_id = %s" if user_id is not None else ""
    since = _days_ago(user_id, days)
    params: tuple = (since, user_id) if user_id is not None else (since,)

    return _fetch_all(
        f"""
        SELECT
            {period_sql} AS period,
            MIN(a.local_date) AS period_start,
            MAX(a.local_date) AS period_end,
            COUNT(DISTINCT a.id) AS workouts,
            COUNT(es.id) AS sets_count,
            COALESCE(SUM(COALESCE(es.reps, 0)), 0) AS reps_total,
            {tonnage_sum} AS volume_kg,
            {tonnage_excluded} AS tonnage_excluded_sets,
            ROUND(AVG(es.rpe), 2) AS avg_rpe,
            SUM(CASE WHEN es.rpe >= {_high_rpe_min()} THEN 1 ELSE 0 END) AS high_rpe_sets
        FROM activities a
        LEFT JOIN exercises e ON e.activity_id = a.id
        LEFT JOIN exercise_sets es ON es.exercise_id = e.id AND {_ES_NOT_PREPARATORY} AND {sql_set_confirmed('es.reps_pending')}
        {_TONNAGE_JOINS}
        WHERE a.local_date >= %s
          AND (
            LOWER(a.activity_type) LIKE '%%muscul%%'
            OR LOWER(a.activity_type) LIKE '%%força%%'
            OR LOWER(a.activity_type) LIKE '%%strength%%'
          )
          {user_filter}
        GROUP BY period
        ORDER BY MIN(a.local_date)
        """,
        params,
    )


def compute_rolling_weekly_trend(days: int, user_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Serie semanal em janela movel de 7 dias ancorada no "hoje" do usuario (`_user_today`),
    usada pelo campo `weekly_trend` de
    `DashboardAggregatorService.build_training_evolution_payload` (contexto
    da IA em /treinos).

    Diferente de `compute_training_trend("week", ...)`, que agrupa por
    semana ISO calendario (`_period_expr("week")`), aqui o agrupamento e por
    bloco de 7 dias contados para tras a partir de hoje
    (`FLOOR(DATEDIFF(hoje, a.local_date) / 7)`), garantindo que o bucket
    mais recente sempre tenha 7 dias completos - nunca uma semana em
    andamento parcial. NAO reutilizar para o endpoint `/analytics/training-trend`
    nem para `_period_expr` - esses continuam agrupando por semana/mes/ano
    calendario normalmente.

    `user_id` e opcional (None preserva o comportamento historico sem filtro);
    `DashboardAggregatorService` (desde o Novo Dashboard F0.1) sempre passa
    `identity.user_id`.

    M63: `volume_kg` exclui series `assisted`/`bodyweight` (ver
    `_TONNAGE_JOINS`); `tonnage_excluded_sets` declara quantas sairam."""
    tonnage_sum, tonnage_excluded = _global_tonnage_columns()
    user_filter = " AND a.user_id = %s" if user_id is not None else ""
    today = _user_today(user_id)
    since = today - timedelta(days=int(days))
    params: tuple = (today, since, user_id) if user_id is not None else (today, since)
    rows = _fetch_all(
        f"""
        SELECT
            FLOOR(DATEDIFF(%s, a.local_date) / 7) AS bucket_index,
            MIN(a.local_date) AS period_start,
            MAX(a.local_date) AS period_end,
            COUNT(DISTINCT a.id) AS workouts,
            COUNT(es.id) AS sets_count,
            COALESCE(SUM(COALESCE(es.reps, 0)), 0) AS reps_total,
            {tonnage_sum} AS volume_kg,
            {tonnage_excluded} AS tonnage_excluded_sets,
            ROUND(AVG(es.rpe), 2) AS avg_rpe,
            SUM(CASE WHEN es.rpe >= {_high_rpe_min()} THEN 1 ELSE 0 END) AS high_rpe_sets
        FROM activities a
        LEFT JOIN exercises e ON e.activity_id = a.id
        LEFT JOIN exercise_sets es ON es.exercise_id = e.id AND {_ES_NOT_PREPARATORY} AND {sql_set_confirmed('es.reps_pending')}
        {_TONNAGE_JOINS}
        WHERE a.local_date >= %s
          AND (
            LOWER(a.activity_type) LIKE '%%muscul%%'
            OR LOWER(a.activity_type) LIKE '%%força%%'
            OR LOWER(a.activity_type) LIKE '%%strength%%'
          )
          {user_filter}
        GROUP BY bucket_index
        ORDER BY bucket_index DESC
        """,
        params,
    )

    result = []
    for row in rows:
        bucket_index = int(row.pop("bucket_index"))
        period = f"D-{bucket_index * 7} a D-{bucket_index * 7 + 6}"
        result.append({"period": period, **row})

    return result


@router.get("/training-trend")
def training_trend(
    period: str = Query(default="week", pattern="^(day|week|month|year)$"),
    days: int = Query(default=365, ge=1, le=3650),
    identity: Identity = Depends(require_permission("dashboards", "ver")),
):
    days = _safe_days(days)

    rows = compute_training_trend(period, days, user_id=identity.user_id)

    return {
        "period": period,
        "days": days,
        "items": rows,
    }


def _high_rpe_min() -> int:
    """Limiar de serie com RPE alto do registro (`metrics_registry.HIGH_RPE_MIN`).
    Import preguicoso: o modulo nao carrega `intelligence` no import."""
    from .intelligence.dashboard.metrics_registry import HIGH_RPE_MIN

    return int(HIGH_RPE_MIN)


#: Teto de exercicios casados pelo filtro livre de `/analytics/exercise-progress`
#: (os de sessao mais recente): cada um custa leituras proprias, e um nome curto
#: casaria dezenas e estouraria os 29 s do API Gateway.
EXERCISE_PROGRESS_MAX_EXERCISES = 12


# Exercicios canonicos do usuario cujo nome lancado ou nome AMAZFIT casa o
# filtro livre da tela `/exercicios`. So resolve QUAIS exercicios: a curva sai
# das exposicoes do dono (`exposure_service`), nunca de agregacao aqui. Uma
# linha por exercicio, da sessao mais recente (`MAX(a.local_date)` so ordena),
# limitada no banco (teto de 1 MB da Data API); `matched_total` (janela sobre
# os grupos, antes do LIMIT) conta todos os casados. Mesmos predicados da
# leitura do dono (deload fora, serie de trabalho confirmada, objetivo): um
# exercicio casado nunca sai sem pontos.
def _exercise_progress_match_sql(with_goal: bool) -> str:
    from .domain.deload import sql_not_deload
    from .domain.form_practice import sql_counts_for_progression
    from .domain.training_goal import sql_goal_is
    from .set_types import sql_is_working, sql_set_confirmed

    goal_filter = f"\n  AND {sql_goal_is('e')}" if with_goal else ""
    return f"""
SELECT CAST(COALESCE(elm.canonical_exercise_id, e.mapping_id) AS SIGNED) AS exercise_id,
       MAX(a.local_date) AS last_date,
       COUNT(*) OVER () AS matched_total
FROM activities a
JOIN exercises e ON e.activity_id = a.id
LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
WHERE a.user_id = %s
  AND a.local_date >= %s
  AND a.local_date <= %s{goal_filter}
  AND {sql_not_deload("a")}
  AND {sql_counts_for_progression("e")}
  AND COALESCE(elm.canonical_exercise_id, e.mapping_id) IS NOT NULL
  AND EXISTS (
    SELECT 1 FROM exercise_sets es
    WHERE es.exercise_id = e.id AND {sql_is_working("es.set_type")} AND {sql_set_confirmed("es.reps_pending")}
  )
  AND (
    LOWER(COALESCE(e.original_name, '')) LIKE %s
    OR LOWER(COALESCE((
        SELECT exm.external_name
        FROM exercise_external_mappings exm
        WHERE exm.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
          AND exm.provider = 'AMAZFIT' AND exm.is_active = 1
        ORDER BY exm.id ASC LIMIT 1
    ), '')) LIKE %s
  )
GROUP BY exercise_id
ORDER BY last_date DESC, exercise_id
LIMIT %s
"""


@router.get("/exercise-progress")
def exercise_progress(
    name: str = Query(..., min_length=1),
    days: int = Query(default=365, ge=1, le=3650),
    goal: Optional[str] = Depends(optional_goal_query),
    identity: Identity = Depends(require_permission("progressao", "ver")),
):
    """Curva de carga/volume/RPE por exposicao dos exercicios cujo nome casa
    `name`. Issue #188: cada ponto e o resumo de exposicao do DONO
    (`exposure_service.get_exposures_between` + `exposure_snapshot`): so
    series de trabalho confirmadas, deload fora, convencao de carga e chave de
    comparacao do dono, com `in_context_run`/`in_verdict_run`
    (`verdict_run_flags`). Uma serie por exercicio canonico (`series`);
    `items` = todos os pontos em ordem de data (tabela). `goal` (M97 F4,
    opcional): so as execucoes daquele objetivo; ausente = todos os objetivos,
    com as marcacoes da sequencia do objetivo de cada ponto. No maximo
    `EXERCISE_PROGRESS_MAX_EXERCISES` exercicios (os de sessao mais recente):
    `truncated`/`matched_exercises` dizem quando houve corte. O contexto de
    fadiga continua lido: a sequencia (`in_context_run`) depende dele."""
    from .intelligence.dashboard.exposure_service import (
        MAX_TABLE_EXPOSURES,
        _fetch_exercise,
        get_exposures_between,
        in_requested_range,
        verdict_run_flags,
    )

    goal = optional_goal_param(goal)
    days = _safe_days(days)
    user_id = identity.user_id
    date_from = _days_ago(user_id, days)
    like = f"%{name.lower()}%"
    # Uma linha por exercicio, da sessao mais recente; o +1 so revela o corte.
    from .domain.training_goal import goal_value

    # Mesma data final da leitura das exposicoes abaixo.
    date_to = _user_today(user_id)
    goal_params = (goal_value(goal),) if goal is not None else ()
    rows = _fetch_all(
        _exercise_progress_match_sql(goal is not None),
        (user_id, date_from, date_to, *goal_params, like, like, EXERCISE_PROGRESS_MAX_EXERCISES + 1),
    )
    matches = [int(row["exercise_id"]) for row in rows][:EXERCISE_PROGRESS_MAX_EXERCISES]
    matched_total = int(rows[0]["matched_total"]) if rows else 0

    series: List[Dict[str, Any]] = []
    if matches:
        with _connect() as conn:
            for exercise_id in matches:
                exercise = _fetch_exercise(conn, exercise_id) or {}
                exposures = get_exposures_between(
                    conn,
                    exercise_id,
                    user_id=user_id,
                    date_from=date_from,
                    date_to=date_to,
                    limit=MAX_TABLE_EXPOSURES,
                    training_goal=goal,
                    all_goals=goal is None,
                    # veredito sobre a janela do dono tambem sem objetivo (#188)
                    verdict_context=True,
                )
                series.append(
                    {
                        "exercise_id": exercise_id,
                        "exercise_name": exercise.get("exercise_name"),
                        "mapped_name": exercise.get("exercise_amazfit_name"),
                        "items": [
                            _exercise_progress_point(exercise_id, exercise, exposure, flags)
                            for exposure, flags in zip(exposures, verdict_run_flags(exposures))
                            # contexto do veredito (#188) decide, mas nao vira ponto do grafico
                            if in_requested_range(exposure)
                        ],
                    }
                )

    items = sorted((p for s in series for p in s["items"]), key=lambda p: str(p["local_date"] or ""))
    activity_ids = sorted({p["activity_id"] for p in items if p["activity_id"] is not None})
    if activity_ids:
        names = {
            row["id"]: row.get("name")
            for row in _fetch_all(
                f"SELECT id, name FROM activities WHERE user_id = %s AND id IN ({', '.join(['%s'] * len(activity_ids))})",
                (user_id, *activity_ids),
            )
        }
        for point in items:
            point["activity_name"] = names.get(point["activity_id"])

    return {
        "name": name,
        "days": days,
        "series": series,
        "items": items,
        "matched_exercises": matched_total,
        "truncated": matched_total > len(matches),
    }


def _exercise_progress_point(
    exercise_id: int, exercise: Dict[str, Any], exposure: Dict[str, Any], flags: Dict[str, bool]
) -> Dict[str, Any]:
    """Um ponto da curva = resumo da exposicao do dono, com os nomes de campo
    que a tela `/exercicios` ja le."""
    from .intelligence.dashboard.exposure_service import exposure_snapshot

    snapshot = exposure_snapshot(exposure) or {}
    return {
        "exercise_id": exercise_id,
        "activity_id": exposure.get("activity_id"),
        "local_date": exposure.get("local_date"),
        "activity_name": None,
        "original_name": exercise.get("exercise_name"),
        "mapped_name": exercise.get("exercise_amazfit_name"),
        "equipment_name": exposure.get("equipment_name"),
        "load_convention": exposure.get("load_convention"),
        "sets_count": snapshot.get("working_sets"),
        "reps_total": exposure.get("total_reps"),
        "best_weight_kg": snapshot.get("best_load_kg"),
        "top_set_reps": snapshot.get("top_set_reps"),
        "volume_kg": exposure.get("tonnage"),
        "avg_rpe": snapshot.get("avg_rpe"),
        "high_rpe_sets": snapshot.get("high_rpe_sets"),
        "e1rm": snapshot.get("e1rm"),
        "e1rm_reps": snapshot.get("e1rm_reps"),
        "e1rm_reliable": snapshot.get("e1rm_reliable"),
        "comparability_key": exposure.get("comparability_key"),
        "in_context_run": flags["in_context_run"],
        "in_verdict_run": flags["in_verdict_run"],
    }


@router.get("/rpe-distribution")
def rpe_distribution(
    days: int = Query(default=90, ge=1, le=3650),
    identity: Identity = Depends(require_permission("dashboards", "ver")),
):
    days = _safe_days(days)

    rows = _fetch_all(
        f"""
        SELECT
            FLOOR(es.rpe) AS rpe,
            COUNT(*) AS sets_count
        FROM activities a
        JOIN exercises e ON e.activity_id = a.id
        JOIN exercise_sets es ON es.exercise_id = e.id AND {_ES_NOT_PREPARATORY} AND {sql_set_confirmed('es.reps_pending')}
        WHERE a.local_date >= %s
          AND a.user_id = %s
          AND es.rpe IS NOT NULL
        GROUP BY FLOOR(es.rpe)
        ORDER BY FLOOR(es.rpe)
        """,
        (_days_ago(identity.user_id, days), identity.user_id),
    )

    return {
        "days": days,
        "items": rows,
    }


@router.get("/cardio")
def cardio_summary(
    period: str = Query(default="week", pattern="^(day|week|month|year)$"),
    days: int = Query(default=365, ge=1, le=3650),
    identity: Identity = Depends(require_permission("cardio", "ver")),
):
    """Cardio por periodo das atividades registradas (`activities`). O que e
    cardio e do dono `intervals_activity_kind` (M98 C4; antes, uma lista de
    `LIKE` propria aqui); o SQL agrega por (periodo, tipo, nome) e o Python junta
    so os grupos de cardio (`summarize_cardio_periods`)."""
    days = _safe_days(days)
    rows = _fetch_all(_cardio_periods_sql(period), (_days_ago(identity.user_id, days), identity.user_id))

    return {
        "period": period,
        "days": days,
        "items": summarize_cardio_periods(rows),
    }

# BEGIN muscle group trend analytics
# As rotas por REGEX no nome do exercicio (`/muscle-groups`, `-trend`,
# `-table`, com `MUSCLE_GROUP_CASE`) sairam no COD-05/M57 (2026-10-09), apos
# 14 dias sem `legacy_endpoint_used` em producao. Grupo muscular vem so da
# classificacao canonica (app/muscle_groups_mapped.py e VolumeService).

@router.get("/muscle-groups-equivalent-sets-by-activity")
def muscle_groups_equivalent_sets_by_activity(
    limit: int = Query(default=400, ge=1, le=2000),
    identity: Identity = Depends(require_permission("volume.grupamentos", "ver")),
):
    """Series EQUIVALENTES ponderadas (PRIMARY peso 1.0, SECONDARY/
    STABILIZER = stimulus_weight real de exercise_muscle_contributions, com
    fallback legado - mesma formula ja usada em producao por
    abc_volume_reinforcement.py), agregadas por (activity_id, muscle_group).
    Usada pela tabela "Series equivalentes executadas - ultimos 4 treinos"
    de /volume-grupamentos, substituindo a contagem BRUTA anterior (1 serie
    logada = 1 unidade, sem considerar contribuicao de musculos secundarios
    de outros exercicios do mesmo treino).

    Cada linha leva tambem o grupo pai e o macro-grupo do grupo, resolvidos
    pelo dono (`methodology_volume_service.load_group_hierarchy`), para a tela
    agrupar sem mapa proprio; os numeros nao mudam."""
    from .intelligence.methodology_volume_service import annotate_group_hierarchy, load_group_hierarchy
    from .intelligence.volume_service import VolumeService

    volume_service = VolumeService()
    items = volume_service.get_equivalent_sets_by_activity(limit=limit, user_id=identity.user_id)
    annotate_group_hierarchy(items, load_group_hierarchy())

    return {
        "limit": limit,
        "items": items,
    }


@router.get("/muscle-activation-by-activity")
def muscle_activation_by_activity(
    activity_id: int = Query(...),
    identity: Identity = Depends(require_permission("volume.grupamentos", "ver")),
):
    """Ativacoes musculares (primary/secondary separadas, com series/peso
    medio/equivalente por role) de UMA atividade especifica, para a tabela
    "Ativacao muscular" de /registro-exercicios/[activityId]. Mesma formula
    de stimulus_weight ja usada em producao (ver
    VolumeService.get_muscle_activation_by_activity)."""
    from .intelligence.volume_service import VolumeService, assess_activation_rows

    volume_service = VolumeService()
    items = volume_service.get_muscle_activation_by_activity(activity_id=activity_id, user_id=identity.user_id)
    training_type_targets = volume_service.get_training_type_weekly_targets_for_activity(
        activity_id=activity_id, user_id=identity.user_id
    )
    # M76 E4: status de cada grupo sai daqui (`assessment`); a tabela so desenha.
    items = assess_activation_rows(items, training_type_targets)

    return {
        "activity_id": activity_id,
        "items": items,
        "training_type_targets": training_type_targets,
    }


INCLUDE_WEEKLY = "weekly"
INCLUDE_EQUIPMENT_WEEKLY = "equipment_weekly"
INCLUDE_HEATMAP = "heatmap"
RECENT_VOLUME_INCLUDES = (INCLUDE_WEEKLY, INCLUDE_EQUIPMENT_WEEKLY, INCLUDE_HEATMAP)


@router.get("/muscle-groups-recent-exercise-volume")
def muscle_groups_recent_exercise_volume(
    limit: Optional[int] = Query(default=None, ge=1, le=2000),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    goal: Optional[str] = Depends(optional_goal_query),
    identity: Identity = Depends(require_permission("volume.grupamentos", "ver")),
    include: Annotated[Optional[str], Query()] = None,
    activity_id: Annotated[Optional[int], Query(ge=1)] = None,
):
    """Volume BRUTO recente por (activity_id, muscle_group, equipment_name),
    usado pelas 4 tabelas client-side de /volume-grupamentos (comparativo dos
    ultimos 4 treinos, comparativo das ultimas 4 semanas, comparativo por
    equipamento e mapa de calor) - motor unico via
    VolumeService.get_recent_exercise_volume, substituindo o calculo que
    antes era feito em JS a partir de /exercises/logs.

    `limit` (default 400, mesmo historico ja usado pelo frontend) e
    `date_from`/`date_to` sao mutuamente exclusivos: quando datas sao
    informadas, `limit` e ignorado (ver docstring de
    `get_recent_exercise_volume`).

    Cada linha leva tambem o grupo pai e o macro-grupo do grupo, resolvidos
    pelo dono (`methodology_volume_service.load_group_hierarchy`), para a tela
    agrupar sem mapa proprio; os numeros nao mudam.

    `goal` (M97, opcional): so os exercicios daquele objetivo (predicado do
    dono `sql_goal_is`, o mesmo do comparativo por slot); ausente = todos, a
    resposta de sempre.

    `include` (opcional, lista separada por virgula de
    `RECENT_VOLUME_INCLUDES`): blocos prontos do dono
    (`slot_session_comparison`) sobre os MESMOS itens; ausente = a resposta de
    sempre. `activity_id` restringe so o `heatmap` a uma sessao."""
    from .intelligence.methodology_volume_service import annotate_group_hierarchy, load_group_hierarchy
    from .intelligence.volume_service import VolumeService

    requested = [part.strip() for part in include.split(",") if part.strip()] if include else []
    invalid = sorted(set(requested) - set(RECENT_VOLUME_INCLUDES))
    if invalid:
        raise HTTPException(status_code=422, detail=f"include invalido: {', '.join(invalid)}")
    goal = optional_goal_param(goal)
    volume_service = VolumeService()
    effective_limit = None if (date_from is not None or date_to is not None) else (limit or 400)
    filters: Dict[str, Any] = {} if goal is None else {"training_goal": goal}
    items = volume_service.get_recent_exercise_volume(
        user_id=identity.user_id,
        limit=effective_limit,
        date_from=date_from,
        date_to=date_to,
        **filters,
    )
    annotate_group_hierarchy(items, load_group_hierarchy())

    body = {
        "limit": effective_limit,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "items": items,
    }
    if requested:
        from .intelligence import slot_session_comparison as comparison

        builders = {
            INCLUDE_WEEKLY: comparison.build_weekly_comparison,
            INCLUDE_EQUIPMENT_WEEKLY: comparison.build_equipment_weekly_comparison,
            INCLUDE_HEATMAP: lambda rows: comparison.build_heatmap(rows, activity_id=activity_id),
        }
        for name in RECENT_VOLUME_INCLUDES:
            if name in requested:
                body[name] = builders[name](items)
    return body


@router.get("/muscle-groups-slot-comparison")
def muscle_groups_slot_comparison(
    limit: int = Query(default=400, ge=1, le=2000),
    goal: Optional[str] = Depends(optional_goal_query),
    identity: Identity = Depends(require_permission("volume.grupamentos", "ver")),
):
    """Comparativo T-0..T-3 de /volume-grupamentos pelo MESMO slot do Modelo
    (pedido do usuario, 2026-10-23): T-0 = ultima sessao da chave; T-1..T-3 =
    as anteriores da mesma chave com a ficha no mesmo slot (`template_id` +
    `template_slot_sequence`, nunca o nome). Volume/RPE e series equivalentes,
    nos tres niveis do "Agrupar por". Regra no dono
    `intelligence/slot_session_comparison.py`; a tela so desenha.

    `goal` (M97 F4, opcional): so os exercicios daquele objetivo; ausente =
    todos, como sempre. Series equivalentes com a serie de forca ponderada
    pelo motor de volume (`methodology_volume`)."""
    from .intelligence.volume_service import VolumeService

    return VolumeService().get_slot_session_comparison(
        user_id=identity.user_id, limit=limit, training_goal=optional_goal_param(goal)
    )


@router.get("/muscle-groups-daily-activation")
def muscle_groups_daily_activation(
    days: int = Query(default=7, ge=1, le=90),
    identity: Identity = Depends(require_permission("volume.grupamentos", "ver")),
):
    """Ativacao muscular DIARIA por (local_date, muscle_group) - motor unico
    via VolumeService.get_daily_muscle_activation, usada pela tabela
    "Ativacao muscular (ultimos 7 dias)" de /treinos. Substitui, so para essa
    tabela, o par legado /muscle-groups-mapped-trend +
    /muscle-groups-mapped-health (muscle_groups_mapped.py), que continua
    ativo para as demais rotas que ainda o usam.

    Nao aplica o limiar de ativacao (>=9 series com pelo menos uma em RPE
    9-10) aqui - devolve `sets_count`/`had_rpe_9_10` brutos por dia/grupo,
    igual `get_daily_muscle_activation`; a regra e aplicada pelo frontend."""
    from .intelligence.volume_service import VolumeService

    volume_service = VolumeService()
    items = volume_service.get_daily_muscle_activation(days=days, user_id=identity.user_id)

    return {
        "days": days,
        "items": items,
    }


# Janela fixa da tabela "Ativacao muscular (ultimos 7 dias)" de /dashboard/musculos.
REALIZED_METHODOLOGY_WINDOW_DAYS = 7


@router.get("/methodology-realized")
def methodology_realized(identity: Identity = Depends(require_permission("volume.grupamentos", "ver"))):
    """Metodologia do REALIZADO na janela de 7 dias (M76 E6): por BLOCO
    (fatia -> bloco pela cadeia inteira), as series DIRETAS de cada dia, a
    frequencia, a faixa por treino e a meta semanal do macro-grupo e os
    status (dia, media por treino, semana) - tudo de
    `intelligence/methodology_realized.py`, com as regras do motor
    `methodology_volume.py`. A tabela de `/dashboard/musculos` so desenha.

    Lote 4 do modo de contagem: a janela usa UM modo, o do Modelo da sessao
    mais recente ate hoje (`load_counting_mode_at`); series, frequencia e
    faixas sao as desse modo, e o payload devolve `counting_mode`."""
    from .db import transaction
    from .intelligence.methodology_realized import METRIC_VERSION, block_map, evaluate_realized_window
    from .intelligence.methodology_volume_service import (
        load_counting_mode_at,
        load_methodology_groups,
        load_volume_classes,
    )
    from .intelligence.volume_service import VolumeService

    volume_service = VolumeService()
    today = volume_service._user_today(identity.user_id)
    window_days = [
        (today - timedelta(days=offset)).isoformat()
        for offset in range(REALIZED_METHODOLOGY_WINDOW_DAYS - 1, -1, -1)
    ]
    with transaction() as conn:
        with conn.cursor() as cur:
            counting_mode = load_counting_mode_at(cur, identity.user_id, window_days[-1])
            groups = load_methodology_groups(cur)
            volume_classes = load_volume_classes(cur, counting_mode)
    items = volume_service.get_daily_muscle_activation(
        user_id=identity.user_id,
        start_date=window_days[0],
        end_date=window_days[-1],
        counting_mode=counting_mode,
        block_of=block_map(groups),
    )

    return {
        "metric_version": METRIC_VERSION,
        "counting_mode": counting_mode.value,
        "days": window_days,
        "items": evaluate_realized_window(items, window_days, groups, volume_classes),
    }


@router.post("/muscle-activation-preview")
def muscle_activation_preview(
    payload: MuscleActivationPreviewIn,
    identity: Identity = Depends(require_permission("volume.grupamentos", "editar")),
):
    """Mesma formula de `muscle_activation_by_activity`, mas para uma lista
    de exercicios fornecida diretamente no corpo (sem `activity_id`
    persistido) - usada pela tabela "Ativacao muscular" AO VIVO do rascunho
    em edicao em /planos-treino, antes de salvar o plano."""
    from .intelligence.volume_service import VolumeService

    volume_service = VolumeService()
    items = volume_service.get_muscle_activation_for_exercises(
        [ex.model_dump() for ex in payload.exercises]
    )

    return {"items": items}


@router.post("/muscle-activation-breakdown")
def muscle_activation_breakdown(
    payload: MuscleActivationPreviewIn,
    identity: Identity = Depends(require_permission("volume.grupamentos", "editar")),
):
    """Mesma fonte de dados de `muscle_activation_preview`
    (`_resolve_muscle_contributions_for_mappings` + `sets_count *
    stimulus_weight`), mas SEM agregar em buckets por `muscle_group_name` do
    plano inteiro - devolve a ativacao POR EXERCICIO (um item de saida por
    item de entrada, mesma ordem/tamanho), usada pela linha de detalhe de
    ativacao muscular POR LINHA da tabela de exercicios em /planos-treino."""
    from .intelligence.volume_service import VolumeService

    volume_service = VolumeService()
    items = volume_service.get_muscle_activation_breakdown_by_exercise(
        [ex.model_dump() for ex in payload.exercises]
    )

    return {"items": items}
# END muscle group trend analytics

# BEGIN dashboard novo (hub paralelo /dashboard/*, Fase 1)
# Ver plano completo em
# C:\Users\dougb\.claude\plans\c-users-dougb-claude-uploads-c8154714-b-happy-perlis.md.
# Hub paralelo: NAO substitui nenhum endpoint acima, so agrega/reformata
# dados que VolumeService/MuscleRecoveryService/progression_service ja
# calculam - nenhuma formula nova de ativacao/recuperacao/progressao aqui.

# Fallback generico de meta semanal quando o usuario nao tem nenhum
# training_type resolvido (nem explicito via query param, nem via template
# atribuido) - MESMOS limiares de MuscleActivationTable.tsx/activationAssessment,
# para nunca ter um segundo padrao de fallback divergente entre a tela antiga
# e o dashboard novo. Constantes definidas em `intelligence/volume_service.py`
# desde a Fase 4 (docstring completa la, importadas SEMPRE dentro de funcao -
# nunca no topo do modulo - porque `app.intelligence.__init__` importa
# `dashboard_aggregator_service`, que por sua vez importa deste MESMO modulo
# `analytics.py`; um import de topo aqui cria ciclo de import na inicializacao
# do pacote `intelligence`, MESMO motivo pelo qual todo import de
# `intelligence.*` neste arquivo ja e feito lazy, dentro de cada funcao), para
# nao duplicar os 3 numeros aqui.


def _build_muscle_status_items(user_id: int, days: int, training_type_id: Optional[int]) -> Dict[str, Any]:
    """Nucleo de `/analytics/dashboard-muscle-status` - uma unica chamada a `VolumeService.get_muscle_group_overview` +
    `classify_weekly_volume_status`, nunca duas queries divergentes para a
    mesma informacao.

    IMPORTANTE (bug pego em teste end-to-end real com dados de producao
    desta sessao): `get_muscle_group_overview(days=days)` devolve
    `stimulus_equivalent_sets` ACUMULADO na janela inteira de `days`, mas
    `min/target/max_weekly_sets` de um `training_type` sao metas POR SEMANA.
    Com `days=14` (ou qualquer valor != 7), comparar o acumulado bruto contra
    a meta semanal sem normalizar faz TODO grupo estourar `ABOVE_MAX`
    (2 semanas de volume vs meta de 1 semana). Normaliza para uma taxa
    semanal equivalente (`methodology_volume.calendar_weekly_rate`, dono da
    normalizacao desde o M76 Lote 4a) antes de classificar - permite usar
    `days` maior que 7 pra suavizar ruido de uma semana atipica isolada, sem
    quebrar a comparacao contra a meta semanal."""
    from .intelligence.methodology_volume import calendar_weekly_rate
    from .intelligence.volume_service import (
        DASHBOARD_FALLBACK_MAX_SETS,
        DASHBOARD_FALLBACK_MIN_SETS,
        DASHBOARD_FALLBACK_TARGET_SETS,
        VolumeService,
        classify_weekly_volume_status,
    )
    from .training_types import get_training_type_muscle_group_targets, resolve_active_training_type_for_user

    volume_service = VolumeService()
    # limit=100: get_muscle_group_overview trunca em `limit` grupos (default
    # 8, pensado pra telas que so mostram os mais volumosos) - o dashboard de
    # status precisa de TODOS os grupos com atividade na janela, entao usamos
    # um teto generoso (o catalogo de muscle_groups do projeto tem ordem de
    # grandeza de dezenas, nao centenas).
    overview_rows = volume_service.get_muscle_group_overview(days=days, limit=100, user_id=user_id)

    resolved_training_type_id = training_type_id
    training_type_source = "explicit"
    if resolved_training_type_id is None:
        resolved_training_type_id = resolve_active_training_type_for_user(user_id)
        training_type_source = "assigned_template" if resolved_training_type_id else "fallback_generic"

    targets_by_group_id: Dict[int, Dict[str, float]] = {}
    if resolved_training_type_id is not None:
        targets_by_group_id = get_training_type_muscle_group_targets(resolved_training_type_id)

    items: List[Dict[str, Any]] = []
    for row in overview_rows:
        group_id = row.get("muscle_group_id")
        actual_in_window = float(row.get("stimulus_equivalent_sets") or 0.0)
        # Sem `days` (0/None) o acumulado fica como esta - o fallback de antes.
        actual_weekly = calendar_weekly_rate(actual_in_window, days) if days else actual_in_window
        target = targets_by_group_id.get(group_id) if group_id is not None else None

        if target:
            min_sets, target_sets, max_sets = target["min"], target["target"], target["max"]
            target_source = "training_type"
        else:
            min_sets = DASHBOARD_FALLBACK_MIN_SETS
            target_sets = DASHBOARD_FALLBACK_TARGET_SETS
            max_sets = DASHBOARD_FALLBACK_MAX_SETS
            target_source = "generic_fallback"

        status = classify_weekly_volume_status(actual_weekly, min_sets, target_sets, max_sets)

        items.append(
            {
                "muscle_group_id": group_id,
                "muscle_group": row.get("muscle_group"),
                # Series equivalentes/semana (normalizado - ver docstring
                # acima), MESMA unidade de min/target/max_weekly_sets.
                "actual_effective_sets": round(actual_weekly, 2),
                # Total bruto acumulado na janela `days` inteira (sem
                # normalizar) - exposto so para contexto/depuracao na UI
                # (ex.: "42 séries em 14 dias"), nunca usado na classificacao.
                "actual_effective_sets_in_window": round(actual_in_window, 2),
                "min_weekly_sets": min_sets,
                "target_weekly_sets": target_sets,
                "max_weekly_sets": max_sets,
                "status": status,
                "target_source": target_source,
                "sessions": row.get("sessions"),
                "last_trained_at": row.get("last_trained_at"),
                "avg_rpe": row.get("avg_rpe"),
                "direct_sets": row.get("direct_sets"),
                "indirect_sets": row.get("indirect_sets"),
                "stabilizer_sets": row.get("stabilizer_sets"),
            }
        )

    items.sort(key=lambda item: item["actual_effective_sets"], reverse=True)

    return {
        "training_type_id": resolved_training_type_id,
        "training_type_source": training_type_source,
        "items": items,
    }


@router.get("/dashboard-muscle-status")
def dashboard_muscle_status(
    days: int = Query(default=14, ge=1, le=3650),
    training_type_id: Optional[int] = Query(default=None),
    identity: Identity = Depends(require_permission("volume.grupamentos", "ver")),
):
    """Volume semanal REALIZADO (series equivalentes, `VolumeService.get_muscle_group_overview`)
    de cada grupo muscular contra a meta min/target/max de um `training_type`
    - explicito via `training_type_id` (prioridade), senao o template
    atualmente atribuido ao usuario (`resolve_active_training_type_for_user`),
    senao o fallback generico (mesmos limiares de `MuscleActivationTable.tsx`).
    Estado de aderencia via `classify_weekly_volume_status`
    (`intelligence/volume_service.py`), unica fonte dos 5 estados - nunca
    reimplementado aqui nem no frontend. Consumido por
    `/dashboard/musculos` (MuscleStatusClient.tsx)."""
    days = _safe_days(days)
    result = _build_muscle_status_items(identity.user_id, days, training_type_id)

    return {
        "days": days,
        **result,
    }


@router.get("/dashboard-muscle-drilldown")
def dashboard_muscle_drilldown(
    muscle_group_id: int = Query(...),
    days: int = Query(default=90, ge=1, le=3650),
    identity: Identity = Depends(require_permission("volume.grupamentos", "ver")),
):
    """Drill-down de UM grupo muscular para `/dashboard/musculos`
    (MuscleStatusClient.tsx, clique em uma linha) - volume semanal
    primario/secundario/estabilizador + ultimo treino
    (`VolumeService.get_muscle_group_overview`, mesmo item usado por
    `dashboard-muscle-status`), readiness (`MuscleRecoveryService.get_recovery_overview`),
    ultima progressao do exercicio mais recente do grupo
    (`services.suggested_progression_for_mapping`, MESMA logica ja usada por
    `GET /exercises/suggested-progression`), principais exercicios
    (`VolumeService.get_top_exercises_for_muscle_group`) e historico temporal
    de series equivalentes (`VolumeService.get_rolling_weekly_trend_by_muscle_group`,
    filtrado ao grupo pedido)."""
    from . import services
    from .intelligence.muscle_recovery_service import MuscleRecoveryService
    from .intelligence.volume_service import VolumeService

    days = _safe_days(days)
    user_id = identity.user_id
    volume_service = VolumeService()

    overview_rows = volume_service.get_muscle_group_overview(days=days, limit=100, user_id=user_id)
    group_row = next((row for row in overview_rows if row.get("muscle_group_id") == muscle_group_id), None)

    # `overview_rows=overview_rows`: a readiness sai das MESMAS linhas ja
    # calculadas acima. Sem isso, `get_recovery_overview` recomputava o
    # overview inteiro (historico completo do usuario) uma segunda vez na
    # mesma requisicao - causa direta do timeout de 29s observado em producao
    # em 2026-09-22.
    recovery_items = MuscleRecoveryService().get_recovery_overview(
        days=days, limit=100, user_id=user_id, overview_rows=overview_rows
    )
    recovery_item = next((item for item in recovery_items if item.get("muscle_group_id") == muscle_group_id), None)

    top_exercises = volume_service.get_top_exercises_for_muscle_group(
        muscle_group_id=muscle_group_id, days=days, user_id=user_id, limit=5
    )

    latest_progression = None
    if top_exercises and top_exercises[0].get("mapping_id"):
        from .generation.deterministic_prescription import readiness_by_group

        # M76 (Lote 1a): mesma decisao do motor v2 das outras telas, composta
        # com o contrato de recuperacao JA calculado acima (sem recomputar).
        latest_progression = services.suggested_progression_for_mapping(
            mapping_id=top_exercises[0]["mapping_id"],
            user_id=user_id,
            readiness=readiness_by_group(recovery_items or []),
        )

    weekly_series_all = volume_service.get_rolling_weekly_trend_by_muscle_group(days=days, user_id=user_id)
    group_name = group_row.get("muscle_group") if group_row else None
    weekly_series = [row for row in weekly_series_all if row.get("muscle_group") == group_name] if group_name else []

    return {
        "muscle_group_id": muscle_group_id,
        "days": days,
        "overview": group_row,
        "readiness": recovery_item,
        "top_exercises": top_exercises,
        "latest_progression": latest_progression,
        "weekly_series": weekly_series,
    }
# END dashboard novo (hub paralelo /dashboard/*, Fase 1)

# BEGIN dashboard novo (hub paralelo /dashboard/*, Fase 2)
# Ver plano completo em
# C:\Users\dougb\.claude\plans\c-users-dougb-claude-uploads-c8154714-b-happy-perlis.md.
# `/dashboard/progressao` + `/dashboard/treinos` (novos, hub paralelo -
# nao tocam em `/exercicios`, `/treinos` nem `/planos-treino` existentes).


def _fetch_exercise_set_rows(
    user_id: int,
    days: int,
    exercise_name: Optional[str] = None,
    mapping_id: Optional[int] = None,
    date_range: Optional[Tuple[date, date]] = None,
    training_goal: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Series individuais (nao agregadas) de UM exercicio, filtradas por
    `mapping_id` (prioridade) ou `exercise_name` (LIKE, mesmo padrao de
    `exercise_progress` acima - MESMOS JOINs/aliases, so a granularidade
    muda de "por exercicio" para "por serie"). Base da tabela de
    `dashboard-progression` (Fase 2): e1RM
    por sessao exige dado por SERIE (reps/weight_kg/rpe), nao a agregacao
    que `exercise_progress` ja devolve.

    `date_range` (M61, janela `custom`): `[inicio, fim]` em datas locais,
    inclusivo, ja validado por `_custom_date_range`; quando presente substitui
    o recorte por `days` (que continua o mesmo de sempre para as janelas
    pre-definidas).

    M97 F4: `training_goal` (opcional) filtra as execucoes do objetivo pelo
    predicado do dono (`sql_goal_is`); ausente = todas, como sempre. Cada
    linha traz o objetivo da execucao e a origem do deload da sessao (colunas
    aditivas; o filtro de sempre nao muda)."""
    from .domain.training_goal import sql_goal_is

    if mapping_id is None and not exercise_name:
        raise HTTPException(status_code=400, detail="Informe exercise_name ou mapping_id.")

    params: List[Any]
    if date_range is not None:
        where = ["a.local_date >= %s", "a.local_date <= %s", "a.user_id = %s"]
        params = [date_range[0], date_range[1], user_id]
    else:
        where = ["a.local_date >= %s", "a.user_id = %s"]
        params = [_days_ago(user_id, days), user_id]

    if mapping_id is not None:
        where.append("COALESCE(elm.canonical_exercise_id, e.mapping_id) = %s")
        params.append(mapping_id)
    else:
        where.append(
            """
            (
                LOWER(COALESCE(e.original_name, '')) LIKE %s
                OR LOWER(COALESCE((
                    SELECT exm.external_name
                    FROM exercise_external_mappings exm
                    WHERE exm.exercise_id = COALESCE(elm.canonical_exercise_id, e.mapping_id)
                      AND exm.provider = 'AMAZFIT' AND exm.is_active = 1
                    ORDER BY exm.id ASC LIMIT 1
                ), '')) LIKE %s
            )
            """
        )
        like = f"%{(exercise_name or '').lower()}%"
        params.extend([like, like])

    if training_goal is not None:
        where.append(sql_goal_is("e"))
        params.append(training_goal)

    where_clause = " AND ".join(where)

    return _fetch_all(
        f"""
        SELECT
            a.id AS activity_id,
            a.local_date,
            a.name AS activity_name,
            CAST(COALESCE(elm.canonical_exercise_id, e.mapping_id) AS SIGNED) AS mapping_id,
            es.set_number,
            es.set_type,
            es.reps,
            es.weight_kg,
            es.rpe,
            es.measure,
            -- PR-14 (secao 5.7): alvo efetivo e evento de seguranca GRAVADOS na
            -- serie; base do `intensity_status` da sessao (sem recalcular
            -- prescricao historica). NULL em serie anterior ao PR-14.
            es.rpe_target_effective,
            es.rpe_target_planned,
            es.safety_event,
            CAST(e.equipment_id AS SIGNED) AS gym_equipment_id,
            e.id AS exercise_row_id,
            e.training_goal AS training_goal,
            e.form_practice AS form_practice,
            a.deload_source AS deload_source
        FROM activities a
        JOIN exercises e ON e.activity_id = a.id
        JOIN exercise_sets es ON es.exercise_id = e.id AND {sql_set_confirmed('es.reps_pending')}
        LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
        WHERE {where_clause}
        ORDER BY a.local_date ASC, a.id ASC, es.set_number ASC
        """,
        tuple(params),
    )


def _build_progression_sessions(
    rows: List[Dict[str, Any]],
    failure_allowed: Optional[Dict[Any, bool]] = None,
    exposures: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Agrupa linhas de serie (ja ordenadas ASC por `_fetch_exercise_set_rows`)
    em sessoes (`activity_id`) - shape da tabela de `dashboard-progression`.

    Issue #188: carga, reps, RPE e e1RM da sessao sao o resumo de exposicao do
    DONO (`exposure_service.exposure_snapshot`) da exposicao lida para a
    sessao (`exposures`, a ultima da sessao vence) ou, se a sessao nao esta
    entre elas (deload, fora do teto, outro exercicio no filtro por nome),
    `exposure_service.snapshot_of_rows` sobre as series dela. Nenhuma conta
    aqui. `best_load_kg` = `top_load_kg` = carga de TOPO; `best_reps` = maior
    rep feita nela. Seta e marcacoes: `_attach_session_evolution`.

    So series `working` (aquecimento/ajuste/tecnica fora, como no dono);
    sessao sem serie working nao aparece. M97 F4: a sessao de DELOAD continua
    na tabela, marcada, mas FORA do e1RM (`e1rm`/`e1rm_reps` = None).
    `training_goal` = objetivo da ultima linha da sessao."""
    from .domain.deload import deload_source_label, deload_source_value, is_deload_session
    from .domain.form_practice import FORM_PRACTICE_COLUMN, counts_for_progression
    from .domain.training_goal import goal_value
    from .intelligence.dashboard.exposure_service import exposure_snapshot, snapshot_of_rows
    from .set_types import is_working_set

    exposicao_da_sessao = {
        int(item["activity_id"]): item for item in exposures or [] if item.get("activity_id") is not None
    }
    sets_by_activity: Dict[Any, List[Dict[str, Any]]] = {}
    for row in rows:
        if is_working_set(row):
            sets_by_activity.setdefault(row["activity_id"], []).append(row)

    results: List[Dict[str, Any]] = []
    for activity_id, sets in sets_by_activity.items():
        exposure = exposicao_da_sessao.get(int(activity_id)) if activity_id is not None else None
        # #196: o snapshot (carga de topo, reps, RPE, e1RM) so soma series que
        # contam para a progressao; `sets_count` e a intensidade seguem com todas.
        # Sem nenhuma que conte, o snapshot usa todas e o e1RM sai (abaixo).
        sets_progressao = [s for s in sets if counts_for_progression(s.get(FORM_PRACTICE_COLUMN))]
        snapshot = (
            exposure_snapshot(exposure)
            if exposure
            else snapshot_of_rows(sets_progressao or sets, sets[-1].get("mapping_id") or 0)
        ) or {}
        top_reps = snapshot.get("top_set_reps") or []

        # `intensity_status` (secao 5.7) da sessao: so quando o chamador
        # informou a falha permitida por equipamento (`failure_allowed`);
        # sem ele (None) o status nao e calculado.
        session_failure_allowed = (
            all((failure_allowed or {}).get(s.get("exercise_row_id"), False) for s in sets)
            if failure_allowed is not None
            else None
        )
        deload_source = deload_source_value(sets[-1].get("deload_source"))
        # Aprimoramento de tecnica (#196, ADR 0017): fora do e1RM, como o deload.
        e1rm_zerado_por_deload = is_deload_session(deload_source)
        fora_do_e1rm = e1rm_zerado_por_deload or not sets_progressao
        results.append(
            {
                "activity_id": activity_id,
                # Rotulo do ponto: nenhuma serie conta para a progressao por ser
                # execucao marcada (deload fica falso; ele tem `deload_source`).
                FORM_PRACTICE_COLUMN: not sets_progressao and not e1rm_zerado_por_deload,
                "local_date": sets[0].get("local_date"),
                "activity_name": sets[0].get("activity_name"),
                "training_goal": goal_value(sets[-1].get("training_goal")),
                "deload_source": deload_source,
                "deload_label": deload_source_label(deload_source),
                "sets_count": len(sets),
                "best_load_kg": snapshot.get("best_load_kg"),
                "best_reps": max(top_reps) if top_reps else None,
                "top_load_kg": snapshot.get("best_load_kg"),
                "top_set_reps": snapshot.get("top_set_reps"),
                "avg_rpe": snapshot.get("avg_rpe"),
                "e1rm": None if fora_do_e1rm else snapshot.get("e1rm"),
                # Acima do teto de reps o Epley nao decide nada na regra; o
                # dono (`e1rm_reliable`) marca o ponto.
                "e1rm_reps": None if fora_do_e1rm else snapshot.get("e1rm_reps"),
                "e1rm_reliable": False if fora_do_e1rm else bool(snapshot.get("e1rm_reliable")),
                "intensity_status": None
                if session_failure_allowed is None
                else effective_intensity.session_intensity_status(sets, failure_allowed=session_failure_allowed),
                "rpe_target_effective": next(
                    (
                        int(s["rpe_target_effective"])
                        for s in reversed(sets)
                        if s.get("rpe_target_effective") is not None
                    ),
                    None,
                ),
                "failure_allowed": session_failure_allowed,
                # #182: contagem pronta do dono; null sem nenhuma serie comparada.
                "rpe_vs_expected_counts": _rpe_counts_or_none(sets),
                # Rotulos das categorias do contador, do dono (frontend nao escreve texto).
                "rpe_vs_expected_labels": dict(effective_intensity.RPE_VS_LABELS),
            }
        )

    return results


def _rpe_counts_or_none(sets: List[Dict[str, Any]]) -> Optional[Dict[str, int]]:
    counts = effective_intensity.rpe_vs_expected_counts(sets)
    return counts if counts["compared"] else None


def _failure_allowed_for_rows(rows: List[Dict[str, Any]]) -> Optional[Dict[Any, bool]]:
    """`exercises.id` -> falha permitida naquele exercicio lancado, pela MESMA
    regra que grava o `safety_event` da serie
    (`effective_intensity.failure_allowed_by_logged_exercise`: prescricao
    congelada - ja composta com o contrato de recuperacao - + overlay; sem
    ficha, o par). M76 (C6): antes era so o equipamento. Leitura em lote.

    `None` = nao foi possivel responder com honestidade (consulta por NOME que
    casou mais de um exercicio, nenhum equipamento gravado, falha de leitura).
    O chamador entao NAO exibe `intensity_status`, em vez de exibir um status
    apoiado num palpite."""
    exercise_ids = {row.get("mapping_id") for row in rows if row.get("mapping_id")}
    if len(exercise_ids) != 1 or not any(row.get("gym_equipment_id") for row in rows):
        return None
    try:
        with _connect() as conn:
            resolved = effective_intensity.failure_allowed_by_logged_exercise(
                conn, [row.get("exercise_row_id") for row in rows]
            )
    except Exception:  # noqa: BLE001 - dashboard nunca 500 por causa do status
        logger.exception("Falha ao resolver falha permitida para o intensity_status")
        return None
    return resolved or None


def _custom_date_range(
    user_id: int, date_from: Optional[date], date_to: Optional[date]
) -> Optional[Tuple[date, date]]:
    """Janela `custom` das rotas v2 de `/dashboard/progressao` (M61).

    Sem nenhuma das datas -> `None`: a rota segue com `days` (contrato de
    sempre das janelas pre-definidas). Com as duas, valida pelas MESMAS regras
    da janela `custom` v3 (`time_windows.resolve_window`: ordem e teto de
    `MAX_WINDOW_DAYS`) e recusa fim no futuro no fuso do usuario (`_user_today`,
    o mesmo "hoje" de `comparison_windows`). Toda combinacao invalida e 400."""
    if date_from is None and date_to is None:
        return None
    if date_from is None or date_to is None:
        raise HTTPException(status_code=400, detail="Informe date_from e date_to juntos.")

    from .intelligence.dashboard.time_windows import resolve_window

    try:
        resolve_window("custom", today=date_to, date_from=date_from, date_to=date_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    today = _user_today(user_id)
    if date_to > today:
        raise HTTPException(
            status_code=400,
            detail=f"date_to ({date_to.isoformat()}) nao pode ser depois de hoje ({today.isoformat()}).",
        )
    return date_from, date_to


def _range_days(date_range: Tuple[date, date]) -> int:
    return (date_range[1] - date_range[0]).days + 1


def _range_echo(date_range: Optional[Tuple[date, date]]) -> Dict[str, Optional[str]]:
    """`date_from`/`date_to` ecoados no payload (None fora da janela custom)."""
    if date_range is None:
        return {"date_from": None, "date_to": None}
    return {"date_from": date_range[0].isoformat(), "date_to": date_range[1].isoformat()}


@router.get("/dashboard-progression")
def dashboard_progression(
    exercise_name: Optional[str] = Query(default=None),
    mapping_id: Optional[int] = Query(default=None),
    days: int = Query(default=180, ge=1, le=3650),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    goal: Optional[str] = Depends(optional_goal_query),
    identity: Identity = Depends(require_permission("progressao", "ver")),
):
    """Tabela Data|Carga|Reps|RPE|Sets|e1RM|Evolução de `/dashboard/progressao`
    (ProgressionClient.tsx, Fase 2): estende `exercise_progress` (acima)
    adicionando e1RM por sessao via `MuscleRecoveryService.estimated_1rm` -
    exige dado por SERIE (`_fetch_exercise_set_rows`), que `exercise_progress`
    nao expunha (so agregado por exercicio/dia).

    "Evolução" (seta) vem PRONTA em `items[].evolution`: o status de
    `comparability.compare_exposures` da sessao contra a exposicao
    imediatamente anterior, se for da mesma chave (exercicio canonico,
    equipamento, convencao de carga) - a MESMA regra da home e do drill-down
    (`exposure_service.consecutive_comparisons`). A anterior pode ser
    de antes da janela (contexto do veredito, #188, ate
    `VERDICT_LOOKBACK_EXPOSURES`): a primeira sessao do periodo tambem ganha
    seta. `None` = nenhuma anterior nem no contexto; `NOT_COMPARABLE` = a anterior foi em outro equipamento/
    convencao. O frontend so desenha.

    `date_from`/`date_to` (M61, opcionais e juntos): janela `custom` exata;
    `days` passa a ser o tamanho dela. Sem eles, `days` como sempre.

    `goal` (M97 F4, opcional): a tabela lista so as sessoes daquele objetivo
    e a seta compara as exposicoes dele. Ausente = todas as sessoes e a seta
    do objetivo default, como sempre. Sessao de deload vem marcada
    (`deload_source`), fora do e1RM e sem seta."""
    if not exercise_name and mapping_id is None:
        raise HTTPException(status_code=400, detail="Informe exercise_name ou mapping_id.")

    goal = optional_goal_param(goal)
    date_range = _custom_date_range(identity.user_id, date_from, date_to)
    days = _range_days(date_range) if date_range else _safe_days(days)
    rows = _fetch_exercise_set_rows(
        identity.user_id,
        days,
        exercise_name=exercise_name,
        mapping_id=mapping_id,
        date_range=date_range,
        training_goal=goal,
    )
    exposures = (
        _session_exposures(identity.user_id, mapping_id, exercise_name, days, date_range, training_goal=goal)
        if rows
        else []
    )
    items = _build_progression_sessions(rows, failure_allowed=_failure_allowed_for_rows(rows), exposures=exposures)
    _attach_session_evolution(items, exposures)

    return {
        "exercise_name": exercise_name,
        "mapping_id": mapping_id,
        "days": days,
        **_range_echo(date_range),
        "items": items,
    }


def _progression_exposures(
    user_id: int,
    canonical_exercise_id: int,
    days: int,
    date_range: Optional[Tuple[date, date]],
    limit: int,
    training_goal: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Exposicoes do exercicio na janela da tela, montadas pelo
    `exposure_service` (a MESMA leitura do drill-down
    `/analytics/dashboard/exercises/{id}/progress` e da home).

    Janela: a `custom` (M61) quando informada; senao `days` dias terminando
    HOJE no fuso do usuario - a mesma conta de `time_windows.resolve_window`
    para `7d`/`28d`/`90d`/`180d`, para o mesmo periodo dar o mesmo veredito
    nas duas telas."""
    from .intelligence.dashboard.exposure_service import get_exposures_between
    from .intelligence.dashboard.time_windows import user_timezone, user_today

    with _connect() as conn:
        if date_range is None:
            today = user_today(user_timezone(conn, user_id))
            date_range = (today - timedelta(days=days - 1), today)
        return get_exposures_between(
            conn,
            canonical_exercise_id,
            user_id=user_id,
            date_from=date_range[0],
            date_to=date_range[1],
            limit=limit,
            training_goal=training_goal,
        )


def _canonical_for_progression(mapping_id: Optional[int], exercise_name: Optional[str]) -> Optional[int]:
    """`mapping_id` (o que a tela manda) ou, so com `exercise_name`, o id do
    catalogo pelo mesmo resolvedor de `dashboard-progression-suggestion`."""
    if mapping_id is not None:
        return int(mapping_id)
    return _resolve_mapping_id_by_name(exercise_name) if exercise_name else None


def _session_exposures(
    user_id: int,
    mapping_id: Optional[int],
    exercise_name: Optional[str],
    days: int,
    date_range: Optional[Tuple[date, date]],
    training_goal: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Exposicoes do dono para a tabela (teto `MAX_TABLE_EXPOSURES`), lidas UMA
    vez e usadas nos numeros da sessao e na seta. Falha na leitura nao derruba
    a tabela: lista vazia (sem seta nem marcacao) e o erro vai para o log."""
    from .intelligence.dashboard.exposure_service import MAX_TABLE_EXPOSURES

    canonical = _canonical_for_progression(mapping_id, exercise_name)
    if canonical is None:
        return []
    try:
        return _progression_exposures(
            user_id, canonical, days, date_range, MAX_TABLE_EXPOSURES, training_goal=training_goal
        )
    except Exception:  # noqa: BLE001 - dashboard nunca 500 por causa da seta
        logger.exception("Falha ao calcular a evolucao por sessao da progressao")
        return []


def _attach_session_evolution(items: List[Dict[str, Any]], exposures: List[Dict[str, Any]]) -> None:
    """Preenche `evolution` e as marcacoes do dono de cada sessao da tabela.

    A tabela agrupa por SESSAO (`activity_id`); a regra compara EXPOSICOES.
    Quando o exercicio aparece duas vezes na mesma sessao, a seta da sessao e
    a da ULTIMA exposicao dela. Sessao sem exposicao correspondente fica sem
    seta e sem marcacoes (None) - nunca inventadas. Os numeros da sessao ja
    vem do dono (`_build_progression_sessions`); nada e sobrescrito aqui."""
    from .intelligence.dashboard.exposure_service import consecutive_comparisons, verdict_run_flags

    for item in items:
        item["evolution"] = None
        item["in_verdict_key"] = None
        item["in_context_run"] = None
        item["in_verdict_run"] = None
        item["fatigue_context"] = None
    if not items or not exposures:
        return

    # Marcacoes do dono (`exposure_service.verdict_run_flags`) sobre as
    # ultimas `comparability.VERDICT_LOOKBACK_EXPOSURES` (a janela do veredito
    # que `progression_verdict` aplica sobre a lista desta rota); as sessoes
    # mais antigas ficam com as duas falsas.
    # `in_context_run` = o grafico plota; `in_verdict_run` = o grafico destaca.
    por_sessao: Dict[int, Optional[Dict[str, Any]]] = {}
    exposicao_da_sessao: Dict[int, Dict[str, Any]] = {}
    no_veredito: Dict[int, Dict[str, bool]] = {}
    for exposure, evolution, in_run in zip(
        exposures, consecutive_comparisons(exposures), verdict_run_flags(exposures)
    ):
        if exposure.get("activity_id") is not None:
            por_sessao[int(exposure["activity_id"])] = evolution  # a ultima da sessao vence
            exposicao_da_sessao[int(exposure["activity_id"])] = exposure
            no_veredito[int(exposure["activity_id"])] = in_run
    for item in items:
        try:
            activity_id = int(item.get("activity_id"))
        except (TypeError, ValueError):
            continue
        item["evolution"] = por_sessao.get(activity_id)
        exposure = exposicao_da_sessao.get(activity_id)
        if exposure is None:
            continue
        # Marcacoes do dono; `in_verdict_key` = `in_verdict_run` (nome antigo,
        # compatibilidade com o frontend anterior).
        item.update(no_veredito[activity_id])
        item["in_verdict_key"] = no_veredito[activity_id]["in_verdict_run"]
        item["fatigue_context"] = exposure.get("fatigue_context")


def _fetch_group_tonnage_rows(
    user_id: int, windows: Dict[str, date], load_convention_expr: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Tonelagem por grupo muscular nas duas janelas de `comparison_windows`
    (mesmo tamanho, datas locais do usuario - M50).

    Entram as MESMAS series do volume por grupo (Fase 5, `technique_volume`):
    tudo menos aquecimento/ajuste (`_ES_NOT_PREPARATORY`) - trabalho (NULL/
    vazio = working), backoff e as aplicacoes de tecnica (mini-bloco de
    rest-pause, drop, reps forcadas). Ate aqui o filtro era `= 'working'`:
    desde que o Modo Treino passou a gravar cada aplicacao como linha propria,
    o trabalho feito em tecnica sumia da janela atual e a comparacao com uma
    janela anterior sem tecnica acusava queda. A tonelagem da aplicacao e
    reps x carga reais, entao ja e proporcional ao que foi feito - nao precisa
    da fracao de serie de `technique_accounting`.

    Cada serie contribui reps x carga x `stimulus_weight` de cada grupo do
    exercicio em `exercise_muscle_contributions` (mesmo mapeamento canonico de
    `_fetch_exercise_set_rows`). `sets_count` conta so as series que NAO sao
    aplicacao de tecnica (`sql_work_unit`): um rest-pause de 2 mini-blocos
    continua sendo 1 serie. `sessions_count` conta as sessoes em que o grupo
    recebeu tonelagem > 0 - e o denominador da comparacao por sessao.

    M58 - convencao de carga: series de par equipamento<->exercicio
    `assisted`/`bodyweight` NAO entram na tonelagem (o numero gravado nao e a
    carga movida), pela MESMA regra do relatorio Markdown
    (`load_conventions.sql_counts_in_tonnage`; NULL = `total`, conta). Como
    `sessions_count` so conta sessao com tonelagem > 0, uma sessao so de
    graviton deixa de contar no denominador. `sets_count` continua contando
    TODAS as series (e volume, nao carga); `tonnage_excluded_sets` declara
    quantas ficaram fora. `load_convention_expr` = saida de
    `_load_convention_expr()` (a rota detecta uma vez e passa as duas leituras).
    Sem migracao: so leitura."""
    from .intelligence.technique_volume import sql_work_unit
    from .load_conventions import equipment_exercises_join_sql, sql_counts_in_tonnage

    lc = load_convention_expr if load_convention_expr is not None else _load_convention_expr()
    canonical = "COALESCE(elm.canonical_exercise_id, e.mapping_id)"
    counts = sql_counts_in_tonnage(lc)
    weighted = "COALESCE(es.reps, 0) * COALESCE(es.weight_kg, 0) * COALESCE(emc.stimulus_weight, 0)"
    work_unit = sql_work_unit("es.set_type")
    return _fetch_all(
        f"""
        SELECT
            CASE WHEN a.local_date >= %s THEN 'current' ELSE 'previous' END AS period,
            emc.muscle_group_id AS muscle_group_id,
            mg.name AS muscle_group_name,
            SUM(CASE WHEN {counts} THEN {weighted} ELSE 0 END) AS tonnage_kg,
            SUM({work_unit}) AS sets_count,
            SUM(CASE WHEN NOT ({counts}) THEN {work_unit} ELSE 0 END) AS tonnage_excluded_sets,
            COUNT(DISTINCT CASE
                WHEN {counts} AND {weighted} > 0
                THEN a.id END) AS sessions_count
        FROM activities a
        JOIN exercises e ON e.activity_id = a.id
        JOIN exercise_sets es ON es.exercise_id = e.id AND {sql_set_confirmed('es.reps_pending')}
        LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
        LEFT JOIN gym_equipment ge ON ge.id = e.equipment_id
        {equipment_exercises_join_sql(canonical_expr=canonical)}
        JOIN exercise_muscle_contributions emc
          ON emc.exercise_id = {canonical} AND emc.is_active = 1
        JOIN muscle_groups mg ON mg.id = emc.muscle_group_id
        WHERE a.user_id = %s
          AND a.local_date >= %s
          AND a.local_date <= %s
          AND {_ES_NOT_PREPARATORY}
        GROUP BY period, emc.muscle_group_id, mg.name
        """,
        (windows["current_from"], user_id, windows["previous_from"], windows["current_to"]),
    )


def _load_convention_expr() -> str:
    """`eqex.load_convention` quando a migracao 045 ja rodou, senao `NULL`
    (= `total`) - mesma deteccao do `exposure_service`."""
    from .load_conventions import load_convention_select_expr

    with _connect() as conn:
        with conn.cursor() as cursor:
            return load_convention_select_expr(cursor)


def _fetch_group_e1rm_rows(
    user_id: int, windows: Dict[str, date], load_convention_expr: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Melhor e1RM por exercicio nas duas janelas de `comparison_windows`, ja
    cruzado com os grupos do exercicio - base da coluna "Desempenho"
    (`intelligence/muscle_group_performance.py`).

    e1RM pelo dono (`domain/e1rm.reliable_e1rm`, M76 Lote 3a) com a politica
    `MissingRpePolicy.RIR_ZERO` - decisao do usuario (2026-10-06, meio-termo):
    RIR = 10 - RPE quando a serie tem RPE, RIR 0 quando nao tem (series sem
    RPE continuam contando, como na decisao de 2026-10-02), e so series com
    ate `E1RM_MAX_RELIABLE_REPS` (12) reps - acima disso o Epley nao e
    confiavel (mesmo teto da regra unica). Ate o Lote 3a a formula era
    reescrita aqui em SQL (`_SQL_RIR`); agora o SQL so traz as colunas cruas.

    Uma consulta, duas partes (`UNION ALL`), para o payload da Data API (teto
    de 1 MB) nao crescer:
    - `row_kind = 'set'`: MAIOR carga por (janela, exercicio canonico,
      equipamento canonico, convencao, reps, RPE). Reduzir pela carga maxima
      em cada (reps, RPE) nao depende da formula - com reps e RPE fixos, o
      e1RM cresce com a carga -, entao a regra continua inteira no dono.
    - `row_kind = 'contribution'`: grupos (`stimulus_weight`) dos exercicios
      da janela. Separados porque cruzar cada (reps, RPE) com cada grupo
      multiplicava as linhas (medido em 2026-10-07, usuario 1, janela de 90
      dias: 487 linhas na consulta antiga, 2.188 cruzando com os grupos, 658
      sem eles).

    Equipamento e convencao saem dos MESMOS joins do `exposure_service`
    (`gym_equipment.equipment_id`, nunca o id por academia;
    `equipment_exercises.load_convention`) porque a chave de comparabilidade
    e montada a partir deles: troca de maquina nao vira "piora". So series de
    TRABALHO (`sql_is_working`): aquecimento/ajuste, aplicacoes de tecnica e
    backoff nao medem forca maxima. Sem migracao: so leitura.

    Devolve uma linha por (janela, exercicio canonico, equipamento canonico,
    convencao de carga, academia, tipo de equipamento, grupo) com
    `best_e1rm_kg`, `gym_id` e `equipment_type_code` (mesmos joins do
    `exposure_service`) - o contrato de `comparability.group_e1rm_changes`
    (academia na chave so para maquina)."""
    from .domain.deload import sql_not_deload
    from .domain.e1rm import MissingRpePolicy, reliable_e1rm, reliable_reps_sql
    from .domain.form_practice import sql_counts_for_progression
    from .set_types import sql_is_working

    lc = load_convention_expr if load_convention_expr is not None else _load_convention_expr()
    canonical = "COALESCE(elm.canonical_exercise_id, e.mapping_id)"
    # `NULL` literal nao entra no GROUP BY (constante; a chave vira `total`).
    group_lc = "" if lc.strip().upper() == "NULL" else f", {lc}"
    rows = _fetch_all(
        f"""
        SELECT
            'set' AS row_kind,
            CASE WHEN a.local_date >= %s THEN 'current' ELSE 'previous' END AS period,
            {canonical} AS exercise_id,
            MAX(e.original_name) AS exercise_name,
            ge.equipment_id AS equipment_id,
            {lc} AS load_convention,
            ge.gym_id AS gym_id,
            et.code AS equipment_type_code,
            NULL AS muscle_group_id,
            NULL AS stimulus_weight,
            es.reps AS reps,
            es.rpe AS rpe,
            MAX(es.weight_kg) AS top_weight_kg
        FROM activities a
        JOIN exercises e ON e.activity_id = a.id
        JOIN exercise_sets es ON es.exercise_id = e.id AND {sql_set_confirmed('es.reps_pending')}
        LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
        LEFT JOIN gym_equipment ge ON ge.id = e.equipment_id
        LEFT JOIN equipment eq ON eq.id = ge.equipment_id
        LEFT JOIN equipment_types et ON et.id = eq.equipment_type_id
        LEFT JOIN equipment_exercises eqex
          ON eqex.equipment_id = ge.equipment_id
         AND eqex.exercise_id = {canonical}
         AND eqex.is_active = 1
        WHERE a.user_id = %s
          AND a.local_date >= %s
          AND a.local_date <= %s
          AND {sql_not_deload("a")}
          AND {sql_counts_for_progression("e")}
          AND {sql_is_working("es.set_type")}
          AND {sql_is_rep_based("es.measure")}
          AND {reliable_reps_sql("es.reps")}
          AND es.weight_kg > 0
        GROUP BY period, {canonical}, ge.equipment_id{group_lc}, ge.gym_id, et.code, es.reps, es.rpe
        UNION ALL
        SELECT
            'contribution' AS row_kind,
            NULL AS period,
            emc.exercise_id AS exercise_id,
            NULL AS exercise_name,
            NULL AS equipment_id,
            NULL AS load_convention,
            NULL AS gym_id,
            NULL AS equipment_type_code,
            emc.muscle_group_id AS muscle_group_id,
            MAX(emc.stimulus_weight) AS stimulus_weight,
            NULL AS reps,
            NULL AS rpe,
            NULL AS top_weight_kg
        FROM exercise_muscle_contributions emc
        WHERE emc.is_active = 1
          AND emc.exercise_id IN (
              SELECT {canonical}
              FROM activities a
              JOIN exercises e ON e.activity_id = a.id
              LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
              WHERE a.user_id = %s
                AND a.local_date >= %s
                AND a.local_date <= %s
          )
        GROUP BY emc.exercise_id, emc.muscle_group_id
        """,
        (
            windows["current_from"],
            user_id,
            windows["previous_from"],
            windows["current_to"],
            user_id,
            windows["previous_from"],
            windows["current_to"],
        ),
    )

    groups_by_exercise: Dict[int, Dict[int, float]] = {}
    best_by_key: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for row in rows:
        if row.get("exercise_id") is None:
            continue
        exercise_id = int(row["exercise_id"])
        if row.get("row_kind") == "contribution":
            if row.get("muscle_group_id") is None:
                continue
            groups = groups_by_exercise.setdefault(exercise_id, {})
            group_id = int(row["muscle_group_id"])
            weight = float(row["stimulus_weight"] or 0.0)
            groups[group_id] = max(groups.get(group_id, weight), weight)
            continue
        value = reliable_e1rm(
            row.get("top_weight_kg"), row.get("reps"), row.get("rpe"), missing_rpe=MissingRpePolicy.RIR_ZERO
        )
        if value is None:
            continue
        equipment_id = None if row.get("equipment_id") is None else int(row["equipment_id"])
        gym_id = None if row.get("gym_id") is None else int(row["gym_id"])
        key = (row["period"], exercise_id, equipment_id, row.get("load_convention"), gym_id, row.get("equipment_type_code"))
        entry = best_by_key.get(key)
        if entry is None:
            best_by_key[key] = {"best": value, "name": row.get("exercise_name")}
        else:
            entry["best"] = max(entry["best"], value)
            entry["name"] = max(filter(None, (entry["name"], row.get("exercise_name"))), default=None)

    result: List[Dict[str, Any]] = []
    for (period, exercise_id, equipment_id, load_convention, gym_id, type_code), entry in best_by_key.items():
        for group_id, stimulus_weight in groups_by_exercise.get(exercise_id, {}).items():
            result.append(
                {
                    "period": period,
                    "exercise_id": exercise_id,
                    "exercise_name": entry["name"],
                    "equipment_id": equipment_id,
                    "load_convention": load_convention,
                    "gym_id": gym_id,
                    "equipment_type_code": type_code,
                    "muscle_group_id": group_id,
                    "stimulus_weight": stimulus_weight,
                    "best_e1rm_kg": entry["best"],
                }
            )
    return result


def _fetch_exercise_group_weights(user_id: int, windows: Dict[str, date]) -> Dict[int, Dict[int, float]]:
    """Participacao muscular dos exercicios do periodo: `{grupo: {exercicio
    canonico: stimulus_weight}}` (`exercise_muscle_contributions`, o maior
    peso por par). So leitura; os pesos sao os que o dono ja usa - a agregacao
    por grupo e `comparability.aggregate_group_progression`."""
    canonical = "COALESCE(elm.canonical_exercise_id, e.mapping_id)"
    rows = _fetch_all(
        f"""
        SELECT emc.exercise_id AS exercise_id,
               emc.muscle_group_id AS muscle_group_id,
               MAX(emc.stimulus_weight) AS stimulus_weight
        FROM exercise_muscle_contributions emc
        WHERE emc.is_active = 1
          AND emc.exercise_id IN (
              SELECT {canonical}
              FROM activities a
              JOIN exercises e ON e.activity_id = a.id
              LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
              WHERE a.user_id = %s
                AND a.local_date >= %s
                AND a.local_date <= %s
          )
        GROUP BY emc.exercise_id, emc.muscle_group_id
        """,
        (user_id, windows["current_from"], windows["current_to"]),
    )
    result: Dict[int, Dict[int, float]] = {}
    for row in rows:
        if row.get("exercise_id") is None or row.get("muscle_group_id") is None:
            continue
        result.setdefault(int(row["muscle_group_id"]), {})[int(row["exercise_id"])] = float(
            row.get("stimulus_weight") or 0.0
        )
    return result


def _exercise_progression_items(
    user_id: int, windows: Dict[str, date], goal: Any, timezone_name: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], bool]:
    """Status de TODOS os exercicios da janela ATUAL, pela MESMA chamada da home
    (`exposure_service.get_exercises_progression_batch`, mesmo objetivo e mesmas
    datas de `current_from`..`current_to`). Com `days=28` terminando hoje (a
    janela padrao da home) os itens sao identicos aos dela."""
    from .intelligence.dashboard.contracts import WindowInfo, WindowKind
    from .intelligence.dashboard.exposure_service import get_exercises_progression_batch
    from .timezones import DEFAULT_TIMEZONE

    # O lote so le `date_from`/`date_to` da janela: o fuso nao muda o resultado,
    # entao nao se releva `users.timezone` (quem ja o resolveu passa).
    with _connect() as conn:
        window = WindowInfo(
            kind=WindowKind.CUSTOM,
            date_from=windows["current_from"],
            date_to=windows["current_to"],
            timezone=timezone_name or DEFAULT_TIMEZONE,
            days=(windows["current_to"] - windows["current_from"]).days + 1,
        )
        items, truncated, _limit = get_exercises_progression_batch(
            conn, user_id=user_id, window=window, training_goal=goal
        )
    return items, bool(truncated)


def _group_phases(user_id: int, training_goal: Any = None) -> Dict[int, Dict[str, Any]]:
    """Fase por grupo NO OBJETIVO pedido (M97 F1; ausente = default) -
    informacao secundaria. Tabela ausente/erro -> {}."""
    from .domain.training_goal import goal_value, sql_goal_is
    from .progression_state import GROUP_STATES_TABLE

    try:
        rows = _fetch_all(
            f"SELECT muscle_group_id, phase, phase_status FROM {GROUP_STATES_TABLE} "
            f"WHERE user_id = %s AND {sql_goal_is(GROUP_STATES_TABLE)}",
            (user_id, goal_value(training_goal)),
        )
    except Exception:  # noqa: BLE001 - fase e opcional; nunca derruba o dashboard
        logger.warning("Fase dos grupos indisponivel para o dashboard de progressao", exc_info=True)
        return {}
    return {int(r["muscle_group_id"]): r for r in rows}


@router.get("/dashboard-progression-by-muscle-group")
def dashboard_progression_by_muscle_group(
    days: int = Query(default=30, ge=1, le=1825),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    window: Optional[str] = Query(
        default=None,
        description="Janela atual como no overview (7d, 28d, 90d, 180d, cycle, custom). "
        "Presente, vence days/date_from/date_to (custom exige date_from e date_to).",
    ),
    goal: str = Depends(goal_query),
    identity: Identity = Depends(require_permission("progressao", "ver")),
):
    """Progressao de TODOS os grupos musculares de uma vez (M7): tonelagem
    ponderada por contribuicao muscular, periodo atual x periodo anterior de
    mesmo tamanho, classificada pela tonelagem POR SESSAO (M50 - a janela
    atual termina hoje e nao pode "regredir" so por ter menos sessoes).
    Desde 2026-10-02 cada grupo tem DUAS dimensoes: volume (`volume_status`,
    rotulo neutro) e desempenho (`performance_status`, melhor e1RM do mesmo
    exercicio). Regras em `intelligence/muscle_group_progression.py` e
    `intelligence/muscle_group_performance.py`.

    Contagem unica de progressao (2026-11-01): o campo de topo
    `exercise_progression` traz a contagem por status de EXERCICIO e a frase
    "Estou evoluindo?" da home, calculadas pelo dono (`comparability`) sobre a
    MESMA chamada `get_exercises_progression_batch`, no mesmo objetivo e na
    janela ATUAL desta rota (`current_from`..`current_to`; `days` ou
    `date_from`/`date_to`). Com `days=28` (a janela padrao da home) os numeros
    sao identicos aos da home. `window` (como no overview, inclusive `cycle`)
    vence `days`/`date_from`/`date_to`. O `performance_status` de cada grupo e a
    agregacao ponderada desses mesmos status.

    Escopo das leituras (issue #152), chaves de topo, todas descritores de
    `domain/progression_status_labels.PROGRESSION_SCOPES` (`code`, `entity`,
    `metric`, `comparator`, `label`, `description`): `performance_status_scope`
    (status de desempenho do grupo), `volume_status_scope` (`volume_status`),
    `performance_change_scope` (`performance_change_pct` e `change_pct` de
    `performance_exercises`) e `exercise_status_scope` (o `status` de cada item
    de `performance_exercises`, status do EXERCICIO). `metric_versions` traz as
    versoes de regra (status, tonelagem, e1RM). As janelas sao
    `current_from`/`current_to`/`previous_from`/`previous_to`."""
    from .domain.progression_status_labels import (
        SCOPE_E1RM_WINDOWS,
        SCOPE_EXERCISE_LAST_VS_PREVIOUS,
        SCOPE_GROUP_VOLUME_WINDOWS,
        SCOPE_GROUP_WEIGHTED_EXERCISES,
        progression_scope,
    )
    from .intelligence.dashboard.comparability import exercise_progression_summary
    from .intelligence.dashboard.metrics_registry import metric_version
    from .intelligence.dashboard.contracts import ExerciseProgressionSummaryOut
    from .intelligence.muscle_group_performance import (
        PERFORMANCE_BASIS,
        STATUS_BASIS,
        build_group_performance,
        mark_progression_unavailable,
    )
    from .intelligence.muscle_group_progression import (
        COMPARISON_BASIS,
        MIN_SESSIONS_PER_WINDOW,
        STABLE_BAND_PCT,
        build_group_progression,
        comparison_windows,
        range_comparison_windows,
    )

    # M61: com `date_from`/`date_to` a janela atual e a escolhida e a anterior
    # e o periodo de mesmo tamanho logo antes dela; sem eles, `days` terminando
    # hoje como sempre. A regra de classificacao (M50) e a mesma nos dois casos
    # - ver o docstring de `muscle_group_progression`.
    timezone_name: Optional[str] = None
    if window:
        # Contagem unica: a janela atual e resolvida EXATAMENTE como no overview
        # (`analytics_dashboard._resolve`: ciclo do Modelo, mesmos valores e 400
        # nas mesmas combinacoes); a anterior tem a mesma duracao, logo antes.
        from .analytics_dashboard import _resolve
        from .intelligence.dashboard.time_windows import user_timezone

        with _connect() as conn:
            timezone_name = user_timezone(conn, identity.user_id)
        resolved = _resolve(window, date_from, date_to, timezone_name, identity.user_id)
        windows = range_comparison_windows(resolved.date_from, resolved.date_to)
        days = resolved.days
    else:
        date_range = _custom_date_range(identity.user_id, date_from, date_to)
        if date_range is not None:
            windows = range_comparison_windows(*date_range)
            days = _range_days(date_range)
        else:
            days = _safe_days(days)
            windows = comparison_windows(_user_today(identity.user_id), days)
    # Convencao de carga detectada UMA vez para as duas leituras (M58).
    load_convention = _load_convention_expr()
    rows = _fetch_group_tonnage_rows(identity.user_id, windows, load_convention)
    # Status do grupo = agregacao (dono) dos status dos exercicios; o e1RM
    # entra so como metrica informativa. Os mesmos itens alimentam o bloco
    # `exercise_progression` (contagem + frase identicas as da home).
    # Falha do lote de progressao NAO derruba o painel de tonelagem: sem os
    # status, `exercise_progression` = null e os grupos ficam DADOS_INSUFICIENTES.
    exercise_items: Optional[List[Dict[str, Any]]]
    truncated = False
    try:
        exercise_items, truncated = _exercise_progression_items(identity.user_id, windows, goal, timezone_name)
        group_weights = _fetch_exercise_group_weights(identity.user_id, windows)
    except Exception:  # noqa: BLE001 - degradacao declarada
        logger.exception("Lote de progressao indisponivel em dashboard-progression-by-muscle-group")
        log_event("progression_batch_degraded", user_id=identity.user_id, route="dashboard-progression-by-muscle-group")
        exercise_items, group_weights = None, {}
    performance = build_group_performance(
        _fetch_group_e1rm_rows(identity.user_id, windows, load_convention),
        exercise_items=exercise_items,
        group_weights=group_weights,
    )
    # M97 F1: a fase (secundaria) e a do objetivo `goal`; tonelagem (volume) e
    # e1RM (unico entre objetivos) nao separam objetivo nesta fase.
    items = build_group_progression(rows, _group_phases(identity.user_id, goal), performance)
    degraded = exercise_items is None
    if degraded:
        mark_progression_unavailable(items)
    return {
        "days": days,
        "degraded": degraded,
        "stable_band_pct": STABLE_BAND_PCT,
        "min_sessions_per_window": MIN_SESSIONS_PER_WINDOW,
        "comparison_basis": COMPARISON_BASIS,
        "performance_basis": PERFORMANCE_BASIS,
        "performance_status_basis": STATUS_BASIS,
        # Issue #152: o que cada leitura compara (valem para todos os itens).
        "performance_status_scope": progression_scope(SCOPE_GROUP_WEIGHTED_EXERCISES),
        "volume_status_scope": progression_scope(SCOPE_GROUP_VOLUME_WINDOWS),
        "performance_change_scope": progression_scope(SCOPE_E1RM_WINDOWS),
        "exercise_status_scope": progression_scope(SCOPE_EXERCISE_LAST_VS_PREVIOUS),
        "metric_versions": {
            "exercise_progression_status": metric_version("exercise_progression_status"),
            "tonnage": metric_version("tonnage"),
            "e1rm": metric_version("e1rm"),
        },
        **{key: value.isoformat() for key, value in windows.items()},
        "exercise_progression": None
        if exercise_items is None
        else ExerciseProgressionSummaryOut.model_validate(
            exercise_progression_summary(
                exercise_items,
                window_days=days,
                period_start=windows["current_from"],
                period_end=windows["current_to"],
                truncated=truncated,
            )
        ).model_dump(mode="json"),
        "items": items,
    }


@router.get("/dashboard-progression-trend")
def dashboard_progression_trend(
    exercise_name: Optional[str] = Query(default=None),
    mapping_id: Optional[int] = Query(default=None),
    days: int = Query(default=180, ge=1, le=3650),
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    goal: str = Depends(goal_query),
    identity: Identity = Depends(require_permission("progressao", "ver")),
):
    """Veredito de `/dashboard/progressao` - a MESMA regra da home e do
    drill-down: `classify_exercise_progression` sobre as exposicoes do
    `exposure_service` (teto `MAX_EXPOSURES`, como o drill-down), para o mesmo
    exercicio e periodo darem o mesmo status nas tres telas.

    `status`: enum v3 (`PROGRESSING`, `STABLE`, `PLATEAU_SUSPECTED`,
    `REGRESSING`, `INSUFFICIENT_DATA`, `NOT_COMPARABLE`); `status_reasons`:
    codigos `comparability.REASON_*`; `equipment_name`/`load_convention`: em
    que chave a comparacao foi feita (a da ultima exposicao).

    Ate 2026-10-06 esta rota usava `classify_progression_trend` (e1RM contra a
    MEDIA das sessoes, sem chave de equipamento) e divergia da home.
    `status_scope` (issue #152): descritor do que `status` compara (ultima
    exposicao vs. anterior), vindo de `progression_verdict`.

    O vocabulario antigo (`classification`/`reason`) foi removido: so `status`."""
    from .intelligence.dashboard.exposure_service import MAX_EXPOSURES, progression_verdict

    if not exercise_name and mapping_id is None:
        raise HTTPException(status_code=400, detail="Informe exercise_name ou mapping_id.")

    date_range = _custom_date_range(identity.user_id, date_from, date_to)
    days = _range_days(date_range) if date_range else _safe_days(days)
    canonical = _canonical_for_progression(mapping_id, exercise_name)
    exposures = (
        _progression_exposures(identity.user_id, canonical, days, date_range, MAX_EXPOSURES, training_goal=goal)
        if canonical is not None
        else []
    )
    verdict = progression_verdict(exposures)

    return {
        "exercise_name": exercise_name,
        "mapping_id": mapping_id,
        "days": days,
        **_range_echo(date_range),
        **verdict,
    }


def _resolve_mapping_id_by_name(exercise_name: str) -> Optional[int]:
    """Resolve `exercise_name` (texto livre) para um `mapping_id` canonico
    de `exercise_catalog` - usado SOMENTE quando `dashboard-progression-suggestion`
    recebe `exercise_name` sem `mapping_id`. Catalogo e GLOBAL (sem
    `user_id`), mesma tabela ja consultada por `services.suggested_progression_for_mapping`."""
    like = f"%{exercise_name.lower()}%"
    row = _fetch_one(
        "SELECT id FROM exercise_catalog WHERE LOWER(name) LIKE %s ORDER BY id ASC LIMIT 1",
        (like,),
    )
    return row.get("id") if row else None


@router.get("/dashboard-progression-suggestion")
def dashboard_progression_suggestion(
    exercise_name: Optional[str] = Query(default=None),
    mapping_id: Optional[int] = Query(default=None),
    goal: Optional[str] = Depends(optional_goal_query),
    identity: Identity = Depends(require_permission("progressao", "ver")),
):
    """Card "Sugestao para a proxima sessao" de `/dashboard/progressao`.

    M76 (Lote 1a): a decisao e do motor v2 (`services.suggested_progression_for_mapping`
    -> `domain/prescription_service`), a MESMA de `GET /exercises/suggested-progression`
    e da geracao de ficha - nunca reimplementada aqui.

    `goal` (M97, opcional): a sugestao na trilha/fase/escada daquele objetivo
    (decisao do motor); a resposta ecoa `training_goal`. Ausente (ou chamada
    direta da funcao sem `goal`) = o objetivo default, a resposta de sempre,
    sem campo novo."""
    from . import services

    goal = optional_goal_param(goal)

    resolved_mapping_id = mapping_id
    if resolved_mapping_id is None:
        if not exercise_name:
            raise HTTPException(status_code=400, detail="Informe exercise_name ou mapping_id.")
        resolved_mapping_id = _resolve_mapping_id_by_name(exercise_name)
        if resolved_mapping_id is None:
            raise HTTPException(status_code=404, detail="Exercício não encontrado no catálogo canônico.")

    result = services.suggested_progression_for_mapping(
        resolved_mapping_id, user_id=identity.user_id, training_goal=goal
    )
    if not result:
        raise HTTPException(status_code=404, detail="Mapeamento de exercício não encontrado.")

    if goal is None:
        return {"mapping_id": resolved_mapping_id, **result}
    return {"mapping_id": resolved_mapping_id, **result, "training_goal": goal}


@router.get("/dashboard-workout-sessions")
def dashboard_workout_sessions(
    days: int = Query(default=90, ge=1, le=3650),
    limit: int = Query(default=200, ge=1, le=1000),
    identity: Identity = Depends(require_permission("dashboards", "ver")),
):
    """Timeline de sessoes (`activities`) com series equivalentes de
    estimulo/fadiga por sessao - gap real confirmado por leitura de
    `WorkoutsClient.tsx` e dos endpoints que ele consome (`training-evolution`,
    `training-trend`, `rpe-distribution`, `training-volume-by-split-day`,
    `muscle-groups-daily-activation`, `muscle-recovery-consecutive-check`):
    nenhum deles expoe uma timeline por SESSAO individual nem permite
    comparar sessoes do mesmo "slot" (mesmo `name`) ao longo do tempo. Usado
    por `/dashboard/treinos` (TreinosHistoryClient, hub novo - NAO substitui
    `/treinos` antigo) e por `WorkoutComparisonTable` (agrupamento por
    `name` feito no FRONTEND, ver `VolumeService.get_workout_sessions_timeline`)."""
    from .intelligence.volume_service import VolumeService

    days = _safe_days(days)
    volume_service = VolumeService()
    items = volume_service.get_workout_sessions_timeline(user_id=identity.user_id, days=days, limit=limit)

    return {
        "days": days,
        "items": items,
    }
# END dashboard novo (hub paralelo /dashboard/*, Fase 2)

# BEGIN dashboard novo (hub paralelo /dashboard/*, Fase 3)
# Ver plano completo em
# C:\Users\dougb\.claude\plans\c-users-dougb-claude-uploads-c8154714-b-happy-perlis.md.
# `/dashboard/recovery` + `/dashboard/ciclo` (novos, hub paralelo - nao
# tocam em `/recuperacao-muscular` nem no builder de Modelos de Ficha
# existentes).

@router.get("/dashboard-recovery")
def dashboard_recovery(
    days: int = Query(default=14, ge=1, le=3650),
    identity: Identity = Depends(require_permission("dashboards", "ver")),
):
    """Quase 1:1 sobre `MuscleRecoveryService.get_recovery_overview` (JA
    calcula tudo que `/dashboard/recovery` (RecoveryClient.tsx) precisa:
    `session_dose`, `hours_since_trained`/`days_since_trained`,
    `recovery_pct`, `estimated_recovery_hours`, `remaining_hours`, `status`
    (green/yellow/red), `guidance`, `readiness_status`, `recommendation`,
    `explanation`, `last_trained_at`/`last_direct_training_at`/
    `last_participation_at`) - MESMA fonte de `/analytics/muscle-recovery`
    (tela antiga `/recuperacao-muscular`), nunca duplicada, so filtrada por
    `identity.user_id` e envelopada no formato `{"days", "items"}` do
    dashboard novo. O antigo acrescimo `recovery_phase`
    (TREINADO/RECUPERANDO/READY, que chamava de READY um grupo ainda em
    NO_DIRECT_WORK) saiu no M76 Lote 2c: o estado do grupo e o
    `readiness_status` do modelo, sem segunda classificacao."""
    from .intelligence.muscle_recovery_service import MuscleRecoveryService

    days = _safe_days(days)
    items = MuscleRecoveryService().get_recovery_overview(days=days, limit=100, user_id=identity.user_id)

    return {
        "days": days,
        "items": items,
    }


def _resolve_latest_assigned_template_id(user_id: int) -> Optional[int]:
    """`template_id` do Modelo de Ficha VIGENTE do usuario.

    Ate a migracao 049 esta funcao era `ORDER BY assigned_at DESC LIMIT 1`
    sobre uma tabela que permitia N modelos atribuidos ao mesmo tempo: o
    `LIMIT 1` desempatava em silencio. Depois da 049 existe no maximo UMA
    atribuicao vigente por usuario (UNIQUE em `active_user_id`), e a resposta
    passa a vir de `template_assignments.resolve_active_template_id` - o
    unico lugar que responde essa pergunta (M23).

    O nome antigo continua porque ele e importado por quatro modulos do
    dashboard; o que mudou foi de onde vem a resposta, nao o contrato."""
    from .template_assignments import resolve_active_template_id

    return resolve_active_template_id(user_id)


_NO_TEMPLATE_CYCLE_PAYLOAD: Dict[str, Any] = {
    "has_template": False,
    "message": "Nenhum modelo de treino atribuído a você.",
}


@router.get("/dashboard-cycle")
def dashboard_cycle(
    identity: Identity = Depends(require_permission("dashboards", "ver")),
):
    """Posicao no ciclo do Modelo de Ficha atribuido ao usuario (gap real,
    endpoint novo - nao existe em `GET /workout-plan-templates/assigned-to-me`,
    que nao expoe posicao no ciclo) para `/dashboard/ciclo` (CycleClient.tsx).

    Fluxo:
    1. Resolve o `template_id` mais recentemente atribuido ao usuario
       (`_resolve_latest_assigned_template_id` acima). Sem nenhum atribuido
       -> `{"has_template": False, "message": ...}` (NUNCA 404/500).
    2. Busca o modelo COMPLETO validando posse via `get_template_if_assigned`
       (`workout_plan_templates_engine.py`) - MESMA funcao de seguranca
       usada por `POST /workout-plans/generate` ao gerar ficha a partir de
       modelo; nunca aceita `template_id` de fora sem essa checagem (aqui
       nem existe `template_id` de fora - vem so da atribuicao do proprio
       usuario -, mas a validacao e mantida por simetria/defesa em
       profundidade, caso o modelo seja desativado entre as duas queries).
    3. Slots ordenados por `sequence` (nunca por nome - mandato do plano:
       "NUNCA hardcodar nome/letra tipo 'A'/'B'/'C'"), cada um com status
       `done`/`next`/`pending`:
       - `done_sequences`: percorre os planos salvos deste usuario+modelo do
         MAIS RECENTE para o mais antigo, acumulando `sequence`s distintos
         ate no maximo `len(slots)` OU ate encontrar um `sequence` repetido
         (sinal de que o ciclo anterior a esse ponto ja e outro ciclo -
         para ali). Criterio de design explicito do plano ("os ultimos
         N=len(slots) planos salvos, do mais recente pra tras, marcam os
         slots ja cobertos no ciclo corrente; quando o mesmo sequence
         aparece de novo, o ciclo reiniciou").
       - `next`: o slot resolvido por `resolve_next_slot(template, user_id)`
         TAL QUAL (sem `explicit_slot_sequence`) - nao duplica a logica de
         wraparound-por-valor-de-sequence que a funcao ja implementa.
       - `pending`: todo slot que nao esta em `done_sequences` e nao e o
         `next` (mas ainda conta para a projecao de volume abaixo - ver
         `pending_sequences`).
    4. Cobertura do ciclo (Grupo | Target | Realizado | Projetado | Status):
       - Target: `template.training_type_id` (prioridade) ou, se `None`,
         `resolve_active_training_type_for_user` (mesmo fallback de
         `_build_muscle_status_items`, Fase 1). Sem nenhum dos dois ->
         secao marcada indisponivel (`coverage: []`,
         `training_type_source: "unavailable"`) - decisao de design: sem
         meta nenhuma para comparar, uma tabela so com Realizado/Projetado
         sem "Target"/"Status" agregaria pouco valor e a UI ja teria que
         tratar `target_weekly_sets: null` linha a linha; mais simples e
         honesto deixar a secao vazia com um aviso.
       - M76 Lote 4a (E1/E2): realizado e planejado na MESMA unidade (series
         equivalentes por SEMANA) e a semana e a do dono
         (`methodology_volume`): 7 dias, qualquer que seja o ciclo.
       - Realizado (ACTUAL): `VolumeService.get_muscle_group_overview` nos
         ultimos `weekly_window_days()` dias (series equivalentes,
         `sets x stimulus_weight`), em taxa semanal por
         `calendar_weekly_rate`. Ate o Lote 4a a janela era o ciclo
         (`x 7/ciclo`).
       - Projetado = PLANEJADO DA SEMANA (`projected_weekly_sets`, tambem em
         `planned_weekly_sets`): o valor por slot de
         `muscle_summary_service.template_activation_by_slot` (Modelo por
         equipamento: ativacao ponderada; Modo A: ponto medio do orcamento
         do item - antes so `sets_count`, e o Modo B projetava 0) somado na
         janela de 7 dias a partir do 1o slot de treino
         (`weekly_normalization`) - o MESMO numero do editor de Modelo.
         Antes era a soma BRUTA dos slots pendentes, somada a uma taxa
         semanal. O que falta no ciclo corrente continua no payload, sem
         status: `pending_cycle_sets`.
       - Status por grupo: `classify_weekly_volume_status` do PLANEJADO DA
         SEMANA contra min/target/max do `training_type` (`status_basis =
         "planned_weekly"`): "se eu cumprir o Modelo, onde fico na semana?".
         Somar o realizado de uma janela ao planejado de outra misturava
         grandezas; o realizado puro tem status em `/dashboard/musculos`."""
    from .db import transaction
    from .observability import log_event
    from .intelligence.dashboard.muscle_summary_service import (
        PlanSlot,
        resolve_template_plan,
        sum_projected_activation_from_pending_slots,
        template_activation_by_slot,
    )
    from .intelligence.methodology_volume import calendar_weekly_rate, weekly_normalization, weekly_window_days
    from .intelligence.volume_service import (
        VolumeService,
        classify_weekly_volume_status,
        template_cycle_length_days,
    )
    from .training_types import get_training_type_muscle_group_targets, resolve_active_training_type_for_user
    from .workout_plan_templates_engine import get_template_if_assigned, resolve_next_slot

    user_id = identity.user_id

    template_id = _resolve_latest_assigned_template_id(user_id)
    if template_id is None:
        return dict(_NO_TEMPLATE_CYCLE_PAYLOAD)

    template = get_template_if_assigned(template_id, user_id)
    if template is None:
        # So acontece se o modelo foi desativado/desatribuido entre as duas
        # queries acima (corrida rara) - nunca deve vazar um 404/500 aqui,
        # mesmo tratamento de "nada atribuido" do passo 1.
        return dict(_NO_TEMPLATE_CYCLE_PAYLOAD)

    ordered_slots = sorted(template.get("slots") or [], key=lambda slot: slot["sequence"])
    if not ordered_slots:
        return {
            "has_template": True,
            "template_id": template_id,
            "template_name": template.get("name"),
            "cycle_length_days": 0,
            "slots": [],
            "next_slot": None,
            "coverage": [],
            "training_type_source": "unavailable",
        }

    slot_count = len(ordered_slots)
    cycle_length_days = template_cycle_length_days(slot_count)

    plan_rows = _fetch_all(
        """
        SELECT template_slot_sequence
        FROM workout_plans
        WHERE user_id = %s AND template_id = %s AND template_slot_sequence IS NOT NULL
        ORDER BY id DESC
        """,
        (user_id, template_id),
    )
    done_sequences: set = set()
    for row in plan_rows:
        seq = row.get("template_slot_sequence")
        if seq in done_sequences:
            break
        done_sequences.add(seq)
        if len(done_sequences) >= slot_count:
            break

    next_slot = resolve_next_slot(template, user_id)
    next_sequence = next_slot.get("sequence") if next_slot else None

    slots_payload: List[Dict[str, Any]] = []
    for slot in ordered_slots:
        seq = slot["sequence"]
        if seq == next_sequence:
            status = "next"
        elif seq in done_sequences:
            status = "done"
        else:
            status = "pending"
        slots_payload.append(
            {
                "sequence": seq,
                "name": slot.get("name"),
                "slot_type": slot.get("slot_type"),
                "weekday": slot.get("weekday"),
                "status": status,
            }
        )

    all_sequences = {slot["sequence"] for slot in ordered_slots}
    pending_sequences = all_sequences - done_sequences

    resolved_training_type_id = template.get("training_type_id")
    training_type_source = "template" if resolved_training_type_id else None
    if resolved_training_type_id is None:
        resolved_training_type_id = resolve_active_training_type_for_user(user_id)
        training_type_source = "assigned_template" if resolved_training_type_id else "unavailable"

    coverage: List[Dict[str, Any]] = []
    if resolved_training_type_id is not None:
        targets_by_group = get_training_type_muscle_group_targets(resolved_training_type_id)

        week_days = weekly_window_days()
        volume_service = VolumeService()
        overview_rows = volume_service.get_muscle_group_overview(days=week_days, limit=100, user_id=user_id)

        actual_by_group: Dict[int, float] = {}
        names_by_group: Dict[int, str] = {}
        for row in overview_rows:
            gid = row.get("muscle_group_id")
            if gid is None:
                continue
            actual_by_group[gid] = calendar_weekly_rate(
                float(row.get("stimulus_equivalent_sets") or 0.0), week_days
            ) or 0.0
            names_by_group[gid] = row.get("muscle_group")

        # Planejado por slot: a mesma fonte do projetado de `/dashboard/musculos`
        # (Modelo por equipamento pela ativacao ponderada da versao vigente).
        template_plan = None
        try:
            with transaction() as conn:
                template_plan = resolve_template_plan(conn, user_id)
        except Exception as exc:  # noqa: BLE001 - sem o plano, cai nos itens de grupo
            log_event("dashboard_cycle.template_plan_unavailable", user_id=user_id, error=str(exc))
        plan_by_slot = template_activation_by_slot(template, template_plan, targets_by_group)
        plan_slots = [PlanSlot(sequence=int(slot["sequence"]), slot_type=slot.get("slot_type")) for slot in ordered_slots]
        planned_weekly_by_group = weekly_normalization(plan_slots, plan_by_slot)
        pending_by_group = sum_projected_activation_from_pending_slots(plan_by_slot, pending_sequences)

        all_group_ids = (
            set(targets_by_group.keys()) | set(actual_by_group.keys()) | set(planned_weekly_by_group.keys())
        )
        missing_name_ids = sorted(all_group_ids - names_by_group.keys())
        if missing_name_ids:
            placeholders = ",".join(["%s"] * len(missing_name_ids))
            for row in _fetch_all(
                f"SELECT id, name FROM muscle_groups WHERE id IN ({placeholders})",
                tuple(missing_name_ids),
            ):
                names_by_group[row["id"]] = row["name"]

        for gid in all_group_ids:
            actual = round(actual_by_group.get(gid, 0.0), 2)
            planned = round(planned_weekly_by_group.get(gid, 0.0), 2)
            target = targets_by_group.get(gid)
            status = (
                classify_weekly_volume_status(planned, target["min"], target["target"], target["max"])
                if target
                else None
            )
            coverage.append(
                {
                    "muscle_group_id": gid,
                    "muscle_group": names_by_group.get(gid, f"Grupo {gid}"),
                    "min_weekly_sets": target["min"] if target else None,
                    "target_weekly_sets": target["target"] if target else None,
                    "max_weekly_sets": target["max"] if target else None,
                    "actual_weekly_sets": actual,
                    # Chave mantida pelo contrato da tela: e o planejado DA SEMANA.
                    "projected_weekly_sets": planned,
                    "planned_weekly_sets": planned,
                    "pending_cycle_sets": round(pending_by_group.get(gid, 0.0), 2),
                    "status": status,
                    "status_basis": "planned_weekly",
                }
            )
        coverage.sort(key=lambda c: c["actual_weekly_sets"] + c["projected_weekly_sets"], reverse=True)

    return {
        "has_template": True,
        "template_id": template_id,
        "template_name": template.get("name"),
        "cycle_length_days": cycle_length_days,
        "slots": slots_payload,
        "next_slot": (
            {
                "sequence": next_slot["sequence"],
                "name": next_slot.get("name"),
                "slot_type": next_slot.get("slot_type"),
                "weekday": next_slot.get("weekday"),
            }
            if next_slot
            else None
        ),
        "coverage": coverage,
        "training_type_source": training_type_source,
        "window_days": weekly_window_days(),
    }
# END dashboard novo (hub paralelo /dashboard/*, Fase 3)

# BEGIN dashboard novo (hub paralelo /dashboard/*, Fase 4)
# Ver plano completo em
# C:\Users\dougb\.claude\plans\c-users-dougb-claude-uploads-c8154714-b-happy-perlis.md.
# stimulus/fatigue - ultima fase do hub paralelo. A auditoria v2 do motor
# (`/dashboard-engine-audit`) saiu no COD-05 (2026-10-09): sucessora
# `/analytics/dashboard/engine`, 0 chamadas em 14 dias de telemetria.

@router.get("/dashboard-stimulus-fatigue")
def dashboard_stimulus_fatigue(
    muscle_group_id: Optional[int] = Query(default=None),
    days: int = Query(default=90, ge=1, le=3650),
    activity_id: Optional[int] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    identity: Identity = Depends(require_permission("volume.grupamentos", "ver")),
):
    """Tabela Exercício|Stimulus|Fatigue|Ratio para o drill-down muscular de
    `/dashboard/musculos` (`MuscleStatusClient.tsx`, Fase 4) -
    `VolumeService.get_stimulus_fatigue_ratio_by_exercise`, MESMA formula
    (`stimulus_weight`/`fatigue_weight` de `exercise_muscle_contributions`)
    ja usada em toda a plataforma, nunca reimplementada aqui. `activity_id`
    (quando informado) tem prioridade sobre `days`/`muscle_group_id` - ver
    docstring do metodo."""
    from .intelligence.volume_service import VolumeService

    days = _safe_days(days)
    volume_service = VolumeService()
    items = volume_service.get_stimulus_fatigue_ratio_by_exercise(
        days=days,
        user_id=identity.user_id,
        muscle_group_id=muscle_group_id,
        activity_id=activity_id,
        limit=limit,
    )

    return {
        "muscle_group_id": muscle_group_id,
        "activity_id": activity_id,
        "days": days,
        "items": items,
    }


# END dashboard novo (hub paralelo /dashboard/*, Fase 4)
