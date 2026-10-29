from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, Depends, Query

from .intelligence.volume_service import OUTROS_GROUP, VolumeService
from .security import Identity, get_current_identity
from .set_types import sql_not_preparatory, sql_set_confirmed
# Leitura pura: estas rotas nao emitem DDL nem DML. `exercises.mapping_id` e
# gravado na escrita (sessao/importacao via resolve_mapping_id; catalogo via
# exercise_mapping_canonical.reclassify_after_catalog_change) - ver
# tests/integration/dataapi/test_muscle_groups_mapped_sem_escrita.py.
from app.exercise_mapping_canonical import (
    fix_obj,
    fix_text,
    normalize,
    q,
    query,
)

router = APIRouter(prefix="/analytics", tags=["analytics"], dependencies=[Depends(get_current_identity)])

# Janela (em dias) de cada consulta ao banco em `load_rows`. A RDS Data API
# (producao) recusa resultados acima de 1 MiB (UnsupportedResultException),
# limite que o PyMySQL (dev) nao tem. A consulta ja devolve linhas AGREGADAS
# por (dia, exercicio, grupo primario, grupo secundario) - nao uma por serie -
# e a janela pedida (ate 2000 dias) e lida em blocos deste tamanho, para que
# cada resultado fique na ordem de centenas de KB mesmo com volume de treino
# alto. Ver tests/integration/dataapi/test_muscle_groups_mapped_paridade.py.
LOAD_ROWS_CHUNK_DAYS = 90


def nfloat(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except Exception:
        return 0.0


def as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y"):
            try:
                return datetime.strptime(value[:19], fmt).date()
            except Exception:
                pass
    return date.today()


def period_label(day: date, period: str) -> str:
    if period == "daily":
        return day.isoformat()
    if period == "monthly":
        return f"{day.year}-{day.month:02d}"
    if period == "quarterly":
        return f"{day.year}-Q{((day.month - 1) // 3) + 1}"
    if period == "yearly":
        return str(day.year)
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def cols(table: str) -> List[str]:
    try:
        return [row.get("Field") or row.get("field") for row in query(f"SHOW COLUMNS FROM {q(table)}")]
    except Exception:
        return []


def pick(columns: List[str], names: List[str]) -> str | None:
    lookup = {str(c).lower(): str(c) for c in columns if c}
    for name in names:
        if name.lower() in lookup:
            return lookup[name.lower()]
    return None


def schema() -> Dict[str, Any]:
    ac = cols("activities")
    ec = cols("exercises")
    sc = cols("exercise_sets")

    out = {
        "activity_id": pick(ac, ["id", "activity_id"]),
        "date_col": pick(ac, ["local_date", "activity_date", "date", "started_at", "created_at"]),
        "exercise_id": pick(ec, ["id", "exercise_id"]),
        "exercise_activity": pick(ec, ["activity_id", "workout_id", "session_id"]),
        "original_name": pick(ec, ["original_name", "name", "exercise_name"]),
        "set_exercise": pick(sc, ["exercise_id"]),
        "reps": pick(sc, ["reps", "repetitions"]),
        "weight": pick(sc, ["weight_kg", "weight", "load_kg", "carga", "peso"]),
        "rpe": pick(sc, ["rpe"]),
        "set_type": pick(sc, ["set_type"]),
        "reps_pending": pick(sc, ["reps_pending"]),
    }
    out["ok"] = all(out.get(k) for k in ["activity_id", "date_col", "exercise_id", "exercise_activity", "original_name", "set_exercise"])
    return out


def day_windows(days: int, chunk_days: int = LOAD_ROWS_CHUNK_DAYS) -> List[Tuple[int, int | None]]:
    """Divide "ultimos `days` dias" em blocos disjuntos `(inicio, fim)`,
    contados em dias antes de CURDATE(), do mais antigo para o mais recente.
    Cada bloco cobre `DATE(data) >= CURDATE() - inicio` e, quando `fim` nao e
    None, `DATE(data) < CURDATE() - fim`. O ultimo bloco nao tem limite
    superior (inclui hoje e datas futuras, igual a consulta de janela unica)."""
    chunk_days = max(1, int(chunk_days))
    windows: List[Tuple[int, int | None]] = []
    start = int(days)
    while start > chunk_days:
        windows.append((start, start - chunk_days))
        start -= chunk_days
    windows.append((start, None))
    return windows


def _load_rows_sql(sc: Dict[str, Any], *, bounded: bool, by_user: bool) -> str:
    date_expr = f"DATE(a.{q(sc['date_col'])})"
    end_filter = f" AND {date_expr} < DATE_SUB(CURDATE(), INTERVAL %s DAY)" if bounded else ""
    user_filter = " AND a.user_id = %s" if by_user else ""
    # Aquecimento/serie de ajuste nao entram em series/volume por grupo.
    # Schema detectado dinamicamente: so filtra se a coluna existir.
    set_type_filter = f" AND {sql_not_preparatory('s.' + q(sc['set_type']))}" if sc.get("set_type") else ""
    # Modo Intensivo (092): série com reps pendentes fica fora até a confirmação.
    pending_filter = f" AND {sql_set_confirmed('s.' + q(sc['reps_pending']))}" if sc.get("reps_pending") else ""
    reps_expr = f"COALESCE(s.{q(sc['reps'])}, 0)" if sc.get("reps") else "0"
    weight_expr = f"COALESCE(s.{q(sc['weight'])}, 0)" if sc.get("weight") else "0"
    rpe_expr = f"s.{q(sc['rpe'])}" if sc.get("rpe") else "NULL"
    canonical_id = "COALESCE(elm.canonical_exercise_id, e.mapping_id)"

    # Agregacao no SQL (padrao ANSI, sem funcao exclusiva de MySQL/pymysql):
    # a subconsulta monta uma linha por serie e o SELECT externo soma por
    # (dia, nome exibido do exercicio, exercicio canonico). O nome e agrupado
    # com collation binaria (utf8mb4_bin) para nao fundir grafias que diferem
    # so por caixa/acento - o Python antes agrupava por string exata.
    #
    # M76 Lote 3c: os grupos musculares (e o PESO de cada um) NAO sao mais
    # resolvidos aqui. Antes o SQL trazia so o 1o PRIMARY e o 1o SECONDARY e o
    # Python aplicava 1,0 / `secondary_factor` 0,5 fixos, ignorando o
    # `stimulus_weight` cadastrado (e os estabilizadores). Agora a consulta
    # devolve o exercicio canonico e `aggregate` pondera pelas contribuicoes
    # do dono (`VolumeService._resolve_contributions`).
    return f"""
        SELECT
            t.activity_date,
            t.exercise_name,
            t.canonical_exercise_id,
            COUNT(*) AS set_count,
            SUM(t.reps) AS reps_sum,
            SUM(t.weight) AS weight_sum,
            SUM(t.reps * t.weight) AS volume_kg_sum,
            SUM(CASE WHEN t.reps * t.weight > 0 THEN t.reps * t.weight ELSE t.reps END) AS volume_metric_sum,
            SUM(t.rpe) AS rpe_sum,
            COUNT(t.rpe) AS rpe_count
        FROM (
            SELECT
                {date_expr} AS activity_date,
                CONVERT(COALESCE(NULLIF((
                    SELECT zam.external_name
                    FROM exercise_external_mappings zam
                    WHERE zam.exercise_id = {canonical_id}
                      AND zam.provider = 'AMAZFIT' AND zam.is_active = 1
                    ORDER BY zam.id ASC LIMIT 1
                ), ''), e.{q(sc['original_name'])}, '') USING utf8mb4) COLLATE utf8mb4_bin AS exercise_name,
                CAST({canonical_id} AS SIGNED) AS canonical_exercise_id,
                {reps_expr} AS reps,
                {weight_expr} AS weight,
                {rpe_expr} AS rpe
            FROM `exercise_sets` s
            JOIN `exercises` e ON e.{q(sc['exercise_id'])} = s.{q(sc['set_exercise'])}
            JOIN `activities` a ON a.{q(sc['activity_id'])} = e.{q(sc['exercise_activity'])}
            LEFT JOIN `exercise_legacy_id_map` elm ON elm.old_exercise_id = e.mapping_id
            WHERE {date_expr} >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
              {end_filter}
              {user_filter}
              {set_type_filter}
              {pending_filter}
        ) t
        GROUP BY t.activity_date, t.exercise_name, t.canonical_exercise_id
        ORDER BY t.activity_date ASC, t.exercise_name ASC, t.canonical_exercise_id ASC
    """


def load_rows(days: int, user_id: int | None = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Series da janela ja agregadas por (dia, exercicio exibido, exercicio
    canonico `canonical_exercise_id`).

    Cada linha traz `set_count`, `reps_sum`, `weight_sum`, `volume_kg_sum`
    (reps x carga), `volume_metric_sum` (reps x carga, ou reps quando a serie
    nao tem carga) e `rpe_sum`/`rpe_count` (so series com RPE)."""
    sc = schema()

    if not sc["ok"]:
        return [], {"ok": False, "schema": sc}

    rows: List[Dict[str, Any]] = []
    for start, end in day_windows(days, LOAD_ROWS_CHUNK_DAYS):
        params: List[Any] = [start]
        if end is not None:
            params.append(end)
        if user_id is not None:
            params.append(user_id)
        sql = _load_rows_sql(sc, bounded=end is not None, by_user=user_id is not None)
        rows.extend(query(sql, tuple(params)))
    return rows, {"ok": True, "schema": sc}


def metric_value(row: Dict[str, Any], metric: str) -> float:
    """Valor da metrica para uma linha AGREGADA de `load_rows`. Mesma regra de
    antes, aplicada por serie no SQL: `volume` = reps x carga, ou reps quando a
    serie nao tem carga (peso corporal)."""
    if metric == "sets":
        return nfloat(row.get("set_count"))
    if metric == "reps":
        return nfloat(row.get("reps_sum"))
    if metric == "load":
        return nfloat(row.get("weight_sum"))
    return nfloat(row.get("volume_metric_sum"))


# Acumuladores internos que nao saem na resposta.
_INTERNAL_KEYS = ("exercises", "periods", "rpe_sum", "rpe_weight")


def _new_bucket(group: str, **extra: Any) -> Dict[str, Any]:
    return {
        **extra,
        "group": group,
        "muscle_group": group,
        "value": 0.0,
        "sets": 0,
        "volume_kg": 0.0,
        "rpe_sets": 0,
        "rpe_sum": 0.0,
        "rpe_weight": 0.0,
        "exercises": set(),
    }


def _accumulate(bucket: Dict[str, Any], row: Dict[str, Any], value: float, factor: float, exercise_name: str) -> None:
    rpe_count = int(nfloat(row.get("rpe_count")))
    bucket["value"] += value * factor
    bucket["sets"] += int(nfloat(row.get("set_count")))
    bucket["volume_kg"] += nfloat(row.get("volume_kg_sum")) * factor
    bucket["rpe_sets"] += rpe_count
    bucket["rpe_sum"] += nfloat(row.get("rpe_sum")) * factor
    bucket["rpe_weight"] += rpe_count * factor
    bucket["exercises"].add(str(exercise_name))


def _finish_bucket(bucket: Dict[str, Any]) -> Dict[str, Any]:
    rpe_weight = bucket["rpe_weight"]
    return {
        **{k: v for k, v in bucket.items() if k not in _INTERNAL_KEYS},
        "value": round(float(bucket["value"]), 2),
        "volume_kg": round(float(bucket["volume_kg"]), 2),
        "avg_rpe": round(bucket["rpe_sum"] / rpe_weight, 2) if rpe_weight > 0 else None,
        "exercise_count": len(bucket["exercises"]),
        "exercises": sorted(bucket["exercises"]),
    }


_OUTROS_NAMES = ("outros", "other", "others")
_SOURCE_BY_ROLE = {
    "PRIMARY": "canonical_primary",
    "SECONDARY": "canonical_secondary",
    "STABILIZER": "canonical_stabilizer",
}


def resolve_exercise_groups(
    exercise_ids: set[int], include_secondary: bool = True
) -> Dict[int | None, List[Tuple[str, str, float]]]:
    """Grupos de cada exercicio canonico como `[(grupo, papel, peso)]`, pelo
    DONO das contribuicoes (`VolumeService._fetch_contributions_by_mapping` +
    `_resolve_contributions`, M76 Lote 3c): o peso e o `stimulus_weight`
    cadastrado, para PRIMARY, SECONDARY e STABILIZER.

    Sem `include_secondary`, so os papeis PRIMARY. Exercicio sem contribuicao
    cadastrada (ou sem exercicio canonico, chave `None`) segue a politica
    unica de `VolumeService`: PRIMARY em "Outros". Um grupo que aparece em
    mais de um papel (incluido) do mesmo exercicio vira UMA entrada (pesos
    somados, papel mais forte) - assim `sets` continua sendo contagem fisica
    por grupo."""
    service = VolumeService()
    contributions_by_mapping = service._fetch_contributions_by_mapping(exercise_ids)
    names_by_id = service.muscle_group_names_by_id() if contributions_by_mapping else {}

    resolved: Dict[int | None, List[Tuple[str, str, float]]] = {}
    for exercise_id in [None, *sorted(exercise_ids)]:
        merged: Dict[str, List[Any]] = {}
        for item in VolumeService._resolve_contributions(
            exercise_id, contributions_by_mapping, None, None, None, None, None
        ):
            group_id = item.get("muscle_group_id")
            name = names_by_id.get(group_id) if group_id is not None else item.get("muscle_group_name")
            group = str(fix_text(name or OUTROS_GROUP)).strip() or OUTROS_GROUP
            if normalize(group) in _OUTROS_NAMES:
                group = OUTROS_GROUP
            role = str(item.get("role") or "PRIMARY").upper()
            if role != "PRIMARY" and not include_secondary:
                continue
            weight = float(item.get("stimulus_weight") or 0.0)
            if group in merged:
                merged[group][1] += weight
                if role == "PRIMARY":
                    merged[group][0] = role
            else:
                merged[group] = [role, weight]
        resolved[exercise_id] = [(group, role, weight) for group, (role, weight) in merged.items()]
    return resolved


def _canonical_id(row: Dict[str, Any]) -> int | None:
    value = row.get("canonical_exercise_id")
    return int(nfloat(value)) if value is not None else None


def aggregate(period: str, metric: str, days: int, include_secondary: bool, user_id: int | None = None) -> Dict[str, Any]:
    """Agregado canonico por (periodo, grupo) e por grupo.

    Campos por grupo: `value` (metrica pedida), `sets`, `volume_kg` (reps x
    carga, independente de `metric`), `avg_rpe` (media das series com RPE,
    ponderada pelo peso do grupo) e `rpe_sets`. Cada serie conta em cada
    grupo do exercicio com o `stimulus_weight` cadastrado em
    `exercise_muscle_contributions` (M76 Lote 3c; antes PRIMARY 1,0 e so o 1o
    SECONDARY com 0,5 fixo). Sem `include_secondary`, so os grupos PRIMARY;
    com ele, tambem SECONDARY e STABILIZER. O peso vale para `value`,
    `volume_kg` e o peso do RPE; `sets` e `rpe_sets` seguem contagem fisica.

    Series de exercicio sem contribuicao cadastrada vao para "Outros" (mesma
    politica de `VolumeService`) e continuam listadas em `unmapped`."""
    rows, meta = load_rows(days, user_id=user_id)
    groups_by_exercise = resolve_exercise_groups(
        {cid for cid in map(_canonical_id, rows) if cid is not None}, include_secondary
    )

    period_groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
    table_groups: Dict[str, Dict[str, Any]] = {}
    unmapped: Dict[str, int] = defaultdict(int)
    total_sets = 0
    mapped_sets = 0

    for row in rows:
        exercise_name = fix_text(row.get("exercise_name") or "")
        set_count = int(nfloat(row.get("set_count")))
        value = metric_value(row, metric)
        period_key = period_label(as_date(row.get("activity_date")), period)
        total_sets += set_count

        groups = groups_by_exercise[_canonical_id(row)]
        if any(group != OUTROS_GROUP for group, _role, _weight in groups):
            mapped_sets += set_count
        else:
            unmapped[str(exercise_name or "(sem nome)")] += set_count

        for group, role, factor in groups:
            source = "unmapped" if group == OUTROS_GROUP else _SOURCE_BY_ROLE.get(role, "canonical_secondary")
            key = (period_key, group)
            if key not in period_groups:
                period_groups[key] = _new_bucket(group, period=period_key, exercise_count=0, source=source)
            _accumulate(period_groups[key], row, value, factor, exercise_name)

            if group not in table_groups:
                table_groups[group] = {
                    **_new_bucket(group, source="exercise_catalog via exercises.mapping_id (resolvido por exercise_legacy_id_map)"),
                    "periods": set(),
                }
            _accumulate(table_groups[group], row, value, factor, exercise_name)
            table_groups[group]["periods"].add(period_key)

    trend_rows = [_finish_bucket(item) for item in period_groups.values()]
    trend_rows.sort(key=lambda r: (r["period"], r["group"]))

    table_rows = []
    for item in sorted(table_groups.values(), key=lambda r: r["value"], reverse=True):
        table_rows.append({**_finish_bucket(item), "period_count": len(item["periods"])})

    return fix_obj(
        {
            "ok": True,
            "source": "canonical_exercises_from_exercise_catalog",
            "metric": metric,
            "period": period,
            "days": days,
            "total_sets": total_sets,
            "mapped_sets": mapped_sets,
            "unmapped_sets": total_sets - mapped_sets,
            "mapped_ratio": round(mapped_sets / total_sets, 4) if total_sets else 0,
            "meta": meta,
            "unmapped": [{"exercise": k, "sets": v} for k, v in sorted(unmapped.items(), key=lambda kv: kv[1], reverse=True)[:100]],
            "rows": trend_rows,
            "data": trend_rows,
            "trend_rows": trend_rows,
            "table": table_rows,
            "table_rows": table_rows,
            "groups": table_rows,
        }
    )


@router.get("/muscle-groups-mapped-health")
def health(
    days: int = Query(180, ge=1, le=2000),
    identity: Identity = Depends(get_current_identity),
) -> Dict[str, Any]:
    rows, meta = load_rows(days, user_id=identity.user_id)
    groups_by_exercise = resolve_exercise_groups(
        {cid for cid in map(_canonical_id, rows) if cid is not None}, include_secondary=False
    )
    groups = sorted(
        {
            group
            for row in rows
            for group, role, _weight in groups_by_exercise.get(_canonical_id(row)) or []
            if role == "PRIMARY" and group != OUTROS_GROUP
        }
    )
    return fix_obj(
        {
            "ok": True,
            "source": "canonical_exercises_from_exercise_catalog",
            "days": days,
            "set_count": sum(int(nfloat(row.get("set_count"))) for row in rows),
            "groups": groups,
            "meta": meta,
        }
    )


@router.get("/muscle-groups-mapped-debug")
def debug(
    days: int = Query(180, ge=1, le=2000),
    identity: Identity = Depends(get_current_identity),
) -> Dict[str, Any]:
    return aggregate("weekly", "sets", days, True, user_id=identity.user_id)


@router.get("/muscle-groups-mapped-trend")
def trend(
    period: str = Query("weekly", pattern="^(daily|weekly|monthly|quarterly|yearly)$"),
    metric: str = Query("volume", pattern="^(volume|sets|reps|load)$"),
    days: int = Query(180, ge=1, le=2000),
    include_secondary: bool = Query(True),
    identity: Identity = Depends(get_current_identity),
) -> Dict[str, Any]:
    result = aggregate(period, metric, days, include_secondary, user_id=identity.user_id)
    result["rows"] = result.get("trend_rows", [])
    result["data"] = result.get("trend_rows", [])
    return result


@router.get("/muscle-groups-mapped-table")
def table(
    period: str = Query("weekly", pattern="^(daily|weekly|monthly|quarterly|yearly)$"),
    metric: str = Query("volume", pattern="^(volume|sets|reps|load)$"),
    days: int = Query(180, ge=1, le=2000),
    include_secondary: bool = Query(True),
    identity: Identity = Depends(get_current_identity),
) -> Dict[str, Any]:
    result = aggregate(period, metric, days, include_secondary, user_id=identity.user_id)
    table_rows = result.get("table_rows", [])
    result["rows"] = table_rows
    result["data"] = table_rows
    result["table"] = table_rows
    result["groups"] = table_rows
    return result
