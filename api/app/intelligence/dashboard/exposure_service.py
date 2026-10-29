"""Exposicoes de um exercicio e o status de progressao (plano §6 `exposure`,
§8, §10, §15).

Uma pergunta, uma query: "quais foram as ultimas execucoes DESTE exercicio
por ESTE usuario, com as series de trabalho, o equipamento e a convencao de
carga de cada uma?". O agrupamento e as regras ficam fora do SQL
(`comparability.py`, funcoes puras).

Decisoes que o SQL implementa:

- `user_id` SEMPRE no `WHERE`, via `require_user_id` (§15). Nenhum caminho
  aceita `None`.
- Id canonico por `LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id
  = e.mapping_id` + `COALESCE(elm.canonical_exercise_id, e.mapping_id)`, o
  mesmo padrao de `analytics.py`/`services._resolve_canonical_exercise_id`.
  Consequencia direta do §16.1: um exercicio renomeado/absorvido continua
  aparecendo no MESMO historico.
- So series `working` (`set_types.sql_is_working`) - aquecimento e serie de
  ajuste nao sao desempenho.
- Equipamento canonico = `gym_equipment.equipment_id` (nunca `gym_equipment.id`,
  que e cache por academia) e a convencao vem de `equipment_exercises`
  (migracao 045; NULL -> `total`).
- Cap de `MAX_EXPOSURES` exposicoes: a resposta da RDS Data API tem teto de
  1 MB (`db_dataapi.py`, `UnsupportedResultException`), e uma exposicao pode
  trazer varias series. O corte e pelas mais RECENTES, que sao as que
  respondem "estou evoluindo?".

Uma exposicao = uma LINHA de `exercises` (um exercicio dentro de uma sessao).
Duas passagens pelo mesmo aparelho na mesma sessao sao duas exposicoes, com
`position_in_session` diferente - somar as duas esconderia a segunda.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping, Optional

from ...domain import e1rm
from ...domain.prior_fatigue import PriorFatigue, band_label, context_band
from ...domain.set_measures import format_set_measure, has_non_rep_based, is_rep_based, normalize_measure
from ...domain.deload import deload_source_label, deload_source_value, sql_is_deload, sql_not_deload
from ...domain.training_goal import goal_value, sql_goal_is
from ...domain.training_history import parse_decimal, parse_int, parse_reps_range
from ...load_conventions import counts_in_tonnage, load_convention_select_expr
from ...observability import log_event
from ...set_types import sql_is_working, sql_set_confirmed
from .comparability import (
    REASON_EQUIPMENT_UNKNOWN,
    REASON_FIRST_IN_CONTEXT,
    REASON_KEY_CHANGED,
    ComparabilityKey,
    ExposureSummary,
    LoadConvention,
    WorkingSet,
    all_equipment_missing,
    build_key,
    classify_exercise_progression,
    compare_exposures,
    comparison_with_current,
    keys_match,
    normalize_load_convention,
    paired_top_reps as _paired_top_reps,
    physical_keys_match,
    supports_e1rm,
    top_load_reps,
    trailing_comparable_indices,
    trailing_comparable_run,
)
from .contracts import DataQuality, WindowInfo
from .identity import require_user_id
from .metrics_registry import THRESHOLDS, ExerciseProgressionStatus, metric_version

# Teto de exposicoes por resposta (plano §15; limite de 1 MB da Data API).
MAX_EXPOSURES = 30

# Ultimas decisoes do motor de progressao anexadas como EVIDENCIA (§8.2: nunca
# substituem a regra).
MAX_PROGRESSION_DECISIONS = 5

_CANONICAL = "COALESCE(elm.canonical_exercise_id, e.mapping_id)"
_ES_WORKING = sql_is_working("es.set_type")
_EXPOSURE_ORDER = "COALESCE({alias}.started_at, {alias}.local_date)"

# Selecao das N exposicoes mais recentes do par (usuario, exercicio canonico)
# dentro da janela. Subconsulta: o LIMIT precisa cair sobre EXPOSICOES, nao
# sobre series - senao uma exposicao de 8 series comeria o cap sozinha.
#
# `{load_convention}` so vira `eqex.load_convention` quando a migracao 045 ja
# rodou; antes disso vira `NULL`, que e exatamente o que a coluna significa
# quando ninguem cadastrou nada (`total`, D12). Assim a leitura funciona com o
# codigo publicado ANTES da migracao (ordem de deploy) e o payload nao muda de
# formato.
#
# `{goal_filter}` (M97 F4): o predicado do objetivo do dono (`sql_goal_is`) na
# leitura da PROGRESSAO (comparabilidade, veredito); vazio na leitura do
# RECORDE, que e unico entre objetivos (chave fisica, decisao do usuario). O
# deload fica fora das duas.
def _exposures_sql_template(goal_filter: str) -> str:
    return f"""
SELECT
  sel.exercise_row_id,
  a.id                      AS activity_id,
  a.local_date              AS local_date,
  a.started_at              AS started_at,
  e.sequence                AS position_in_session,
  e.training_goal           AS training_goal,
  ge.equipment_id           AS equipment_id,
  eq.name                   AS equipment_name,
  {{load_convention}}       AS load_convention,
  wpe.reps_target           AS prescribed_reps_target,
  es.set_number             AS set_number,
  es.reps                   AS reps,
  es.weight_kg              AS weight_kg,
  es.rpe                    AS rpe,
  es.measure                AS measure,
  es.duration_seconds       AS duration_seconds,
  es.rest_seconds           AS rest_seconds,
  es.notes                  AS notes
FROM (
  SELECT e2.id AS exercise_row_id, {_EXPOSURE_ORDER.format(alias="a2")} AS occurred_at
  FROM activities a2
  JOIN exercises e2 ON e2.activity_id = a2.id
  LEFT JOIN exercise_legacy_id_map elm2 ON elm2.old_exercise_id = e2.mapping_id
  WHERE a2.user_id = %s
    AND COALESCE(elm2.canonical_exercise_id, e2.mapping_id) = %s{goal_filter}
    AND {sql_not_deload("a2")}
    AND a2.local_date BETWEEN %s AND %s
    AND EXISTS (
      SELECT 1 FROM exercise_sets es2
      WHERE es2.exercise_id = e2.id AND {sql_is_working("es2.set_type")} AND {sql_set_confirmed("es2.reps_pending")}
    )
  ORDER BY occurred_at DESC, a2.id DESC, e2.sequence DESC, e2.id DESC
  LIMIT %s
) sel
JOIN exercises e ON e.id = sel.exercise_row_id
JOIN activities a ON a.id = e.activity_id
LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
LEFT JOIN gym_equipment ge ON ge.id = e.equipment_id
LEFT JOIN equipment eq ON eq.id = ge.equipment_id
LEFT JOIN equipment_exercises eqex
  ON eqex.equipment_id = ge.equipment_id
 AND eqex.exercise_id = {_CANONICAL}
 AND eqex.is_active = 1
LEFT JOIN workout_plan_exercises wpe ON wpe.id = e.workout_plan_exercise_id
JOIN exercise_sets es ON es.exercise_id = e.id AND {_ES_WORKING} AND {sql_set_confirmed('es.reps_pending')}
ORDER BY sel.occurred_at ASC, a.id ASC, e.sequence ASC, e.id ASC, es.set_number ASC, es.id ASC
"""


EXPOSURES_SQL_TEMPLATE = _exposures_sql_template(f"\n    AND {sql_goal_is('e2')}")
# Recorde (M97 F4): todos os objetivos, sem o parametro do objetivo.
EXPOSURES_ALL_GOALS_SQL_TEMPLATE = _exposures_sql_template("")

# Nome canonico + nome AMAZFIT do exercicio (o mesmo subselect de
# `equipment_exercises.list_equipment_exercises`).
EXERCISE_SQL = """
SELECT
  ec.id   AS exercise_id,
  ec.name AS exercise_name,
  (
    SELECT exm.external_name
    FROM exercise_external_mappings exm
    WHERE exm.exercise_id = ec.id AND exm.provider = 'AMAZFIT' AND exm.is_active = 1
    ORDER BY exm.id ASC LIMIT 1
  ) AS exercise_amazfit_name
FROM exercise_catalog ec
WHERE ec.id = %s
"""

# Evidencia secundaria: ultimas decisoes do motor de progressao v2 para o par
# (usuario, exercicio canonico) - opcionalmente filtradas pelo equipamento da
# chave corrente.
PROGRESSION_DECISIONS_SQL = f"""
SELECT
  pd.id, pd.kind, pd.decision, pd.phase_before, pd.phase_after,
  pd.engine_version, pd.created_at, pt.equipment_id
FROM progression_decisions pd
JOIN progression_tracks pt ON pt.id = pd.track_id
WHERE pd.user_id = %s AND pt.exercise_id = %s AND {sql_goal_is("pt")}
ORDER BY pd.created_at DESC, pd.id DESC
LIMIT %s
"""


def _float(value: Any) -> Optional[float]:
    parsed = parse_decimal(value)
    return None if parsed is None else float(parsed)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


# ---------------------------------------------------------------------------
# Agregacao (funcao PURA sobre as linhas da query)
# ---------------------------------------------------------------------------


def build_exposures(rows) -> list[dict[str, Any]]:
    """Agrupa as linhas planas em exposicoes, em ordem cronologica crescente.

    Funcao pura: o unico lugar que conhece o formato do resultado da query, e
    por isso testavel sem banco."""
    por_exposicao: dict[int, dict[str, Any]] = {}
    for row in rows or []:
        exercise_row_id = int(row["exercise_row_id"])
        exposure = por_exposicao.get(exercise_row_id)
        if exposure is None:
            reps_min, reps_max = parse_reps_range(row.get("prescribed_reps_target"))
            exposure = {
                "exercise_row_id": exercise_row_id,
                "activity_id": parse_int(row.get("activity_id")),
                "local_date": row.get("local_date"),
                "started_at": row.get("started_at"),
                "position_in_session": parse_int(row.get("position_in_session")),
                "training_goal": goal_value(row.get("training_goal")),
                "equipment_id": parse_int(row.get("equipment_id")),
                "equipment_name": row.get("equipment_name"),
                "load_convention_raw": row.get("load_convention"),
                "prescribed_reps_min": reps_min,
                "prescribed_reps_max": reps_max,
                "sets": [],
            }
            por_exposicao[exercise_row_id] = exposure
        exposure["sets"].append(
            {
                "set_number": parse_int(row.get("set_number")),
                "reps": parse_int(row.get("reps")),
                "weight_kg": _float(row.get("weight_kg")),
                "rpe": _float(row.get("rpe")),
                "rest_seconds": parse_int(row.get("rest_seconds")),
                "notes": row.get("notes"),
                # Lote T3: medida da serie (NULL = reps) e o texto do dono.
                "measure": normalize_measure(row.get("measure")).value,
                "duration_seconds": parse_int(row.get("duration_seconds")),
                "measure_text": format_set_measure(
                    row.get("measure"), row.get("reps"), row.get("duration_seconds")
                ),
            }
        )
    return list(por_exposicao.values())


def _summarize_sets(sets: list[dict[str, Any]], convention: LoadConvention) -> dict[str, Any]:
    """Agregados de UMA exposicao: reps totais, melhor carga, RPE medio, e1RM.

    e1RM = `domain/e1rm.estimated_1rm` (Epley+RIR, `epley-rir-v1`) - nunca
    reimplementado aqui. `None` (nao zero) quando a serie nao tem RPE
    (`MissingRpePolicy.SKIP`): sem RPE nao ha RIR, e inventar RIR 0
    produziria um numero que ninguem mediu (§6). Tambem `None` onde a
    convencao nao suporta e1RM (`assisted`/`bodyweight`).

    `reliable_e1rm` = o maior e1RM entre as series com ate
    `E1RM_MAX_RELIABLE_REPS` reps (`domain/e1rm.reliable_e1rm`) - base do
    recorde (`_records`). `unreliable_e1rm` = o maior e1RM das series com
    MAIS de `E1RM_MAX_RELIABLE_REPS` reps (ainda exigindo RPE - `SKIP`), que
    o recorde so mostra, marcado, quando a chave nao tem nenhum confiavel
    (M76 Lote 3d). O `e1rm` da exposicao continua sendo o maior de
    todas as series (com `e1rm_reps`), que e o que a regra de progressao
    consome e trava pelo teto.

    Medidas de serie (Lote T3): so as series de REPETICOES
    (`set_measures.is_rep_based`; ausente = reps) entram nos agregados -
    reps, carga, RPE, e1RM e tonelagem. Serie por tempo/maximo de reps
    nao e marca de carga; a exposicao a marca em `has_non_rep_sets` e a
    comparabilidade a declara NOT_COMPARABLE."""
    sets = [item for item in sets if is_rep_based(item.get("measure"))]
    total_reps = sum(item["reps"] for item in sets if item["reps"] is not None)
    loads = [item["weight_kg"] for item in sets if item["weight_kg"] is not None]
    rpes = [item["rpe"] for item in sets if item["rpe"] is not None]

    best_load: Optional[float] = None
    if loads:
        # `assisted`: a MELHOR carga e a MENOR (menos assistencia).
        best_load = min(loads) if convention is LoadConvention.ASSISTED else max(loads)

    best_e1rm: Optional[float] = None
    best_e1rm_reps: Optional[int] = None
    best_reliable_e1rm: Optional[float] = None
    best_unreliable_e1rm: Optional[float] = None
    if supports_e1rm(convention):
        for item in sets:
            value = e1rm.estimated_1rm(item["weight_kg"], item["reps"], item["rpe"])
            if value is None:
                continue
            if best_e1rm is None or value > best_e1rm:
                best_e1rm = float(value)
                best_e1rm_reps = item["reps"]
            if e1rm.is_reliable_reps(item["reps"]):
                if best_reliable_e1rm is None or value > best_reliable_e1rm:
                    best_reliable_e1rm = float(value)
            elif best_unreliable_e1rm is None or value > best_unreliable_e1rm:
                best_unreliable_e1rm = float(value)

    # M58: mesma regra do relatorio e do resto do dashboard. Ate aqui so
    # `bodyweight` saia; `assisted` somava reps x quilos de ASSISTENCIA.
    tonnage = None
    if counts_in_tonnage(convention):
        pares = [(i["reps"], i["weight_kg"]) for i in sets if i["reps"] is not None and i["weight_kg"] is not None]
        if pares:
            tonnage = float(sum(reps * weight for reps, weight in pares))

    return {
        "total_reps": total_reps if any(item["reps"] is not None for item in sets) else None,
        "best_load_kg": best_load,
        "avg_rpe": round(sum(rpes) / len(rpes), 2) if rpes else None,
        "e1rm": best_e1rm,
        "e1rm_reps": best_e1rm_reps,
        "reliable_e1rm": best_reliable_e1rm,
        "unreliable_e1rm": best_unreliable_e1rm,
        "tonnage": tonnage,
    }


def fatigue_context_payload(prior: Optional[PriorFatigue]) -> dict[str, Any]:
    """Contexto de fadiga publico de uma exposicao: a faixa do dono
    (`prior_fatigue.context_band`) e o rotulo dele (a tela nunca o escreve),
    com o musculo principal e as series equivalentes previas como evidencia."""
    band = context_band(prior)
    return {
        "band": band.value,
        "band_label": band_label(band),
        "muscle_group_id": prior.muscle_group_id if prior is not None else None,
        "muscle_group_name": prior.muscle_group_name if prior is not None else None,
        "prior_sets": round(prior.prior_sets, 2) if prior is not None else 0.0,
    }


def assemble_exposures(
    raw_exposures: list[dict[str, Any]],
    canonical_exercise_id: int,
    fatigue_by_row: Optional[Mapping[int, Optional[PriorFatigue]]] = None,
) -> list[dict[str, Any]]:
    """Exposicoes cruas -> exposicoes completas (chave + agregados).

    A excecao do §8.1 e decidida AQUI, porque so quem ve o conjunto inteiro
    sabe se TODAS as exposicoes estao sem equipamento.

    `fatigue_by_row` (`exercises.id` -> fadiga previa na sessao real, de
    `session_fatigue_reader`) poe o contexto de fadiga na chave. `None` =
    contexto nao medido (chave sem contexto, o comportamento anterior)."""
    agnostic = all_equipment_missing([item["equipment_id"] for item in raw_exposures])

    resultado: list[dict[str, Any]] = []
    for item in raw_exposures:
        convention = normalize_load_convention(item["load_convention_raw"])
        agregados = _summarize_sets(item["sets"], convention)
        context: Optional[dict[str, Any]] = None
        if fatigue_by_row is not None:
            context = fatigue_context_payload(fatigue_by_row.get(int(item.get("exercise_row_id") or 0)))
        key = build_key(
            canonical_exercise_id,
            item["equipment_id"],
            convention,
            equipment_agnostic=agnostic,
            fatigue_context=context["band"] if context else None,
            training_goal=item.get("training_goal"),
        )
        resultado.append(
            {
                "activity_id": item["activity_id"],
                "local_date": _jsonable(item["local_date"]),
                "started_at": _jsonable(item["started_at"]),
                "equipment_id": item["equipment_id"],
                "equipment_name": item["equipment_name"],
                "load_convention": convention.value,
                "load_convention_source": "equipment_exercises" if item["load_convention_raw"] else "default_total",
                "position_in_session": item["position_in_session"],
                "fatigue_context": context,
                "sets": item["sets"],
                # Lote T3: alguma serie por tempo/maximo de reps (fora dos
                # agregados; a comparabilidade fica NOT_COMPARABLE).
                "has_non_rep_sets": has_non_rep_based(serie.get("measure") for serie in item["sets"]),
                "total_reps": agregados["total_reps"],
                "best_load_kg": agregados["best_load_kg"],
                "avg_rpe": agregados["avg_rpe"],
                "e1rm": agregados["e1rm"],
                "tonnage": agregados["tonnage"],
                "comparability_key": key.to_dict(),
                "_key": key,
                "_e1rm_reps": agregados["e1rm_reps"],
                "_reliable_e1rm": agregados["reliable_e1rm"],
                "_unreliable_e1rm": agregados["unreliable_e1rm"],
                "_prescribed_reps_min": item["prescribed_reps_min"],
                "_prescribed_reps_max": item["prescribed_reps_max"],
                # M97 F4: objetivo da execucao (interno; o contrato publico
                # da exposicao nao muda).
                "_training_goal": goal_value(item.get("training_goal")),
            }
        )
    return resultado


def to_summaries(exposures: list[dict[str, Any]]) -> list[ExposureSummary]:
    """Exposicoes da resposta -> entrada das regras puras do §8.2."""
    return [
        ExposureSummary(
            key=item["_key"],
            total_reps=item["total_reps"],
            best_load_kg=item["best_load_kg"],
            avg_rpe=item["avg_rpe"],
            e1rm=item["e1rm"],
            e1rm_reps=item["_e1rm_reps"],
            prescribed_reps_min=item["_prescribed_reps_min"],
            prescribed_reps_max=item["_prescribed_reps_max"],
            # Detalhe por serie: as regras comparam reps POR SERIE emparelhada
            # na carga de topo, nunca a soma (menos series nao e regressao).
            # O RPE por serie entra para as reps emparelhadas valerem
            # `reps + RIR` (M78) - a conta e de `comparability`.
            # Lote T3: so series de repeticoes viram `WorkingSet`.
            sets=tuple(
                WorkingSet(reps=serie["reps"], weight_kg=serie["weight_kg"], rpe=serie.get("rpe"))
                for serie in item["sets"]
                if is_rep_based(serie.get("measure"))
            ),
            has_non_rep_sets=bool(item.get("has_non_rep_sets")),
        )
        for item in exposures
    ]


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def get_exercise_exposures(
    conn: Any,
    exercise_id: int,
    *,
    user_id: Any,
    window: WindowInfo,
    limit: int = MAX_EXPOSURES,
    training_goal: Any = None,
) -> list[dict[str, Any]]:
    """As ultimas exposicoes (ate `limit`, teto `MAX_EXPOSURES`) do exercicio
    canonico `exercise_id` para `user_id`, dentro de `window`, SO do objetivo
    `training_goal` (M97 F1; ausente = default).

    UMA query (sem N+1), ordem cronologica crescente."""
    return get_exposures_between(
        conn,
        exercise_id,
        user_id=user_id,
        date_from=window.date_from,
        date_to=window.date_to,
        limit=max(1, min(int(limit), MAX_EXPOSURES)),
        training_goal=training_goal,
    )


# Teto da TABELA de `/dashboard/progressao` (seta "Evolucao" por sessao): a
# tabela lista a janela inteira, nao so as ultimas `MAX_EXPOSURES`. 120
# exposicoes ~ um ano treinando o exercicio 2x/semana e ainda bem abaixo do
# 1 MB da Data API. Sessao mais antiga que o corte fica sem seta (nunca com
# uma seta calculada contra uma exposicao que nao foi lida).
MAX_TABLE_EXPOSURES = 120


def get_exposures_between(
    conn: Any,
    exercise_id: int,
    *,
    user_id: Any,
    date_from: date,
    date_to: date,
    limit: int = MAX_EXPOSURES,
    training_goal: Any = None,
    all_goals: bool = False,
    with_fatigue_context: bool = True,
) -> list[dict[str, Any]]:
    """Mesma leitura de `get_exercise_exposures` (mesma query, mesma montagem),
    recebendo as datas locais `[date_from, date_to]` em vez de uma `WindowInfo`
    - e o que as rotas v2 de `/dashboard/progressao` tem em maos. Teto
    `MAX_TABLE_EXPOSURES`. So o objetivo `training_goal` (M97 F1; ausente =
    default): a progressao de um objetivo nao ve as exposicoes do outro.

    `all_goals=True` (M97 F4): TODOS os objetivos (`training_goal` ignorado) -
    a leitura do recorde/e1RM, que sao unicos (chave fisica). Deload fica fora
    nos dois casos. `with_fatigue_context=False` pula o contexto de fadiga (a
    chave fisica nao o usa; poupa as leituras dele)."""
    owner = require_user_id(user_id)
    canonical = int(exercise_id)
    capped = max(1, min(int(limit), MAX_TABLE_EXPOSURES))

    with conn.cursor() as cursor:
        template = EXPOSURES_ALL_GOALS_SQL_TEMPLATE if all_goals else EXPOSURES_SQL_TEMPLATE
        sql = template.format(load_convention=load_convention_select_expr(cursor))
        goal_params = () if all_goals else (goal_value(training_goal),)
        cursor.execute(sql, (owner, canonical, *goal_params, date_from, date_to, capped))
        rows = cursor.fetchall() or []

    raws = build_exposures(rows)
    fatigue = load_fatigue_contexts(conn, owner, raws) if with_fatigue_context else None
    return assemble_exposures(raws, canonical, fatigue)


def load_fatigue_contexts(
    conn: Any,
    user_id: int,
    raw_exposures: list[dict[str, Any]],
    group_names: Optional[Mapping[int, str]] = None,
) -> Optional[dict[int, Optional[PriorFatigue]]]:
    """Fadiga previa (dono `domain/prior_fatigue.py`) de cada exposicao na
    sessao REAL, numa leitura em lote das sessoes envolvidas
    (`session_fatigue_reader`). Falha de leitura nao derruba a resposta:
    devolve `None` (chave sem contexto, o comportamento anterior), loga e a
    resposta declara a limitacao."""
    activity_ids = {item.get("activity_id") for item in raw_exposures if item.get("activity_id") is not None}
    if not activity_ids:
        return {}
    try:
        from ..session_fatigue_reader import load_session_prior_fatigue

        return load_session_prior_fatigue(conn, user_id, activity_ids, group_names=group_names)
    except Exception as exc:  # noqa: BLE001 - contexto e refinamento, nunca derruba a leitura
        log_event("dashboard.fatigue_context_unavailable", user_id=int(user_id), error=str(exc))
        return None


# ---------------------------------------------------------------------------
# `/dashboard/progressao` (rotas v2) - MESMA regra da home e do drill-down.
# Nenhuma regra nova aqui: so `classify_exercise_progression` (veredito) e
# `compare_exposures` (seta por sessao) aplicados as exposicoes montadas acima.
# ---------------------------------------------------------------------------


def progression_verdict(exposures: list[dict[str, Any]]) -> dict[str, Any]:
    """Veredito da tela de progressao = o `status` do drill-down
    (`build_exercise_progress`) para as mesmas exposicoes: a mesma chamada
    `classify_exercise_progression(to_summaries(...))`.

    Junto vai o contexto da comparacao (equipamento/convencao da ultima
    exposicao, que e a chave do run comparavel) e quantas exposicoes do run
    foram consideradas - a tela diz em que equipamento comparou."""
    summaries = to_summaries(exposures)
    status, reasons = classify_exercise_progression(summaries)
    ultima = exposures[-1] if exposures else None
    return {
        "status": status.value,
        "status_reasons": reasons,
        "comparable_exposures": len(trailing_comparable_run(summaries)),
        "exposures_in_window": len(exposures),
        # Contexto de fadiga da ultima exposicao: o status E deste contexto
        # ("Progresso · pre-fatigado"), rotulo do dono.
        "fatigue_context": ultima.get("fatigue_context") if ultima else None,
        "comparability_key": ultima["comparability_key"] if ultima else None,
        "equipment_name": ultima["equipment_name"] if ultima else None,
        "load_convention": ultima["load_convention"] if ultima else None,
        "load_convention_source": ultima["load_convention_source"] if ultima else None,
        "last_local_date": ultima["local_date"] if ultima else None,
    }


def consecutive_comparisons(exposures: list[dict[str, Any]]) -> list[Optional[dict[str, Any]]]:
    """Seta "Evolucao" de cada exposicao contra a IMEDIATAMENTE anterior.

    Paralela a `exposures` (mesma ordem cronologica):
    - primeira exposicao lida -> `None` (nao ha contra o que comparar);
    - anterior com outra chave FISICA (equipamento/convencao) ->
      `NOT_COMPARABLE` com as mesmas razoes de `classify_exercise_progression`;
    - mesma chave fisica: compara com a exposicao anterior do MESMO contexto
      de fadiga dentro da sequencia (as de outro contexto sao puladas, como
      em `trailing_comparable_indices`) -> `compare_exposures`:
      `PROGRESSING`/`REGRESSING`, ou `STABLE`; sem anterior no contexto ->
      `NOT_COMPARABLE` com `first_exposure_in_fatigue_context`.

    A troca de maquina continua sendo lida contra a IMEDIATAMENTE anterior
    (o run comparavel do §8.1): a seta da ultima sessao e sempre a ultima
    transicao que o veredito avaliou."""
    summaries = to_summaries(exposures)
    resultado: list[Optional[dict[str, Any]]] = []
    for index, current in enumerate(summaries):
        if index == 0:
            resultado.append(None)
            continue
        if not physical_keys_match(summaries[index - 1].key, current.key):
            reasons = [REASON_KEY_CHANGED]
            if current.key.equipment_id is None and not current.key.equipment_agnostic:
                reasons.append(REASON_EQUIPMENT_UNKNOWN)
            resultado.append({"status": ExerciseProgressionStatus.NOT_COMPARABLE.value, "reasons": reasons})
            continue
        indices, _skipped = trailing_comparable_indices(summaries[: index + 1])
        if len(indices) < 2:
            resultado.append(
                {"status": ExerciseProgressionStatus.NOT_COMPARABLE.value, "reasons": [REASON_FIRST_IN_CONTEXT]}
            )
            continue
        previous = summaries[indices[-2]]
        status, reasons = compare_exposures(previous, current)
        resultado.append(
            {
                "status": (status or ExerciseProgressionStatus.STABLE).value,
                "reasons": reasons,
            }
        )
    return resultado


# ---------------------------------------------------------------------------
# Leituras auxiliares da MESMA regra (M74 e alinhamento das telas). Nenhuma
# regra nova: so expoem, para quem monta tela/alerta/contexto de IA, os
# mesmos numeros que `comparability` ja usa para decidir.
# ---------------------------------------------------------------------------


def e1rm_reliable(exposure: Mapping[str, Any]) -> bool:
    """O e1RM desta exposicao e confiavel? Mesmo teto de `comparability`
    (`e1rm_max_reliable_reps`): a serie que gerou o e1RM tem ate 12 reps.
    `False` quando nao ha e1RM ou nao se sabe de que serie ele veio."""
    reps = exposure.get("_e1rm_reps", exposure.get("e1rm_reps"))
    if exposure.get("e1rm") is None or reps is None:
        return False
    return e1rm.is_reliable_reps(reps)


def reliable_e1rm(exposure: Mapping[str, Any]) -> Optional[float]:
    """O e1RM da exposicao so quando confiavel (`e1rm_reliable`), senao `None`."""
    return exposure.get("e1rm") if e1rm_reliable(exposure) else None


def comparable_run_exposures(exposures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """As exposicoes (dicts montados por `assemble_exposures`) do run
    comparavel final - as MESMAS que `trailing_comparable_run` considera para
    o veredito, na mesma ordem cronologica."""
    if not exposures:
        return []
    indices, _skipped = trailing_comparable_indices(to_summaries(exposures))
    return [exposures[index] for index in indices]


def paired_top_reps(previous: Mapping[str, Any], current: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """Reps POR SERIE emparelhada nas cargas de topo - exatamente o que
    `compare_exposures` compara (`comparability.paired_top_reps`, N = menor numero de series
    de topo dos dois lados). `None` quando um dos lados nao tem serie de topo
    com reps, ou as chaves nao sao comparaveis.

    Devolve `{pairs, previous, current, previous_sum, current_sum, delta,
    effort_adjusted, previous_reps, current_reps}`: `previous`/`current` sao
    os VALORES comparados das N series emparelhadas (maior para menor) -
    `reps + RIR` quando `effort_adjusted` (M78), senao as reps cruas;
    `previous_reps`/`current_reps` sao as reps cruas das mesmas series. A
    tela/IA so exibem; nenhuma conta fora de `comparability`."""
    if not keys_match(previous.get("_key"), current.get("_key")):
        return None
    prev_summary, curr_summary = to_summaries([dict(previous), dict(current)])
    tolerance = float(THRESHOLDS["same_load_tolerance_pct"])
    paired = _paired_top_reps(prev_summary, curr_summary, tolerance)
    if paired is None:
        return None
    return {
        "pairs": paired.pairs,
        "previous": list(paired.previous),
        "current": list(paired.current),
        "previous_sum": paired.previous_sum,
        "current_sum": paired.current_sum,
        "delta": paired.delta,
        "effort_adjusted": paired.effort_adjusted,
        "previous_reps": list(paired.previous_reps),
        "current_reps": list(paired.current_reps),
    }


def latest_comparison(exposures: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """O par "anterior vs atual" (as duas ultimas exposicoes da sequencia
    comparavel - mesmo contexto de fadiga -, as mesmas que
    `classify_exercise_progression` compara primeiro) com o que a regra usou:
    reps emparelhadas nas series de topo e se o e1RM foi decisivo (series do
    e1RM com ate 12 reps nos DOIS lados). `None` com menos de 2 exposicoes."""
    if len(exposures) < 2:
        return None
    previous, current = exposures[-2], exposures[-1]
    if physical_keys_match(previous.get("_key"), current.get("_key")):
        run = comparable_run_exposures(exposures)
        if len(run) >= 2:
            previous = run[-2]
    comparable = keys_match(previous.get("_key"), current.get("_key"))
    return {
        "comparable": comparable,
        "paired_reps": paired_top_reps(previous, current) if comparable else None,
        "e1rm_decisive": comparable and e1rm_reliable(previous) and e1rm_reliable(current),
        "e1rm_max_reliable_reps": int(THRESHOLDS["e1rm_max_reliable_reps"]),
    }


def exposure_snapshot(exposure: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    """Resumo publico de UMA exposicao para contexto de IA/alerta: data, carga
    de topo, reps das series de topo, e1RM e se ele e confiavel."""
    if not exposure:
        return None
    summary = to_summaries([dict(exposure)])[0]
    return {
        "local_date": exposure.get("local_date"),
        "best_load_kg": exposure.get("best_load_kg"),
        "top_set_reps": top_load_reps(summary, float(THRESHOLDS["same_load_tolerance_pct"])),
        "working_sets": len(exposure.get("sets") or ()),
        "avg_rpe": exposure.get("avg_rpe"),
        "e1rm": exposure.get("e1rm"),
        "e1rm_reps": exposure.get("_e1rm_reps"),
        "e1rm_reliable": e1rm_reliable(exposure),
    }


def _fetch_exercise(conn: Any, exercise_id: int) -> Optional[dict[str, Any]]:
    with conn.cursor() as cursor:
        cursor.execute(EXERCISE_SQL, (int(exercise_id),))
        return cursor.fetchone()


def _fetch_progression_decisions(
    conn: Any, exercise_id: int, *, user_id: int, training_goal: Any = None
) -> list[dict[str, Any]]:
    """Evidencia secundaria (§8.2), so das trilhas do objetivo (M97 F1;
    ausente = default). Nunca substitui a regra - se a tabela nem existir
    (banco anterior a 036), a resposta segue sem ela."""
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                PROGRESSION_DECISIONS_SQL,
                (int(user_id), int(exercise_id), goal_value(training_goal), MAX_PROGRESSION_DECISIONS),
            )
            rows = cursor.fetchall() or []
    except Exception as exc:  # noqa: BLE001 - evidencia opcional nunca derruba a resposta
        log_event("dashboard.progression_decisions_unavailable", exercise_id=int(exercise_id), error=str(exc))
        return []
    return [{key: _jsonable(value) for key, value in row.items()} for row in rows]


def _data_quality(
    exposures: list[dict[str, Any]], status: ExerciseProgressionStatus
) -> tuple[DataQuality, list[str]]:
    """Qualidade e limitacoes da resposta (§6 linha `exercise_progression_status`
    e §8.1)."""
    limitations: list[str] = []
    if not exposures:
        return DataQuality.INSUFFICIENT, ["Nenhuma exposicao com serie de trabalho na janela."]

    if status is ExerciseProgressionStatus.INSUFFICIENT_DATA:
        quality = DataQuality.INSUFFICIENT
    elif len(exposures) >= 3:
        quality = DataQuality.HIGH
    else:
        quality = DataQuality.MEDIUM

    key = exposures[-1]["_key"]
    if key.equipment_agnostic:
        quality = DataQuality.MEDIUM if quality is DataQuality.HIGH else quality
        limitations.append(
            "Nenhuma exposicao tem equipamento resolvido: a comparacao ignora o equipamento (plano §8.1)."
        )
    if any(item["load_convention_source"] == "default_total" for item in exposures):
        limitations.append(
            "Ha par equipamento/exercicio sem convencao de carga cadastrada; assumida 'Carga total' (D12)."
        )
    if any(item.get("fatigue_context") is None for item in exposures):
        limitations.append(
            "Contexto de fadiga previa nao medido: a comparacao nao separou as exposicoes pela ordem na sessao."
        )
    if any(item["e1rm"] is None for item in exposures):
        limitations.append("e1RM ausente em alguma exposicao (serie sem RPE ou convencao sem carga levantada).")
    if status is ExerciseProgressionStatus.PLATEAU_SUSPECTED:
        limitations.append("Suspeita de plato nao e diagnostico: e ausencia de progresso nas ultimas exposicoes.")
    return quality, limitations


def build_exercise_progress(
    conn: Any,
    exercise_id: int,
    *,
    user_id: Any,
    window: WindowInfo,
    limit: int = MAX_EXPOSURES,
    training_goal: Any = None,
) -> Optional[dict[str, Any]]:
    """Resposta completa de `GET /analytics/dashboard/exercises/{id}/progress`,
    no objetivo `training_goal` (M97 F1; ausente = default).

    `None` quando o exercicio nao existe no catalogo canonico (o endpoint
    converte em 404). Exercicio existente SEM historico do usuario devolve
    payload valido com `status=INSUFFICIENT_DATA` e `exposures: []` - nunca
    404 nem zeros inventados."""
    owner = require_user_id(user_id)
    exercise = _fetch_exercise(conn, exercise_id)
    if not exercise:
        return None

    canonical = int(exercise["exercise_id"])
    exposures = get_exercise_exposures(
        conn, canonical, user_id=owner, window=window, limit=limit, training_goal=training_goal
    )
    status, reasons = classify_exercise_progression(to_summaries(exposures))
    quality, limitations = _data_quality(exposures, status)

    current_key: Optional[ComparabilityKey] = exposures[-1]["_key"] if exposures else None
    # M97 F4: recorde e linha de e1RM UNICOS (todos os objetivos, sem deload).
    every_goal = _all_goal_exposures(conn, canonical, owner, window, limit)
    records = records_all_goals(every_goal, exposures, current_key)
    reference_key = _physical_reference_key(every_goal, exposures, current_key)

    log_event(
        "dashboard.computed",
        block="exercise_progress",
        user_id=owner,
        exercise_id=canonical,
        exposures=len(exposures),
        status=status.value,
        window=window.kind.value,
    )

    return {
        "window": window,
        "exercise": {
            "exercise_id": canonical,
            "name": exercise.get("exercise_name"),
            "amazfit_name": exercise.get("exercise_amazfit_name"),
        },
        "comparability_key": current_key.to_dict() if current_key else None,
        "status": status.value,
        "status_reasons": reasons,
        # O status e do contexto de fadiga da exposicao mais recente.
        "fatigue_context": exposures[-1].get("fatigue_context") if exposures else None,
        # Comparavel com a chave atual e, se nao, por que (M76 Lote 3c, C13):
        # o FE nao reimplementa `keys_match`.
        "exposures": [
            {**_public_exposure(item), **comparison_with_current(item["_key"], current_key)} for item in exposures
        ],
        # Par "anterior vs atual" com os numeros que a regra usou (reps
        # emparelhadas nas series de topo, e1RM decisivo ou nao) - a tela nao
        # recalcula nada disso.
        "latest_comparison": latest_comparison(exposures),
        "records": records,
        "training_goal": goal_value(training_goal),
        "e1rm_series": e1rm_series(every_goal, reference_key),
        "deload_sessions": deload_sessions_between(
            conn, canonical, user_id=owner, date_from=window.date_from, date_to=window.date_to
        ),
        "progression_decisions": _fetch_progression_decisions(
            conn, canonical, user_id=owner, training_goal=training_goal
        ),
        "data_quality": quality,
        "limitations": limitations,
        "metric_versions": {
            "exposure": metric_version("exposure"),
            "e1rm": metric_version("e1rm"),
            "exercise_progression_status": metric_version("exercise_progression_status"),
            "tonnage": metric_version("tonnage"),
        },
    }


def _public_exposure(item: Mapping[str, Any]) -> dict[str, Any]:
    """Remove os campos internos (`_key`, `_e1rm_reps`, ...) da resposta e
    publica de que serie veio o e1RM (`e1rm_reps`) e se ele e confiavel
    (`e1rm_reliable`, teto de reps da regra)."""
    public = {key: value for key, value in item.items() if not key.startswith("_")}
    public["e1rm_reps"] = item.get("_e1rm_reps")
    public["e1rm_reliable"] = e1rm_reliable(item)
    return public


def _all_goal_exposures(
    conn: Any, canonical: int, owner: int, window: WindowInfo, limit: int
) -> list[dict[str, Any]]:
    """Exposicoes de TODOS os objetivos na janela/teto do drill-down, sem
    deload e sem contexto de fadiga (M97 F4): base do recorde e da linha unica
    de e1RM, que sao unicos entre objetivos (chave fisica, decisao do
    usuario)."""
    return get_exposures_between(
        conn,
        canonical,
        user_id=owner,
        date_from=window.date_from,
        date_to=window.date_to,
        limit=max(1, min(int(limit), MAX_EXPOSURES)),
        all_goals=True,
        with_fatigue_context=False,
    )


def _physical_reference_key(
    every_goal: list[dict[str, Any]],
    goal_exposures: list[dict[str, Any]],
    current_key: Optional[ComparabilityKey],
) -> Optional[ComparabilityKey]:
    """Chave do recorde/linha de e1RM: a da exposicao mais recente do objetivo
    pedido, relida na leitura de todos os objetivos (a excecao "todas sem
    equipamento" do §8.1 e decidida sobre o conjunto lido); sem ela, a mais
    recente de qualquer objetivo."""
    if not every_goal:
        return current_key
    latest = goal_exposures[-1] if goal_exposures else None
    same = [
        item
        for item in every_goal
        if latest is not None
        and item["activity_id"] == latest["activity_id"]
        and item["position_in_session"] == latest["position_in_session"]
    ]
    return same[-1]["_key"] if same else (current_key or every_goal[-1]["_key"])


# e1RM ATUAL de um par (fix M97, 2026-10-26; mapa §66.5): base da carga de
# FORCA por %1RM (`progression_engine.e1rm_load_range`). Unico entre objetivos
# e sem deload (a mesma leitura do recorde, `all_goals=True`): o e1RM
# CONFIAVEL (`domain/e1rm`, ate 12 reps com RPE) da exposicao MAIS RECENTE do
# exercicio no MESMO equipamento que tenha um, dentro de
# `CURRENT_E1RM_LOOKBACK_DAYS` dias. Valor a calibrar pelo usuario.
CURRENT_E1RM_LOOKBACK_DAYS = 90


def current_e1rm_from_exposures(exposures: list[dict[str, Any]], equipment_id: Optional[int]) -> Optional[float]:
    """e1RM confiavel da exposicao mais recente (lista em ordem cronologica,
    como `assemble_exposures` devolve) no equipamento pedido. None sem
    equipamento ou sem exposicao com e1RM confiavel."""
    if equipment_id is None:
        return None
    for exposure in reversed(exposures):
        if exposure.get("equipment_id") is None or int(exposure["equipment_id"]) != int(equipment_id):
            continue
        value = exposure.get("_reliable_e1rm")
        if value is not None:
            return float(value)
    return None


def current_e1rm_by_pair(
    conn: Any,
    user_id: Any,
    pairs: Any,
    *,
    today: date,
) -> dict[tuple[int, int], float]:
    """`(exercicio canonico, equipment.id)` -> e1RM atual, para os pares pedidos
    (uma leitura de exposicoes por exercicio). Par sem e1RM confiavel na
    janela fica fora do dict (a progressao dupla decide a carga)."""
    from datetime import timedelta

    wanted: dict[int, set[int]] = {}
    for exercise_id, equipment_id in pairs or ():
        if exercise_id is None or equipment_id is None:
            continue
        wanted.setdefault(int(exercise_id), set()).add(int(equipment_id))
    result: dict[tuple[int, int], float] = {}
    for exercise_id in sorted(wanted):
        exposures = get_exposures_between(
            conn,
            exercise_id,
            user_id=user_id,
            date_from=today - timedelta(days=CURRENT_E1RM_LOOKBACK_DAYS),
            date_to=today,
            limit=MAX_EXPOSURES,
            all_goals=True,
            with_fatigue_context=False,
        )
        for equipment_id in sorted(wanted[exercise_id]):
            value = current_e1rm_from_exposures(exposures, equipment_id)
            if value is not None:
                result[(exercise_id, equipment_id)] = value
    return result


def records_all_goals(
    every_goal: list[dict[str, Any]],
    goal_exposures: list[dict[str, Any]],
    current_key: Optional[ComparabilityKey],
) -> dict[str, Any]:
    """Bloco `records` do drill-down (pendencia da F1, M97 F4): recorde UNICO
    entre objetivos, sem deload. Com um objetivo so no historico, as duas
    leituras trazem as mesmas linhas e o bloco e o de antes."""
    if not every_goal:
        return _records(goal_exposures, current_key)
    return _records(every_goal, _physical_reference_key(every_goal, goal_exposures, current_key))


def e1rm_series(every_goal: list[dict[str, Any]], key: Optional[ComparabilityKey]) -> list[dict[str, Any]]:
    """Linha UNICA de e1RM do exercicio (M97 F4): um ponto por exposicao de
    QUALQUER objetivo, sem deload, com o objetivo da execucao (a tela destaca
    as de forca, sem regra numerica) e se o ponto e da mesma chave fisica do
    recorde. e1RM e confiabilidade sao os da exposicao (`domain/e1rm`)."""
    points: list[dict[str, Any]] = []
    for item in every_goal:
        if item.get("e1rm") is None:
            continue
        points.append(
            {
                "activity_id": item["activity_id"],
                "local_date": item["local_date"],
                "e1rm": item["e1rm"],
                "e1rm_reps": item.get("_e1rm_reps"),
                "e1rm_reliable": e1rm_reliable(item),
                "training_goal": item.get("_training_goal") or goal_value(None),
                "same_physical_key": key is not None and item["_key"].physical == key.physical,
            }
        )
    return points


# Sessoes de DELOAD do exercicio na janela (M97 F4): ficam fora das
# exposicoes (comparabilidade, e1RM, recorde) e entram nos graficos so como
# marca. Predicado do dono (`sql_is_deload`).
DELOAD_SESSIONS_SQL = f"""
SELECT a.id AS activity_id, a.local_date AS local_date, a.deload_source AS deload_source
FROM activities a
WHERE a.user_id = %s
  AND {sql_is_deload("a")}
  AND a.local_date BETWEEN %s AND %s
  AND EXISTS (
    SELECT 1
    FROM exercises e
    LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
    WHERE e.activity_id = a.id AND {_CANONICAL} = %s
  )
ORDER BY a.local_date ASC, a.id ASC
LIMIT %s
"""


def deload_sessions_between(
    conn: Any, exercise_id: int, *, user_id: Any, date_from: date, date_to: date, limit: int = MAX_EXPOSURES
) -> list[dict[str, Any]]:
    """Sessoes de deload do exercicio em `[date_from, date_to]` (so a marca:
    data e origem do dono `deload`)."""
    owner = require_user_id(user_id)
    with conn.cursor() as cursor:
        cursor.execute(DELOAD_SESSIONS_SQL, (owner, date_from, date_to, int(exercise_id), max(1, int(limit))))
        rows = cursor.fetchall() or []
    return [
        {
            "activity_id": parse_int(row.get("activity_id")),
            "local_date": _jsonable(row.get("local_date")),
            "deload_source": deload_source_value(row.get("deload_source")),
            "deload_label": deload_source_label(row.get("deload_source")),
        }
        for row in rows
        if deload_source_value(row.get("deload_source")) is not None
    ]


def _records(exposures: list[dict[str, Any]], key: Optional[ComparabilityKey]) -> dict[str, Any]:
    """Melhor e1RM e melhor carga DENTRO DA MESMA CHAVE (§8.1): recorde de
    outra maquina nao e recorde desta.

    O recorde de e1RM so considera e1RM CONFIAVEL (series com ate
    `E1RM_MAX_RELIABLE_REPS` reps, M76 Lote 3a): uma serie de 20 reps nao vira
    "melhor 1RM estimado" - o mesmo teto da regra de progressao.

    M76 Lote 3d (decisao de tela de 2026-10-07): quando a chave NAO tem
    nenhum e1RM confiavel, `best_e1rm` traz o maior e1RM das series com mais
    de 12 reps (`_unreliable_e1rm`, ainda exigindo RPE) com
    `reliable = False` - a tela marca "nao confiavel (>12 reps)", com o teto
    em `max_reliable_reps` (o dono, `domain/e1rm`; a tela nao escreve o 12).
    Havendo confiavel, so ele (`reliable = True`)."""
    vazio = {"best_e1rm": None, "best_load_kg": None}
    if key is None:
        return vazio

    # Recorde e ABSOLUTO no contexto de fadiga (decisao do usuario,
    # 2026-10-07): compara so a chave FISICA - o melhor supino e o melhor
    # supino, descansado ou nao.
    mesmos = [item for item in exposures if item["_key"].physical == key.physical]
    if not mesmos:
        return vazio

    com_e1rm = [item for item in mesmos if item.get("_reliable_e1rm") is not None]
    com_carga = [item for item in mesmos if item["best_load_kg"] is not None]
    melhor_carga = None
    if com_carga:
        # `assisted`: menos assistencia e melhor.
        reverso = key.load_convention is LoadConvention.ASSISTED
        melhor_carga = (min if reverso else max)(com_carga, key=lambda item: item["best_load_kg"])

    def _record(item: Optional[dict[str, Any]], field: str) -> Optional[dict[str, Any]]:
        if item is None:
            return None
        return {
            "value": item[field],
            "activity_id": item["activity_id"],
            "local_date": item["local_date"],
        }

    if com_e1rm:
        melhor_e1rm = _record(max(com_e1rm, key=lambda item: item["_reliable_e1rm"]), "_reliable_e1rm")
        melhor_e1rm["reliable"] = True
        melhor_e1rm["max_reliable_reps"] = e1rm.E1RM_MAX_RELIABLE_REPS
    else:
        nao_confiaveis = [item for item in mesmos if item.get("_unreliable_e1rm") is not None]
        melhor_e1rm = (
            _record(max(nao_confiaveis, key=lambda item: item["_unreliable_e1rm"]), "_unreliable_e1rm")
            if nao_confiaveis
            else None
        )
        if melhor_e1rm is not None:
            melhor_e1rm["reliable"] = False
            melhor_e1rm["max_reliable_reps"] = e1rm.E1RM_MAX_RELIABLE_REPS
    return {
        "best_e1rm": melhor_e1rm,
        "best_load_kg": _record(melhor_carga, "best_load_kg"),
    }


# ---------------------------------------------------------------------------
# F1.6 - caminho em LOTE: status de TODOS os exercicios com historico na
# janela, numa unica consulta (o bloco "Progressao" da visao geral).
#
# Por que nao chamar `get_exercise_exposures` N vezes: seria N+1 (uma
# consulta por exercicio, dezenas por requisicao) e a visao geral precisa
# caber em 3 s no shim da Data API. Aqui `ROW_NUMBER() OVER (PARTITION BY
# exercicio canonico)` recorta as ultimas `per_exercise_limit` exposicoes de
# CADA exercicio no proprio SQL; a agregacao e a classificacao reusam
# EXATAMENTE as mesmas funcoes do caminho individual (`build_exposures`,
# `assemble_exposures`, `to_summaries`, `classify_exercise_progression`) -
# nao existe uma segunda classificacao.
#
# Teto de 1 MB (§15): o cap e por exercicio (`MAX_BATCH_EXPOSURES_PER_EXERCISE`)
# E global (`MAX_BATCH_EXPOSURES`, pelas mais recentes); a resposta declara
# `truncated` quando o cap global cortou algum exercicio.
# ---------------------------------------------------------------------------

# Exposicoes por exercicio no lote. 8 cobre o insight de plato (>= 4
# comparaveis) com folga e mantem o lote pequeno; o drill-down por exercicio
# continua lendo ate `MAX_EXPOSURES`.
MAX_BATCH_EXPOSURES_PER_EXERCISE = 8

# Teto global de exposicoes por lote (pelas mais recentes).
MAX_BATCH_EXPOSURES = 600

BATCH_EXPOSURES_SQL_TEMPLATE = f"""
SELECT
  sel.canonical_exercise_id AS canonical_exercise_id,
  sel.exercise_name         AS exercise_name,
  sel.exercise_row_id,
  a.id                      AS activity_id,
  a.local_date              AS local_date,
  a.started_at              AS started_at,
  e.sequence                AS position_in_session,
  e.training_goal           AS training_goal,
  ge.equipment_id           AS equipment_id,
  NULL                      AS equipment_name,
  {{load_convention}}       AS load_convention,
  wpe.reps_target           AS prescribed_reps_target,
  es.set_number             AS set_number,
  es.reps                   AS reps,
  es.weight_kg              AS weight_kg,
  es.rpe                    AS rpe,
  es.measure                AS measure,
  es.duration_seconds       AS duration_seconds,
  NULL                      AS rest_seconds,
  NULL                      AS notes
FROM (
  SELECT ranked.exercise_row_id, ranked.canonical_exercise_id, ranked.exercise_name, ranked.occurred_at
  FROM (
    SELECT
      e2.id AS exercise_row_id,
      COALESCE(elm2.canonical_exercise_id, e2.mapping_id) AS canonical_exercise_id,
      ec.name AS exercise_name,
      {_EXPOSURE_ORDER.format(alias="a2")} AS occurred_at,
      ROW_NUMBER() OVER (
        PARTITION BY COALESCE(elm2.canonical_exercise_id, e2.mapping_id)
        ORDER BY {_EXPOSURE_ORDER.format(alias="a2")} DESC, a2.id DESC, e2.sequence DESC, e2.id DESC
      ) AS rn
    FROM activities a2
    JOIN exercises e2 ON e2.activity_id = a2.id
    LEFT JOIN exercise_legacy_id_map elm2 ON elm2.old_exercise_id = e2.mapping_id
    JOIN exercise_catalog ec ON ec.id = COALESCE(elm2.canonical_exercise_id, e2.mapping_id)
    WHERE a2.user_id = %s
      AND {sql_goal_is("e2")}
      AND {sql_not_deload("a2")}
      AND a2.local_date BETWEEN %s AND %s
      AND EXISTS (
        SELECT 1 FROM exercise_sets es2
        WHERE es2.exercise_id = e2.id AND {sql_is_working("es2.set_type")} AND {sql_set_confirmed("es2.reps_pending")}
      )
  ) ranked
  WHERE ranked.rn <= %s
  ORDER BY ranked.occurred_at DESC, ranked.exercise_row_id DESC
  LIMIT %s
) sel
JOIN exercises e ON e.id = sel.exercise_row_id
JOIN activities a ON a.id = e.activity_id
LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
LEFT JOIN gym_equipment ge ON ge.id = e.equipment_id
LEFT JOIN equipment_exercises eqex
  ON eqex.equipment_id = ge.equipment_id
 AND eqex.exercise_id = {_CANONICAL}
 AND eqex.is_active = 1
LEFT JOIN workout_plan_exercises wpe ON wpe.id = e.workout_plan_exercise_id
JOIN exercise_sets es ON es.exercise_id = e.id AND {_ES_WORKING} AND {sql_set_confirmed('es.reps_pending')}
ORDER BY sel.canonical_exercise_id ASC, sel.occurred_at ASC, a.id ASC, e.sequence ASC, e.id ASC, es.set_number ASC, es.id ASC
"""


def split_batch_rows(rows) -> tuple[dict[int, list[dict[str, Any]]], dict[int, Optional[str]]]:
    """Linhas planas do lote -> `{exercicio canonico: linhas daquele exercicio}`
    (ordem preservada) + nomes. Funcao pura; e o unico ponto que conhece a
    coluna `canonical_exercise_id` do lote."""
    por_exercicio: dict[int, list[dict[str, Any]]] = {}
    nomes: dict[int, Optional[str]] = {}
    for row in rows or []:
        canonical = row.get("canonical_exercise_id")
        if canonical is None:
            continue
        canonical = int(canonical)
        por_exercicio.setdefault(canonical, []).append(row)
        nomes.setdefault(canonical, row.get("exercise_name"))
    return por_exercicio, nomes


def classify_batch(
    rows,
    *,
    per_exercise_limit: int,
    fatigue_by_row: Optional[Mapping[int, Optional[PriorFatigue]]] = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Classifica CADA exercicio do lote com `classify_exercise_progression`
    sobre as exposicoes montadas por `build_exposures`/`assemble_exposures` -
    o mesmo caminho do endpoint individual, exercicio a exercicio.

    Devolve `(itens, truncated)`. `truncated` fica `True` quando o teto global
    cortou exposicoes: algum exercicio ficou com menos exposicoes do que teria
    com `per_exercise_limit` - o bloco declara o corte em vez de classificar
    como se o historico fosse mais curto."""
    por_exercicio, nomes = split_batch_rows(rows)
    itens: list[dict[str, Any]] = []
    total_exposicoes = 0
    for canonical, linhas in por_exercicio.items():
        exposures = assemble_exposures(build_exposures(linhas), canonical, fatigue_by_row)
        total_exposicoes += len(exposures)
        summaries = to_summaries(exposures)
        status, reasons = classify_exercise_progression(summaries)
        run = trailing_comparable_run(summaries)
        itens.append(
            {
                "exercise_id": canonical,
                "name": nomes.get(canonical),
                "status": status.value,
                "status_reasons": reasons,
                "fatigue_context": exposures[-1].get("fatigue_context") if exposures else None,
                "comparable_exposures": len(run),
                "exposures_in_window": len(exposures),
                "last_local_date": exposures[-1]["local_date"] if exposures else None,
                "_exposures": exposures,
                # As exposicoes do run comparavel (mesmo contexto de fadiga; as
                # de outro contexto intercaladas ficam de fora): consumidores
                # do lote leem daqui, nunca "as ultimas N".
                "_comparable_run": [exposures[index] for index in trailing_comparable_indices(summaries)[0]],
            }
        )
    truncated = total_exposicoes >= MAX_BATCH_EXPOSURES
    return itens, truncated


def get_exercises_progression_batch(
    conn: Any,
    *,
    user_id: Any,
    window: WindowInfo,
    per_exercise_limit: int = MAX_BATCH_EXPOSURES_PER_EXERCISE,
    group_names: Optional[Mapping[int, str]] = None,
    training_goal: Any = None,
) -> tuple[list[dict[str, Any]], bool, int]:
    """Status de progressao de TODOS os exercicios com serie de trabalho na
    janela, em UMA consulta, no objetivo `training_goal` (M97 F1; ausente =
    default). Devolve `(itens, truncated, per_exercise_limit)`."""
    owner = require_user_id(user_id)
    capped = max(1, min(int(per_exercise_limit), MAX_EXPOSURES))

    with conn.cursor() as cursor:
        sql = BATCH_EXPOSURES_SQL_TEMPLATE.format(load_convention=load_convention_select_expr(cursor))
        cursor.execute(
            sql, (owner, goal_value(training_goal), window.date_from, window.date_to, capped, MAX_BATCH_EXPOSURES)
        )
        rows = cursor.fetchall() or []

    # Contexto de fadiga de TODAS as exposicoes do lote numa leitura so (as
    # sessoes envolvidas), nunca por exercicio.
    # `group_names`: nomes ja lidos pelo chamador (visao geral), sem reler
    # `muscle_groups`.
    fatigue_by_row = load_fatigue_contexts(conn, owner, list(rows), group_names)
    itens, truncated = classify_batch(rows, per_exercise_limit=capped, fatigue_by_row=fatigue_by_row)
    return itens, truncated, capped


__all__ = [
    "BATCH_EXPOSURES_SQL_TEMPLATE",
    "EXPOSURES_SQL_TEMPLATE",
    "MAX_BATCH_EXPOSURES",
    "MAX_BATCH_EXPOSURES_PER_EXERCISE",
    "MAX_EXPOSURES",
    "MAX_TABLE_EXPOSURES",
    "assemble_exposures",
    "build_exercise_progress",
    "build_exposures",
    "classify_batch",
    "comparable_run_exposures",
    "consecutive_comparisons",
    "e1rm_reliable",
    "exposure_snapshot",
    "get_exercise_exposures",
    "get_exposures_between",
    "get_exercises_progression_batch",
    "latest_comparison",
    "paired_top_reps",
    "progression_verdict",
    "reliable_e1rm",
    "split_batch_rows",
    "to_summaries",
]
