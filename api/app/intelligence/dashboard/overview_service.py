"""Visao geral do Novo Dashboard - `GET /analytics/dashboard/overview` e
`GET /analytics/dashboard/insights` (plano §7, §8.1 do brief, §10, §11, §12;
F1.6).

Este servico NAO calcula metrica nova: ele COMPOE, DECIDE e EXPLICA. Tudo que
a visao geral mostra ja existe como servico v3 e e chamado por CHAMADA DIRETA
(nunca HTTP interno), UMA vez por requisicao, com o resultado passado adiante:

- `adherence_service.build_adherence`        -> bloco Consistencia
- `exposure_service.get_exercises_progression_batch` (1 query, lote)
                                               -> bloco Progressao (sem score)
- `muscle_summary_service.build_muscle_summary` -> bloco Musculos prioritarios
  (+ duas leituras curtas das duas ultimas semanas, so para a regra "abaixo
  do minimo por 2 semanas", sem planejado/projetado)
- `body_service` (F1.4): peso das 3 fontes com prioridade por dia,
  bioimpedancia e cintura (`body_measurements`)
                                               -> bloco Corpo (`weight-ma-v1`, `waist-v1`)
- `recovery_signals_service.build_recovery_signals` (janela propria de 7d)
                                               -> bloco Recuperacao
- `insight_rules.evaluate_insights`            -> bloco Proxima acao (max. 2)
- `data_quality_service.build_data_quality`    -> veredito sobre os dados

Regras estruturais (brief): sem score unico opaco; cada bloco responde uma
pergunta, aponta o drill-down e carrega a propria `data_quality`; maximo 2
acoes na home; nada muda automaticamente.

Desempenho (§15): o alvo e <= 3 s no shim da Data API com o dataset de 3
anos (`tests/integration/dataapi/test_dashboard_overview_performance.py`).
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

from ...observability import log_event
from ...parallel_reads import ConnFactory, run_blocks
from ...set_types import sql_is_working, sql_set_confirmed
from ...exercise_mapping_canonical import UNRESOLVED_LINK_CONDITION, UNRESOLVED_LINK_JOINS
from .adherence_service import (
    PLAN_ITEMS_SQL,
    SESSION_EXERCISES_SQL,
    _in_clause,
    build_adherence,
    build_session_items,
)
from .body_service import (  # noqa: F401 - reexportados: `body_block`, `pick_daily_weights`... vivem la desde a F1.4
    BIOIMPEDANCE_SQL,
    WEIGHT_SOURCES_SQL,
    body_block,
    load_bioimpedance_latest,
    load_measurement_rows,
    load_weight_rows,
    moving_average_points,
    pick_daily_weights,
    waist_summary,
)
from .coercion import to_date, to_float
from .comparability import MIN_COMPARABLE_FOR_RELIABLE_ANSWER, progression_answer, progression_counts, progression_total
from .context_service import load_context_events, load_deload_ranges, load_training_gap
from .contracts import DataNature, DataQuality, Severity, TargetSource, WindowInfo, WindowKind
from .data_quality_service import build_data_quality
from .exposure_service import get_exercises_progression_batch
from .identity import require_user_id
from .alert_state_service import apply_cooldown
from .insight_rules import (
    InsightContext,
    alert_key,
    below_min_ruler_start,
    disabled_rule_codes,
    enabled_rule_codes,
    evaluate_insights,
    select_next_actions,
)
from .metrics_registry import (
    EXERCISE_PROGRESSION_STATUS_LABELS,
    ExerciseProgressionStatus,
    PlanItemStatus,
    metric_version,
    threshold,
)
from .muscle_summary_service import build_muscle_summary, muscle_group_names, resolve_template_plan
from .recovery_signals_service import build_recovery_signals
from .time_windows import MAX_WINDOW_DAYS, resolve_window

# Grupos listados por categoria no bloco Musculos prioritarios.
MAX_PRIORITY_GROUPS = 5

# Sessoes com ficha lidas para a regra "item pulado no fim da sessao".
MAX_SESSIONS_FOR_SKIPS = 60

_ES_WORKING = sql_is_working("es.set_type")

# Series de trabalho da janela: total e quantas pertencem a exercicios sem
# vinculo canonico (mesma condicao da fila de nao mapeados e da regra de
# qualidade `EXERCISES_WITHOUT_CANONICAL_MAPPING`).
UNMAPPED_SETS_SQL = f"""
SELECT
  COUNT(*) AS total_sets,
  SUM(CASE WHEN {UNRESOLVED_LINK_CONDITION} THEN 1 ELSE 0 END) AS unmapped_sets
FROM `exercises` e
JOIN activities a ON a.id = e.activity_id
JOIN exercise_sets es ON es.exercise_id = e.id AND {_ES_WORKING} AND {sql_set_confirmed('es.reps_pending')}
{UNRESOLVED_LINK_JOINS}
WHERE a.user_id = %s
  AND a.local_date BETWEEN %s AND %s
"""

# Sessoes EXECUTADAS com ficha na janela (mais recentes primeiro), base da
# regra "item pulado no fim da sessao".
PLANNED_SESSIONS_SQL = """
SELECT a.id AS activity_id, a.local_date AS local_date, a.workout_plan_id AS workout_plan_id
FROM activities a
JOIN workout_plans wp ON wp.id = a.workout_plan_id AND wp.user_id = a.user_id
WHERE a.user_id = %s
  AND a.local_date BETWEEN %s AND %s
  AND (a.session_status IS NULL OR a.session_status <> 'in_progress')
ORDER BY a.local_date DESC, a.id DESC
LIMIT %s
"""

_METRIC_VERSIONS: Mapping[str, str] = {
    key: metric_version(key)
    for key in (
        "adherence",
        "exercise_progression_status",
        "progression_summary",
        "weekly_volume_status",
        "effective_sets_stimulus",
        "role_sets",
        "muscle_frequency",
        "body_weight_ma7",
        "waist_cm",
        "readiness",
        "recovery_signals",
        "insight_rules",
        "data_quality",
        # F2.2: a decisao de mostrar/silenciar tem versao propria - mudar a
        # regra do cooldown muda o que a tela mostra, e isso precisa aparecer
        # no payload como qualquer outra formula.
        "alert_cooldown",
    )
}

_QUALITY_ORDER = [DataQuality.HIGH, DataQuality.MEDIUM, DataQuality.LOW, DataQuality.INSUFFICIENT]


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------


def metric(
    value: Optional[float],
    *,
    unit: Optional[str],
    nature: DataNature,
    version_key: str,
    quality: DataQuality = DataQuality.HIGH,
    limitations: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """Envelope `Metric` (§8.4): `None` continua `None`, nunca zero."""
    return {
        "value": round(value, 2) if isinstance(value, float) else value,
        "unit": unit,
        "nature": nature,
        "metric_version": _METRIC_VERSIONS[version_key],
        "data_quality": quality,
        "limitations": list(limitations or []),
    }


def _quality(value: Any) -> DataQuality:
    if isinstance(value, DataQuality):
        return value
    try:
        return DataQuality(str(value))
    except ValueError:
        return DataQuality.LOW


def worst_quality(*qualities: Any) -> DataQuality:
    worst = DataQuality.HIGH
    for raw in qualities:
        quality = _quality(raw)
        if _QUALITY_ORDER.index(quality) > _QUALITY_ORDER.index(worst):
            worst = quality
    return worst


def _value(m: Optional[Mapping[str, Any]]) -> Optional[float]:
    if not m or m.get("value") is None:
        return None
    return float(m["value"])


def _rows(conn: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall() or []


def _source_value(source: Any) -> str:
    return source.value if hasattr(source, "value") else str(source)


def _fmt(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}".replace(".", ",")


# ---------------------------------------------------------------------------
# Blocos (funcoes puras sobre os payloads dos servicos)
# ---------------------------------------------------------------------------


def consistency_block(adherence: Mapping[str, Any]) -> dict[str, Any]:
    slots = list(adherence.get("slots", []))
    done = len([slot for slot in slots if slot.get("status") == "done"])
    rate = _value(adherence.get("rate"))
    completed = int(_value(adherence.get("completed_sessions")) or 0)
    expected = _value(adherence.get("expected_sessions"))
    planned = int(_value(adherence.get("planned_sessions")) or 0)
    unplanned = int(_value(adherence.get("unplanned_sessions")) or 0)
    next_slot = adherence.get("next_slot")

    if not adherence.get("has_template"):
        answer = (
            f"{completed} sessões no período, sem Modelo atribuído: não há planejado contra o que comparar."
        )
    elif rate is None:
        answer = f"{completed} sessões no período; aderência não calculável."
    else:
        answer = (
            f"{_fmt(rate * 100, 0)}% do planejado: {planned} sessões com ficha para {_fmt(expected, 0)} esperadas"
            + (f", mais {unplanned} avulsas" if unplanned else "")
            + f"; ciclo em {done}/{len(slots)}"
            + (f", próximo: {next_slot.get('name') or 'slot ' + str(next_slot.get('sequence'))}." if next_slot else ".")
        )

    return {
        "question": "Cumpri o planejado?",
        "answer": answer,
        "drilldown_href": "/dashboard/treinos",
        "data_quality": _quality(adherence.get("data_quality")),
        "limitations": list(adherence.get("limitations", [])),
        "has_template": bool(adherence.get("has_template")),
        "template_name": adherence.get("template_name"),
        "cycle_length_days": adherence.get("cycle_length_days"),
        "next_slot": next_slot,
        "cycle_slots_done": done,
        "cycle_slots_total": len(slots),
        "expected_sessions": adherence["expected_sessions"],
        "completed_sessions": adherence["completed_sessions"],
        "planned_sessions": adherence["planned_sessions"],
        "unplanned_sessions": adherence["unplanned_sessions"],
        "missed_sessions": adherence["missed_sessions"],
        "rate": adherence["rate"],
        "cycle_rate": adherence["cycle_rate"],
    }


def progression_block(
    items: Sequence[Mapping[str, Any]],
    *,
    truncated: bool,
    exposures_per_exercise: int,
    window: WindowInfo,
) -> dict[str, Any]:
    """Contagem por status, com TODOS os exercicios de cada status (a tela
    mostra alguns e expande o resto). SEM score: as contagens sao o resumo, e
    cada uma e decomponivel. A lista ja e limitada pelos exercicios da janela."""
    by_status: dict[str, list[Mapping[str, Any]]] = {status.value: [] for status in ExerciseProgressionStatus}
    for item in items:
        by_status.setdefault(str(item["status"]), []).append(item)

    def _public(item: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "exercise_id": int(item["exercise_id"]),
            "name": item.get("name"),
            "status": item["status"],
            "status_reasons": list(item.get("status_reasons", [])),
            "fatigue_context": item.get("fatigue_context"),
            "comparable_exposures": int(item.get("comparable_exposures") or 0),
            "exposures_in_window": int(item.get("exposures_in_window") or 0),
            "last_local_date": to_date(item.get("last_local_date")),
        }

    counts = []
    for status in ExerciseProgressionStatus:
        bucket = sorted(
            by_status.get(status.value, []),
            key=lambda item: (-(int(item.get("comparable_exposures") or 0)), str(item.get("name") or "")),
        )
        counts.append(
            {
                "status": status.value,
                "label": EXERCISE_PROGRESSION_STATUS_LABELS[status],
                "count": len(bucket),
                "exercises": [_public(item) for item in bucket],
            }
        )

    # Contagem e frase vêm do dono (`comparability`): a mesma de
    # `/dashboard/progressao` (`exercise_progression`).
    summary = progression_counts(items)
    total = progression_total(summary)
    comparable = summary["comparable_total"]

    limitations: list[str] = []
    answer = progression_answer(summary)
    if total == 0:
        quality = DataQuality.INSUFFICIENT
    elif comparable == 0:
        quality = DataQuality.INSUFFICIENT
        limitations.append("Progressão exige ao menos 2 exposições na mesma máquina/convenção de carga.")
    else:
        quality = DataQuality.HIGH if comparable >= MIN_COMPARABLE_FOR_RELIABLE_ANSWER else DataQuality.MEDIUM
        if comparable < MIN_COMPARABLE_FOR_RELIABLE_ANSWER:
            limitations.append(
                f"Menos de {MIN_COMPARABLE_FOR_RELIABLE_ANSWER} exercícios comparáveis: a leitura geral ainda é fraca (§7)."
            )
    if truncated:
        limitations.append(
            "Lote de exposições cortado pelo teto de 1 MB da Data API: alguns exercícios foram lidos com "
            "menos exposições do que o limite por exercício."
        )
        quality = worst_quality(quality, DataQuality.MEDIUM)
    limitations.append(
        f"Cada exercício é classificado pelas últimas {exposures_per_exercise} exposições do período "
        "(o drill-down lê até 30)."
    )

    return {
        "question": "Estou evoluindo?",
        "answer": answer,
        "drilldown_href": "/dashboard/progressao",
        "data_quality": quality,
        "limitations": limitations,
        "exercises_total": total,
        "comparable_total": comparable,
        "counts": counts,
        "exposures_per_exercise": int(exposures_per_exercise),
        "truncated": bool(truncated),
    }


def _priority_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "muscle_group_id": int(item["muscle_group_id"]),
        "muscle_group": item["muscle_group"],
        "status": item["status"],
        "weekly_effective_sets": item["realized"]["weekly_effective_sets"],
        "target_min": item["targets"]["min"],
        "target_max": item["targets"]["max"],
        "target_source": item["targets"]["source"],
        "direct_sets": item["realized"]["direct_sets"],
        "indirect_sets": item["realized"]["indirect_sets"],
        "direct_sessions": item["frequency"]["direct_sessions"],
        "hours_since_last_stimulus": item["hours_since_last_stimulus"],
        "last_direct_local_date": to_date(item.get("last_direct_local_date")),
    }


def priority_muscles_block(muscles: Mapping[str, Any], *, window: WindowInfo) -> dict[str, Any]:
    items = list(muscles.get("items", []))
    with_target = [item for item in items if _source_value(item["targets"]["source"]) != "fallback_generic"]
    lag_hours = float(threshold("insight_frequency_lag_hours"))

    # `BELOW_TARGET`/`ABOVE_TARGET` sao os estados de `template_plan` (meta do
    # proprio Modelo `equipment_series`, sem min/max). Grupo FORA do Modelo
    # (alvo 0) nao e "excesso": o realizado nele e trabalho extra.
    below = [item for item in with_target if item["status"] in ("BELOW_MIN", "BELOW_TARGET")]
    above = [
        item
        for item in with_target
        if item["status"] in ("ABOVE_MAX", "ABOVE_TARGET") and (_value(item["targets"]["target"]) or 0.0) > 0.0
    ]
    lagging = []
    if window.days >= 7:
        for item in with_target:
            hours = _value(item.get("hours_since_last_stimulus"))
            trained = (_value(item["realized"]["physical_sets"]) or 0.0) > 0
            if not trained or (hours is not None and hours > lag_hours):
                lagging.append(item)

    def _sort(bucket: list[Mapping[str, Any]], reverse: bool) -> list[Mapping[str, Any]]:
        return sorted(
            bucket,
            key=lambda item: (_value(item["realized"]["weekly_effective_sets"]) or 0.0, item["muscle_group"]),
            reverse=reverse,
        )

    below = _sort(below, reverse=False)
    above = _sort(above, reverse=True)
    lagging = sorted(lagging, key=lambda item: -(_value(item.get("hours_since_last_stimulus")) or 1e9))

    source = muscles.get("target_source", TargetSource.FALLBACK_GENERIC)
    limitations = list(muscles.get("limitations", []))
    if not with_target:
        quality = DataQuality.LOW
        answer = "Sem meta cadastrada: não dá para dizer quem está atrasado ou em excesso."
        limitations.append(
            "Nenhum grupo tem meta (Modelo atribuído sem Meta de Ativação): 'abaixo/acima' não existe."
        )
    elif not items or all((_value(item["realized"]["physical_sets"]) or 0.0) <= 0 for item in items):
        quality = DataQuality.INSUFFICIENT
        answer = "Nenhuma série no período: os grupos com meta estão todos sem estímulo."
    else:
        quality = _quality(muscles.get("data_quality"))
        parts = []
        if below:
            parts.append(f"{len(below)} abaixo do mínimo ({', '.join(i['muscle_group'] for i in below[:3])}{'…' if len(below) > 3 else ''})")
        if above:
            parts.append(f"{len(above)} acima do máximo ({', '.join(i['muscle_group'] for i in above[:3])}{'…' if len(above) > 3 else ''})")
        if lagging:
            parts.append(f"{len(lagging)} sem estímulo há mais de {int(lag_hours // 24)} dias")
        answer = (
            f"De {len(with_target)} grupos com meta: " + "; ".join(parts) + "."
            if parts
            else f"Os {len(with_target)} grupos com meta estão dentro da faixa e com frequência em dia."
        )

    return {
        "question": "Quais grupos estão atrasados ou em excesso?",
        "answer": answer,
        "drilldown_href": "/dashboard/musculos",
        "data_quality": quality,
        "limitations": limitations,
        "groups_total": len(items),
        "groups_with_target": len(with_target),
        "target_source": source,
        "below_min": [_priority_item(item) for item in below[:MAX_PRIORITY_GROUPS]],
        "above_max": [_priority_item(item) for item in above[:MAX_PRIORITY_GROUPS]],
        "frequency_lagging": [_priority_item(item) for item in lagging[:MAX_PRIORITY_GROUPS]],
    }


def recovery_block(recovery: Mapping[str, Any], *, window: WindowInfo) -> dict[str, Any]:
    groups = list(recovery.get("groups", []))
    statuses = Counter(str(group.get("readiness_status")) for group in groups)
    assessment = recovery.get("assessment", {})
    return {
        "question": "Estou recuperando bem?",
        "answer": assessment.get("headline") or "Sem leitura de recuperação no período.",
        "drilldown_href": "/dashboard/recovery",
        "data_quality": _quality(recovery.get("data_quality")),
        "limitations": list(recovery.get("limitations", [])),
        "window": window,
        "level": assessment.get("level", Severity.INFO),
        "readiness_reading": assessment.get("readiness_reading", "UNKNOWN"),
        "headline": assessment.get("headline") or "",
        "uncertainty": assessment.get("uncertainty", DataQuality.INSUFFICIENT),
        "allow_intensification": bool(assessment.get("allow_intensification", True)),
        "intensification_blocked_reason": assessment.get("intensification_blocked_reason"),
        "groups_total": len(groups),
        "groups_ready": statuses.get("READY", 0),
        "groups_reduced_dose": statuses.get("REDUCED_DOSE", 0),
        "groups_no_direct_work": statuses.get("NO_DIRECT_WORK", 0),
        "signals_agreeing": len(assessment.get("agreeing_codes", [])),
        "signals_diverging": len(assessment.get("diverging_codes", [])),
        "signals_unavailable": len(assessment.get("unavailable_codes", [])),
        "reassess_after_hours": assessment.get("reassess_after_hours"),
    }


def next_actions_block(insights: Sequence[Mapping[str, Any]], *, max_items: int) -> dict[str, Any]:
    selected = select_next_actions(insights, max_items)
    if not insights:
        answer = "Nenhuma ação sugerida agora — continue o plano."
        quality = DataQuality.HIGH
    else:
        answer = (
            f"{len(selected)} de {len(insights)} sugestões em destaque; nada muda sem sua decisão."
        )
        quality = worst_quality(*(item["data_quality"] for item in selected))
    return {
        "question": "Qual é a próxima ação?",
        "answer": answer,
        "drilldown_href": "/dashboard/insights",
        "data_quality": quality,
        "limitations": [
            "Toda sugestão é determinística e explicável; nenhuma muda ficha, meta ou prescrição sozinha."
        ],
        "items": selected,
        "total_triggered": len(insights),
        "max_items": int(max_items),
    }


def skipped_items_from_sessions(
    sessions: Sequence[Mapping[str, Any]],
    plan_items_by_plan: Mapping[int, Sequence[Mapping[str, Any]]],
    performed_by_activity: Mapping[int, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """Por item da ficha: em quantas sessoes (com aquela ficha) ele foi
    PULADO. Reusa `build_session_items` - a MESMA derivacao de status da tela
    de treinos (§5), nunca uma segunda."""
    acc: dict[int, dict[str, Any]] = {}
    for session in sessions:
        plan_id = session.get("workout_plan_id")
        if plan_id is None:
            continue
        plan_items = list(plan_items_by_plan.get(int(plan_id), []))
        if not plan_items:
            continue
        items = build_session_items(
            plan_items, performed_by_activity.get(int(session["activity_id"]), []), classify_missing=True
        )
        total = len(plan_items)
        for item in items:
            plan_exercise_id = item.get("plan_exercise_id")
            if plan_exercise_id is None:
                continue
            entry = acc.setdefault(
                int(plan_exercise_id),
                {
                    "plan_exercise_id": int(plan_exercise_id),
                    "name": item.get("planned_name"),
                    "plan_sequence": item.get("plan_sequence"),
                    "plan_items_total": total,
                    "sessions": 0,
                    "skipped": 0,
                    "last_local_date": None,
                },
            )
            entry["sessions"] += 1
            if item["status"] == PlanItemStatus.SKIPPED.value:
                entry["skipped"] += 1
                day = to_date(session.get("local_date"))
                if day and (entry["last_local_date"] is None or day > entry["last_local_date"]):
                    entry["last_local_date"] = day
    return sorted(acc.values(), key=lambda entry: (-entry["skipped"], entry["plan_exercise_id"]))


# ---------------------------------------------------------------------------
# Leituras proprias (poucas consultas, cada uma UMA vez)
# ---------------------------------------------------------------------------


def _load_skipped_items(conn: Any, *, user_id: int, window: WindowInfo) -> list[dict[str, Any]]:
    sessions = _rows(conn, PLANNED_SESSIONS_SQL, (user_id, window.date_from, window.date_to, MAX_SESSIONS_FOR_SKIPS))
    if not sessions:
        return []
    activity_ids = [int(row["activity_id"]) for row in sessions]
    plan_ids = sorted({int(row["workout_plan_id"]) for row in sessions if row.get("workout_plan_id") is not None})
    plan_rows = _rows(conn, _in_clause(PLAN_ITEMS_SQL, plan_ids), (user_id, *plan_ids)) if plan_ids else []
    performed_rows = _rows(conn, _in_clause(SESSION_EXERCISES_SQL, activity_ids), (user_id, *activity_ids))

    plan_items_by_plan: dict[int, list[Mapping[str, Any]]] = {}
    for row in plan_rows:
        plan_items_by_plan.setdefault(int(row["plan_id"]), []).append(row)
    performed_by_activity: dict[int, list[Mapping[str, Any]]] = {}
    for row in performed_rows:
        performed_by_activity.setdefault(int(row["activity_id"]), []).append(row)
    return skipped_items_from_sessions(sessions, plan_items_by_plan, performed_by_activity)


def _load_unmapped_sets(conn: Any, *, user_id: int, window: WindowInfo) -> tuple[int, int]:
    rows = _rows(conn, UNMAPPED_SETS_SQL, (user_id, window.date_from, window.date_to))
    row = rows[0] if rows else {}
    return int(to_float(row.get("total_sets")) or 0), int(to_float(row.get("unmapped_sets")) or 0)


def _week_windows(window: WindowInfo, week_days: int) -> tuple[Optional[WindowInfo], Optional[WindowInfo]]:
    """As duas ultimas semanas terminando em `date_to`: (corrente, anterior).
    Regua da regra "abaixo do minimo" - SEMANAL, como as metas, e independente
    do ciclo do Modelo. `None` quando as duas nao cabem no teto de 365 dias."""
    if week_days < 1 or week_days * 2 > MAX_WINDOW_DAYS:
        return None, None
    current = resolve_window(
        WindowKind.CUSTOM,
        today=window.date_to,
        date_from=window.date_to - timedelta(days=week_days - 1),
        date_to=window.date_to,
        timezone_name=window.timezone,
    )
    previous = resolve_window(
        WindowKind.CUSTOM,
        today=window.date_to,
        date_from=window.date_to - timedelta(days=2 * week_days - 1),
        date_to=window.date_to - timedelta(days=week_days),
        timezone_name=window.timezone,
    )
    return current, previous


def _strip_private(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if not str(key).startswith("_")}


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------


def build_overview(
    conn: Any,
    *,
    user_id: Any,
    window: WindowInfo,
    now_local: datetime,
    training_type_id: Optional[int] = None,
    training_type_source: TargetSource = TargetSource.FALLBACK_GENERIC,
    max_next_actions: Optional[int] = None,
    include_data_quality: bool = True,
    conn_factory: Optional[ConnFactory] = None,
) -> dict[str, Any]:
    """Payload de `GET /analytics/dashboard/overview`. Cada servico v3 e
    chamado UMA vez; o resultado alimenta o bloco e as regras.

    `include_data_quality=False` (PERF-02, usado por `build_insights`): pula o
    veredito de qualidade dos dados (`build_data_quality`, ~10 statements), que
    so alimenta `data_quality_overall`/`data_quality_issues` da visao geral e
    NAO entra nas regras nem no cooldown. Os insights saem identicos; o payload
    devolvido vem sem essas duas chaves (nenhum consumidor de `/insights` as le)."""
    owner = require_user_id(user_id)
    max_actions = int(max_next_actions if max_next_actions is not None else threshold("overview_max_next_actions"))
    timings: dict[str, float] = {}

    def _timed(label: str, fn):
        started = datetime.now()
        result = fn()
        timings[label] = round((datetime.now() - started).total_seconds(), 3)
        return result

    # PERF-05: os blocos 1-7 so LEEM e nao dependem uns dos outros. Com
    # `conn_factory` (a rota), rodam em paralelo, cada um com a propria conexao
    # (`parallel_reads.run_blocks`); sem ele (testes, scripts), em sequencia com
    # `conn`, na MESMA ordem de antes. As funcoes e os argumentos de cada
    # leitura nao mudaram - so "quando" cada bloco roda.

    # 1) Consistencia
    def _consistency_block(c: Any) -> dict[str, Any]:
        return _timed("adherence", lambda: build_adherence(c, user_id=owner, window=window))

    # 2) Musculos (janela principal; prontidao vem do bloco de recuperacao,
    #    nao e calculada duas vezes). As metas sao resolvidas UMA vez e
    #    passadas adiante para as leituras dos ciclos.
    def _muscles_block(c: Any) -> dict[str, Any]:
        targets_by_group: dict[int, dict[str, float]] = {}
        if training_type_id is not None:
            # M12 (migracao 052): mesma resolucao por data de `build_muscle_summary`
            # - resolvida UMA vez aqui e passada adiante.
            from ...training_type_targets import resolve_targets_for_window

            targets_by_group = _timed(
                "targets", lambda: resolve_targets_for_window(training_type_id, window.date_to)
            )
        # Meta do proprio Modelo `equipment_series` (`template_plan`): resolvida
        # UMA vez por requisicao e reaproveitada nas tres leituras abaixo.
        explicit_target = training_type_id is not None and training_type_source is TargetSource.EXPLICIT
        template_plan = None if explicit_target else _timed("template_plan", lambda: resolve_template_plan(c, owner))
        # Nomes dos grupos musculares: tabela de referencia, igual nas tres
        # leituras abaixo. Resolvida UMA vez por requisicao (M29) - eram 3 leituras
        # identicas, e no caminho da RDS Data API cada uma e uma ida ao servico.
        group_names = _timed("group_names", lambda: muscle_group_names(c))
        muscles = _timed(
            "muscles",
            lambda: build_muscle_summary(
                c,
                user_id=owner,
                window=window,
                training_type_id=training_type_id,
                training_type_source=training_type_source,
                include_readiness=False,
                now_local=now_local,
                targets_by_group=targets_by_group,
                template_plan=template_plan,
                template_plan_resolved=True,
                group_names=group_names,
            ),
        )

        # 2b) Duas ultimas semanas (so status do realizado) para a regra de
        #     "abaixo do minimo por 2 semanas". A regua e semanal, como as metas,
        #     e nao depende do ciclo do Modelo (com ciclo de 56 dias, 2 ciclos
        #     eram 112 dias). A semana corrente reusa a leitura principal quando
        #     a janela E a semana.
        week_days = int(threshold("insight_below_min_week_days"))
        current_week, previous_week = _week_windows(window, week_days)
        muscles_current = None
        muscles_previous = None
        if current_week and previous_week:
            if current_week.date_from == window.date_from and current_week.date_to == window.date_to:
                muscles_current = muscles
            else:
                muscles_current = _timed(
                    "muscles_current_week",
                    lambda: build_muscle_summary(
                        c, user_id=owner, window=current_week, training_type_id=training_type_id,
                        training_type_source=training_type_source, include_readiness=False,
                        now_local=now_local, status_only=True, targets_by_group=targets_by_group,
                        template_plan=template_plan, template_plan_resolved=True,
                        group_names=group_names,
                    ),
                )
            muscles_previous = _timed(
                "muscles_previous_week",
                lambda: build_muscle_summary(
                    c, user_id=owner, window=previous_week, training_type_id=training_type_id,
                    training_type_source=training_type_source, include_readiness=False,
                    now_local=now_local, status_only=True, targets_by_group=targets_by_group,
                    template_plan=template_plan, template_plan_resolved=True,
                    group_names=group_names,
                ),
            )

        # 3) Progressao em lote (1 consulta) - usa os mesmos `group_names`.
        progression = _timed(
            "progression_batch",
            lambda: get_exercises_progression_batch(c, user_id=owner, window=window, group_names=group_names),
        )
        return {
            "muscles": muscles,
            "muscles_current": muscles_current,
            "muscles_previous": muscles_previous,
            "progression": progression,
        }

    # 4) Recuperacao (janela propria: 7 dias terminando em date_to)
    recovery_window = resolve_window(WindowKind.ROLLING_7D, today=window.date_to, timezone_name=window.timezone)

    def _recovery_block(c: Any) -> dict[str, Any]:
        return _timed("recovery", lambda: build_recovery_signals(c, user_id=owner, window=recovery_window))

    # 5) Corpo - MESMAS funcoes de `body_service` que servem
    #    `/analytics/dashboard/body` (uma implementacao da media movel e da
    #    cintura, nao duas).
    # 5b) Contexto (F2.1): eventos manuais + derivados da janela e o intervalo
    #     desde a ultima sessao. Alimentam `PERFORMANCE_DROP_WITH_CONTEXT_EVENT`
    #     e `TRAINING_PAUSE_DETECTED`; a visao geral nao ganha bloco proprio -
    #     o contexto aparece DENTRO dos insights e na tela `/dashboard/contexto`.
    # 6) Insumos das regras: itens pulados e series sem mapping
    def _body_context_block(c: Any) -> dict[str, Any]:
        weight_rows, weight_limitations = _timed("weight", lambda: load_weight_rows(c, user_id=owner, window=window))
        bio_latest = _timed("bioimpedance", lambda: load_bioimpedance_latest(c, user_id=owner, date_to=window.date_to))
        measurement_rows, measurements_available = _timed(
            "measurements", lambda: load_measurement_rows(c, user_id=owner, window=window)
        )
        context_events, context_available, context_limitations = _timed(
            "context_events", lambda: load_context_events(c, user_id=owner, window=window)
        )
        training_gap = _timed("training_gap", lambda: load_training_gap(c, user_id=owner, window=window))
        deload_ranges = _timed(
            "deload_ranges",
            lambda: load_deload_ranges(
                c, user_id=owner, window=window, since=below_min_ruler_start(window.date_to)
            ),
        )
        skipped = _timed("skipped_items", lambda: _load_skipped_items(c, user_id=owner, window=window))
        total_sets, unmapped_sets = _timed("unmapped_sets", lambda: _load_unmapped_sets(c, user_id=owner, window=window))
        return {
            "weight_rows": weight_rows,
            "weight_limitations": weight_limitations,
            "bio_latest": bio_latest,
            "measurement_rows": measurement_rows,
            "measurements_available": measurements_available,
            "context_events": context_events,
            "context_available": context_available,
            "context_limitations": context_limitations,
            "training_gap": training_gap,
            "deload_ranges": deload_ranges,
            "skipped": skipped,
            "total_sets": total_sets,
            "unmapped_sets": unmapped_sets,
        }

    # 7) Qualidade dos dados (veredito) - so a visao geral exibe; as regras
    #    e o cooldown nao leem (PERF-02: `/insights` nao paga por ele).
    def _data_quality_block(c: Any) -> dict[str, Any]:
        return _timed(
            "data_quality",
            lambda: build_data_quality(
                c, user_id=owner, window=window, now_local=now_local, training_type_id=training_type_id
            ),
        )

    blocks_to_run = [_consistency_block, _muscles_block, _recovery_block, _body_context_block]
    if include_data_quality:
        blocks_to_run.append(_data_quality_block)
    results = run_blocks(blocks_to_run, conn=conn, conn_factory=conn_factory)
    adherence, muscles_part, recovery, body_part = results[:4]
    dq = results[4] if include_data_quality else None

    cycle_days = int(adherence.get("cycle_length_days") or threshold("insight_default_cycle_days"))
    muscles = muscles_part["muscles"]
    muscles_current = muscles_part["muscles_current"]
    muscles_previous = muscles_part["muscles_previous"]
    progression_items, truncated, per_exercise = muscles_part["progression"]
    body = body_block(
        weight_rows=body_part["weight_rows"],
        bioimpedance=body_part["bio_latest"],
        window=window,
        extra_limitations=body_part["weight_limitations"],
        waist=waist_summary(
            body_part["measurement_rows"], window=window, table_available=body_part["measurements_available"]
        ),
    )
    context_events = body_part["context_events"]
    context_available = body_part["context_available"]
    _context_limitations = body_part["context_limitations"]
    training_gap = body_part["training_gap"]
    skipped = body_part["skipped"]
    total_sets = body_part["total_sets"]
    unmapped_sets = body_part["unmapped_sets"]

    # 8) Regras
    ctx = InsightContext(
        window=window,
        cycle_days=cycle_days,
        muscles=muscles,
        muscles_current_week=muscles_current,
        muscles_previous_week=muscles_previous,
        recovery=recovery,
        progression=progression_items,
        skipped_items=skipped,
        body={
            "weight_ma7_change_pct": body["weight_ma7_change_pct"],
            "weight_ma7_latest": body["weight_ma7_latest"],
            "weight_ma7_first": {"value": body["_weight_ma7_first"]},
            "span_days": body["_span_days"],
            "limitations": [l for l in body["limitations"] if l not in body["_waist"]["limitations"]],
            "waist": body["_waist"],
        },
        working_sets_total=total_sets,
        unmapped_working_sets=unmapped_sets,
        context_events=context_events,
        context_events_available=context_available,
        days_since_last_session=training_gap["days_since_last_session"],
        last_session_local_date=training_gap["last_session_local_date"],
        sessions_before_pause=training_gap["sessions_before"],
        deload_ranges=body_part.get("deload_ranges", ()),
    )
    insights, counts = _timed("insight_rules", lambda: evaluate_insights(ctx))

    # 8b) Cooldown (F2.2): o que o usuario JA viu e continua igual fica de
    #     fora. `now_local` e o relogio da requisicao no fuso do usuario - o
    #     mesmo que as janelas usam; nenhum `datetime.now()` por aqui, para a
    #     decisao ser reproduzivel num teste. A escrita do estado acontece
    #     dentro de `apply_cooldown` (ver o cabecalho de `alert_state_service`
    #     para o porque de uma leitura gravar).
    cooldown = _timed(
        "alert_cooldown",
        lambda: apply_cooldown(
            conn, user_id=owner, insights=insights, now=now_local, alert_key_for=alert_key
        ),
    )
    visible_insights = cooldown["visible"]
    suppressed_insights = cooldown["suppressed"]

    blocks = {
        "consistency": consistency_block(adherence),
        "progression": progression_block(
            progression_items, truncated=truncated, exposures_per_exercise=per_exercise, window=window
        ),
        "priority_muscles": priority_muscles_block(muscles, window=window),
        "body": _strip_private(body),
        "recovery": recovery_block(recovery, window=recovery_window),
        # A home mostra as acoes que PASSARAM pelo cooldown: eram justamente as
        # duas vagas daqui que 19 associacoes de tres semanas atras ocupavam.
        "next_actions": next_actions_block(visible_insights, max_items=max_actions),
    }
    envelope_quality = worst_quality(*(block["data_quality"] for block in blocks.values()))

    limitations = [
        "Cada bloco carrega a própria confiança e limitações; a confiança do envelope é a pior delas.",
        *cooldown["limitations"],
    ]
    if window.kind is not WindowKind.CYCLE and not adherence.get("has_template"):
        limitations.append(
            f"Sem Modelo atribuído, 'ciclo' das regras é de {cycle_days} dias (metas semanais)."
        )

    log_event(
        "dashboard.computed",
        block="overview",
        user_id=owner,
        window=window.kind.value,
        days=window.days,
        timings=timings,
        insights=len(insights),
        insights_visible=len(visible_insights),
        insights_suppressed=len(suppressed_insights),
        next_actions=len(blocks["next_actions"]["items"]),
    )
    log_event(
        "insight.emitted",
        user_id=owner,
        window=window.kind.value,
        counts=counts,
        fingerprints=[item["fingerprint"] for item in visible_insights],
    )

    payload: dict[str, Any] = {
        "window": window,
        "timezone": window.timezone,
        "metric_versions": dict(_METRIC_VERSIONS),
        "data_quality": envelope_quality,
        "limitations": limitations,
        "blocks": blocks,
        # A origem efetiva vem do bloco de musculos: e la que `template_plan`
        # (meta do proprio Modelo) e decidido.
        "target_source": muscles["target_source"],
        "training_type_id": muscles["training_type_id"],
        "_insights": visible_insights,
        "_suppressed_insights": suppressed_insights,
        # F3.2 - insumos que o `intelligence_package` reaproveita SEM ir ao
        # banco de novo. Todos ja foram lidos acima para alimentar as regras;
        # o pacote so precisava de um caminho de saida para eles. E por isso
        # que o pacote custa exatamente os mesmos statements do overview - se
        # um dia ele voltar a chamar `build_context`/`build_muscle_summary`
        # por conta propria, o teto de statements do dataset pequeno reprova.
        "_context_events": context_events,
        "_context_events_available": context_available,
        "_context_limitations": _context_limitations,
        "_muscle_items": list(muscles.get("items", [])),
        "_training_gap": training_gap,
        "_cooldown_available": cooldown["available"],
        "_cooldown_limitations": cooldown["limitations"],
        "_rule_counts": counts,
        "_timings": timings,
    }
    if dq is not None:
        payload["data_quality_overall"] = dq["overall"]
        payload["data_quality_issues"] = len(dq["items"])
    return payload


def build_insights(
    conn: Any,
    *,
    user_id: Any,
    window: WindowInfo,
    now_local: datetime,
    training_type_id: Optional[int] = None,
    training_type_source: TargetSource = TargetSource.FALLBACK_GENERIC,
    limit: int = 20,
    offset: int = 0,
    include_suppressed: bool = False,
    conn_factory: Optional[ConnFactory] = None,
) -> dict[str, Any]:
    """Payload de `GET /analytics/dashboard/insights`: os insights disparados
    (deduplicados por fingerprint), com paginacao simples. Reusa
    `build_overview` - e a mesma avaliacao, nao uma segunda.

    F2.2: por padrao a lista traz so o que PASSOU pelo cooldown, e a contagem
    do que ficou silenciado vem ao lado. `include_suppressed=True` acrescenta
    os silenciados ao FIM da lista (cada um com o bloco `cooldown` dizendo ate
    quando e por que) - a tela precisa poder mostrar "o que voce ja viu" sem
    que um cartao desapareca sem explicacao."""
    overview = build_overview(
        conn,
        user_id=user_id,
        window=window,
        now_local=now_local,
        training_type_id=training_type_id,
        training_type_source=training_type_source,
        # PERF-02: o veredito de qualidade dos dados nao entra nos insights
        # nem no cooldown - so na visao geral.
        include_data_quality=False,
        conn_factory=conn_factory,
    )
    visible = overview["_insights"]
    suppressed = overview["_suppressed_insights"]
    insights = [*visible, *suppressed] if include_suppressed else visible
    limit = max(1, int(limit))
    offset = max(0, int(offset))
    page = insights[offset : offset + limit]
    return {
        "window": window,
        "metric_versions": {
            "insight_rules": _METRIC_VERSIONS["insight_rules"],
            "alert_cooldown": _METRIC_VERSIONS["alert_cooldown"],
        },
        "data_quality": worst_quality(*(item["data_quality"] for item in page)) if page else DataQuality.HIGH,
        "limitations": [
            "Insights são determinísticos e explicáveis; nenhum muda ficha, meta ou prescrição.",
            *overview["_cooldown_limitations"],
        ],
        "items": page,
        "total": len(insights),
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(page) < len(insights),
        "rules_evaluated": len(enabled_rule_codes()),
        "rules_disabled": disabled_rule_codes(),
        "suppressed_total": len(suppressed),
        "include_suppressed": bool(include_suppressed),
        "cooldown_available": bool(overview["_cooldown_available"]),
    }


__all__ = [
    "BIOIMPEDANCE_SQL",
    "MAX_PRIORITY_GROUPS",
    "UNMAPPED_SETS_SQL",
    "WEIGHT_SOURCES_SQL",
    "body_block",
    "build_insights",
    "build_overview",
    "consistency_block",
    "metric",
    "moving_average_points",
    "next_actions_block",
    "pick_daily_weights",
    "priority_muscles_block",
    "progression_block",
    "recovery_block",
    "skipped_items_from_sessions",
    "worst_quality",
]
