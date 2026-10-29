"""Coletor do relatorio em Markdown (`GET /reports/markdown`).

Substitui o relatorio PDF + IA (`reports_core.py`/`ReportDataService`). Nao
calcula numero novo de treino: reaproveita os MESMOS servicos v3 que alimentam
as abas do dashboard, para o arquivo baixado bater com o que a tela mostra:

- grupos musculares -> `muscle_summary_service.build_muscle_summary`
  (series fisicas, series efetivas ja com a regra de tecnicas da Fase 5,
  series/semana, RPE medio, frequencia, meta e status);
- macro-grupos -> `methodology_volume_service.load_methodology_groups` /
  `load_volume_classes` (cadastro da 057);
- recuperacao -> `recovery_signals_service.build_recovery_signals`;
- corpo -> `body_service.build_body`;
- cardio -> `cardio_service.build_cardio` (janela inteira, sem o teto de 30
  dias do relatorio antigo);
- ciclos -> `template_assignments.segment_window` + `adherence_service.
  build_adherence` + o Modelo vigente (`get_template_if_assigned`).

Duas leituras sao PROPRIAS deste relatorio, porque nenhum servico do dashboard
as entrega do jeito certo:

1. **Tonelagem por grupo** (`TONNAGE_BY_GROUP_SQL`). O par
   equipamento<->exercicio entra no JOIN
   (`load_conventions.load_convention_select_expr`, NULL = total), so contam
   as series de TRABALHO (`set_types.sql_is_working`) e cada serie vale
   reps x kg x `stimulus_weight` do grupo - mesma ponderacao de
   `_fetch_group_tonnage_rows`. `assisted` (o peso ALIVIA o movimento) e
   `bodyweight` (a carga movida nao esta na serie) ficam de fora, e quantas
   series sairam e declarado. A carga ja e gravada como TOTAL (D12): nada e
   convertido. Desde o M58 o dashboard usa a MESMA regra
   (`load_conventions.TONNAGE_EXCLUDED_CONVENTIONS`/`sql_counts_in_tonnage`);
   a leitura continua propria porque o relatorio quer so series de TRABALHO,
   ponderadas, e a contagem global das excluidas.
2. **Medias de sono e wearable**. Os sinais de recuperacao trazem mediana,
   baseline e direcao, mas nao sono profundo/REM nem a media simples da
   janela. A media sai das MESMAS linhas diarias do detalhamento
   (`daily_breakdown.sleep_stats`/`wellness_stats`).

**Detalhamento diario** (`daily_breakdown`): cada secao traz, abaixo do
resumo, uma linha por data com dado. Grupo muscular e equipamento usam as
mesmas fontes e regras do resumo, e a soma dos dias e conferida contra o total
do periodo (`check_daily_invariant`); divergencia vira linha na qualidade dos
dados, nunca correcao silenciosa.

Cada secao e coletada isolada: se um servico falhar, a secao vira
"indisponivel" (com o motivo na secao de qualidade dos dados) e o resto do
relatorio continua saindo. Somente leitura - nenhum INSERT/UPDATE/DDL.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Optional

from ... import request_memo
from ...load_conventions import (
    TONNAGE_EXCLUDED_CONVENTIONS,
    LoadConvention,
    equipment_exercises_join_sql,
    load_convention_select_expr,
    normalize_load_convention,
)
from ...domain import cardio_prescription
from ...domain.body_measurement_sites import SITE_LABELS, WAIST_SITE
from ...observability import log_event
from ...domain.set_measures import sql_if_rep_based
from ...set_types import sql_is_working, sql_set_confirmed
from ..dashboard.coercion import to_date, to_float
from ..dashboard.contracts import TargetSource, WindowInfo
from ..dashboard.identity import require_user_id
from ..methodology_volume import VOLUME_CLASS_UNCLASSIFIED_LABEL
from . import daily_breakdown as daily
from ...db_helpers import rows as _fetchall

logger = logging.getLogger("fitness.reports.markdown")

# Convencoes que NAO entram na tonelagem (assistido alivia; peso corporal nao
# esta na serie): `load_conventions.TONNAGE_EXCLUDED_CONVENTIONS`, a MESMA
# regra do dashboard desde o M58 - importada acima e reexportada em `__all__`
# por compatibilidade.

_CANONICAL = "COALESCE(elm.canonical_exercise_id, e.mapping_id)"

# `{load_convention}` e preenchido por `load_convention_select_expr` (coluna
# da 045 ou NULL). `equipment_exercises` tem UNIQUE (equipment_id,
# exercise_id), entao o LEFT JOIN nao duplica serie; `emc` tem UNIQUE
# (exercicio, grupo), entao dentro de um grupo cada serie aparece UMA vez.
_TONNAGE_JOINS = f"""
FROM activities a
JOIN exercises e ON e.activity_id = a.id
JOIN exercise_sets es ON es.exercise_id = e.id AND {sql_set_confirmed('es.reps_pending')}
LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
LEFT JOIN gym_equipment ge ON ge.id = e.equipment_id
{equipment_exercises_join_sql(canonical_expr=_CANONICAL)}"""

_TONNAGE_WHERE = f"""
WHERE a.user_id = %s
  AND a.local_date BETWEEN %s AND %s
  AND {sql_is_working("es.set_type")}
"""

TONNAGE_BY_GROUP_SQL_TEMPLATE = f"""
SELECT
  a.local_date                                              AS local_date,
  emc.muscle_group_id                                       AS muscle_group_id,
  {{load_convention}}                                       AS load_conv,
  SUM({sql_if_rep_based('COALESCE(es.reps, 0) * COALESCE(es.weight_kg, 0) * COALESCE(emc.stimulus_weight, 0)')}) AS tonnage_kg,
  COUNT(DISTINCT es.id)                                     AS sets_count
{_TONNAGE_JOINS}
JOIN exercise_muscle_contributions emc
       ON emc.exercise_id = {_CANONICAL}
      AND emc.is_active = 1
{_TONNAGE_WHERE}
GROUP BY emc.muscle_group_id, load_conv, a.local_date
"""

# Series de trabalho por convencao, SEM o JOIN de grupos: e daqui que sai o
# total de series excluidas (uma serie conta uma vez, mesmo que o exercicio
# ative tres grupos).
WORKING_SETS_BY_CONVENTION_SQL_TEMPLATE = f"""
SELECT
  {{load_convention}}                                       AS load_conv,
  COUNT(DISTINCT es.id)                                     AS sets_count
{_TONNAGE_JOINS}
{_TONNAGE_WHERE}
GROUP BY load_conv
"""

# Sessoes de musculacao por dia (atividade com pelo menos um exercicio), para
# contar quantas caem em cada trecho de vigencia de Modelo.
STRENGTH_SESSIONS_BY_DAY_SQL = """
SELECT a.local_date AS local_date, COUNT(DISTINCT a.id) AS sessions
FROM activities a
WHERE a.user_id = %s
  AND a.local_date BETWEEN %s AND %s
  AND EXISTS (SELECT 1 FROM exercises e WHERE e.activity_id = a.id)
GROUP BY a.local_date
"""

# Rotulo do balde sem macro-grupo: do dono (`methodology_volume`), reexportado
# com o nome antigo para os chamadores e testes do relatorio.
MACRO_GROUP_UNCLASSIFIED = VOLUME_CLASS_UNCLASSIFIED_LABEL


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------


def _value(item: Any) -> Any:
    """Enum -> `.value`; o resto passa direto. O renderer recebe so tipos
    simples (str/int/float/date) - nunca um Enum cujo `str()` mude entre
    versoes do Python."""
    return item.value if isinstance(item, Enum) else item


def _metric_value(metric: Any) -> Optional[float]:
    """`Metric` (dict ou modelo) -> `float | None`. Ausencia continua `None`."""
    if metric is None:
        return None
    raw = metric.get("value") if isinstance(metric, Mapping) else getattr(metric, "value", None)
    return to_float(raw)


def _int(value: Any) -> int:
    number = to_float(value)
    return int(number) if number is not None else 0


def summarize_tonnage(
    group_rows: Iterable[Mapping[str, Any]],
    convention_rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aplica a regra de convencao de carga sobre as linhas agregadas.

    `group_rows`: uma linha por (grupo, convencao) com `tonnage_kg` ja
    ponderada por `stimulus_weight` e `sets_count` (so series de trabalho -
    o filtro de tipo mora no SQL). `convention_rows`: series de trabalho por
    convencao, sem o JOIN de grupos.

    Devolve `by_group` (`id -> {tonnage_kg, sets, excluded_sets}`),
    `excluded_sets` (total, cada serie uma vez) e `excluded_by_convention`.
    Convencao NULL/vazia = `total` (`normalize_load_convention`); valor fora
    do vocabulario tambem conta como `total`, mas fica registrado em
    `unknown_conventions` - o relatorio nunca cai por um cadastro ruim."""
    by_group: dict[int, dict[str, Any]] = {}
    unknown: set[str] = set()

    def _convention(raw: Any) -> LoadConvention:
        try:
            return normalize_load_convention(raw)
        except ValueError:
            unknown.add(str(raw))
            return LoadConvention.TOTAL

    for row in group_rows:
        group_id = row.get("muscle_group_id")
        if group_id is None:
            continue
        bucket = by_group.setdefault(int(group_id), {"tonnage_kg": 0.0, "sets": 0, "excluded_sets": 0})
        sets = _int(row.get("sets_count"))
        if _convention(row.get("load_conv")) in TONNAGE_EXCLUDED_CONVENTIONS:
            bucket["excluded_sets"] += sets
            continue
        bucket["sets"] += sets
        bucket["tonnage_kg"] += to_float(row.get("tonnage_kg")) or 0.0

    excluded_by_convention: dict[str, int] = {}
    for row in convention_rows:
        convention = _convention(row.get("load_conv"))
        if convention in TONNAGE_EXCLUDED_CONVENTIONS:
            excluded_by_convention[convention.value] = excluded_by_convention.get(convention.value, 0) + _int(
                row.get("sets_count")
            )

    for bucket in by_group.values():
        bucket["tonnage_kg"] = round(bucket["tonnage_kg"], 1)

    return {
        "by_group": by_group,
        "excluded_sets": sum(excluded_by_convention.values()),
        "excluded_by_convention": dict(sorted(excluded_by_convention.items())),
        "unknown_conventions": sorted(unknown),
    }


def resolve_macro_groups(
    groups: Iterable[Any], volume_classes: Mapping[int, Any]
) -> dict[int, str]:
    """`muscle_group_id -> nome do macro-grupo`.

    O macro-grupo e o do BLOCO com que o motor avalia o grupo
    (`methodology_volume.resolve_group_hierarchy`, que usa
    `block_volume_class_id`: sobe ate a raiz, com guarda contra ciclo) - M76
    Lote 4a, E12. Ate aqui o relatorio parava no 1o no com `volume_class_id`, e
    uma fatia com classe propria saia num macro-grupo diferente do que o motor
    usa. Sem classe no bloco -> `MACRO_GROUP_UNCLASSIFIED`."""
    from ..methodology_volume import resolve_group_hierarchy

    names = {int(class_id): str(spec.name or "") for class_id, spec in volume_classes.items()}
    hierarchy = resolve_group_hierarchy(groups, names)
    return {group_id: item.volume_class_name for group_id, item in hierarchy.items()}


def macro_group_subtotals(groups: Iterable[Mapping[str, Any]], class_order: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Soma, por macro-grupo, os numeros que JA estao na tabela por grupo.

    Ordem: a do cadastro (`class_order`, `sort_order` da 057) e, no fim,
    `Sem macro-grupo`. Tonelagem `None` em todos os grupos do macro continua
    `None` (nunca vira 0)."""
    buckets: dict[str, dict[str, Any]] = {}
    for group in groups:
        name = group.get("macro_group") or MACRO_GROUP_UNCLASSIFIED
        bucket = buckets.setdefault(
            name,
            {
                "name": name,
                "groups": 0,
                "physical_sets": 0.0,
                "effective_sets": 0.0,
                "weekly_effective_sets": 0.0,
                "tonnage_kg": None,
            },
        )
        bucket["groups"] += 1
        bucket["physical_sets"] += group.get("physical_sets") or 0.0
        bucket["effective_sets"] += group.get("effective_sets") or 0.0
        bucket["weekly_effective_sets"] += group.get("weekly_effective_sets") or 0.0
        if group.get("tonnage_kg") is not None:
            bucket["tonnage_kg"] = (bucket["tonnage_kg"] or 0.0) + float(group["tonnage_kg"])

    order = [name for name in class_order if name in buckets]
    order += sorted(name for name in buckets if name not in order and name != MACRO_GROUP_UNCLASSIFIED)
    if MACRO_GROUP_UNCLASSIFIED in buckets:
        order.append(MACRO_GROUP_UNCLASSIFIED)

    out = []
    for name in order:
        bucket = buckets[name]
        for key in ("physical_sets", "effective_sets", "weekly_effective_sets", "tonnage_kg"):
            if bucket[key] is not None:
                bucket[key] = round(bucket[key], 2)
        out.append(bucket)
    return out


def sessions_per_segment(
    segments: Iterable[Any], sessions_by_day: Mapping[date, int]
) -> list[int]:
    """Sessoes de musculacao que caem em cada trecho `[date_from, date_to]`."""
    counts = []
    for segment in segments:
        counts.append(
            sum(n for day, n in sessions_by_day.items() if segment.date_from <= day <= segment.date_to)
        )
    return counts


# ---------------------------------------------------------------------------
# Leitura (cada funcao recebe a conexao da requisicao)
# ---------------------------------------------------------------------------


def load_tonnage(conn: Any, *, user_id: int, window: WindowInfo) -> dict[str, Any]:
    params = (user_id, window.date_from, window.date_to)
    with conn.cursor() as cur:
        convention = load_convention_select_expr(cur, alias="eqex")
    group_rows = _fetchall(conn, TONNAGE_BY_GROUP_SQL_TEMPLATE.format(load_convention=convention), params)
    convention_rows = _fetchall(
        conn, WORKING_SETS_BY_CONVENTION_SQL_TEMPLATE.format(load_convention=convention), params
    )
    # As MESMAS linhas (dia x grupo x convencao) dao o total do periodo e o
    # detalhamento diario: a soma dos dias fecha por construcao.
    out = summarize_tonnage(group_rows, convention_rows)
    out["daily"] = daily.daily_tonnage(group_rows)
    return out


def _load_macro_groups(conn: Any) -> tuple[dict[int, str], list[str]]:
    from ..methodology_volume_service import load_methodology_groups, load_volume_classes

    with conn.cursor() as cur:
        groups = load_methodology_groups(cur)
        classes = load_volume_classes(cur)
    class_order = [str(spec.name) for spec in classes.values()]
    return resolve_macro_groups(groups, classes), class_order


def _collect_muscles(
    conn: Any,
    *,
    user_id: int,
    window: WindowInfo,
    now_local: datetime,
    techniques: Optional[daily.WindowTechniqueSets] = None,
) -> dict[str, Any]:
    from ...training_types import resolve_active_training_type_for_user
    from ..dashboard.muscle_summary_service import build_muscle_summary

    training_type_id = resolve_active_training_type_for_user(user_id)
    source = TargetSource.ASSIGNED_TEMPLATE if training_type_id is not None else TargetSource.FALLBACK_GENERIC
    summary = build_muscle_summary(
        conn,
        user_id=user_id,
        window=window,
        training_type_id=training_type_id,
        training_type_source=source,
        include_readiness=False,
        now_local=now_local,
    )
    tonnage = load_tonnage(conn, user_id=user_id, window=window)
    try:
        macro_by_group, class_order = _load_macro_groups(conn)
    except Exception:  # noqa: BLE001 - cadastro de macro-grupos e informativo; sem ele a tabela sai sem subtotal
        logger.warning("macro-grupos indisponiveis para o relatorio", exc_info=True)
        macro_by_group, class_order = {}, []

    groups: list[dict[str, Any]] = []
    for item in summary.get("items") or []:
        group_id = int(item["muscle_group_id"])
        realized = item.get("realized") or {}
        targets = item.get("targets") or {}
        frequency = item.get("frequency") or {}
        group_tonnage = tonnage["by_group"].get(group_id)
        groups.append(
            {
                "muscle_group_id": group_id,
                "name": item.get("muscle_group") or f"Grupo {group_id}",
                "macro_group": macro_by_group.get(group_id, MACRO_GROUP_UNCLASSIFIED),
                "physical_sets": _metric_value(realized.get("physical_sets")) or 0.0,
                "effective_sets": _metric_value(realized.get("effective_sets_stimulus")) or 0.0,
                "weekly_effective_sets": _metric_value(realized.get("weekly_effective_sets")),
                "tonnage_kg": group_tonnage["tonnage_kg"] if group_tonnage and group_tonnage["sets"] else None,
                "tonnage_excluded_sets": group_tonnage["excluded_sets"] if group_tonnage else 0,
                "avg_rpe": _metric_value(realized.get("avg_rpe")),
                "direct_sessions": _int(_metric_value(frequency.get("direct_sessions"))),
                "participation_sessions": _int(_metric_value(frequency.get("participation_sessions"))),
                "target_min": _metric_value(targets.get("min")),
                "target": _metric_value(targets.get("target")),
                "target_max": _metric_value(targets.get("max")),
                "status": _value(item.get("status")),
            }
        )

    groups.sort(key=lambda g: (-(g["effective_sets"] or 0.0), g["name"]))

    names = {g["muscle_group_id"]: g["name"] for g in groups}
    days = daily.load_group_days(
        conn,
        user_id=user_id,
        window=window,
        tonnage_daily=tonnage.get("daily") or {},
        names=names,
        techniques=techniques or daily.WindowTechniqueSets(conn, user_id, window),
    )
    invariant = daily.check_daily_invariant(groups, days, key="muscle_group_id")
    if invariant:
        logger.warning("relatorio markdown: detalhamento diario por grupo nao fecha: %s", invariant)

    return {
        "available": True,
        "days": days,
        "daily_invariant_problems": invariant,
        "target_source": _value(summary.get("target_source")),
        "template_name": summary.get("template_name"),
        "groups": groups,
        "macro_groups": macro_group_subtotals(groups, class_order),
        "unattributed_sets": _int(summary.get("unattributed_sets")),
        "tonnage_excluded_sets": tonnage["excluded_sets"],
        "tonnage_excluded_by_convention": tonnage["excluded_by_convention"],
        "unknown_load_conventions": tonnage["unknown_conventions"],
    }


def _collect_equipment(
    conn: Any,
    *,
    user_id: int,
    window: WindowInfo,
    techniques: Optional[daily.WindowTechniqueSets] = None,
) -> dict[str, Any]:
    """Resumo do periodo por equipamento + detalhe diario (dia x equipamento
    x exercicio). O resumo e a soma das linhas diarias; a invariante e
    conferida mesmo assim, para um ajuste futuro nao quebrar isso calado."""
    out = daily.load_equipment(
        conn,
        user_id=user_id,
        window=window,
        techniques=techniques or daily.WindowTechniqueSets(conn, user_id, window),
    )
    invariant = daily.check_daily_invariant(out["period"], out["days"], key="equipment", label="equipment")
    if invariant:
        logger.warning("relatorio markdown: detalhamento diario por equipamento nao fecha: %s", invariant)
    return {"available": True, **out, "daily_invariant_problems": invariant}


def _collect_recovery(conn: Any, *, user_id: int, window: WindowInfo) -> dict[str, Any]:
    from ..dashboard.checkin_service import load_checkins
    from ..dashboard.recovery_signals_service import PAIN_DAILY_SQL, build_recovery_signals

    params = (user_id, window.date_from, window.date_to)
    sleep_rows = daily.load_sleep_rows(conn, user_id=user_id, window=window)
    wellness_rows = daily.load_wellness_rows(conn, user_id=user_id, window=window)
    checkin_rows, _, _ = load_checkins(conn, user_id=user_id, date_from=window.date_from, date_to=window.date_to)
    pain_rows = _fetchall(conn, PAIN_DAILY_SQL, params)
    sleep = daily.sleep_stats(sleep_rows)
    wellness = daily.wellness_stats(wellness_rows)
    payload = build_recovery_signals(conn, user_id=user_id, window=window)

    signals = [
        {
            "code": signal.get("code"),
            "label": signal.get("label"),
            "unit": (signal.get("current") or {}).get("unit"),
            "current": _metric_value(signal.get("current")),
            "baseline": _metric_value(signal.get("baseline")),
            "delta": _metric_value(signal.get("delta")),
            "direction": _value(signal.get("direction")),
            "reading": _value(signal.get("reading")),
            "observations": _int(signal.get("observations")),
            "baseline_observations": _int(signal.get("baseline_observations")),
        }
        for signal in payload.get("signals") or []
    ]
    return {
        "available": True,
        "sleep": {
            "days": _int(sleep.get("days")),
            "avg_duration_hours": to_float(sleep.get("avg_duration_hours")),
            "duration_days": _int(sleep.get("duration_days")),
            "avg_deep_hours": to_float(sleep.get("avg_deep_hours")),
            "deep_days": _int(sleep.get("deep_days")),
            "avg_rem_hours": to_float(sleep.get("avg_rem_hours")),
            "rem_days": _int(sleep.get("rem_days")),
            "avg_regularity_percent": to_float(sleep.get("avg_regularity_percent")),
            "regularity_days": _int(sleep.get("regularity_days")),
        },
        "wellness": {
            "days": _int(wellness.get("days")),
            "avg_hrv_ms": to_float(wellness.get("avg_hrv_ms")),
            "hrv_days": _int(wellness.get("hrv_days")),
            "avg_resting_hr_bpm": to_float(wellness.get("avg_resting_hr_bpm")),
            "resting_hr_days": _int(wellness.get("resting_hr_days")),
            "avg_form_tsb": to_float(wellness.get("avg_form_tsb")),
            "form_tsb_days": _int(wellness.get("form_tsb_days")),
        },
        "signals": signals,
        "baseline_window_days": _int(payload.get("baseline_window_days")),
        "days": daily.build_recovery_days(sleep_rows, wellness_rows, checkin_rows, pain_rows),
    }


def _collect_body(conn: Any, *, user_id: int, window: WindowInfo) -> dict[str, Any]:
    from ..dashboard.body_service import (
        MEASUREMENT_SITE_LABELS,
        build_body,
        load_measurement_rows,
        load_weight_rows,
        pick_daily_weights,
    )

    payload = build_body(conn, user_id=user_id, window=window)
    # Detalhe diario: as MESMAS leituras do `body_service` (peso com a
    # prioridade por dia de `pick_daily_weights`, medidas da janela) + a
    # bioimpedancia completa do dia.
    weight_rows, _ = load_weight_rows(conn, user_id=user_id, window=window)
    measurement_rows, _ = load_measurement_rows(conn, user_id=user_id, window=window)
    body_days = daily.build_body_days(
        pick_daily_weights(weight_rows),
        daily.load_body_composition_rows(conn, user_id=user_id, window=window),
        measurement_rows,
        date_from=window.date_from,
        date_to=window.date_to,
        site_labels=MEASUREMENT_SITE_LABELS,
    )
    weight = payload.get("weight") or {}
    points = weight.get("points") or []
    first = points[0] if points else {}
    waist = payload.get("waist") or {}
    bio = payload.get("bioimpedance") or {}

    # Cintura primeiro (e a medida com protocolo e serie propria no
    # `body_service`), depois os demais locais na ordem do servico.
    measurements: list[dict[str, Any]] = []
    if _metric_value(waist.get("latest")) is not None:
        measurements.append(
            {
                "label": SITE_LABELS[WAIST_SITE],
                "last_cm": _metric_value(waist.get("latest")),
                "last_date": to_date(waist.get("latest_local_date")),
                "change_cm": _metric_value(waist.get("change")),
                "count": len(waist.get("points") or []),
            }
        )
    for series in payload.get("measurements") or []:
        measurements.append(
            {
                "label": series.get("label") or series.get("site"),
                "last_cm": _metric_value(series.get("latest")),
                "last_date": to_date(series.get("latest_local_date")),
                "change_cm": _metric_value(series.get("change")),
                "count": len(series.get("points") or []),
            }
        )

    return {
        "available": True,
        "weight": {
            "count": _int(weight.get("weighings_count")),
            "first_date": to_date(first.get("local_date")) if first else None,
            "first_kg": to_float(first.get("weight_kg")) if first else None,
            "last_date": to_date(points[-1].get("local_date")) if points else None,
            "last_kg": _metric_value(weight.get("latest")),
            "change_kg": _metric_value(weight.get("change")),
            "change_pct": _metric_value(weight.get("change_pct")),
            "ma7_kg": _metric_value(weight.get("ma7_latest")),
            "per_week": _metric_value(weight.get("weighings_per_week")),
        },
        "bioimpedance": {
            "count": _int(bio.get("readings_count")),
            "last_date": to_date(bio.get("latest_local_date")),
            "body_fat_percent": _metric_value(bio.get("body_fat_percent")),
            "muscle_mass_kg": _metric_value(bio.get("muscle_mass_kg")),
            "body_water_percent": _metric_value(bio.get("body_water_percent")),
            "body_fat_change": _metric_value(bio.get("body_fat_change")),
            "muscle_mass_change": _metric_value(bio.get("muscle_mass_change")),
        },
        "measurements": measurements,
        "daily": body_days,
    }


def _collect_cardio(conn: Any, *, user_id: int, window: WindowInfo) -> dict[str, Any]:
    from ..dashboard.cardio_service import MAX_CARDIO_SESSIONS, build_cardio

    # Sem os blocos de acompanhamento da C4 (o relatorio so le volume e FC).
    payload = build_cardio(conn, user_id=user_id, window=window, tracking=False)
    totals = payload.get("totals") or {}
    strength = payload.get("strength_heart_rate") or {}
    return {
        "available": True,
        "intervals_configured": bool(payload.get("intervals_configured")),
        "totals": {
            "sessions": _int(totals.get("sessions")),
            "sessions_with_hr": _int(totals.get("sessions_with_hr")),
            "minutes": _int(totals.get("minutes")),
            "distance_km": to_float(totals.get("distance_km")),
            "calories_kcal": to_float(totals.get("calories_kcal")),
            "training_load": to_float(totals.get("training_load")),
            "avg_hr_bpm": to_float(totals.get("avg_hr_bpm")),
            "max_hr_bpm": to_float(totals.get("max_hr_bpm")),
        },
        "by_sport": [
            {
                "sport_type": sport.get("sport_type"),
                "sessions": _int(sport.get("sessions")),
                "minutes": _int(sport.get("minutes")),
                "distance_km": to_float(sport.get("distance_km")),
                "calories_kcal": to_float(sport.get("calories_kcal")),
                "avg_hr_bpm": to_float(sport.get("avg_hr_bpm")),
                "max_hr_bpm": to_float(sport.get("max_hr_bpm")),
            }
            for sport in payload.get("by_sport") or []
        ],
        "strength_hr": {
            "sessions_total": _int(strength.get("sessions_total")),
            "sessions_with_hr": _int(strength.get("sessions_with_hr")),
            "coverage_pct": to_float(strength.get("coverage_pct")),
            "avg_hr_bpm": to_float(strength.get("avg_hr_bpm")),
            "max_hr_bpm": to_float(strength.get("max_hr_bpm")),
        },
        "truncated": _int(totals.get("sessions")) >= MAX_CARDIO_SESSIONS,
        # Uma linha por sessao, em ordem cronologica (o servico entrega da
        # mais nova para a mais antiga). Mesmas sessoes dos totais.
        "sessions": sorted(
            (
                {
                    "local_date": to_date(item.get("activity_date")),
                    "start_time": item.get("start_time"),
                    "name": item.get("name"),
                    "sport_type": item.get("sport_type"),
                    "minutes": item.get("minutes"),
                    "distance_km": to_float(item.get("distance_km")),
                    "avg_hr_bpm": to_float(item.get("avg_hr_bpm")),
                    "max_hr_bpm": to_float(item.get("max_hr_bpm")),
                    "calories_kcal": to_float(item.get("calories_kcal")),
                    "training_load": to_float(item.get("training_load")),
                }
                for item in payload.get("sessions") or []
            ),
            key=lambda item: (item["local_date"] or date.min, str(item["start_time"] or "")),
        ),
    }


def _training_type_names(conn: Any, ids: set[int]) -> dict[int, str]:
    ids = {int(i) for i in ids if i}
    if not ids:
        return {}
    placeholders = ", ".join(["%s"] * len(ids))
    rows = _fetchall(conn, f"SELECT id, name FROM training_types WHERE id IN ({placeholders})", tuple(sorted(ids)))
    return {int(row["id"]): str(row["name"]) for row in rows}


def _current_template(conn: Any, *, user_id: int, as_of: Optional[date] = None) -> Optional[dict[str, Any]]:
    """Modelo VIGENTE com intencao metodologica e slots. `None` sem Modelo.

    `as_of` (PR3, flag ligada): os slots sao os da VERSAO do Modelo no fim da
    janela do relatorio - um relatorio de um periodo antigo descreve a
    estrutura que o usuario seguia entao. Mesmos statements."""
    from ...template_assignments import resolve_active_template_id
    from ...workout_plan_templates_engine import get_template_if_assigned
    from ..methodology_volume import VolumeCountingMode, counted_contributions
    from ..methodology_volume_service import load_volume_contributions

    template_id = resolve_active_template_id(user_id)
    if template_id is None:
        return None
    template = get_template_if_assigned(template_id, user_id, as_of=as_of)
    if not template:
        return None

    slots = template.get("slots") or []
    items = [item for slot in slots for item in (slot.get("items") or [])]
    exercise_ids = {int(i["mapping_id"]) for i in items if i.get("mapping_id")}
    equipment_ids = {int(i["equipment_id"]) for i in items if i.get("equipment_id")}

    group_names = {
        int(row["id"]): str(row["name"])
        for row in request_memo.memoize(("muscle_groups", "id_name"), lambda: _fetchall(conn, "SELECT id, name FROM muscle_groups", ()))
    }
    exercise_names: dict[int, str] = {}
    equipment_names: dict[int, str] = {}
    primary_groups: dict[int, frozenset[int]] = {}
    if exercise_ids:
        placeholders = ", ".join(["%s"] * len(exercise_ids))
        rows = _fetchall(
            conn, f"SELECT id, name FROM exercise_catalog WHERE id IN ({placeholders})", tuple(sorted(exercise_ids))
        )
        exercise_names = {int(row["id"]): str(row["name"]) for row in rows}
        with conn.cursor() as cur:
            # Rotulo do item = grupo(s) PRIMARIO(s), qualquer que seja o modo
            # de contagem do Modelo (o rotulo nao e volume).
            primary_groups = {
                exercise_id: frozenset(counted_contributions(rows, VolumeCountingMode.PRIMARY_ONLY))
                for exercise_id, rows in load_volume_contributions(
                    cur, exercise_ids, VolumeCountingMode.PRIMARY_ONLY
                ).items()
            }
    if equipment_ids:
        placeholders = ", ".join(["%s"] * len(equipment_ids))
        rows = _fetchall(
            conn, f"SELECT id, name FROM equipment WHERE id IN ({placeholders})", tuple(sorted(equipment_ids))
        )
        equipment_names = {int(row["id"]): str(row["name"]) for row in rows}

    technique_names: dict[str, str] = {}
    try:
        from ...advanced_techniques import list_techniques

        technique_names = {t["code"]: t["name"] for t in list_techniques(include_inactive=True)}
    except Exception:  # noqa: BLE001 - nome da tecnica e cosmetico; sem catalogo o codigo aparece
        logger.warning("catalogo de tecnicas indisponivel para o relatorio", exc_info=True)

    out_slots = []
    for slot in slots:
        slot_items = []
        slot_groups: list[str] = []
        for item in slot.get("items") or []:
            group_ids: list[int] = []
            if item.get("muscle_group_id"):
                group_ids = [int(item["muscle_group_id"])]
            elif item.get("mapping_id"):
                group_ids = sorted(primary_groups.get(int(item["mapping_id"]), frozenset()))
            names = [group_names.get(g, f"Grupo {g}") for g in group_ids]
            for name in names:
                if name not in slot_groups:
                    slot_groups.append(name)
            technique = item.get("advanced_technique")
            slot_items.append(
                {
                    "kind": item.get("item_kind"),
                    "groups": names,
                    "exercise": exercise_names.get(int(item["mapping_id"])) if item.get("mapping_id") else None,
                    "equipment": equipment_names.get(int(item["equipment_id"])) if item.get("equipment_id") else None,
                    "sets": _int(item.get("sets_count")) if item.get("sets_count") is not None else None,
                    "reps_target": item.get("reps_target"),
                    "rpe_target": item.get("rpe_target"),
                    "technique": technique_names.get(technique, technique) if technique else None,
                    "technique_applications": _int(item.get("advanced_technique_applications"))
                    if item.get("advanced_technique_applications") is not None
                    else None,
                    # M98 C0: prescrição de cardio pelo dono (texto canônico e
                    # tempo total, com aquecimento, recuperação e HIIT).
                    "cardio_minutes": cardio_prescription.slot_cardio_minutes([item])
                    if cardio_prescription.is_cardio_item(item)
                    else None,
                    "cardio_text": cardio_prescription.describe(item)
                    if cardio_prescription.is_cardio_item(item)
                    else None,
                }
            )
        out_slots.append(
            {
                "sequence": _int(slot.get("sequence")),
                "name": slot.get("name"),
                "weekday": slot.get("weekday"),
                "slot_type": slot.get("slot_type") or "normal",
                "groups": slot_groups,
                "target_sets": sum(i["sets"] or 0 for i in slot_items),
                "items": slot_items,
            }
        )

    intent = template.get("methodology_intent") or {}
    training_types = _training_type_names(conn, {template.get("training_type_id")} if template.get("training_type_id") else set())
    return {
        "id": int(template["id"]),
        "name": template.get("name"),
        "volume_mode": template.get("volume_mode"),
        "training_type": training_types.get(int(template["training_type_id"])) if template.get("training_type_id") else None,
        "intent": intent.get("intent") or "standard",
        "intent_targets": [group_names.get(int(g), f"Grupo {g}") for g in intent.get("target_group_ids") or []],
        "slots": out_slots,
    }


def _collect_cycles(conn: Any, *, user_id: int, window: WindowInfo) -> dict[str, Any]:
    from ...template_assignments import segment_window
    from ..dashboard.adherence_service import build_adherence

    segments = segment_window(user_id, window.date_from, window.date_to)
    rows = _fetchall(conn, STRENGTH_SESSIONS_BY_DAY_SQL, (user_id, window.date_from, window.date_to))
    sessions_by_day: dict[date, int] = {}
    for row in rows:
        day = to_date(row.get("local_date"))
        if day is not None:
            sessions_by_day[day] = sessions_by_day.get(day, 0) + _int(row.get("sessions"))
    counts = sessions_per_segment(segments, sessions_by_day)
    type_names = _training_type_names(
        conn, {s.assignment.training_type_id for s in segments if s.assignment and s.assignment.training_type_id}
    )

    adherence = build_adherence(conn, user_id=user_id, window=window)
    cycle_sessions, rpe_rows = daily.load_cycle_sessions(conn, user_id=user_id, window=window)
    return {
        "available": True,
        "timeline": daily.build_cycle_timeline(cycle_sessions, rpe_rows, segments),
        "segments": [
            {
                "date_from": segment.date_from,
                "date_to": segment.date_to,
                "template_name": segment.assignment.template_name if segment.assignment else None,
                "training_type": type_names.get(int(segment.assignment.training_type_id))
                if segment.assignment and segment.assignment.training_type_id
                else None,
                "volume_mode": segment.assignment.volume_mode if segment.assignment else None,
                "sessions": count,
            }
            for segment, count in zip(segments, counts)
        ],
        "sessions_total": sum(sessions_by_day.values()),
        "adherence": {
            "has_template": bool(adherence.get("has_template")),
            "template_name": adherence.get("template_name"),
            "cycle_length_days": adherence.get("cycle_length_days"),
            "expected_sessions": _metric_value(adherence.get("expected_sessions")),
            "completed_sessions": _metric_value(adherence.get("completed_sessions")),
            "planned_sessions": _metric_value(adherence.get("planned_sessions")),
            "unplanned_sessions": _metric_value(adherence.get("unplanned_sessions")),
            "missed_sessions": _metric_value(adherence.get("missed_sessions")),
            "rate": _metric_value(adherence.get("rate")),
        },
        "current_template": _current_template(conn, user_id=user_id, as_of=window.date_to),
    }


# ---------------------------------------------------------------------------
# Orquestracao
# ---------------------------------------------------------------------------

SECTION_LABELS: Mapping[str, str] = {
    "muscles": "Treino por grupo muscular",
    "equipment": "Treino por equipamento",
    "recovery": "Recuperação e indicadores corporais",
    "body": "Corpo",
    "cardio": "Cardio",
    "cycles": "Ciclos de treino",
}


def _empty_sources(data: Mapping[str, Any]) -> list[str]:
    """Fontes sem nenhum registro no periodo - vira lista na secao de
    qualidade dos dados, para "sem dados" nunca parecer "zero"."""
    out: list[str] = []
    muscles = data.get("muscles") or {}
    if muscles.get("available") and not any(g.get("physical_sets") for g in muscles.get("groups") or []):
        out.append("Séries de musculação")
    recovery = data.get("recovery") or {}
    if recovery.get("available"):
        if not (recovery.get("sleep") or {}).get("days"):
            out.append("Sono (registro manual)")
        if not (recovery.get("wellness") or {}).get("days"):
            out.append("Wearable / Intervals.icu (HRV, FC de repouso, TSB)")
    body = data.get("body") or {}
    if body.get("available"):
        if not (body.get("weight") or {}).get("count"):
            out.append("Pesagens")
        if not (body.get("bioimpedance") or {}).get("count"):
            out.append("Bioimpedância")
        if not body.get("measurements"):
            out.append("Medidas corporais")
    cardio = data.get("cardio") or {}
    if cardio.get("available") and not (cardio.get("totals") or {}).get("sessions"):
        out.append("Cardio (Intervals.icu)")
    return out


def collect_report_data(
    conn: Any,
    *,
    user_id: Any,
    window: WindowInfo,
    now_local: Optional[datetime] = None,
) -> dict[str, Any]:
    """Dados do relatorio para `window` (datas locais do usuario).

    `now_local` e o "gerado em" (parede do usuario); os testes passam um valor
    fixo. Cada secao e coletada isolada: falha vira `{"available": False}` e
    entra em `quality.unavailable_sections`, sem derrubar as outras."""
    owner = require_user_id(user_id)
    now_local = now_local or datetime.now()
    data: dict[str, Any] = {
        "generated_at": now_local,
        "timezone": window.timezone,
        "window": {"date_from": window.date_from, "date_to": window.date_to, "days": window.days},
    }

    # Leitura das series com tecnica: no maximo UMA, dividida entre grupo e
    # equipamento (e so quando alguma agregacao acusou aplicacao).
    techniques = daily.WindowTechniqueSets(conn, owner, window)
    collectors: dict[str, Callable[[], dict[str, Any]]] = {
        "muscles": lambda: _collect_muscles(
            conn, user_id=owner, window=window, now_local=now_local, techniques=techniques
        ),
        "equipment": lambda: _collect_equipment(conn, user_id=owner, window=window, techniques=techniques),
        "recovery": lambda: _collect_recovery(conn, user_id=owner, window=window),
        "body": lambda: _collect_body(conn, user_id=owner, window=window),
        "cardio": lambda: _collect_cardio(conn, user_id=owner, window=window),
        "cycles": lambda: _collect_cycles(conn, user_id=owner, window=window),
    }
    unavailable: list[str] = []
    timings: dict[str, float] = {}
    for key, collect in collectors.items():
        started = datetime.now()
        try:
            data[key] = collect()
        except Exception:  # noqa: BLE001 - uma secao quebrada nao pode derrubar o relatorio inteiro
            logger.exception("relatorio markdown: secao %s indisponivel (user_id=%s)", key, owner)
            data[key] = {"available": False}
            unavailable.append(SECTION_LABELS[key])
        timings[key] = round((datetime.now() - started).total_seconds() * 1000, 1)

    muscles = data.get("muscles") or {}
    data["quality"] = {
        "unattributed_sets": muscles.get("unattributed_sets", 0) if muscles.get("available") else None,
        "tonnage_excluded_sets": muscles.get("tonnage_excluded_sets", 0) if muscles.get("available") else None,
        "tonnage_excluded_by_convention": muscles.get("tonnage_excluded_by_convention") or {},
        "unknown_load_conventions": muscles.get("unknown_load_conventions") or [],
        "empty_sources": _empty_sources(data),
        "unavailable_sections": unavailable,
        "cardio_truncated": bool((data.get("cardio") or {}).get("truncated")),
        "daily_invariant_problems": list(muscles.get("daily_invariant_problems") or [])
        + list((data.get("equipment") or {}).get("daily_invariant_problems") or []),
    }

    log_event(
        "reports.markdown.collected",
        user_id=owner,
        days=window.days,
        unavailable=len(unavailable),
        timings_ms=timings,
    )
    return data


def report_filename(date_from: date, date_to: date) -> str:
    return f"relatorio-musclefit-{date_from.isoformat()}_{date_to.isoformat()}.md"


def default_custom_today_window(today: date, days: int) -> tuple[date, date]:
    """`[today - days + 1, today]` - usado para janelas sem `WindowKind`
    proprio (30 dias)."""
    return today - timedelta(days=days - 1), today


__all__ = [
    "MACRO_GROUP_UNCLASSIFIED",
    "STRENGTH_SESSIONS_BY_DAY_SQL",
    "TONNAGE_BY_GROUP_SQL_TEMPLATE",
    "TONNAGE_EXCLUDED_CONVENTIONS",
    "WORKING_SETS_BY_CONVENTION_SQL_TEMPLATE",
    "collect_report_data",
    "default_custom_today_window",
    "load_tonnage",
    "macro_group_subtotals",
    "report_filename",
    "resolve_macro_groups",
    "sessions_per_segment",
    "summarize_tonnage",
]
