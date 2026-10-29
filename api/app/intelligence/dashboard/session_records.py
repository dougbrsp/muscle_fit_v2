"""Recordes e progressoes POR SESSAO de `/dashboard/treinos` - a MESMA regra
do drill-down (`/dashboard/exercicios/[id]`) e da tela de progressao.

Antes (session-records-v1) a lista de treinos tinha uma regra propria: maior
`weight_kg` por exercicio canonico, sem equipamento nem convencao de carga.
Trocar de maquina virava "recorde", `assisted` contava ao contrario (mais
assistencia = "recorde") e mais carga com menos reps virava "progressao". Este
modulo e o consumidor fino da fonte unica:

- **Chave**: `comparability.build_key`/`keys_match` - exercicio canonico +
  equipamento + `load_convention`. A excecao do §8.1 (nenhuma exposicao com
  equipamento resolvido -> chave sem equipamento) e decidida sobre o
  HISTORICO INTEIRO do exercicio, igual a `assemble_exposures`.
- **Progressao**: `compare_exposures(anterior, atual)` devolveu `PROGRESSING`,
  onde "anterior" e a exposicao imediatamente anterior DENTRO DA MESMA CHAVE
  (a troca de maquina no meio nao e comparada com nada). Os agregados
  (melhor carga, e1RM Epley+RIR, series por serie) saem de
  `exposure_service.build_exposures`/`assemble_exposures`/`to_summaries` -
  nada e recalculado aqui.
- **Recorde**: mesma semantica de `exposure_service._records` - melhor carga
  DENTRO DA CHAVE, direcional (`assisted`: MENOS assistencia e melhor). Recorde
  = superou ESTRITAMENTE todas as exposicoes anteriores da mesma chave; a
  primeira exposicao numa chave nao e recorde (nao ha marca a bater).
  `bodyweight` nao tem recorde de CARGA: a carga movida nao esta na serie
  (`compares_reps_only`).

Desempenho (§15): duas consultas, nenhuma por sessao.

1. `HISTORY_SQL` - UMA linha por exposicao (linha de `exercises`) dos
   exercicios da pagina, historico inteiro ate a data da pagina, ja agregada
   (max/min de carga). Mesma cardinalidade da consulta v1.
2. `SETS_SQL` - as series de trabalho SO das exposicoes que serao comparadas
   (as da pagina + a anterior de mesma chave de cada uma): no maximo 2x as
   exposicoes da pagina.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Optional, Sequence

from ...domain.deload import sql_not_deload
from ...domain.set_measures import sql_is_rep_based
from ...domain.training_history import parse_int
from ...load_conventions import compares_reps_only, inverts_load_direction
from ...set_types import sql_is_working, sql_set_confirmed
from .coercion import to_float
from .comparability import (
    ComparabilityKey,
    all_equipment_missing,
    build_key,
    compare_exposures,
    keys_match,
    normalize_load_convention,
)
from .exposure_service import assemble_exposures, build_exposures, to_summaries
from .metrics_registry import ExerciseProgressionStatus

_CANONICAL = "COALESCE(elm.canonical_exercise_id, e.mapping_id)"
_WORKING = sql_is_working("es.set_type")
# Lote T3 (medidas de serie): recorde e progressao por sessao so olham
# series de REPETICOES (`set_measures.sql_is_rep_based`; NULL = reps). Uma
# prancha de 60 s ou um maximo de reps nao e marca de carga.
_REP_BASED = sql_is_rep_based("es.measure")

# Uma linha por EXPOSICAO (linha de `exercises`) com serie de trabalho, em
# ordem cronologica por exercicio. `{load_convention}` vem de
# `load_convention_select_expr` (coluna da 045 ou `NULL` = `total`); as colunas
# que dependem da exposicao vao por `MAX()` para o GROUP BY ficar so nas
# chaves (`ONLY_FULL_GROUP_BY`). `{placeholders}` = ids canonicos da pagina.
HISTORY_SQL_TEMPLATE = f"""
SELECT
  a.id                                  AS activity_id,
  e.id                                  AS exercise_row_id,
  {_CANONICAL}                          AS exercise_id,
  MAX(a.local_date)                     AS local_date,
  MAX(a.started_at)                     AS started_at,
  MAX(COALESCE(a.started_at, a.local_date)) AS occurred_at,
  MAX(e.sequence)                       AS position_in_session,
  MAX(ge.equipment_id)                  AS equipment_id,
  MAX({{load_convention}})              AS load_convention,
  MAX(wpe.reps_target)                  AS prescribed_reps_target,
  MAX(es.weight_kg)                     AS max_weight_kg,
  MIN(es.weight_kg)                     AS min_weight_kg
FROM activities a
JOIN exercises e ON e.activity_id = a.id
JOIN exercise_sets es ON es.exercise_id = e.id AND {sql_set_confirmed('es.reps_pending')}
LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
LEFT JOIN gym_equipment ge ON ge.id = e.equipment_id
LEFT JOIN equipment_exercises eqex
  ON eqex.equipment_id = ge.equipment_id
 AND eqex.exercise_id = {_CANONICAL}
 AND eqex.is_active = 1
LEFT JOIN workout_plan_exercises wpe ON wpe.id = e.workout_plan_exercise_id
WHERE a.user_id = %s
  AND a.local_date <= %s
  AND {sql_not_deload("a")}
  AND {_WORKING}
  AND {_REP_BASED}
  AND {_CANONICAL} IN ({{{{placeholders}}}})
GROUP BY a.id, e.id, {_CANONICAL}
ORDER BY {_CANONICAL} ASC, occurred_at ASC, a.id ASC, position_in_session ASC, e.id ASC
"""

# Series de trabalho das exposicoes que serao comparadas. `user_id` no WHERE
# (§15) mesmo com os ids ja vindos de uma consulta filtrada por dono.
SETS_SQL = f"""
SELECT
  es.exercise_id AS exercise_row_id,
  es.set_number  AS set_number,
  es.reps        AS reps,
  es.weight_kg   AS weight_kg,
  es.rpe         AS rpe
FROM exercise_sets es
JOIN exercises e ON e.id = es.exercise_id
JOIN activities a ON a.id = e.activity_id
WHERE a.user_id = %s
  AND {_WORKING}
  AND {_REP_BASED}
  AND {sql_set_confirmed('es.reps_pending')}
  AND es.exercise_id IN ({{placeholders}})
ORDER BY es.exercise_id ASC, es.set_number ASC, es.id ASC
"""


def history_sql(load_convention_expr: str) -> str:
    """`HISTORY_SQL_TEMPLATE` com a expressao da convencao (ainda com
    `{placeholders}` para o `IN` dos exercicios)."""
    return HISTORY_SQL_TEMPLATE.format(load_convention=load_convention_expr)


# ---------------------------------------------------------------------------
# Funcoes PURAS (testadas sem banco)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoryExposure:
    """Uma exposicao do historico, com a chave de comparabilidade ja montada
    sobre o historico INTEIRO do exercicio."""

    activity_id: int
    exercise_row_id: int
    exercise_id: int
    key: ComparabilityKey
    # Melhor carga na DIRECAO do progresso (`assisted` = -min). `None` sem
    # carga registrada ou em `bodyweight` (sem recorde de carga).
    directional_load: Optional[float]
    row: Mapping[str, Any]


def _directional_best_load(row: Mapping[str, Any], key: ComparabilityKey) -> Optional[float]:
    convention = key.load_convention
    if compares_reps_only(convention):
        return None
    if inverts_load_direction(convention):
        lowest = to_float(row.get("min_weight_kg"))
        return None if lowest is None else -lowest
    return to_float(row.get("max_weight_kg"))


def parse_history(rows: Iterable[Mapping[str, Any]]) -> list[HistoryExposure]:
    """Linhas de `HISTORY_SQL` -> exposicoes com chave, na ordem recebida
    (cronologica por exercicio).

    A excecao "sem equipamento" do §8.1 vale por EXERCICIO e olha o historico
    inteiro dele - o mesmo criterio de `assemble_exposures`."""
    validas = [
        row
        for row in rows or []
        if parse_int(row.get("exercise_id")) is not None
        and parse_int(row.get("activity_id")) is not None
        and parse_int(row.get("exercise_row_id")) is not None
    ]
    equipamentos: dict[int, list[Optional[int]]] = {}
    for row in validas:
        equipamentos.setdefault(int(parse_int(row["exercise_id"])), []).append(parse_int(row.get("equipment_id")))
    agnostico = {exercise_id: all_equipment_missing(ids) for exercise_id, ids in equipamentos.items()}

    resultado: list[HistoryExposure] = []
    for row in validas:
        exercise_id = int(parse_int(row["exercise_id"]))
        key = build_key(
            exercise_id,
            parse_int(row.get("equipment_id")),
            normalize_load_convention(row.get("load_convention")),
            equipment_agnostic=agnostico[exercise_id],
        )
        resultado.append(
            HistoryExposure(
                activity_id=int(parse_int(row["activity_id"])),
                exercise_row_id=int(parse_int(row["exercise_row_id"])),
                exercise_id=exercise_id,
                key=key,
                directional_load=_directional_best_load(row, key),
                row=row,
            )
        )
    return resultado


def _comparable(key: ComparabilityKey) -> bool:
    """A chave pode ser comparada com alguma outra? (equipamento resolvido, ou
    a excecao do §8.1)."""
    return keys_match(key, key)


@dataclass(frozen=True)
class RecordEvent:
    """Uma exposicao que BATEU recorde de carga na sua chave.

    `previous_best` = a melhor carga direcional da chave ANTES dela (a marca
    que foi superada estritamente)."""

    exposure: HistoryExposure
    previous_best: float

    @property
    def load_kg(self) -> float:
        """Carga do recorde no numero REGISTRADO (`assisted`: a menor
        assistencia, em kg positivos)."""
        return record_load_kg(self.exposure.key, self.exposure.directional_load)

    @property
    def previous_load_kg(self) -> float:
        return record_load_kg(self.exposure.key, self.previous_best)


def record_load_kg(key: ComparabilityKey, directional_load: Optional[float]) -> Optional[float]:
    """Carga direcional -> numero registrado (`assisted` guarda -min)."""
    if directional_load is None:
        return None
    return -directional_load if inverts_load_direction(key.load_convention) else directional_load


def record_events(history: Sequence[HistoryExposure]) -> list[RecordEvent]:
    """A REGRA do recorde (decisao 3 do usuario, M76) - unica para o
    dashboard, o Modo Treino (`domain/equipment_record`) e os destaques do
    relatorio (`progression_service.get_recent_records`).

    Recorde = a melhor carga direcional da exposicao superou ESTRITAMENTE
    todas as anteriores da MESMA chave (exercicio canonico + equipamento +
    `load_convention`, via `comparability`). Consequencias:

    - a primeira exposicao de uma chave nao e recorde (nao ha marca a bater);
    - empate na carga NAO e recorde, mesmo com mais repeticoes (as reps da
      serie recorde sao so informacao - `record_set_reps`);
    - chave sem equipamento resolvido nao e comparavel (fora da excecao do
      §8.1, em que NENHUMA exposicao do exercicio tem equipamento);
    - `bodyweight` nao tem recorde de carga (`directional_load` = None).

    `history` em ordem cronologica (a de `HISTORY_SQL`/`parse_history`)."""
    eventos: list[RecordEvent] = []
    melhor_por_chave: dict[ComparabilityKey, float] = {}
    for exposure in history:
        load = exposure.directional_load
        if load is None or not _comparable(exposure.key):
            continue
        melhor = melhor_por_chave.get(exposure.key)
        if melhor is not None and load > melhor:
            eventos.append(RecordEvent(exposure=exposure, previous_best=melhor))
        melhor_por_chave[exposure.key] = load if melhor is None else max(melhor, load)
    return eventos


def record_exercise_ids(history: Sequence[HistoryExposure]) -> dict[int, set[int]]:
    """`{activity_id: {exercicio canonico que bateu recorde de carga}}` -
    `record_events` agrupado por sessao."""
    result: dict[int, set[int]] = {}
    for evento in record_events(history):
        result.setdefault(evento.exposure.activity_id, set()).add(evento.exposure.exercise_id)
    return result


def key_for(
    history: Sequence[HistoryExposure],
    exercise_id: int,
    equipment_id: Optional[int],
    load_convention: Any,
) -> ComparabilityKey:
    """Chave de comparabilidade de uma exposicao NOVA (ou de um escopo
    pedido) do exercicio `exercise_id`, com a MESMA excecao do §8.1 que
    `parse_history` aplica: sem equipamento so quando nenhuma exposicao do
    historico desse exercicio tem equipamento."""
    ids = [item.row.get("equipment_id") for item in history if item.exercise_id == int(exercise_id)]
    agnostico = all_equipment_missing([parse_int(value) for value in ids] + [equipment_id])
    return build_key(
        int(exercise_id),
        equipment_id,
        normalize_load_convention(load_convention),
        equipment_agnostic=agnostico,
    )


def current_record(history: Sequence[HistoryExposure], key: ComparabilityKey) -> Optional[RecordEvent]:
    """O recorde VIGENTE da chave: o ultimo `record_events` dela (a marca
    atual a bater). `None` quando ninguem superou a primeira exposicao, a
    chave nao e comparavel ou e `bodyweight`."""
    vigente: Optional[RecordEvent] = None
    for evento in record_events(history):
        if evento.exposure.key == key:
            vigente = evento
    return vigente


@dataclass(frozen=True)
class BestLoad:
    """A MELHOR carga direcional ja registrada numa chave (M76 Lote 3d,
    decisao de tela de 2026-10-07): a marca a bater no Modo Treino quando a
    chave ainda NAO tem recorde (`current_record` = None - a 1a exposicao
    ainda detem a marca). `exposure` = a PRIMEIRA exposicao que atingiu essa
    carga (quem detem a marca; empate depois nao a toma, como no recorde)."""

    exposure: HistoryExposure

    @property
    def load_kg(self) -> Optional[float]:
        """Carga no numero REGISTRADO (`assisted`: a menor assistencia)."""
        return record_load_kg(self.exposure.key, self.exposure.directional_load)


def best_load(history: Sequence[HistoryExposure], key: ComparabilityKey) -> Optional[BestLoad]:
    """Melhor carga direcional da chave, com as MESMAS restricoes do recorde
    (`record_events`): chave sem equipamento resolvido (fora da excecao do
    §8.1) nao e comparavel -> `None`; `bodyweight` nao tem carga -> `None`;
    `assisted` e inverso (menos assistencia e melhor). Nao e recorde: e so a
    marca a bater. Quando a chave TEM recorde vigente, a carga e a mesma do
    recorde (o recorde e por definicao a maior carga)."""
    if not _comparable(key):
        return None
    detentora: Optional[HistoryExposure] = None
    for exposure in history:
        if exposure.key != key or exposure.directional_load is None:
            continue
        if detentora is None or exposure.directional_load > detentora.directional_load:
            detentora = exposure
    return None if detentora is None else BestLoad(exposure=detentora)


MARK_KIND_RECORD = "record"
MARK_KIND_BEST_LOAD = "best_load"


def current_mark(
    history: Sequence[HistoryExposure], key: ComparabilityKey
) -> Optional[tuple[str, Any]]:
    """A marca a bater na chave: `("record", RecordEvent)` quando ha recorde
    vigente (`current_record`), senao `("best_load", BestLoad)` (a 1a
    exposicao ainda detem a marca), senao `None` (sem equipamento resolvido,
    `bodyweight` ou sem carga)."""
    vigente = current_record(history, key)
    if vigente is not None:
        return MARK_KIND_RECORD, vigente
    melhor = best_load(history, key)
    if melhor is not None:
        return MARK_KIND_BEST_LOAD, melhor
    return None


def record_set_reps(event: Any, sets: Iterable[Mapping[str, Any]]) -> Optional[int]:
    """Repeticoes da serie recorde - SO informacao (nao decidem recorde): o
    maximo de reps entre as series de trabalho da exposicao recorde na carga
    do recorde. Aceita tambem um `BestLoad` (mesma interface: `exposure`,
    `load_kg`). `sets` = linhas de `SETS_SQL` (qualquer exposicao; filtra
    pela do evento)."""
    alvo = event.load_kg
    melhor: Optional[int] = None
    for row in sets or []:
        if parse_int(row.get("exercise_row_id")) != event.exposure.exercise_row_id:
            continue
        weight = to_float(row.get("weight_kg"))
        reps = parse_int(row.get("reps"))
        if weight is None or reps is None or reps <= 0 or alvo is None or abs(weight - alvo) > 1e-6:
            continue
        melhor = reps if melhor is None else max(melhor, reps)
    return melhor


def previous_in_key(history: Sequence[HistoryExposure]) -> dict[int, HistoryExposure]:
    """`{exercise_row_id: exposicao ANTERIOR de mesma chave}` - so para quem
    tem uma. Chave sem equipamento resolvido (fora da excecao do §8.1) nao e
    comparavel com nada."""
    ultima_por_chave: dict[ComparabilityKey, HistoryExposure] = {}
    anterior: dict[int, HistoryExposure] = {}
    for exposure in history:
        if not _comparable(exposure.key):
            continue
        previous = ultima_por_chave.get(exposure.key)
        if previous is not None:
            anterior[exposure.exercise_row_id] = previous
        ultima_por_chave[exposure.key] = exposure
    return anterior


def exposures_to_compare(
    history: Sequence[HistoryExposure], activity_ids: Iterable[int]
) -> list[int]:
    """Ids de `exercises` cujas series precisam ser lidas: as exposicoes das
    sessoes `activity_ids` que tem anterior de mesma chave, e essa anterior."""
    pagina = {int(item) for item in activity_ids}
    anterior = previous_in_key(history)
    ids: set[int] = set()
    for exposure in history:
        if exposure.activity_id not in pagina:
            continue
        previous = anterior.get(exposure.exercise_row_id)
        if previous is None:
            continue
        ids.add(exposure.exercise_row_id)
        ids.add(previous.exercise_row_id)
    return sorted(ids)


def _summary(exposure: HistoryExposure, sets: Sequence[Mapping[str, Any]]):
    """`ExposureSummary` da exposicao pelos montadores do `exposure_service`
    (melhor carga, e1RM, series), com a chave do HISTORICO inteiro."""
    meta = exposure.row
    linhas = [
        {
            "exercise_row_id": exposure.exercise_row_id,
            "activity_id": exposure.activity_id,
            "local_date": meta.get("local_date"),
            "started_at": meta.get("started_at"),
            "position_in_session": meta.get("position_in_session"),
            "equipment_id": meta.get("equipment_id"),
            "equipment_name": None,
            "load_convention": meta.get("load_convention"),
            "prescribed_reps_target": meta.get("prescribed_reps_target"),
            "set_number": item.get("set_number"),
            "reps": item.get("reps"),
            "weight_kg": item.get("weight_kg"),
            "rpe": item.get("rpe"),
        }
        for item in sets
    ]
    montadas = assemble_exposures(build_exposures(linhas), exposure.exercise_id)
    if not montadas:
        return None
    return replace(to_summaries(montadas)[0], key=exposure.key)


def progression_exercise_ids(
    history: Sequence[HistoryExposure],
    set_rows: Iterable[Mapping[str, Any]],
    activity_ids: Iterable[int],
) -> dict[int, set[int]]:
    """`{activity_id: {exercicio canonico que PROGREDIU}}` para as sessoes
    `activity_ids`: `compare_exposures(anterior de mesma chave, atual)` ==
    `PROGRESSING`. Sem series de um dos lados, nao ha comparacao."""
    series: dict[int, list[Mapping[str, Any]]] = {}
    for row in set_rows or []:
        row_id = parse_int(row.get("exercise_row_id"))
        if row_id is not None:
            series.setdefault(row_id, []).append(row)

    pagina = {int(item) for item in activity_ids}
    anterior = previous_in_key(history)
    result: dict[int, set[int]] = {}
    for exposure in history:
        if exposure.activity_id not in pagina:
            continue
        previous = anterior.get(exposure.exercise_row_id)
        if previous is None:
            continue
        atual_sets = series.get(exposure.exercise_row_id)
        anterior_sets = series.get(previous.exercise_row_id)
        if not atual_sets or not anterior_sets:
            continue
        atual = _summary(exposure, atual_sets)
        antes = _summary(previous, anterior_sets)
        if atual is None or antes is None:
            continue
        status, _reasons = compare_exposures(antes, atual)
        if status is ExerciseProgressionStatus.PROGRESSING:
            result.setdefault(exposure.activity_id, set()).add(exposure.exercise_id)
    return result


def compute_session_records(
    history: Sequence[HistoryExposure],
    set_rows: Iterable[Mapping[str, Any]],
    activity_ids: Iterable[int],
) -> dict[int, dict[str, Any]]:
    """Por sessao de `activity_ids`: `{"records": n, "progressions": n,
    "record_exercise_ids": set, "progression_exercise_ids": set}`. A
    contagem e de EXERCICIOS (duas passagens do mesmo exercicio contam uma)."""
    pagina = sorted({int(item) for item in activity_ids})
    recordes = record_exercise_ids(history)
    progressoes = progression_exercise_ids(history, set_rows, pagina)
    result: dict[int, dict[str, Any]] = {}
    for activity_id in pagina:
        rec = recordes.get(activity_id, set())
        prog = progressoes.get(activity_id, set())
        result[activity_id] = {
            "records": len(rec),
            "progressions": len(prog),
            "record_exercise_ids": set(rec),
            "progression_exercise_ids": set(prog),
        }
    return result


__all__ = [
    "BestLoad",
    "HISTORY_SQL_TEMPLATE",
    "HistoryExposure",
    "MARK_KIND_BEST_LOAD",
    "MARK_KIND_RECORD",
    "RecordEvent",
    "SETS_SQL",
    "best_load",
    "compute_session_records",
    "current_mark",
    "current_record",
    "exposures_to_compare",
    "history_sql",
    "key_for",
    "parse_history",
    "previous_in_key",
    "progression_exercise_ids",
    "record_events",
    "record_exercise_ids",
    "record_load_kg",
    "record_set_reps",
]
