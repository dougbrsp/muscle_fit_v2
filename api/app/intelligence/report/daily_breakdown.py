"""Detalhamento DIARIO do relatorio em Markdown (`GET /reports/markdown`).

O resumo do periodo continua vindo dos servicos do dashboard
(`markdown_report_service`). Este modulo entrega a mesma informacao cortada
por dia local (`local_date`, ja no fuso do usuario), com uma regra so:
**a soma dos dias e o total do periodo**. Para isso cada leitura usa a MESMA
fonte, os MESMOS filtros e a MESMA regra do resumo:

- grupo muscular: `muscle_summary_service.DAILY_GROUP_TOTALS_SQL` (mesma
  cadeia de JOIN/filtro de `ROLE_TOTALS_SQL`/`GROUP_SESSIONS_SQL`) + as
  aplicacoes de tecnica pela mesma `TECHNIQUE_SETS_SQL` e pela regra da
  Fase 5 (`TechniqueSets`); tonelagem pela consulta do proprio relatorio
  (`TONNAGE_BY_GROUP_SQL_TEMPLATE`, agora agrupada tambem por dia).
  `check_daily_invariant` confere dia a dia contra o resumo e declara
  qualquer diferenca na secao de qualidade - nunca corrige em silencio;
- equipamento: UMA consulta por (dia, linha de exercicio). O resumo do
  periodo por equipamento e a soma das linhas diarias (por construcao);
- sono/wearable/check-in/dor, corpo e ciclos: uma consulta por fonte, por
  `local_date` - nunca uma consulta por dia.

Funcoes puras (sem banco) fazem toda a agregacao e sao testadas em
`tests/unit/test_reports_markdown_daily.py`. Somente leitura.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping, Optional, Sequence

from ...load_conventions import (
    TONNAGE_EXCLUDED_CONVENTIONS,
    LoadConvention,
    equipment_exercises_join_sql,
    load_convention_select_expr,
    normalize_load_convention,
)
from ...domain.deload import deload_source_label, deload_source_value
from ...domain.set_measures import sql_duration_seconds, sql_is_rep_based
from ...domain.training_goal import goal_value, goals_in, is_default
from ...set_types import sql_is_working, sql_not_preparatory, sql_set_confirmed
from ...template_versions import plan_version_expr
from ...timezones import DEFAULT_TIMEZONE
from ..dashboard.time_windows import TRAINED_AT_TOKEN, with_trained_at
from ..dashboard.coercion import to_date, to_float
from ..technique_volume import TechniqueSets, new_catalog, sql_application_rows, sql_work_unit
from ...db_helpers import rows as _fetchall

NO_EQUIPMENT = "Sem equipamento"

# O MESMO conjunto do dashboard e do coletor: mora em
# `load_conventions.TONNAGE_EXCLUDED_CONVENTIONS` (M58). O nome local fica por
# compatibilidade com quem ja o importava.
EXCLUDED_CONVENTIONS: frozenset[LoadConvention] = TONNAGE_EXCLUDED_CONVENTIONS

_CANONICAL = "COALESCE(elm.canonical_exercise_id, e.mapping_id)"
_WORK = sql_work_unit("es.set_type")
_WORKING = sql_is_working("es.set_type")

# Uma linha por (dia, linha de `exercises`). Sem o JOIN de contribuicoes: aqui
# a serie conta UMA vez (equipamento nao e grupo). `equipment_exercises` tem
# UNIQUE (equipment_id, exercise_id): o LEFT JOIN nao duplica serie. As
# colunas fora do GROUP BY vem em MAX() para rodar com ONLY_FULL_GROUP_BY nos
# dois backends (PyMySQL e shim da Data API).
#
# - `physical_sets`/`rpe`: series nao-preparatorias (mesma base do resumo
#   por grupo);
# - `work_units`: series que NAO sao aplicacao de tecnica; cada aplicacao
#   entra depois com a sua fatia (regra da Fase 5);
# - carga/reps/tonelagem: so series de TRABALHO (mesma regra da tonelagem do
#   #298). A carga e a gravada (TOTAL, D12) - nada e convertido.
EQUIPMENT_DAILY_SQL_TEMPLATE = f"""
SELECT
  a.local_date                                              AS local_date,
  e.id                                                      AS exercise_row_id,
  MAX({_CANONICAL})                                         AS exercise_id,
  MAX(COALESCE(xc.name, e.original_name))                   AS exercise_name,
  MAX(eq.id)                                                AS equipment_id,
  MAX(eq.name)                                              AS equipment_name,
  MAX(g.name)                                               AS gym_name,
  MAX({{load_convention}})                                  AS load_conv,
  COUNT(es.id)                                              AS physical_sets,
  SUM({_WORK})                                              AS work_units,
  {sql_application_rows("es.set_type")}                     AS technique_rows,
  SUM(CASE WHEN es.rpe IS NOT NULL THEN es.rpe ELSE 0 END)  AS rpe_sum,
  SUM(CASE WHEN es.rpe IS NOT NULL THEN 1 ELSE 0 END)       AS rpe_n,
  SUM(CASE WHEN {_WORKING} THEN 1 ELSE 0 END)               AS working_sets,
  SUM(CASE WHEN {_WORKING} THEN COALESCE(es.reps, 0) ELSE 0 END) AS reps_sum,
  SUM(CASE WHEN {_WORKING} AND {sql_is_rep_based('es.measure')} THEN COALESCE(es.reps, 0) * COALESCE(es.weight_kg, 0) ELSE 0 END) AS tonnage_kg,
  SUM(CASE WHEN {_WORKING} THEN COALESCE({sql_duration_seconds('es.measure', 'es.duration_seconds')}, 0) ELSE 0 END) AS duration_seconds_sum,
  MAX(CASE WHEN {_WORKING} THEN es.weight_kg ELSE NULL END) AS load_max,
  SUM(CASE WHEN {_WORKING} AND es.weight_kg IS NOT NULL THEN es.weight_kg ELSE 0 END) AS load_sum,
  SUM(CASE WHEN {_WORKING} AND es.weight_kg IS NOT NULL THEN 1 ELSE 0 END) AS load_n,
  MAX(e.training_goal)                                      AS training_goal
FROM activities a
JOIN exercises e ON e.activity_id = a.id
JOIN exercise_sets es ON es.exercise_id = e.id AND {sql_set_confirmed('es.reps_pending')}
LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
LEFT JOIN exercise_catalog xc ON xc.id = {_CANONICAL}
LEFT JOIN gym_equipment ge ON ge.id = e.equipment_id
LEFT JOIN equipment eq ON eq.id = ge.equipment_id
LEFT JOIN gyms g ON g.id = ge.gym_id
{equipment_exercises_join_sql(canonical_expr=_CANONICAL)}WHERE a.user_id = %s
  AND a.local_date BETWEEN %s AND %s
  AND {sql_not_preparatory("es.set_type")}
GROUP BY a.local_date, e.id
"""

SLEEP_DAILY_SQL = """
SELECT local_date, duration_hours, deep_sleep_hours, rem_sleep_hours, regularity_percent
FROM sleep_metrics
WHERE user_id = %s AND local_date BETWEEN %s AND %s
ORDER BY local_date ASC
"""

WELLNESS_DAILY_SQL = """
SELECT wellness_date AS local_date, hrv_ms, resting_hr_bpm, form_tsb, readiness_score
FROM intervals_wellness
WHERE user_id = %s AND wellness_date BETWEEN %s AND %s
ORDER BY wellness_date ASC
"""

# Uma leitura de bioimpedancia por (dia, fonte) - UNIQUE da tabela. Mais de
# uma fonte no mesmo dia: vale a ultima gravada (maior `id`).
BODY_COMPOSITION_DAILY_SQL = """
SELECT id, local_date, weight_kg, bmi, body_fat_percent, fat_mass_kg, fat_free_mass_kg, muscle_mass_kg,
       skeletal_muscle_percent, body_water_percent, visceral_fat_rating, bone_mass_kg, bmr_kcal,
       subcutaneous_fat_percent, protein_percent, metabolic_age
FROM body_composition
WHERE user_id = %s AND local_date BETWEEN %s AND %s
ORDER BY local_date ASC, id ASC
"""

# Sessoes de musculacao (atividade com pelo menos um exercicio - mesma
# definicao de `STRENGTH_SESSIONS_BY_DAY_SQL`), com o dia do Modelo quando a
# sessao nasceu de uma ficha (`workout_plans.template_slot_sequence`) e as
# duas origens de FC (mesmas colunas de `cardio_service.SESSION_HR_SQL`, para
# `normalize_session_hr_row` decidir a origem). `activity_intervals_link` tem
# UNIQUE (activity_id): o LEFT JOIN nao duplica sessao.
#
# PR3: o nome do slot vem da VERSAO da ficha (a gravada nela ou a vigente no
# `plan_date`) - JOIN no mesmo statement.
_CYCLE_SESSIONS_TEMPLATE = """
SELECT
  a.id                                              AS activity_id,
  a.local_date                                      AS local_date,
  {trained_at}                                      AS trained_at,
  a.name                                            AS name,
  a.started_at                                      AS started_at,
  a.duration_seconds                                AS duration_seconds,
  a.avg_hr_bpm                                      AS activity_avg_hr_bpm,
  a.max_hr_bpm                                      AS activity_max_hr_bpm,
  l.avg_hr_bpm                                      AS avg_hr_bpm,
  l.max_hr_bpm                                      AS max_hr_bpm,
  l.moving_time_seconds                             AS moving_time_seconds,
  wp.template_id                                    AS template_id,
  wp.template_slot_sequence                         AS slot_sequence,
  s.name                                            AS slot_name,
  a.deload_source                                   AS deload_source
FROM activities a
LEFT JOIN activity_intervals_link l ON l.activity_id = a.id AND l.user_id = a.user_id
LEFT JOIN workout_plans wp ON wp.id = a.workout_plan_id AND wp.user_id = a.user_id
LEFT JOIN workout_plan_template_versions v ON v.id = {version_expr}
LEFT JOIN workout_plan_template_slots s
       ON s.version_id = v.id AND s.sequence = wp.template_slot_sequence
WHERE a.user_id = %s
  AND a.local_date BETWEEN %s AND %s
  AND EXISTS (SELECT 1 FROM exercises ex WHERE ex.activity_id = a.id)
ORDER BY a.local_date ASC, {trained_at} ASC, a.id ASC
"""

CYCLE_SESSIONS_SQL = _CYCLE_SESSIONS_TEMPLATE.format(
    version_expr=plan_version_expr("wp", by_date=True), trained_at=TRAINED_AT_TOKEN
)

# RPE e series por sessao (nao-preparatorias), mesma base de
# `adherence_service.SESSION_TOTALS_SQL`.
SESSION_RPE_SQL = f"""
SELECT
  a.id                                                      AS activity_id,
  COUNT(es.id)                                              AS physical_sets,
  SUM(CASE WHEN es.rpe IS NOT NULL THEN es.rpe ELSE 0 END)  AS rpe_sum,
  SUM(CASE WHEN es.rpe IS NOT NULL THEN 1 ELSE 0 END)       AS rpe_n
FROM activities a
JOIN exercises e ON e.activity_id = a.id
JOIN exercise_sets es ON es.exercise_id = e.id AND {sql_set_confirmed('es.reps_pending')}
WHERE a.user_id = %s
  AND a.local_date BETWEEN %s AND %s
  AND {sql_not_preparatory("es.set_type")}
GROUP BY a.id
"""


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------


def _num(value: Any) -> float:
    return to_float(value) or 0.0


def _avg(total: float, count: float) -> Optional[float]:
    return total / count if count else None


def _convention(raw: Any) -> LoadConvention:
    """NULL/vazio = `total`; valor fora do vocabulario tambem conta como
    `total` (mesma tolerancia de `summarize_tonnage`)."""
    try:
        return normalize_load_convention(raw)
    except ValueError:
        return LoadConvention.TOTAL


def daily_tonnage(group_rows: Iterable[Mapping[str, Any]]) -> dict[tuple[date, int], dict[str, Any]]:
    """`(dia, grupo) -> {tonnage_kg, sets, excluded_sets}` a partir das linhas
    de `TONNAGE_BY_GROUP_SQL_TEMPLATE` (uma por dia x grupo x convencao).
    Mesma regra de `summarize_tonnage`, SEM arredondar (quem arredonda e o
    renderer) - assim a soma dos dias fecha com o total do periodo."""
    out: dict[tuple[date, int], dict[str, Any]] = {}
    for row in group_rows:
        day = to_date(row.get("local_date"))
        group_id = row.get("muscle_group_id")
        if day is None or group_id is None:
            continue
        bucket = out.setdefault((day, int(group_id)), {"tonnage_kg": 0.0, "sets": 0, "excluded_sets": 0})
        sets = int(_num(row.get("sets_count")))
        if _convention(row.get("load_conv")) in EXCLUDED_CONVENTIONS:
            bucket["excluded_sets"] += sets
            continue
        bucket["sets"] += sets
        bucket["tonnage_kg"] += _num(row.get("tonnage_kg"))
    return out


def build_group_days(
    rows: Iterable[Mapping[str, Any]],
    applications: Iterable[tuple[Mapping[str, Any], float]],
    tonnage: Mapping[tuple[date, int], Mapping[str, Any]],
    names: Mapping[int, str],
) -> list[dict[str, Any]]:
    """Linhas diarias por grupo.

    `rows`: `DAILY_GROUP_TOTALS_SQL`. `applications`: `TechniqueSets.
    applications()` (linha, fatia) - somam `stimulus_weight x fatia` no
    estimulo do dia/grupo, como em `_build_items`. `tonnage`: `daily_tonnage`.
    Ordem: data, depois nome do grupo. Dia/grupo sem serie nao existe."""
    buckets: dict[tuple[date, int], dict[str, Any]] = {}

    def _bucket(day: date, group_id: int) -> dict[str, Any]:
        return buckets.setdefault(
            (day, group_id),
            {
                "local_date": day,
                "muscle_group_id": group_id,
                "name": names.get(group_id, f"Grupo {group_id}"),
                "physical_sets": 0.0,
                "effective_sets": 0.0,
                "rpe_sum": 0.0,
                "rpe_n": 0,
                "exercises": 0,
                "sessions": 0,
            },
        )

    for row in rows:
        day = to_date(row.get("local_date"))
        group_id = row.get("muscle_group_id")
        if day is None or group_id is None:
            continue
        bucket = _bucket(day, int(group_id))
        bucket["physical_sets"] += _num(row.get("physical_sets"))
        bucket["effective_sets"] += _num(row.get("stimulus_sets"))
        bucket["rpe_sum"] += _num(row.get("rpe_sum"))
        bucket["rpe_n"] += int(_num(row.get("rpe_n")))
        bucket["exercises"] += int(_num(row.get("exercises")))
        bucket["sessions"] += int(_num(row.get("sessions")))

    for row, units in applications:
        day = to_date(row.get("local_date"))
        group_id = row.get("muscle_group_id")
        if day is None or group_id is None:
            continue
        _bucket(day, int(group_id))["effective_sets"] += _num(row.get("stimulus_weight")) * float(units or 0.0)

    out = []
    for key in sorted(buckets, key=lambda k: (k[0], buckets[k]["name"])):
        bucket = buckets[key]
        group_tonnage = tonnage.get(key) or {}
        bucket["tonnage_kg"] = group_tonnage.get("tonnage_kg") if group_tonnage.get("sets") else None
        bucket["tonnage_excluded_sets"] = int(group_tonnage.get("excluded_sets") or 0)
        bucket["avg_rpe"] = _avg(bucket["rpe_sum"], bucket["rpe_n"])
        out.append(bucket)
    return out


# Tolerancia da invariante: o resumo arredonda em 2 casas (`metric`) e a
# tonelagem em 1 casa (`summarize_tonnage`); os dias somam sem arredondar.
INVARIANT_TOLERANCE: Mapping[str, float] = {
    "physical_sets": 0.006,
    "effective_sets": 0.006,
    "tonnage_kg": 0.051,
    "avg_rpe": 0.006,
}


def check_daily_invariant(
    period: Iterable[Mapping[str, Any]],
    days: Iterable[Mapping[str, Any]],
    *,
    key: str,
    fields: Sequence[str] = ("physical_sets", "effective_sets", "tonnage_kg", "avg_rpe"),
    label: str = "name",
) -> list[str]:
    """Confere que a soma dos dias bate com o total do periodo, por chave
    (`muscle_group_id`, equipamento...). `avg_rpe` e conferido pela media
    ponderada dos dias (`rpe_sum / rpe_n`). Devolve as divergencias em texto
    (vazio = fecha). `None` no periodo exige soma `None`/0 nos dias."""
    totals: dict[Any, dict[str, float]] = {}
    for row in days:
        bucket = totals.setdefault(row[key], {"rpe_sum": 0.0, "rpe_n": 0.0})
        for field in fields:
            if field != "avg_rpe":
                bucket[field] = bucket.get(field, 0.0) + _num(row.get(field))
        bucket["rpe_sum"] += _num(row.get("rpe_sum"))
        bucket["rpe_n"] += _num(row.get("rpe_n"))

    def _summed(bucket: Mapping[str, float], field: str) -> float:
        if field == "avg_rpe":
            return _avg(bucket["rpe_sum"], bucket["rpe_n"]) or 0.0
        return bucket.get(field, 0.0)

    problems: list[str] = []
    for item in period:
        item_key = item[key]
        bucket = totals.pop(item_key, {"rpe_sum": 0.0, "rpe_n": 0.0})
        for field in fields:
            expected = _num(item.get(field))
            got = _summed(bucket, field)
            if abs(expected - got) > INVARIANT_TOLERANCE.get(field, 0.006):
                problems.append(f"{item.get(label, item_key)}: {field} período={expected:g} dias={got:g}")
    # Chave que so aparece nos dias: o periodo esqueceu alguma coisa.
    for item_key, bucket in totals.items():
        if any(abs(_summed(bucket, f)) > INVARIANT_TOLERANCE.get(f, 0.006) for f in fields):
            problems.append(f"{item_key}: aparece no detalhamento diário e não no total do período")
    return problems


def equipment_label(row: Mapping[str, Any]) -> str:
    name = row.get("equipment_name")
    if not name:
        return NO_EQUIPMENT
    gym = row.get("gym_name")
    return f"{name} ({gym})" if gym else str(name)


def _empty_equipment_bucket(**extra: Any) -> dict[str, Any]:
    return {
        **extra,
        "physical_sets": 0.0,
        "effective_sets": 0.0,
        "tonnage_kg": 0.0,
        "tonnage_sets": 0,
        "tonnage_excluded_sets": 0,
        "rpe_sum": 0.0,
        "rpe_n": 0,
        "working_sets": 0,
        "reps": 0,
        # Lote T3: segundos das series de trabalho POR TEMPO (prancha); 0 sem.
        "duration_seconds": 0,
        "load_max": None,
        "load_sum": 0.0,
        "load_n": 0,
        "conventions": set(),
    }


def _add_equipment_row(bucket: dict[str, Any], row: Mapping[str, Any], application_units: float) -> None:
    convention = _convention(row.get("load_conv"))
    working = int(_num(row.get("working_sets")))
    bucket["physical_sets"] += _num(row.get("physical_sets"))
    bucket["effective_sets"] += _num(row.get("work_units")) + application_units
    bucket["rpe_sum"] += _num(row.get("rpe_sum"))
    bucket["rpe_n"] += int(_num(row.get("rpe_n")))
    bucket["working_sets"] += working
    bucket["reps"] += int(_num(row.get("reps_sum")))
    bucket["duration_seconds"] += int(_num(row.get("duration_seconds_sum")))
    bucket["load_sum"] += _num(row.get("load_sum"))
    bucket["load_n"] += int(_num(row.get("load_n")))
    load_max = to_float(row.get("load_max"))
    if load_max is not None and (bucket["load_max"] is None or load_max > bucket["load_max"]):
        bucket["load_max"] = load_max
    bucket["conventions"].add(convention.value)
    if convention in EXCLUDED_CONVENTIONS:
        bucket["tonnage_excluded_sets"] += working
    else:
        bucket["tonnage_sets"] += working
        bucket["tonnage_kg"] += _num(row.get("tonnage_kg"))


def _finish_equipment_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    bucket["avg_rpe"] = _avg(bucket["rpe_sum"], bucket["rpe_n"])
    bucket["load_avg"] = _avg(bucket["load_sum"], bucket["load_n"])
    if not bucket["tonnage_sets"]:
        bucket["tonnage_kg"] = None
    # So as convencoes que mudam a leitura (assistido / peso corporal).
    bucket["excluded_conventions"] = sorted(
        c for c in bucket.pop("conventions") if c in {x.value for x in EXCLUDED_CONVENTIONS}
    )
    return bucket


def build_equipment(
    rows: Iterable[Mapping[str, Any]],
    application_units_by_exercise_row: Mapping[int, float],
) -> dict[str, list[dict[str, Any]]]:
    """`{"days": [...], "period": [...]}` por equipamento.

    `rows`: `EQUIPMENT_DAILY_SQL_TEMPLATE` (dia x linha de exercicio).
    Detalhe diario por (dia, equipamento, exercicio); resumo do periodo por
    equipamento = soma das MESMAS linhas, entao a invariante fecha por
    construcao. Series efetivas = series que nao sao aplicacao + fatia de
    cada aplicacao (regra da Fase 5). Tonelagem = reps x carga TOTAL das
    series de trabalho; par `assisted`/`bodyweight` fica fora e so as reps
    aparecem.

    M97 F4: so quando o periodo tem objetivo diferente do default, `goals`
    (objetivos presentes, ordem do dono `training_goal`) e `by_goal`; com MAIS
    de um, `by_goal` traz o mesmo bloco (resumo + diario) para cada
    objetivo, cortado pelas MESMAS linhas - o relatorio separa os blocos. Com
    um objetivo so, `by_goal` fica vazio e o bloco e o de sempre."""
    rows = list(rows)
    goals = list(goals_in(row.get("training_goal") for row in rows))
    out = _build_equipment_rows(rows, application_units_by_exercise_row)
    if not any(not is_default(goal) for goal in goals):
        # Periodo so do objetivo default (todo historico atual): o bloco de
        # sempre, sem chave nova.
        return out
    out["goals"] = goals
    out["by_goal"] = (
        {
            goal: _build_equipment_rows(
                [row for row in rows if goal_value(row.get("training_goal")) == goal],
                application_units_by_exercise_row,
            )
            for goal in goals
        }
        if len(goals) > 1
        else {}
    )
    return out


def _build_equipment_rows(
    rows: Iterable[Mapping[str, Any]],
    application_units_by_exercise_row: Mapping[int, float],
) -> dict[str, list[dict[str, Any]]]:
    days: dict[tuple[date, str, str], dict[str, Any]] = {}
    period: dict[str, dict[str, Any]] = {}
    for row in rows:
        day = to_date(row.get("local_date"))
        if day is None:
            continue
        label = equipment_label(row)
        exercise = str(row.get("exercise_name") or f"Exercício {row.get('exercise_id')}")
        units = float(application_units_by_exercise_row.get(int(row.get("exercise_row_id") or 0), 0.0))
        day_bucket = days.setdefault(
            (day, label, exercise), _empty_equipment_bucket(local_date=day, equipment=label, exercise=exercise)
        )
        _add_equipment_row(day_bucket, row, units)
        period_bucket = period.setdefault(
            label, _empty_equipment_bucket(equipment=label, days=set(), exercises=set())
        )
        _add_equipment_row(period_bucket, row, units)
        period_bucket["days"].add(day)
        period_bucket["exercises"].add(exercise)

    out_days = [_finish_equipment_bucket(days[k]) for k in sorted(days)]
    out_period = []
    for label, bucket in period.items():
        bucket["days"] = len(bucket["days"])
        bucket["exercises"] = len(bucket["exercises"])
        out_period.append(_finish_equipment_bucket(bucket))
    # Sem equipamento por ultimo; os demais por series (maior primeiro).
    out_period.sort(key=lambda b: (b["equipment"] == NO_EQUIPMENT, -b["physical_sets"], b["equipment"]))
    return {"days": out_days, "period": out_period}


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def sleep_stats(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Medias do periodo a partir das linhas diarias (dias com valor; NULL
    fica fora da media - mesmo resultado do `AVG` do SQL)."""
    out: dict[str, Any] = {"days": len(rows)}
    for field, avg_key, days_key in (
        ("duration_hours", "avg_duration_hours", "duration_days"),
        ("deep_sleep_hours", "avg_deep_hours", "deep_days"),
        ("rem_sleep_hours", "avg_rem_hours", "rem_days"),
        ("regularity_percent", "avg_regularity_percent", "regularity_days"),
    ):
        values = [v for v in (to_float(r.get(field)) for r in rows) if v is not None]
        out[avg_key] = _mean(values)
        out[days_key] = len(values)
    return out


def wellness_stats(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"days": len(rows)}
    for field, avg_key, days_key in (
        ("hrv_ms", "avg_hrv_ms", "hrv_days"),
        ("resting_hr_bpm", "avg_resting_hr_bpm", "resting_hr_days"),
        ("form_tsb", "avg_form_tsb", "form_tsb_days"),
    ):
        values = [v for v in (to_float(r.get(field)) for r in rows) if v is not None]
        out[avg_key] = _mean(values)
        out[days_key] = len(values)
    return out


RECOVERY_DAY_FIELDS: tuple[str, ...] = (
    "sleep_hours",
    "deep_hours",
    "rem_hours",
    "regularity_percent",
    "hrv_ms",
    "resting_hr_bpm",
    "form_tsb",
    "readiness",
    "energy",
    "soreness",
    "stress",
    "mood",
    "muscle_pain",
    "joint_pain",
)


def build_recovery_days(
    sleep_rows: Iterable[Mapping[str, Any]],
    wellness_rows: Iterable[Mapping[str, Any]],
    checkin_rows: Iterable[Mapping[str, Any]],
    pain_rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Uma linha por data com TODAS as metricas daquele dia. Dia sem nenhum
    valor nao existe; campo sem valor fica `None` (o renderer mostra "—")."""
    days: dict[date, dict[str, Any]] = {}

    def _put(day: Any, field: str, value: Any) -> None:
        parsed_day = to_date(day)
        number = to_float(value)
        if parsed_day is None or number is None:
            return
        days.setdefault(parsed_day, {"local_date": parsed_day, **{f: None for f in RECOVERY_DAY_FIELDS}})[field] = number

    for row in sleep_rows:
        _put(row.get("local_date"), "sleep_hours", row.get("duration_hours"))
        _put(row.get("local_date"), "deep_hours", row.get("deep_sleep_hours"))
        _put(row.get("local_date"), "rem_hours", row.get("rem_sleep_hours"))
        _put(row.get("local_date"), "regularity_percent", row.get("regularity_percent"))
    for row in wellness_rows:
        _put(row.get("local_date"), "hrv_ms", row.get("hrv_ms"))
        _put(row.get("local_date"), "resting_hr_bpm", row.get("resting_hr_bpm"))
        _put(row.get("local_date"), "form_tsb", row.get("form_tsb"))
        _put(row.get("local_date"), "readiness", row.get("readiness_score"))
    for row in checkin_rows:
        for field in ("energy", "soreness", "stress", "mood"):
            _put(row.get("local_date"), field, row.get(field))
    for row in pain_rows:
        # `PAIN_DAILY_SQL` faz COALESCE(joint_pain, 0): dia de treino sem dor
        # registrada nao vira "dor articular 0".
        muscle = to_float(row.get("muscle_pain"))
        joint = to_float(row.get("joint_pain"))
        if muscle is None and not joint:
            continue
        _put(row.get("local_date"), "muscle_pain", muscle)
        _put(row.get("local_date"), "joint_pain", joint)
    return [days[d] for d in sorted(days)]


BODY_COMPOSITION_FIELDS: tuple[str, ...] = (
    "bmi",
    "body_fat_percent",
    "fat_mass_kg",
    "fat_free_mass_kg",
    "muscle_mass_kg",
    "skeletal_muscle_percent",
    "body_water_percent",
    "visceral_fat_rating",
    "bone_mass_kg",
    "bmr_kcal",
    "subcutaneous_fat_percent",
    "protein_percent",
    "metabolic_age",
)


def build_body_days(
    weights: Iterable[Mapping[str, Any]],
    composition_rows: Iterable[Mapping[str, Any]],
    measurement_rows: Iterable[Mapping[str, Any]],
    *,
    date_from: date,
    date_to: date,
    site_labels: Mapping[str, str],
) -> dict[str, Any]:
    """`{"days": [...], "fields": [...], "measurement_sites": [...],
    "measurement_days": [...]}`.

    `weights`: `pick_daily_weights` (uma pesagem por dia, com a prioridade do
    dashboard). Bioimpedancia: a ultima leitura do dia. `fields` lista so as
    metricas com algum valor no periodo (coluna vazia nao vira tabela de
    travessoes). Medidas: uma linha por data, uma coluna por local medido."""
    days: dict[date, dict[str, Any]] = {}

    def _day(day: date) -> dict[str, Any]:
        return days.setdefault(day, {"local_date": day, "weight_kg": None, "weight_source": None})

    for point in weights:
        day = to_date(point.get("local_date"))
        if day is None or not (date_from <= day <= date_to):
            continue
        entry = _day(day)
        entry["weight_kg"] = to_float(point.get("weight_kg"))
        entry["weight_source"] = point.get("source")

    for row in composition_rows:
        day = to_date(row.get("local_date"))
        if day is None or not (date_from <= day <= date_to):
            continue
        values = {f: to_float(row.get(f)) for f in BODY_COMPOSITION_FIELDS}
        if all(v is None for v in values.values()):
            continue
        _day(day).update(values)  # linhas em ordem de id: a ultima do dia vence

    fields = [f for f in BODY_COMPOSITION_FIELDS if any(d.get(f) is not None for d in days.values())]
    out_days = []
    for day in sorted(days):
        entry = days[day]
        for field in BODY_COMPOSITION_FIELDS:
            entry.setdefault(field, None)
        out_days.append(entry)

    measured: dict[date, dict[str, float]] = {}
    sites_seen: set[str] = set()
    for row in measurement_rows:
        day = to_date(row.get("local_date"))
        value = to_float(row.get("value_cm"))
        site = row.get("site")
        if day is None or value is None or not site:
            continue
        measured.setdefault(day, {})[str(site)] = value  # ordem por id: a ultima vence
        sites_seen.add(str(site))
    ordered_sites = [s for s in site_labels if s in sites_seen] + sorted(s for s in sites_seen if s not in site_labels)
    return {
        "days": out_days,
        "fields": fields,
        "measurement_sites": [{"site": s, "label": site_labels.get(s, s)} for s in ordered_sites],
        "measurement_days": [{"local_date": d, "values": measured[d]} for d in sorted(measured)],
    }


def segment_for(day: date, segments: Sequence[Any]) -> Optional[Any]:
    for segment in segments:
        if segment.date_from <= day <= segment.date_to:
            return segment
    return None


def build_cycle_timeline(
    sessions: Iterable[Mapping[str, Any]],
    rpe_rows: Iterable[Mapping[str, Any]],
    segments: Sequence[Any],
) -> list[dict[str, Any]]:
    """Uma linha por sessao de musculacao, em ordem cronologica: Modelo
    vigente no dia (trecho de `segment_window`), dia do Modelo quando a
    sessao veio de uma ficha, duracao, FC (`hr`: ja normalizada por
    `normalize_session_hr_row`) e RPE medio das series."""
    rpe = {int(r["activity_id"]): r for r in rpe_rows if r.get("activity_id") is not None}
    out = []
    for session in sessions:
        day = to_date(session.get("local_date"))
        if day is None:
            continue
        segment = segment_for(day, segments)
        totals = rpe.get(int(session.get("activity_id") or 0)) or {}
        sequence = session.get("slot_sequence")
        slot = None
        if sequence is not None:
            slot = f"{int(_num(sequence))}" + (f" — {session['slot_name']}" if session.get("slot_name") else "")
        duration = to_float(session.get("duration_seconds"))
        out.append(
            {
                "local_date": day,
                "activity_id": session.get("activity_id"),
                "name": session.get("name"),
                "template_name": segment.assignment.template_name if segment and segment.assignment else None,
                "slot": slot,
                "minutes": round(duration / 60) if duration else session.get("minutes"),
                "avg_hr_bpm": session.get("avg_hr_bpm"),
                "max_hr_bpm": session.get("max_hr_bpm"),
                "physical_sets": int(_num(totals.get("physical_sets"))),
                "avg_rpe": _avg(_num(totals.get("rpe_sum")), _num(totals.get("rpe_n"))),
                # M97 F4: sessao de deload sinalizada (rotulo do dono `deload`).
                "deload_source": deload_source_value(session.get("deload_source")),
                "deload_label": deload_source_label(session.get("deload_source")),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


class WindowTechniqueSets:
    """`TECHNIQUE_SETS_SQL` da janela, lida no maximo UMA vez para o grupo e
    para o equipamento, e so quando alguma agregacao acusou aplicacao."""

    def __init__(self, conn: Any, user_id: int, window: Any) -> None:
        self._conn = conn
        self._params = (user_id, window.date_from, window.date_to)
        self._loaded: Optional[TechniqueSets] = None

    def get(self) -> TechniqueSets:
        if self._loaded is None:
            from ..dashboard.muscle_summary_service import TECHNIQUE_SETS_SQL

            self._loaded = TechniqueSets(_fetchall(self._conn, TECHNIQUE_SETS_SQL, self._params), new_catalog())
        return self._loaded


def _has_applications(rows: Iterable[Mapping[str, Any]]) -> bool:
    return any(_num(row.get("technique_rows")) > 0 for row in rows)


def load_group_days(
    conn: Any,
    *,
    user_id: int,
    window: Any,
    tonnage_daily: Mapping[tuple[date, int], Mapping[str, Any]],
    names: Mapping[int, str],
    techniques: WindowTechniqueSets,
) -> list[dict[str, Any]]:
    from ..dashboard.muscle_summary_service import DAILY_GROUP_TOTALS_SQL

    rows = _fetchall(conn, DAILY_GROUP_TOTALS_SQL, (user_id, window.date_from, window.date_to))
    applications = techniques.get().applications() if _has_applications(rows) else []
    return build_group_days(rows, applications, tonnage_daily, names)


def load_equipment(conn: Any, *, user_id: int, window: Any, techniques: WindowTechniqueSets) -> dict[str, Any]:
    with conn.cursor() as cur:
        convention = load_convention_select_expr(cur, alias="eqex")
    rows = _fetchall(
        conn, EQUIPMENT_DAILY_SQL_TEMPLATE.format(load_convention=convention), (user_id, window.date_from, window.date_to)
    )
    units = techniques.get().application_units_by_exercise_row() if _has_applications(rows) else {}
    return build_equipment(rows, units)


def load_sleep_rows(conn: Any, *, user_id: int, window: Any) -> list[dict[str, Any]]:
    return _fetchall(conn, SLEEP_DAILY_SQL, (user_id, window.date_from, window.date_to))


def load_wellness_rows(conn: Any, *, user_id: int, window: Any) -> list[dict[str, Any]]:
    return _fetchall(conn, WELLNESS_DAILY_SQL, (user_id, window.date_from, window.date_to))


def load_body_composition_rows(conn: Any, *, user_id: int, window: Any) -> list[dict[str, Any]]:
    return _fetchall(conn, BODY_COMPOSITION_DAILY_SQL, (user_id, window.date_from, window.date_to))


def load_cycle_sessions(conn: Any, *, user_id: int, window: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from ..dashboard.cardio_service import normalize_session_hr_row

    params = (user_id, window.date_from, window.date_to)
    sessions = []
    sql = with_trained_at(
        CYCLE_SESSIONS_SQL,
        getattr(window, "timezone", None) or DEFAULT_TIMEZONE,
    )
    for row in _fetchall(conn, sql, params):
        hr = normalize_session_hr_row(row)
        sessions.append({**row, "avg_hr_bpm": hr["avg_hr_bpm"], "max_hr_bpm": hr["max_hr_bpm"], "minutes": hr["minutes"]})
    return sessions, _fetchall(conn, SESSION_RPE_SQL, params)


__all__ = [
    "BODY_COMPOSITION_FIELDS",
    "EQUIPMENT_DAILY_SQL_TEMPLATE",
    "NO_EQUIPMENT",
    "RECOVERY_DAY_FIELDS",
    "WindowTechniqueSets",
    "build_body_days",
    "build_cycle_timeline",
    "build_equipment",
    "build_group_days",
    "build_recovery_days",
    "check_daily_invariant",
    "daily_tonnage",
    "equipment_label",
    "load_body_composition_rows",
    "load_cycle_sessions",
    "load_equipment",
    "load_group_days",
    "load_sleep_rows",
    "load_wellness_rows",
    "sleep_stats",
    "wellness_stats",
]
