"""Corpo no Novo Dashboard - `GET /analytics/dashboard/body` e o bloco
Corpo da visao geral (plano §6 `body_weight_ma7`/`waist_cm`/
`recomposition_signal`, §6.7 do brief, §10, §11 `/dashboard/corpo`; F1.4).

UMA implementacao para as duas telas: o `overview_service` (F1.6) chamava
uma consulta propria de peso; a partir da F1.4 ele chama ESTE modulo
(`load_weight_rows` + `body_block`), e a rota nova reusa as mesmas funcoes
puras. Nao existe segunda media movel.

Metricas (versoes no `metrics_registry`):

- `weight-ma-v1` (DERIVED): pesagem de `body_composition` ∪ `daily_biometrics`
  ∪ `intervals_wellness.weight_kg` com PRIORIDADE por dia nessa ordem; media
  movel de 7 dias SOBRE OS DIAS COM PESAGEM - dias sem pesagem nao entram no
  denominador e nunca viram zero; a fonte de cada dia e declarada.
- `waist-v1` (OBSERVED): serie de `body_measurements(site='waist')`; a
  "ultima cintura" e a ultima medida COM `protocol_ok`; medidas fora do
  protocolo entram na serie com `data_quality: LOW` e NAO alimentam o sinal
  de recomposicao.
- bioimpedancia (ESTIMATED): gordura %, massa magra e agua da balanca. Uma
  leitura isolada nao confirma nada - tendencia so com >= 2 leituras, e
  mesmo assim MEDIUM (e um modelo da balanca, sensivel a hidratacao).
- `recomp-v1` (INFERRED): peso estavel (±1% na media movel, com >= 14 dias
  de cobertura) + cintura caindo >= 1 cm (so protocolo) + >= 50% dos
  exercicios comparaveis PROGRESSING/STABLE em 28 dias. Resultado
  `compatible | not_compatible | insufficient_data`, sempre com `evidence[]`
  e `limitations[]`. O texto e "compativel com recomposicao" - nunca
  "recomposicao confirmada" (§6.7 do brief).

Marcos: derivados do que existe hoje (atribuicao de Modelo de Treino);
`diet_phase` chega com os eventos de contexto (F2.1) - limitacao declarada.

Sobreposicao com desempenho: serie de e1RM medio dos exercicios comparaveis
(do MESMO lote de progressao, 1 consulta), para um segundo grafico alinhado
no tempo - sem dual-axis.

Desempenho (§15): alvo <= 2 s no shim da Data API com o dataset de 3 anos
(`tests/integration/dataapi/test_dashboard_body_performance.py`).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Optional, Sequence

from ...body_measurements_schema import TABLE_MISSING_LIMITATION, has_body_measurements_table
from ...domain.body_measurement_sites import SITE_LABELS, WAIST_SITE
from ...observability import log_event
from .coercion import to_date, to_float
from .contracts import DataNature, DataQuality, WindowInfo, WindowKind
from .exposure_service import get_exercises_progression_batch, reliable_e1rm
from .identity import require_user_id
from .insight_rules import WAIST_UNAVAILABLE_LIMITATION
from .metrics_registry import ExerciseProgressionStatus, metric_version, threshold
from .time_windows import resolve_window

# Pontos de peso devolvidos (um por dia COM pesagem; janela maxima 365d).
MAX_WEIGHT_POINTS = 120

# Teto de linhas de peso na consulta (3 fontes x 365 dias).
MAX_WEIGHT_ROWS = 1200

# Teto de medidas por fita (12 locais; 45 linhas em producao em 2026-10-15).
MAX_MEASUREMENT_ROWS = 2000

# Teto de leituras de bioimpedancia devolvidas (uma por dia).
MAX_BIOIMPEDANCE_POINTS = 120

# Teto de pontos da serie de desempenho (um por dia de treino).
MAX_PERFORMANCE_POINTS = 120

# Teto de marcos devolvidos.
MAX_MILESTONES = 20

# Janela do sinal de recomposicao (§6: "em 28d").
RECOMPOSITION_WINDOW = WindowKind.ROLLING_28D

DRILLDOWN_HREF = "/dashboard/corpo"

# Rotulos dos locais: dono unico `domain/body_measurement_sites.py` (Python puro,
# sem o router da api-core). Os locais da 075 (pescoco, antebracos) entram nas
# series automaticamente.
MEASUREMENT_SITE_LABELS: Mapping[str, str] = SITE_LABELS

# ---------------------------------------------------------------------------
# Limitacoes (texto pronto para a tela; nunca escrito no frontend)
# ---------------------------------------------------------------------------

# Tabela ausente (janela de deploy da 047): `WAIST_UNAVAILABLE_LIMITATION`,
# importado de `insight_rules` (onde nasceu na F1.6).
WAIST_NOT_RECORDED_LIMITATION = (
    "Cintura: nenhuma medida registrada no período; sem ela o sinal não distingue recomposição "
    "de simples manutenção."
)
WAIST_SINGLE_PROTOCOL_LIMITATION = (
    "Cintura: uma única medida no protocolo no período; a variação precisa de duas."
)
WAIST_ONLY_OUT_OF_PROTOCOL_LIMITATION = (
    "Cintura: só medidas fora do protocolo no período; elas aparecem na série (confiança LOW) "
    "mas não alimentam o sinal de recomposição."
)
WAIST_OUT_OF_PROTOCOL_LIMITATION = (
    "Medidas de cintura fora do protocolo entram na série com confiança LOW e não alimentam o "
    "sinal de recomposição."
)
OUT_OF_PROTOCOL_LIMITATION = (
    "Só medidas fora do protocolo neste local: confiança LOW."
)
BIOIMPEDANCE_ESTIMATED_LIMITATION = (
    "Bioimpedância é estimativa da balança (ESTIMATED), sensível a hidratação e horário."
)
BIOIMPEDANCE_SINGLE_LIMITATION = (
    "Uma única bioimpedância não confirma tendência: são necessárias ao menos 2 leituras."
)
RECOMPOSITION_NEVER_PROOF_LIMITATION = (
    "Sinal INFERIDO: 'compatível com recomposição' nunca é prova de recomposição."
)
PERFORMANCE_MIX_LIMITATION = (
    "e1RM médio mistura os exercícios comparáveis do dia: trocar quais exercícios entram altera a "
    "média sem que a força tenha mudado."
)
MILESTONES_LIMITATION = (
    "Marcos derivados só da atribuição de Modelo de Treino; fases de dieta e outros eventos "
    "chegam com os eventos de contexto (F2.1)."
)
MILESTONES_UNAVAILABLE_LIMITATION = "Marcos indisponíveis neste banco (tabela de atribuições ausente)."

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

# Peso das tres fontes, com PRIORIDADE por dia (body_composition >
# daily_biometrics > intervals_wellness, plano §6 `weight-ma-v1`). A escolha
# do vencedor do dia e feita em Python (`pick_daily_weights`), funcao pura.
WEIGHT_SOURCES_SQL = """
SELECT d.dia AS local_date, d.weight_kg AS weight_kg, d.prio AS prio, d.fonte AS fonte
FROM (
  SELECT bc.local_date AS dia, bc.weight_kg AS weight_kg, 1 AS prio, 'body_composition' AS fonte
  FROM body_composition bc
  WHERE bc.user_id = %s AND bc.weight_kg IS NOT NULL AND bc.local_date BETWEEN %s AND %s
  UNION ALL
  SELECT db.local_date, db.weight_kg, 2, 'daily_biometrics'
  FROM daily_biometrics db
  WHERE db.user_id = %s AND db.weight_kg IS NOT NULL AND db.local_date BETWEEN %s AND %s
  UNION ALL
  SELECT iw.wellness_date, iw.weight_kg, 3, 'intervals_wellness'
  FROM intervals_wellness iw
  WHERE iw.user_id = %s AND iw.weight_kg IS NOT NULL AND iw.wellness_date BETWEEN %s AND %s
) d
ORDER BY d.dia ASC, d.prio ASC
LIMIT %s
"""

# Mesma leitura SEM `intervals_wellness` (banco anterior a migracao 027).
WEIGHT_SOURCES_LOCAL_SQL = """
SELECT d.dia AS local_date, d.weight_kg AS weight_kg, d.prio AS prio, d.fonte AS fonte
FROM (
  SELECT bc.local_date AS dia, bc.weight_kg AS weight_kg, 1 AS prio, 'body_composition' AS fonte
  FROM body_composition bc
  WHERE bc.user_id = %s AND bc.weight_kg IS NOT NULL AND bc.local_date BETWEEN %s AND %s
  UNION ALL
  SELECT db.local_date, db.weight_kg, 2, 'daily_biometrics'
  FROM daily_biometrics db
  WHERE db.user_id = %s AND db.weight_kg IS NOT NULL AND db.local_date BETWEEN %s AND %s
) d
ORDER BY d.dia ASC, d.prio ASC
LIMIT %s
"""

# Ultima bioimpedancia ate o fim da janela (ESTIMATED: e um modelo da balanca).
BIOIMPEDANCE_SQL = """
SELECT local_date, weight_kg, body_fat_percent, muscle_mass_kg
FROM body_composition
WHERE user_id = %s AND local_date <= %s
  AND (body_fat_percent IS NOT NULL OR muscle_mass_kg IS NOT NULL)
ORDER BY local_date DESC, id DESC
LIMIT 1
"""

# Serie de bioimpedancia da janela (mais recentes primeiro; a ultima leitura
# antes da janela tambem entra, para "ultima leitura" existir quando o
# periodo nao tem nenhuma).
BIOIMPEDANCE_SERIES_SQL = """
SELECT local_date, weight_kg, body_fat_percent, muscle_mass_kg, body_water_percent
FROM body_composition
WHERE user_id = %s AND local_date <= %s
  AND (body_fat_percent IS NOT NULL OR muscle_mass_kg IS NOT NULL OR body_water_percent IS NOT NULL)
ORDER BY local_date DESC, id DESC
LIMIT %s
"""

# Medidas por fita da janela (todos os locais; a separacao e em Python).
MEASUREMENTS_SQL = """
SELECT id, local_date, site, value_cm, protocol_ok, notes
FROM body_measurements
WHERE user_id = %s AND local_date BETWEEN %s AND %s
ORDER BY local_date ASC, site ASC, id ASC
LIMIT %s
"""

# Marcos: Modelos de Treino atribuidos DENTRO da janela.
#
# So o INICIO da vigencia vira marco, de proposito. Depois da migracao 049
# (M23) o fim tambem e observavel (`unassigned_at`), mas numa TROCA os dois
# instantes sao o mesmo e o marco viraria duas linhas sobre o mesmo evento -
# numa curva de peso/cintura, o que interessa e "daqui para frente o treino
# era outro", que o inicio ja marca. A timeline de `/dashboard/contexto`
# (`context_service`) e quem mostra o fim, porque la ele responde a outra
# pergunta ("fiquei sem Modelo").
TEMPLATE_ASSIGNMENTS_SQL = """
SELECT a.assigned_at, t.name AS template_name, t.id AS template_id
FROM workout_plan_template_assignments a
JOIN workout_plan_templates t ON t.id = a.template_id
WHERE a.user_id = %s AND a.assigned_at >= %s AND a.assigned_at < %s
ORDER BY a.assigned_at ASC
LIMIT %s
"""

_COMPARABLE_STATUSES = frozenset(
    {
        ExerciseProgressionStatus.PROGRESSING.value,
        ExerciseProgressionStatus.STABLE.value,
        ExerciseProgressionStatus.PLATEAU_SUSPECTED.value,
        ExerciseProgressionStatus.REGRESSING.value,
    }
)
_PROGRESS_OK_STATUSES = frozenset(
    {ExerciseProgressionStatus.PROGRESSING.value, ExerciseProgressionStatus.STABLE.value}
)

_QUALITY_ORDER = [DataQuality.HIGH, DataQuality.MEDIUM, DataQuality.LOW, DataQuality.INSUFFICIENT]

METRIC_VERSIONS: Mapping[str, str] = {
    key: metric_version(key)
    for key in ("body_weight_ma7", "waist_cm", "recomposition_signal", "e1rm", "exercise_progression_status")
}


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------


def _metric(
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
        "metric_version": METRIC_VERSIONS[version_key],
        "data_quality": quality,
        "limitations": list(limitations or []),
    }


def _worst(*qualities: DataQuality) -> DataQuality:
    worst = DataQuality.HIGH
    for quality in qualities:
        if _QUALITY_ORDER.index(quality) > _QUALITY_ORDER.index(worst):
            worst = quality
    return worst


def _fmt(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}".replace(".", ",")


def _rows(conn: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall() or []


def _percent(value: Optional[float]) -> Optional[float]:
    """Percentual da balanca: fracao (0,21) vira 21,0; fora de [0, 100] vira None."""
    if value is None:
        return None
    if 0 < value <= 1:
        value *= 100.0
    if value < 0 or value > 100:
        return None
    return value


# ---------------------------------------------------------------------------
# Peso (`weight-ma-v1`) - funcoes puras sobre as linhas da consulta
# ---------------------------------------------------------------------------


def pick_daily_weights(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Uma pesagem por dia: a de MENOR `prio` (body_composition > daily_biometrics
    > intervals_wellness). Dias sem pesagem simplesmente nao existem na lista."""
    by_day: dict[date, dict[str, Any]] = {}
    for row in rows:
        day = to_date(row.get("local_date"))
        weight = to_float(row.get("weight_kg"))
        if day is None or weight is None:
            continue
        prio = int(to_float(row.get("prio")) or 99)
        current = by_day.get(day)
        if current is None or prio < current["prio"]:
            by_day[day] = {"local_date": day, "weight_kg": weight, "prio": prio, "source": str(row.get("fonte"))}
    return [by_day[day] for day in sorted(by_day)]


def moving_average_points(
    daily: Sequence[Mapping[str, Any]],
    *,
    ma_days: int,
    date_from: date,
    date_to: date,
) -> list[dict[str, Any]]:
    """Media movel de `ma_days` dias sobre os dias COM pesagem: para cada dia
    pesado dentro de `[date_from, date_to]`, a media das pesagens em
    `[dia - ma_days + 1, dia]`. Dias sem pesagem nao entram no denominador -
    nunca viram zero. `ma7_points` diz de quantas pesagens a media saiu."""
    ordered = list(daily)
    out: list[dict[str, Any]] = []
    for index, point in enumerate(ordered):
        day = point["local_date"]
        if day < date_from or day > date_to:
            continue
        start = day - timedelta(days=ma_days - 1)
        window_values = [
            float(other["weight_kg"]) for other in ordered[: index + 1] if start <= other["local_date"] <= day
        ]
        out.append(
            {
                "local_date": day,
                "weight_kg": round(float(point["weight_kg"]), 2),
                "source": point["source"],
                "ma7_kg": round(sum(window_values) / len(window_values), 2) if window_values else None,
                "ma7_points": max(1, len(window_values)),
            }
        )
    return out


def weight_summary(
    weight_rows: Sequence[Mapping[str, Any]],
    *,
    window: WindowInfo,
    extra_limitations: Sequence[str] = (),
) -> dict[str, Any]:
    """Resumo do peso na janela: pontos com media movel, ultima pesagem,
    variacao da media (primeira -> ultima media da janela) e qualidade por
    frequencia de pesagem. Campos `_first_ma`/`_span_days` sao internos."""
    ma_days = int(threshold("weight_ma_days"))
    min_per_week = float(threshold("weight_ma_min_weighings_per_week"))
    daily = pick_daily_weights(weight_rows)
    points = moving_average_points(daily, ma_days=ma_days, date_from=window.date_from, date_to=window.date_to)
    if len(points) > MAX_WEIGHT_POINTS:
        points = points[-MAX_WEIGHT_POINTS:]

    weighings = len(points)
    per_week = round(weighings * 7.0 / float(window.days), 2) if window.days else None
    latest = points[-1] if points else None
    first_with_ma = next((p for p in points if p["ma7_kg"] is not None), None)
    latest_ma = latest["ma7_kg"] if latest else None
    first_ma = first_with_ma["ma7_kg"] if first_with_ma else None
    span_days = (latest["local_date"] - first_with_ma["local_date"]).days if latest and first_with_ma else 0

    change = None
    change_pct = None
    if latest_ma is not None and first_ma is not None and span_days >= 1:
        change = round(latest_ma - first_ma, 2)
        change_pct = round((latest_ma - first_ma) / first_ma * 100.0, 2) if first_ma else None

    limitations: list[str] = list(extra_limitations)
    if weighings == 0:
        quality = DataQuality.INSUFFICIENT
        limitations.append("Nenhuma pesagem registrada no período (body_composition, daily_biometrics ou Intervals).")
    elif per_week is not None and per_week >= min_per_week:
        quality = DataQuality.HIGH
    elif weighings >= 2:
        quality = DataQuality.MEDIUM
        limitations.append(
            f"Menos de {_fmt(min_per_week, 0)} pesagens por semana: a média móvel de {ma_days} dias fica "
            "sensível a pesagens isoladas."
        )
    else:
        quality = DataQuality.LOW
        limitations.append("Uma única pesagem no período: não há tendência a ler.")

    if latest is None:
        answer = "Sem pesagem no período: a tendência de peso não pode ser lida."
    elif change is None:
        answer = f"Última pesagem {_fmt(latest['weight_kg'])} kg; ainda sem tendência (uma pesagem só)."
    else:
        direction = "estável" if abs(change_pct or 0.0) <= float(threshold("weight_stable_pct")) * 100 else (
            "subindo" if change > 0 else "caindo"
        )
        answer = (
            f"Peso {direction}: média de {ma_days} dias em {_fmt(latest_ma)} kg "
            f"({'+' if change > 0 else ''}{_fmt(change)} kg em {span_days} dias)."
        )

    return {
        "answer": answer,
        "data_quality": quality,
        "limitations": limitations,
        "latest": _metric(
            latest["weight_kg"] if latest else None, unit="kg", nature=DataNature.OBSERVED,
            version_key="body_weight_ma7", quality=quality,
        ),
        "ma7_latest": _metric(
            latest_ma, unit="kg", nature=DataNature.DERIVED, version_key="body_weight_ma7", quality=quality
        ),
        "change": _metric(change, unit="kg", nature=DataNature.DERIVED, version_key="body_weight_ma7", quality=quality),
        "change_pct": _metric(
            change_pct, unit="%", nature=DataNature.DERIVED, version_key="body_weight_ma7", quality=quality
        ),
        "weighings_count": weighings,
        "weighings_per_week": _metric(
            per_week if weighings else None, unit="pesagens/sem", nature=DataNature.DERIVED,
            version_key="body_weight_ma7", quality=quality,
        ),
        "points": points,
        "_first_ma": first_ma,
        "_span_days": span_days,
    }


# ---------------------------------------------------------------------------
# Medidas por fita (`waist-v1` e demais locais)
# ---------------------------------------------------------------------------


def measurement_points(rows: Sequence[Mapping[str, Any]], *, site: str) -> list[dict[str, Any]]:
    """Serie de UM local, ordenada por data. Fora do protocolo -> LOW."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("site")) != site:
            continue
        day = to_date(row.get("local_date"))
        value = to_float(row.get("value_cm"))
        if day is None or value is None:
            continue
        protocol_ok = bool(int(to_float(row.get("protocol_ok")) or 0))
        out.append(
            {
                "id": int(row["id"]) if row.get("id") is not None else None,
                "local_date": day,
                "value_cm": round(value, 1),
                "protocol_ok": protocol_ok,
                "data_quality": DataQuality.HIGH if protocol_ok else DataQuality.LOW,
                "notes": row.get("notes"),
            }
        )
    out.sort(key=lambda item: (item["local_date"], item["id"] or 0))
    return out


def waist_summary(
    measurement_rows: Sequence[Mapping[str, Any]],
    *,
    window: WindowInfo,
    table_available: bool = True,
) -> dict[str, Any]:
    """`waist-v1`: ultima cintura COM protocolo, variacao entre a primeira e a
    ultima medida no protocolo dentro da janela, e a serie inteira (as fora do
    protocolo com LOW). `available=False` quando nao ha nenhuma medida."""
    points = measurement_points(measurement_rows, site=WAIST_SITE)
    protocol = [p for p in points if p["protocol_ok"]]
    out_of_protocol = len(points) - len(protocol)
    latest = protocol[-1] if protocol else None
    first = protocol[0] if protocol else None

    change = None
    span_days = None
    if latest and first and latest is not first:
        span_days = (latest["local_date"] - first["local_date"]).days
        if span_days >= 1:
            change = round(latest["value_cm"] - first["value_cm"], 1)
        else:
            span_days = None

    limitations: list[str] = []
    if not table_available:
        quality = DataQuality.INSUFFICIENT
        limitations.append(WAIST_UNAVAILABLE_LIMITATION)
    elif not points:
        quality = DataQuality.INSUFFICIENT
        limitations.append(WAIST_NOT_RECORDED_LIMITATION)
    elif not protocol:
        quality = DataQuality.LOW
        limitations.append(WAIST_ONLY_OUT_OF_PROTOCOL_LIMITATION)
    elif change is None:
        quality = DataQuality.MEDIUM
        limitations.append(WAIST_SINGLE_PROTOCOL_LIMITATION)
    else:
        quality = DataQuality.HIGH
    if protocol and out_of_protocol:
        limitations.append(WAIST_OUT_OF_PROTOCOL_LIMITATION)

    return {
        "available": bool(points),
        "table_available": table_available,
        "latest": _metric(
            latest["value_cm"] if latest else None, unit="cm", nature=DataNature.OBSERVED,
            version_key="waist_cm", quality=quality, limitations=limitations,
        ),
        "latest_local_date": latest["local_date"] if latest else None,
        "change": _metric(
            change, unit="cm", nature=DataNature.DERIVED, version_key="waist_cm", quality=quality,
            limitations=limitations,
        ),
        "change_span_days": span_days,
        "points": points,
        "protocol_points": len(protocol),
        "out_of_protocol_points": out_of_protocol,
        "data_quality": quality,
        "limitations": limitations,
        "_first": first["value_cm"] if first else None,
        "_last": latest["value_cm"] if latest else None,
    }


def measurement_series(
    measurement_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Series simples dos demais locais de `MEASUREMENT_SITES` (dono
    `domain/body_measurement_sites.py`) presentes nas linhas - a cintura tem
    resumo proprio."""
    out: list[dict[str, Any]] = []
    for site, label in MEASUREMENT_SITE_LABELS.items():
        if site == WAIST_SITE:
            continue
        points = measurement_points(measurement_rows, site=site)
        if not points:
            continue
        protocol = [p for p in points if p["protocol_ok"]]
        latest = protocol[-1] if protocol else points[-1]
        first = protocol[0] if protocol else None
        change = None
        if first and latest is not first and (latest["local_date"] - first["local_date"]).days >= 1:
            change = round(latest["value_cm"] - first["value_cm"], 1)
        quality = DataQuality.HIGH if len(protocol) >= 2 else (DataQuality.MEDIUM if protocol else DataQuality.LOW)
        limitations = [] if protocol else [OUT_OF_PROTOCOL_LIMITATION]
        out.append(
            {
                "site": site,
                "label": label,
                "latest": _metric(
                    latest["value_cm"], unit="cm", nature=DataNature.OBSERVED, version_key="waist_cm",
                    quality=quality, limitations=limitations,
                ),
                "latest_local_date": latest["local_date"],
                "change": _metric(
                    change, unit="cm", nature=DataNature.DERIVED, version_key="waist_cm", quality=quality,
                    limitations=limitations,
                ),
                "points": points,
                "data_quality": quality,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Bioimpedancia (ESTIMATED)
# ---------------------------------------------------------------------------


def bioimpedance_points(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        day = to_date(row.get("local_date"))
        if day is None:
            continue
        out.append(
            {
                "local_date": day,
                "weight_kg": to_float(row.get("weight_kg")),
                "body_fat_percent": _percent(to_float(row.get("body_fat_percent"))),
                "muscle_mass_kg": to_float(row.get("muscle_mass_kg")),
                "body_water_percent": _percent(to_float(row.get("body_water_percent"))),
            }
        )
    out.sort(key=lambda item: item["local_date"])
    return out


def bioimpedance_summary(rows: Sequence[Mapping[str, Any]], *, window: WindowInfo) -> dict[str, Any]:
    """Ultima leitura ate `date_to` (mesmo anterior a janela) e a serie DENTRO
    da janela. Tendencia so com >= 2 leituras na janela; sempre ESTIMATED."""
    all_points = bioimpedance_points(rows)
    in_window = [p for p in all_points if window.date_from <= p["local_date"] <= window.date_to]
    if len(in_window) > MAX_BIOIMPEDANCE_POINTS:
        in_window = in_window[-MAX_BIOIMPEDANCE_POINTS:]
    latest = all_points[-1] if all_points else None

    limitations = [BIOIMPEDANCE_ESTIMATED_LIMITATION]
    if latest is None:
        quality = DataQuality.INSUFFICIENT
        limitations.append("Nenhuma bioimpedância registrada.")
    elif len(in_window) < 2:
        quality = DataQuality.LOW
        limitations.append(BIOIMPEDANCE_SINGLE_LIMITATION)
        if latest["local_date"] < window.date_from:
            limitations.append(f"Última bioimpedância é anterior ao período ({latest['local_date'].isoformat()}).")
    else:
        quality = DataQuality.MEDIUM

    def _change(key: str) -> Optional[float]:
        if len(in_window) < 2:
            return None
        first = next((p[key] for p in in_window if p[key] is not None), None)
        last = next((p[key] for p in reversed(in_window) if p[key] is not None), None)
        if first is None or last is None:
            return None
        return round(last - first, 2)

    def _estimated(value: Optional[float], unit: str) -> dict[str, Any]:
        return _metric(
            value, unit=unit, nature=DataNature.ESTIMATED, version_key="body_weight_ma7",
            quality=quality if value is not None else DataQuality.INSUFFICIENT, limitations=limitations,
        )

    return {
        "readings_count": len(in_window),
        "latest_local_date": latest["local_date"] if latest else None,
        "body_fat_percent": _estimated(latest["body_fat_percent"] if latest else None, "%"),
        "muscle_mass_kg": _estimated(latest["muscle_mass_kg"] if latest else None, "kg"),
        "body_water_percent": _estimated(latest["body_water_percent"] if latest else None, "%"),
        "body_fat_change": _estimated(_change("body_fat_percent"), "%"),
        "muscle_mass_change": _estimated(_change("muscle_mass_kg"), "kg"),
        "points": in_window,
        "data_quality": quality,
        "limitations": limitations,
    }


# ---------------------------------------------------------------------------
# Desempenho (e1RM medio dos comparaveis, do lote de progressao)
# ---------------------------------------------------------------------------


def performance_series(progression_items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Serie diaria de e1RM medio entre os exercicios COMPARAVEIS do lote.
    ESTIMATED como o e1RM; um ponto por dia.

    Alinhado a regra unica (2026-10-06): de cada exercicio entram so as
    exposicoes do RUN COMPARAVEL final (`comparable_exposures` ultimas - mesma
    chave de equipamento/convencao que o status avaliou) e so com e1RM
    confiavel (serie de ate 12 reps, `exposure_service.reliable_e1rm`). Uma
    exposicao em outra maquina nao entra mais na media."""
    comparable = [item for item in progression_items if item.get("status") in _COMPARABLE_STATUSES]
    by_day: dict[date, list[float]] = defaultdict(list)
    for item in comparable:
        # As exposicoes do run comparavel (mesmo contexto de fadiga; as de
        # outro contexto intercaladas ficam de fora) - `classify_batch`.
        for exposure in item.get("_comparable_run") or ():
            day = to_date(exposure.get("local_date"))
            value = to_float(reliable_e1rm(exposure))
            if day is None or value is None:
                continue
            by_day[day].append(value)
    points = [
        {"local_date": day, "e1rm_avg_kg": round(sum(values) / len(values), 2), "exercises": len(values)}
        for day, values in sorted(by_day.items())
    ]
    if len(points) > MAX_PERFORMANCE_POINTS:
        points = points[-MAX_PERFORMANCE_POINTS:]

    min_comparable = int(threshold("insight_recomp_min_comparable_exercises"))
    limitations = [PERFORMANCE_MIX_LIMITATION]
    if not comparable:
        quality = DataQuality.INSUFFICIENT
        limitations.append("Nenhum exercício com histórico comparável no período.")
    elif len(comparable) < min_comparable:
        quality = DataQuality.MEDIUM
        limitations.append(f"Menos de {min_comparable} exercícios comparáveis: a média fica sensível a um só exercício.")
    else:
        quality = DataQuality.HIGH
    return {
        "points": points,
        "exercises_total": len(progression_items),
        "comparable_exercises": len(comparable),
        "progressing_or_stable": sum(1 for item in comparable if item.get("status") in _PROGRESS_OK_STATUSES),
        "data_quality": quality,
        "limitations": limitations,
    }


# ---------------------------------------------------------------------------
# Sinal de recomposicao (`recomp-v1`, INFERRED)
# ---------------------------------------------------------------------------

RECOMP_COMPATIBLE = "compatible"
RECOMP_NOT_COMPATIBLE = "not_compatible"
RECOMP_INSUFFICIENT = "insufficient_data"

# Como cada verificacao e dita na frase do veredito, conforme o resultado.
# Ficam FORA do dicionario da verificacao de proposito: `RecompositionCheckOut`
# e um contrato fechado (`extra="forbid"`) e estes textos sao apresentacao.
CHECK_POSITIVE: Mapping[str, str] = {
    "weight_stable": "o peso ficou estável",
    "waist_dropping": "a cintura caiu",
    "performance_kept": "o desempenho se manteve",
}
CHECK_NEGATIVE: Mapping[str, str] = {
    "weight_stable": "o peso não ficou estável",
    "waist_dropping": "a cintura não caiu",
    "performance_kept": "o desempenho não se manteve",
}
CHECK_MISSING: Mapping[str, str] = {
    "weight_stable": "pesagens suficientes para uma média móvel comparável",
    "waist_dropping": "duas medidas de cintura no protocolo",
    "performance_kept": "exercícios com histórico comparável",
}


def _join(partes: Sequence[str]) -> str:
    """"a, b e c" - enumeracao em portugues, sem virgula antes do "e"."""
    itens = list(partes)
    if not itens:
        return ""
    if len(itens) == 1:
        return itens[0]
    return f"{', '.join(itens[:-1])} e {itens[-1]}"


def recomposition_signal(
    *,
    weight: Mapping[str, Any],
    waist: Mapping[str, Any],
    progression_items: Sequence[Mapping[str, Any]],
    window: WindowInfo,
) -> dict[str, Any]:
    """Tres verificacoes, cada uma `True | False | None` (nao avaliavel):
    peso estavel, cintura caindo (so protocolo) e desempenho mantido.

    - `insufficient_data`: FALTA DADO para avaliar alguma das tres (menos de
      duas cinturas no protocolo, pesagens insuficientes, sem exercicio
      comparavel). As `limitations[]` dizem o que falta, verificacao a
      verificacao - "insuficiente" nunca e um vazio sem explicacao.
    - `not_compatible`: as tres FORAM avaliadas e ao menos uma falhou -
      inclusive o caso "peso estavel + cintura parada", que e manutencao, nao
      recomposicao. A frase diz o que se confirmou e o que nao, e `evidence[]`
      traz os tres numeros.
    - `compatible`: as tres verdadeiras.

    Sempre "compativel / nao compativel com recomposicao" - nunca
    "confirmada" nem "descartada".

    A regra de insight equivalente (`insight_rules.rule_recomposition_
    compatible`) SILENCIA no caso `not_compatible`: a home tem espaco para
    duas acoes e um "nao compativel" ocuparia lugar sem sugerir nada. Aqui,
    na tela de corpo, o usuario foi olhar de proposito - entao o veredito
    aparece com as evidencias (decisao do usuario, 2026-09-24)."""
    stable_pct = float(threshold("weight_stable_pct")) * 100.0
    min_span = int(threshold("insight_weight_min_span_days"))
    waist_drop = float(threshold("waist_drop_cm"))
    min_comparable = int(threshold("insight_recomp_min_comparable_exercises"))
    share_needed = float(threshold("insight_recomp_progress_share"))

    # 1) peso
    change_pct = weight["change_pct"]["value"]
    span_days = int(weight.get("_span_days") or 0)
    if change_pct is None or span_days < min_span:
        weight_stable = None
        weight_detail = (
            f"Média móvel cobre {span_days} dias; são necessários {min_span}."
            if change_pct is not None
            else "Sem duas médias móveis no período."
        )
    else:
        weight_stable = abs(float(change_pct)) <= stable_pct
        weight_detail = (
            f"Média de 7 dias {_fmt(weight.get('_first_ma'))} → {_fmt(weight['ma7_latest']['value'])} kg "
            f"({'+' if float(change_pct) > 0 else ''}{_fmt(float(change_pct))}% em {span_days} dias; "
            f"estável = até ±{_fmt(stable_pct, 0)}%)."
        )

    # 2) cintura (so protocolo)
    waist_change = waist["change"]["value"]
    if waist_change is None:
        waist_dropping = None
        waist_detail = waist["limitations"][0] if waist.get("limitations") else "Sem variação de cintura avaliável."
    else:
        waist_dropping = (-float(waist_change)) >= waist_drop
        waist_detail = (
            f"Cintura no protocolo {_fmt(waist.get('_first'))} → {_fmt(waist.get('_last'))} cm "
            f"({'+' if float(waist_change) > 0 else ''}{_fmt(float(waist_change))} cm em "
            f"{waist.get('change_span_days')} dias; queda esperada ≥ {_fmt(waist_drop)} cm)."
        )

    # 3) desempenho
    comparable = [item for item in progression_items if item.get("status") in _COMPARABLE_STATUSES]
    ok = [item for item in comparable if item.get("status") in _PROGRESS_OK_STATUSES]
    share = (len(ok) / len(comparable)) if comparable else None
    if len(comparable) < min_comparable:
        performance_ok = None
        performance_detail = (
            f"{len(comparable)} exercício(s) com histórico comparável; são necessários {min_comparable}."
        )
    else:
        performance_ok = float(share or 0.0) >= share_needed
        performance_detail = (
            f"{len(ok)} de {len(comparable)} exercícios comparáveis progredindo ou estáveis "
            f"({_fmt((share or 0.0) * 100, 0)}%; esperado ≥ {_fmt(share_needed * 100, 0)}%)."
        )

    checks = [
        {"code": "weight_stable", "label": "Peso estável", "result": weight_stable, "detail": weight_detail},
        {"code": "waist_dropping", "label": "Cintura caindo", "result": waist_dropping, "detail": waist_detail},
        {"code": "performance_kept", "label": "Desempenho mantido", "result": performance_ok, "detail": performance_detail},
    ]
    results = [check["result"] for check in checks]

    if any(result is None for result in results):
        status = RECOMP_INSUFFICIENT
        quality = DataQuality.INSUFFICIENT
        missing = [CHECK_MISSING[check["code"]] for check in checks if check["result"] is None]
        answer = f"Dados insuficientes para ler o sinal: falta {_join(missing)}."
    elif all(results):
        status = RECOMP_COMPATIBLE
        quality = _worst(DataQuality.MEDIUM, waist["data_quality"], weight["data_quality"])
        answer = (
            "Compatível com recomposição: peso estável, cintura caindo e desempenho mantido nos "
            f"últimos {window.days} dias. É um sinal, não uma prova."
        )
    else:
        # As tres verificacoes SAO avaliaveis e ao menos uma falhou: a tela diz
        # o que se confirmou E o que nao - "peso estavel e desempenho mantido,
        # mas a cintura nao caiu" e mais util (e mais honesto) do que um
        # veredito seco. Continua "nao compativel com", nunca "descartada".
        status = RECOMP_NOT_COMPATIBLE
        quality = _worst(DataQuality.MEDIUM, waist["data_quality"], weight["data_quality"])
        held = [CHECK_POSITIVE[check["code"]] for check in checks if check["result"] is True]
        failed = [CHECK_NEGATIVE[check["code"]] for check in checks if check["result"] is False]
        if held:
            answer = (
                f"Não compatível com recomposição: {_join(held)}, mas {_join(failed)} "
                f"nos últimos {window.days} dias."
            )
        else:
            answer = f"Não compatível com recomposição: {_join(failed)} nos últimos {window.days} dias."

    evidence: list[dict[str, Any]] = []
    if change_pct is not None:
        evidence.append(
            {
                "metric": "body_weight_ma7",
                "from_value": weight.get("_first_ma"),
                "to_value": weight["ma7_latest"]["value"],
                "unit": "kg",
                "nature": DataNature.DERIVED,
                "observed_at": window.date_to,
            }
        )
    if waist_change is not None:
        evidence.append(
            {
                "metric": "waist_cm",
                "from_value": waist.get("_first"),
                "to_value": waist.get("_last"),
                "unit": "cm",
                "nature": DataNature.OBSERVED,
                "observed_at": waist.get("latest_local_date"),
            }
        )
    if share is not None:
        evidence.append(
            {
                "metric": "progress_share",
                "from_value": None,
                "to_value": round(share * 100.0, 1),
                "unit": "%",
                "nature": DataNature.DERIVED,
                "observed_at": window.date_to,
            }
        )

    limitations = [RECOMPOSITION_NEVER_PROOF_LIMITATION]
    if status == RECOMP_INSUFFICIENT:
        # O que FALTA, verificacao a verificacao: sem isso a tela mostra
        # "dados insuficientes" sem dizer o que registrar para sair do limbo.
        limitations.extend(
            f"{check['label']}: {check['detail']}" for check in checks if check["result"] is None
        )
    if waist_change is None:
        limitations.extend(waist.get("limitations") or [WAIST_NOT_RECORDED_LIMITATION])
    elif waist.get("out_of_protocol_points"):
        limitations.append(WAIST_OUT_OF_PROTOCOL_LIMITATION)
    limitations.extend(weight.get("limitations") or [])
    # Dedup preservando a ordem (a limitacao da cintura pode chegar pelas duas
    # vias acima quando a propria verificacao dela e a que falta).
    limitations = list(dict.fromkeys(limitations))

    return {
        "status": status,
        "nature": DataNature.INFERRED,
        "metric_version": METRIC_VERSIONS["recomposition_signal"],
        "window": window,
        "answer": answer,
        "checks": checks,
        "evidence": evidence,
        "limitations": limitations,
        "alternative_explanations": [
            "Manutenção simples de peso e força (sem mudança de composição).",
            "Variação de peso mascarada por hidratação ou horário das pesagens.",
            "Cintura medida em pontos ou horários diferentes.",
        ],
        "data_quality": quality,
        "comparable_exercises": len(comparable),
        "progressing_or_stable": len(ok),
    }


# ---------------------------------------------------------------------------
# Bloco Corpo da visao geral (usado pelo `overview_service`)
# ---------------------------------------------------------------------------


def body_block(
    *,
    weight_rows: Sequence[Mapping[str, Any]],
    bioimpedance: Optional[Mapping[str, Any]],
    window: WindowInfo,
    extra_limitations: Sequence[str] = (),
    waist: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Bloco Corpo. Peso com media movel (DERIVED), bioimpedancia ESTIMATED,
    cintura de `waist_summary` quando informada - sem ela (chamador antigo ou
    tabela ausente) a limitacao da F1.4 fica declarada."""
    weight = weight_summary(weight_rows, window=window, extra_limitations=extra_limitations)
    if waist is None:
        waist = waist_summary((), window=window, table_available=False)

    bio_quality = DataQuality.MEDIUM  # sempre ESTIMATED (modelo da balanca)
    bio_date = to_date((bioimpedance or {}).get("local_date"))
    fat = _percent(to_float((bioimpedance or {}).get("body_fat_percent")))
    muscle = to_float((bioimpedance or {}).get("muscle_mass_kg"))
    bio_limitations = [BIOIMPEDANCE_ESTIMATED_LIMITATION]
    if bio_date and bio_date < window.date_from:
        bio_limitations.append(f"Última bioimpedância é anterior ao período ({bio_date.isoformat()}).")

    limitations = [*(waist["limitations"] if not waist["available"] else []), *weight["limitations"]]
    quality = weight["data_quality"]

    return {
        "question": "Como está o corpo?",
        "answer": weight["answer"],
        "drilldown_href": DRILLDOWN_HREF,
        "data_quality": quality,
        "limitations": limitations,
        "weight_latest": weight["latest"],
        "weight_ma7_latest": weight["ma7_latest"],
        "weight_ma7_change": weight["change"],
        "weight_ma7_change_pct": weight["change_pct"],
        "weighings_count": weight["weighings_count"],
        "weighings_per_week": weight["weighings_per_week"],
        "weight_points": weight["points"],
        "body_fat_percent": _metric(
            fat, unit="%", nature=DataNature.ESTIMATED, version_key="body_weight_ma7",
            quality=bio_quality if fat is not None else DataQuality.INSUFFICIENT, limitations=bio_limitations,
        ),
        "muscle_mass_kg": _metric(
            muscle, unit="kg", nature=DataNature.ESTIMATED, version_key="body_weight_ma7",
            quality=bio_quality if muscle is not None else DataQuality.INSUFFICIENT, limitations=bio_limitations,
        ),
        "bioimpedance_local_date": bio_date,
        "waist_cm": waist["latest"],
        # Campos internos para as regras (removidos da resposta publica).
        "_weight_ma7_first": weight["_first_ma"],
        "_span_days": weight["_span_days"],
        "_waist": {
            "available": waist["available"],
            "change": waist["change"],
            "first": waist["_first"],
            "last": waist["_last"],
            "limitations": list(waist["limitations"]),
        },
    }


# ---------------------------------------------------------------------------
# Leituras (I/O)
# ---------------------------------------------------------------------------


def load_weight_rows(
    conn: Any, *, user_id: int, window: WindowInfo
) -> tuple[list[dict[str, Any]], list[str]]:
    """Pesagens das tres fontes, da janela ESTENDIDA em `ma_days - 1` dias
    para tras (a media do primeiro dia precisa dos dias anteriores).
    `intervals_wellness` nasce na migracao 027 (grupo B): num banco sem ela a
    consulta cai para as duas fontes locais e a ausencia vira limitacao
    declarada - nunca 500, nunca zero."""
    ma_days = int(threshold("weight_ma_days"))
    params = (user_id, window.date_from - timedelta(days=ma_days - 1), window.date_to)
    try:
        return _rows(conn, WEIGHT_SOURCES_SQL, (*params, *params, *params, MAX_WEIGHT_ROWS)), []
    except Exception as exc:  # noqa: BLE001 - fonte opcional nunca derruba o bloco
        log_event("dashboard.weight_source_unavailable", user_id=user_id, source="intervals_wellness", error=str(exc))
    rows = _rows(conn, WEIGHT_SOURCES_LOCAL_SQL, (*params, *params, MAX_WEIGHT_ROWS))
    return rows, ["Peso do Intervals.icu indisponível neste banco (tabela ausente); só as fontes locais foram lidas."]


def load_measurement_rows(
    conn: Any, *, user_id: int, window: WindowInfo
) -> tuple[list[dict[str, Any]], bool]:
    """Medidas por fita da janela. Devolve `(linhas, tabela_existe)`: antes da
    047 a lista e vazia e `tabela_existe=False` (limitacao declarada)."""
    with conn.cursor() as cursor:
        if not has_body_measurements_table(cursor):
            return [], False
        cursor.execute(MEASUREMENTS_SQL, (user_id, window.date_from, window.date_to, MAX_MEASUREMENT_ROWS))
        return list(cursor.fetchall() or []), True


def load_bioimpedance_latest(conn: Any, *, user_id: int, date_to: date) -> Optional[dict[str, Any]]:
    rows = _rows(conn, BIOIMPEDANCE_SQL, (user_id, date_to))
    return rows[0] if rows else None


def load_bioimpedance_series(conn: Any, *, user_id: int, window: WindowInfo) -> list[dict[str, Any]]:
    return _rows(conn, BIOIMPEDANCE_SERIES_SQL, (user_id, window.date_to, MAX_BIOIMPEDANCE_POINTS + 1))


def load_milestones(conn: Any, *, user_id: int, window: WindowInfo) -> tuple[list[dict[str, Any]], list[str]]:
    """Marcos observaveis hoje: Modelos de Treino atribuidos na janela."""
    started = datetime.combine(window.date_from, datetime.min.time())
    ended = datetime.combine(window.date_to + timedelta(days=1), datetime.min.time())
    try:
        rows = _rows(conn, TEMPLATE_ASSIGNMENTS_SQL, (user_id, started, ended, MAX_MILESTONES))
    except Exception as exc:  # noqa: BLE001 - marco e contexto, nunca derruba a tela
        log_event("dashboard.milestones_unavailable", user_id=user_id, error=str(exc))
        return [], [MILESTONES_LIMITATION, MILESTONES_UNAVAILABLE_LIMITATION]
    items: list[dict[str, Any]] = []
    for row in rows:
        assigned = row.get("assigned_at")
        day = assigned.date() if isinstance(assigned, datetime) else to_date(assigned)
        if day is None:
            continue
        items.append(
            {
                "local_date": day,
                "kind": "template_assigned",
                "label": f"Modelo de Treino atribuído: {row.get('template_name') or row.get('template_id')}",
                "detail": None,
            }
        )
    return items, [MILESTONES_LIMITATION]


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------


def build_body(
    conn: Any,
    *,
    user_id: Any,
    window: WindowInfo,
) -> dict[str, Any]:
    """Payload de `GET /analytics/dashboard/body`."""
    owner = require_user_id(user_id)
    timings: dict[str, float] = {}

    def _timed(label: str, fn):
        started = datetime.now()
        result = fn()
        timings[label] = round((datetime.now() - started).total_seconds(), 3)
        return result

    weight_rows, weight_limitations = _timed("weight", lambda: load_weight_rows(conn, user_id=owner, window=window))
    measurement_rows, table_available = _timed(
        "measurements", lambda: load_measurement_rows(conn, user_id=owner, window=window)
    )
    bio_rows = _timed("bioimpedance", lambda: load_bioimpedance_series(conn, user_id=owner, window=window))
    milestones, milestone_limitations = _timed("milestones", lambda: load_milestones(conn, user_id=owner, window=window))

    # Lote de progressao da janela da tela (1 consulta) - serie de desempenho.
    progression_items, truncated, _per_exercise = _timed(
        "progression_batch", lambda: get_exercises_progression_batch(conn, user_id=owner, window=window)
    )

    weight = weight_summary(weight_rows, window=window, extra_limitations=weight_limitations)
    waist = waist_summary(measurement_rows, window=window, table_available=table_available)
    others = measurement_series(measurement_rows)
    bio = bioimpedance_summary(bio_rows, window=window)
    performance = performance_series(progression_items)
    if truncated:
        performance["limitations"].append(
            "Histórico de exposições truncado no teto do lote; a série de desempenho pode estar incompleta."
        )

    # Sinal de recomposicao: janela PROPRIA de 28 dias terminando em date_to
    # (§6). Quando a tela ja esta em 28d, tudo e reaproveitado sem consulta
    # nova; senao, uma segunda leitura de peso/cintura/lote.
    recomp_window = resolve_window(RECOMPOSITION_WINDOW, today=window.date_to, timezone_name=window.timezone)
    if recomp_window.date_from == window.date_from and recomp_window.date_to == window.date_to:
        recomp_weight, recomp_waist, recomp_items = weight, waist, progression_items
    else:
        recomp_weight_rows, recomp_weight_limitations = _timed(
            "recomp_weight", lambda: load_weight_rows(conn, user_id=owner, window=recomp_window)
        )
        recomp_weight = weight_summary(recomp_weight_rows, window=recomp_window, extra_limitations=recomp_weight_limitations)
        if table_available:
            recomp_measurement_rows, _ = _timed(
                "recomp_measurements", lambda: load_measurement_rows(conn, user_id=owner, window=recomp_window)
            )
        else:
            recomp_measurement_rows = []
        recomp_waist = waist_summary(recomp_measurement_rows, window=recomp_window, table_available=table_available)
        recomp_items, _t, _p = _timed(
            "recomp_progression_batch",
            lambda: get_exercises_progression_batch(conn, user_id=owner, window=recomp_window),
        )
    recomposition = recomposition_signal(
        weight=recomp_weight, waist=recomp_waist, progression_items=recomp_items, window=recomp_window
    )

    envelope_quality = _worst(weight["data_quality"], waist["data_quality"])
    limitations = [
        "Cada seção carrega a própria confiança e limitações; a confiança do envelope é a pior entre peso e cintura.",
    ]
    if not table_available:
        limitations.append(TABLE_MISSING_LIMITATION)

    log_event(
        "dashboard.computed",
        block="body",
        user_id=owner,
        window=window.kind.value,
        days=window.days,
        timings=timings,
        recomposition=recomposition["status"],
    )

    return {
        "window": window,
        "timezone": window.timezone,
        "metric_versions": dict(METRIC_VERSIONS),
        "data_quality": envelope_quality,
        "limitations": limitations,
        "weight": {
            "answer": weight["answer"],
            "latest": weight["latest"],
            "ma7_latest": weight["ma7_latest"],
            "change": weight["change"],
            "change_pct": weight["change_pct"],
            "weighings_count": weight["weighings_count"],
            "weighings_per_week": weight["weighings_per_week"],
            "points": weight["points"],
            "data_quality": weight["data_quality"],
            "limitations": weight["limitations"],
        },
        "waist": {key: value for key, value in waist.items() if not str(key).startswith("_")},
        "measurements": others,
        "bioimpedance": bio,
        "recomposition": recomposition,
        "performance": performance,
        "milestones": milestones,
        "milestones_limitations": milestone_limitations,
        "_timings": timings,
    }


__all__ = [
    "BIOIMPEDANCE_SERIES_SQL",
    "BIOIMPEDANCE_SQL",
    "DRILLDOWN_HREF",
    "MAX_WEIGHT_POINTS",
    "MEASUREMENTS_SQL",
    "MEASUREMENT_SITE_LABELS",
    "METRIC_VERSIONS",
    "RECOMP_COMPATIBLE",
    "RECOMP_INSUFFICIENT",
    "RECOMP_NOT_COMPATIBLE",
    "WAIST_NOT_RECORDED_LIMITATION",
    "WAIST_ONLY_OUT_OF_PROTOCOL_LIMITATION",
    "WAIST_OUT_OF_PROTOCOL_LIMITATION",
    "WAIST_SINGLE_PROTOCOL_LIMITATION",
    "WAIST_UNAVAILABLE_LIMITATION",
    "WEIGHT_SOURCES_LOCAL_SQL",
    "WEIGHT_SOURCES_SQL",
    "bioimpedance_summary",
    "body_block",
    "build_body",
    "load_bioimpedance_latest",
    "load_measurement_rows",
    "load_milestones",
    "load_weight_rows",
    "measurement_points",
    "measurement_series",
    "moving_average_points",
    "performance_series",
    "pick_daily_weights",
    "recomposition_signal",
    "waist_summary",
    "weight_summary",
]
