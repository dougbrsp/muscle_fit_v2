"""Persistencia do Motor de Progressao v2 e o avaliador em MODO SOMBRA
(PR-10; PLANO_20260912.md secoes 5.11 e 15, linha do PR 10).

Este modulo e a UNICA fronteira entre o motor (funcoes puras em
`app/domain/progression_engine.py` + `app/domain/progression_evaluator.py`) e
as tabelas da migracao 036. Ele:

1. carrega as entradas do motor (seguranca do par, contrato de recuperacao,
   catalogo de tecnicas, grupo PRIMARY, `is_compound`, `load_increment_kg`);
2. le e grava `progression_group_states` / `progression_tracks`;
3. faz o append em `progression_decisions`.

**Modo sombra.** `on_session_finish` NAO altera nenhuma ficha, prescricao nem
resposta de API: so grava nas tres tabelas novas. A flag `PROGRESSION_ENGINE`
decide se ele roda:

| valor      | efeito                                                     |
|------------|------------------------------------------------------------|
| `v1`       | nada roda (comportamento atual, **default**)               |
| `shadow`   | grava decisoes, sem afetar nada visivel                    |
| `v2`       | igual ao `shadow` neste PR; a prescricao passa a ler no PR-11 |

O default e `v1` de proposito: producao nao tem a variavel definida, entao o
deploy deste PR nao muda comportamento nenhum. Em DEV o `docker-compose.yml`
define `PROGRESSION_ENGINE=shadow`.

Falha do motor sombra NUNCA derruba o `finish`: `on_session_finish` captura
qualquer excecao, loga JSON estruturado (`progression.shadow_failed`) e segue.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from . import catalog_reads, request_memo
from .domain.progression_engine import (
    DEFAULT_FATIGUE_CONTEXT,
    ENGINE_VERSION,
    GroupState,
    ProgressionDecision,
    ReadinessContract,
    TechniqueCatalogEntry,
    TrackState,
    clamp_sets_count,
    track_key,
    track_label,
)
from .domain.progression_evaluator import (
    EvaluationResult,
    GoalEvaluation,
    SessionInputs,
    evaluate_history_by_goal,
    evaluate_session_by_goal,
)
from .domain.technique_accounting import LazyCatalog
from .domain.training_goal import DEFAULT_GOAL, TRAINING_GOAL_COLUMN, goal_value, sql_goal_is
from .domain.training_history import DEFAULT_SESSION_WINDOW, SessionRecord, TrainingHistoryRepository
from .domain.training_safety import resolve_training_safety_bulk
from .intelligence.methodology_volume import DEFAULT_VOLUME_COUNTING_MODE
from .intelligence.methodology_volume_service import (
    load_methodology_groups,
    load_session_counting_modes,
    load_volume_classes,
    load_volume_contributions_for_modes,
)
from .intelligence.progression_activation import ActivationCeiling
from .set_types import sql_set_confirmed
from .observability import EVENT_PROGRESSION_BLOCKED, EVENT_PROGRESSION_TRANSITION, log_event

logger = logging.getLogger("fitness.progression")

ENGINE_FLAG_ENV = "PROGRESSION_ENGINE"
ENGINE_V1 = "v1"
ENGINE_SHADOW = "shadow"
ENGINE_V2 = "v2"
VALID_ENGINE_MODES = (ENGINE_V1, ENGINE_SHADOW, ENGINE_V2)

GROUP_STATES_TABLE = "progression_group_states"
TRACKS_TABLE = "progression_tracks"
DECISIONS_TABLE = "progression_decisions"

# Janela do avaliador: dono e `training_history.DEFAULT_SESSION_WINDOW` (M76, C10).
SESSION_WINDOW = DEFAULT_SESSION_WINDOW


def engine_mode() -> str:
    """`PROGRESSION_ENGINE` normalizado. Valor ausente/desconhecido -> `v1`
    (nunca liga nada por acidente)."""
    raw = (os.environ.get(ENGINE_FLAG_ENV) or "").strip().lower()
    return raw if raw in VALID_ENGINE_MODES else ENGINE_V1


def shadow_enabled() -> bool:
    return engine_mode() in (ENGINE_SHADOW, ENGINE_V2)


# ---------------------------------------------------------------------------
# Leitura das entradas do motor
# ---------------------------------------------------------------------------


def load_technique_catalog(conn: Any) -> dict[str, TechniqueCatalogEntry]:
    """`advanced_techniques` ativas. Tabela ausente (banco antes da 031) nao e
    erro: sem catalogo, nenhuma tecnica e elegivel (D3, conservador)."""
    try:
        # Mesma leitura (memoizada na requisicao) que `training_safety` usa
        # para `failure_requirement` (PERF-04).
        rows = catalog_reads.active_technique_rows(conn)
    except Exception:  # noqa: BLE001 - catalogo ausente e cenario previsto
        logger.info(json.dumps({"event": "progression.technique_catalog_missing"}))
        return {}
    return {
        row["code"]: TechniqueCatalogEntry(
            code=row["code"],
            failure_requirement=row.get("failure_requirement") or "failure_not_required",
            auto_applicable=bool(row.get("auto_applicable")),
            min_experience=row.get("min_experience") or "intermediate",
            set_type=row.get("set_type"),
            is_active=bool(row.get("is_active", 1)),
            activation_set_equivalent=_optional_float(row.get("activation_set_equivalent")),
            max_equivalent_per_anchor=_optional_float(row.get("max_equivalent_per_anchor")),
            default_sets_count=None if row.get("default_sets_count") is None else int(row["default_sets_count"]),
        )
        for row in rows
    }


def _optional_float(value: Any) -> Optional[float]:
    return None if value is None else float(value)


def load_technique_coefficients(conn: Any) -> dict[str, dict[str, Any]]:
    """Coeficientes de TODAS as tecnicas (inclusive inativas: registro antigo
    de tecnica desativada vale o que valia - mesma regra do volume realizado,
    `technique_volume.load_catalog`), na conexao do chamador. So e chamada
    quando a janela tem aplicacao de tecnica (`LazyCatalog`)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT code, set_type, activation_set_equivalent, max_equivalent_per_anchor, "
                "recovery_cost_factor, default_sets_count FROM advanced_techniques"
            )
            rows = cur.fetchall() or []
    except Exception:  # noqa: BLE001 - sem catalogo, aplicacao vale o fallback (0)
        logger.info(json.dumps({"event": "progression.technique_coefficients_missing"}))
        return {}
    return {str(row["code"]): dict(row) for row in rows}


def load_activation_ceiling(
    conn: Any, exercise_ids: Sequence[int], sessions: Sequence[SessionRecord] = ()
) -> ActivationCeiling:
    """Entradas do teto de ativacao (Fase 5, PR 6) em numero FIXO de
    statements, qualquer que seja o numero de grupos/exercicios/sessoes: modo
    de contagem de cada sessao (1, so com sessoes; Lote 4), contribuicoes
    ativas nos papeis dos modos presentes (1), grupos (1) e macro-grupos (2
    POR MODO presente - no maximo 2 modos) - as MESMAS leituras da metodologia
    (`methodology_volume_service`). Sem nenhuma sessao em modo ponderado sao
    exatamente as leituras de antes do Lote 4 (+ a do modo). O catalogo de
    coeficientes so e lido se alguma sessao da janela tiver aplicacao de
    tecnica."""
    with conn.cursor() as cur:
        session_modes = load_session_counting_modes(cur, (session.activity_id for session in sessions))
        modes = sorted({DEFAULT_VOLUME_COUNTING_MODE, *session_modes.values()}, key=lambda mode: mode.value)
        # Papeis de TODOS os modos presentes num statement so (o service le a
        # uniao do motor); cada modo filtra o seu em `counted_contributions`.
        contributions = load_volume_contributions_for_modes(cur, set(exercise_ids), modes)
        groups = load_methodology_groups(cur)
        volume_classes_by_mode = {mode: load_volume_classes(cur, mode) for mode in modes}
    return ActivationCeiling(
        contributions=contributions,
        groups=groups,
        volume_classes=volume_classes_by_mode[DEFAULT_VOLUME_COUNTING_MODE],
        technique_catalog=LazyCatalog(lambda: load_technique_coefficients(conn)),
        session_modes=session_modes,
        volume_classes_by_mode=volume_classes_by_mode,
    )


def load_primary_groups(conn: Any, exercise_ids: Sequence[int]) -> tuple[dict[int, int], dict[int, float]]:
    """Grupo PRIMARY e `stimulus_weight` de cada exercicio (secao 5.1).
    Contribuicoes SECONDARY/STABILIZER nao participam da fase do grupo - em
    NENHUM modo de contagem (Lote 4, D2): a fase e a trilha pertencem ao grupo
    do exercicio, nao ao volume; o modo so muda o teto de ativacao."""
    if not exercise_ids:
        return {}, {}
    with conn.cursor() as cur:
        return primary_groups_from_cursor(cur, exercise_ids)


def primary_groups_from_cursor(cur: Any, exercise_ids: Sequence[int]) -> tuple[dict[int, int], dict[int, float]]:
    """`load_primary_groups` num cursor ja aberto (save da ficha, que roda
    dentro da transacao do caller). Mesma leitura e mesmo desempate."""
    ids = sorted({int(value) for value in exercise_ids})
    if not ids:
        return {}, {}
    placeholders = ", ".join(["%s"] * len(ids))
    cur.execute(
        "SELECT exercise_id, muscle_group_id, stimulus_weight FROM exercise_muscle_contributions "
        f"WHERE exercise_id IN ({placeholders}) AND UPPER(role) = 'PRIMARY' "
        "ORDER BY stimulus_weight DESC, muscle_group_id ASC",
        tuple(ids),
    )
    rows = cur.fetchall() or []
    groups: dict[int, int] = {}
    weights: dict[int, float] = {}
    for row in rows:
        exercise_id = int(row["exercise_id"])
        if exercise_id in groups:
            continue
        groups[exercise_id] = int(row["muscle_group_id"])
        weights[exercise_id] = float(row["stimulus_weight"] or 0)
    return groups, weights


def load_exercise_flags(conn: Any, exercise_ids: Sequence[int]) -> dict[int, bool]:
    """`exercise_catalog.is_compound` (define o Δbase do incremento)."""
    ids = sorted({int(value) for value in exercise_ids})
    if not ids:
        return {}
    placeholders = ", ".join(["%s"] * len(ids))
    with conn.cursor() as cur:
        cur.execute(f"SELECT id, is_compound FROM exercise_catalog WHERE id IN ({placeholders})", tuple(ids))
        return {int(row["id"]): bool(row.get("is_compound")) for row in cur.fetchall() or []}


def load_laboral_ids(conn: Any, exercise_ids: Sequence[int]) -> frozenset[int]:
    """Exercicios LABORAIS (M100, `exercise_catalog.is_laboral`): sem
    progressao nem prescricao do motor. Leitura de `laboral_catalog`."""
    from .laboral_catalog import load_laboral_ids as _load_laboral_ids

    return _load_laboral_ids(conn, exercise_ids)


def load_equipment_steps(conn: Any, equipment_ids: Sequence[int]) -> dict[int, Decimal]:
    """`equipment.load_increment_kg` - menor incremento TOTAL (D12)."""
    ids = sorted({int(value) for value in equipment_ids})
    if not ids:
        return {}
    # Uma leitura por requisicao GET, compartilhada com `training_safety`
    # (PERF-04); `equipment` so muda em rota de escrita.
    return {
        equipment_id: Decimal(str(row["load_increment_kg"]))
        for equipment_id, row in catalog_reads.equipment_rows(conn, ids).items()
        if row.get("load_increment_kg") is not None
    }


def load_fatigue_contexts(conn: Any, sessions: Sequence[SessionRecord]) -> dict[int, str]:
    """`exercises.id` -> contexto de fadiga (`PriorFatigueBand.value`) de cada
    execucao na sessao REAL, pelo dono `domain/prior_fatigue.py` (montagem em
    `domain/session_fatigue.py`): series nao preparatorias dos exercicios
    anteriores, na ordem, x `stimulus_weight` cadastrado. Duas leituras
    (contribuicoes + nomes) para a janela inteira; o catalogo de tecnicas so
    se houver aplicacao."""
    from .domain.session_fatigue import executions_from_session, session_contexts
    from .intelligence.session_fatigue_reader import load_contributions

    executions = {session.activity_id: executions_from_session(session) for session in sessions}
    contributions = load_contributions(
        conn, (item.exercise_id for items in executions.values() for item in items)
    )
    catalog = LazyCatalog(lambda: load_technique_coefficients(conn))
    result: dict[int, str] = {}
    for items in executions.values():
        result.update({row_id: band.value for row_id, band in session_contexts(items, contributions, catalog).items()})
    return result


def load_user_flags(conn: Any, user_id: int) -> bool:
    """`users.is_advanced_user` (D11: so elegibilidade de tecnica)."""
    with conn.cursor() as cur:
        cur.execute("SELECT is_advanced_user FROM users WHERE id = %s", (int(user_id),))
        row = cur.fetchone()
    return bool(row and row.get("is_advanced_user"))


def build_session_inputs(
    conn: Any,
    user_id: int,
    sessions: Sequence[SessionRecord],
    *,
    readiness: Optional[Mapping[int, ReadinessContract]] = None,
) -> SessionInputs:
    """Junta, em numero FIXO de queries, tudo o que o avaliador precisa.

    `readiness` so vale para execucoes SEM ficha congelada (treino livre,
    ficha legada); ausente = READY. Com ficha congelada, o avaliador usa o
    teto, a falha, o alvo e as series que a ficha daquele dia congelou - ja
    compostos com o contrato de recuperacao PROJETADO no congelamento -, com
    o overlay de seguranca de hoje, como o Modo Treino (Lote 2/P7,
    2026-10-11; `SessionInputs.evaluation_intensity_for`). A sessao em dose
    reduzida que seguiu a ficha conta; o contrato nao e reaplicado.
    """
    pairs = sorted({execution.pair for session in sessions for execution in session.exercises if execution.pair})
    exercise_ids = sorted({pair[0] for pair in pairs})
    equipment_ids = sorted({pair[1] for pair in pairs})
    primary_group, stimulus_weight = load_primary_groups(conn, exercise_ids)
    return SessionInputs(
        fatigue_context=load_fatigue_contexts(conn, sessions),
        safety=resolve_training_safety_bulk(conn, pairs) if pairs else {},
        readiness=dict(readiness or {}),
        catalog=load_technique_catalog(conn),
        primary_group=primary_group,
        stimulus_weight=stimulus_weight,
        is_compound=load_exercise_flags(conn, exercise_ids),
        load_increment_kg=load_equipment_steps(conn, equipment_ids),
        is_advanced_user=load_user_flags(conn, user_id),
        activation_probe=load_activation_ceiling(conn, exercise_ids, sessions),
    )


# ---------------------------------------------------------------------------
# Leitura / escrita do estado
# ---------------------------------------------------------------------------

_GROUP_COLUMNS = (
    "phase",
    "phase_status",
    "reps_min",
    "reps_max",
    "pending_action",
    "pending_payload",
    "phase_entered_at",
    "last_rep_range_change_at",
    "last_load_change_at",
    "last_sets_change_at",
    "technique_applied_at",
    "next_eligible_progression_at",
    # M55 (063): par alvo da F4, gravado na efetivacao do APPLY_TECHNIQUE
    "technique_target_exercise_id",
    "technique_target_equipment_id",
)
_TRACK_COLUMNS = (
    "muscle_group_id",
    "load_status",
    "working_load_kg",
    "sets_count",
    "technique_code",
    "pending_action_applied_at",
    "last_load_change_at",
    "last_sets_change_at",
    "last_executed_at",
    # migracao 068 (PR 2 da fadiga previa)
    "calibrated_at",
    # migracao 071 (Lote 3/P4 RPE 10): aumento de carga pendente DA TRILHA
    "pending_load_kg",
    "pending_load_since",
    # migracao 072 (Lote 4/P6 RPE 10): origem do aumento e marca do aumento por plato
    "pending_load_source",
    "plateau_load_applied_at",
)


def _json_or_none(value: Any) -> Optional[str]:
    return None if value in (None, {}, []) else json.dumps(value, ensure_ascii=False, default=str)


def _parse_json(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _target_key(exercise_id: Any, equipment_id: Any) -> Optional[tuple[int, int]]:
    """Par alvo da F4 so quando as DUAS colunas estao preenchidas."""
    if exercise_id is None or equipment_id is None:
        return None
    return (int(exercise_id), int(equipment_id))


def _group_state_from_row(row: Mapping[str, Any]) -> GroupState:
    return GroupState(
        muscle_group_id=int(row["muscle_group_id"]),
        id=int(row["id"]),
        phase=row["phase"],
        phase_status=row["phase_status"],
        reps_min=int(row["reps_min"]),
        reps_max=int(row["reps_max"]),
        pending_action=row["pending_action"],
        pending_payload=_parse_json(row["pending_payload"]),
        phase_entered_at=row["phase_entered_at"],
        last_rep_range_change_at=row["last_rep_range_change_at"],
        last_load_change_at=row["last_load_change_at"],
        last_sets_change_at=row["last_sets_change_at"],
        technique_applied_at=row["technique_applied_at"],
        next_eligible_progression_at=row["next_eligible_progression_at"],
        technique_target_key=_target_key(row["technique_target_exercise_id"], row["technique_target_equipment_id"]),
        training_goal=goal_value(row.get(TRAINING_GOAL_COLUMN)),
    )


_GROUP_SELECT = (
    f"SELECT id, muscle_group_id, {TRAINING_GOAL_COLUMN}, {', '.join(_GROUP_COLUMNS)} "
    f"FROM {GROUP_STATES_TABLE} WHERE user_id = %s"
)


def load_group_states(conn: Any, user_id: int, training_goal: Any = None) -> dict[int, GroupState]:
    """Estados de grupo de UM objetivo (M97 F1; ausente = default), por
    `muscle_group_id`. Quem precisa de todos os objetivos usa
    `load_all_group_states`."""
    goal = goal_value(training_goal)
    with conn.cursor() as cur:
        cur.execute(f"{_GROUP_SELECT} AND {sql_goal_is(GROUP_STATES_TABLE)}", (int(user_id), goal))
        rows = cur.fetchall() or []
    return {int(row["muscle_group_id"]): _group_state_from_row(row) for row in rows}


def load_all_group_states(conn: Any, user_id: int) -> dict[tuple[int, str], GroupState]:
    """Estados de grupo de TODOS os objetivos, por `GroupState.key`
    (`(muscle_group_id, objetivo)`), numa leitura."""
    with conn.cursor() as cur:
        cur.execute(_GROUP_SELECT, (int(user_id),))
        rows = cur.fetchall() or []
    states = (_group_state_from_row(row) for row in rows)
    return {state.key: state for state in states}


# Nome do memo por requisicao (`request_memo`) das trilhas do usuario: quem ESCREVE
# `progression_tracks` (`save_track`, `replay_user`) chama `forget(TRACKS_MEMO)`.
TRACKS_MEMO = "progression_tracks"


def load_tracks(conn: Any, user_id: int) -> dict[tuple[int, int, str, str], TrackState]:
    """Trilhas do usuario (TODOS os objetivos) por `TrackState.key` =
    (exercicio, equipamento, contexto de fadiga, objetivo). Quem prescreve
    resolve a trilha do contexto e do objetivo pedidos com
    `progression_engine.resolve_track` (nunca `tracks.get(par)`).

    Lidas UMA vez por requisicao GET (PERF-04): `suggest_for_pairs` e
    `prescribe_pairs` pedem as mesmas trilhas na mesma chamada."""
    return request_memo.memoize((TRACKS_MEMO, int(user_id)), lambda: _read_tracks(conn, user_id))


def _read_tracks(conn: Any, user_id: int) -> dict[tuple[int, int, str, str], TrackState]:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id, exercise_id, equipment_id, fatigue_context, {TRAINING_GOAL_COLUMN}, "
            f"{', '.join(_TRACK_COLUMNS)} "
            f"FROM {TRACKS_TABLE} WHERE user_id = %s",
            (int(user_id),),
        )
        rows = cur.fetchall() or []
    return {
        track_key(
            row["exercise_id"], row["equipment_id"], row.get("fatigue_context"), row.get(TRAINING_GOAL_COLUMN)
        ): TrackState(
            exercise_id=int(row["exercise_id"]),
            equipment_id=int(row["equipment_id"]),
            fatigue_context=str(row.get("fatigue_context") or DEFAULT_FATIGUE_CONTEXT),
            training_goal=goal_value(row.get(TRAINING_GOAL_COLUMN)),
            calibrated_at=row.get("calibrated_at"),
            muscle_group_id=None if row["muscle_group_id"] is None else int(row["muscle_group_id"]),
            id=int(row["id"]),
            load_status=row["load_status"],
            working_load_kg=None if row["working_load_kg"] is None else Decimal(str(row["working_load_kg"])),
            # Teto de 4 da secao 5.6 aplicado na LEITURA: linhas gravadas
            # antes da correcao (prescricao legada com 5-9 series) param de
            # congelar a trilha no predicado da secao 5.4 sem esperar replay.
            sets_count=clamp_sets_count(row["sets_count"]),
            technique_code=row["technique_code"],
            pending_action_applied_at=row["pending_action_applied_at"],
            last_load_change_at=row["last_load_change_at"],
            last_sets_change_at=row["last_sets_change_at"],
            last_executed_at=row["last_executed_at"],
            pending_load_kg=None if row.get("pending_load_kg") is None else Decimal(str(row["pending_load_kg"])),
            pending_load_since=row.get("pending_load_since"),
            pending_load_source=row.get("pending_load_source"),
            plateau_load_applied_at=row.get("plateau_load_applied_at"),
        )
        for row in rows
    }


def save_group_state(conn: Any, user_id: int, state: GroupState) -> int:
    """Upsert por `(user_id, muscle_group_id, training_goal)` (chave unica
    `uk_pgs_user_group`, 093); devolve o id."""
    values = (
        state.phase,
        state.phase_status,
        int(state.reps_min),
        int(state.reps_max),
        state.pending_action,
        _json_or_none(state.pending_payload),
        state.phase_entered_at,
        state.last_rep_range_change_at,
        state.last_load_change_at,
        state.last_sets_change_at,
        state.technique_applied_at,
        state.next_eligible_progression_at,
        None if state.technique_target_key is None else int(state.technique_target_key[0]),
        None if state.technique_target_key is None else int(state.technique_target_key[1]),
    )
    assignments = ", ".join(f"{column} = %s" for column in _GROUP_COLUMNS)
    key = (int(user_id), int(state.muscle_group_id), goal_value(state.training_goal))
    where = f"WHERE user_id = %s AND muscle_group_id = %s AND {sql_goal_is(GROUP_STATES_TABLE)}"
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {GROUP_STATES_TABLE} SET {assignments}, engine_version = %s {where}",
            values + (ENGINE_VERSION,) + key,
        )
        if cur.rowcount == 0 and state.id is None:
            columns = ", ".join(
                ("user_id", "muscle_group_id", TRAINING_GOAL_COLUMN) + _GROUP_COLUMNS + ("engine_version",)
            )
            placeholders = ", ".join(["%s"] * (len(_GROUP_COLUMNS) + 4))
            cur.execute(
                f"INSERT INTO {GROUP_STATES_TABLE} ({columns}) VALUES ({placeholders})",
                key + values + (ENGINE_VERSION,),
            )
        cur.execute(f"SELECT id FROM {GROUP_STATES_TABLE} {where}", key)
        return int(cur.fetchone()["id"])


def save_track(conn: Any, user_id: int, track: TrackState) -> int:
    """Upsert por `(user_id, exercise_id, equipment_id, fatigue_context,
    training_goal)` (chave unica `uk_ptr_user_pair_context`, 093); devolve o
    id."""
    values = (
        track.muscle_group_id,
        track.load_status,
        track.working_load_kg,
        # Nenhuma origem grava `sets_count` acima do teto (secao 5.6).
        clamp_sets_count(track.sets_count),
        track.technique_code,
        track.pending_action_applied_at,
        track.last_load_change_at,
        track.last_sets_change_at,
        track.last_executed_at,
        track.calibrated_at,
        track.pending_load_kg,
        track.pending_load_since,
        track.pending_load_source,
        track.plateau_load_applied_at,
    )
    assignments = ", ".join(f"{column} = %s" for column in _TRACK_COLUMNS)
    key = (
        int(user_id),
        int(track.exercise_id),
        int(track.equipment_id),
        str(track.fatigue_context or DEFAULT_FATIGUE_CONTEXT),
        goal_value(track.training_goal),
    )
    where = (
        "WHERE user_id = %s AND exercise_id = %s AND equipment_id = %s AND fatigue_context = %s "
        f"AND {sql_goal_is(TRACKS_TABLE)}"
    )
    request_memo.forget(TRACKS_MEMO)
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {TRACKS_TABLE} SET {assignments}, engine_version = %s {where}",
            values + (ENGINE_VERSION,) + key,
        )
        if cur.rowcount == 0 and track.id is None:
            columns = ", ".join(
                ("user_id", "exercise_id", "equipment_id", "fatigue_context", TRAINING_GOAL_COLUMN)
                + _TRACK_COLUMNS
                + ("engine_version",)
            )
            placeholders = ", ".join(["%s"] * (len(_TRACK_COLUMNS) + 6))
            cur.execute(
                f"INSERT INTO {TRACKS_TABLE} ({columns}) VALUES ({placeholders})",
                key + values + (ENGINE_VERSION,),
            )
        cur.execute(f"SELECT id FROM {TRACKS_TABLE} {where}", key)
        return int(cur.fetchone()["id"])


def insert_decision(
    conn: Any,
    user_id: int,
    decision: ProgressionDecision,
    *,
    kind: str = "EVALUATION",
    activity_id: Optional[int] = None,
    workout_plan_exercise_id: Optional[int] = None,
    target_datetime: Optional[datetime] = None,
    training_goal: Any = None,
) -> int:
    """Append em `progression_decisions`, com o objetivo avaliado (M97 F1;
    ausente = default)."""
    payload = decision.to_dict()
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {DECISIONS_TABLE} "
            "(group_state_id, track_id, user_id, kind, activity_id, workout_plan_exercise_id, target_datetime, "
            " phase_before, phase_after, decision, output_json, evidence_json, blocked_by, engine_version, "
            f" {TRAINING_GOAL_COLUMN}) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                decision.group_state_id,
                decision.track_id,
                int(user_id),
                kind,
                activity_id,
                workout_plan_exercise_id,
                target_datetime,
                decision.phase_before,
                decision.phase_after,
                decision.decision,
                _json_or_none(payload),
                _json_or_none(payload.get("evidence")),
                _json_or_none(payload.get("blocked_by")),
                ENGINE_VERSION,
                goal_value(training_goal),
            ),
        )
        return int(cur.lastrowid)


def persist_result(
    conn: Any,
    user_id: int,
    result: EvaluationResult,
    *,
    activity_id: Optional[int] = None,
    per_session: bool = False,
    training_goal: Any = None,
) -> dict[str, int]:
    """Grava estado + decisoes de UM objetivo (`training_goal`, ausente =
    default: as decisoes levam o objetivo; estados e trilhas, o deles). Os ids das trilhas/grupos sao resolvidos antes
    das decisoes, para a FK de `progression_decisions` sempre fechar.

    `per_session=True` (replay): grava `result.session_decisions`, cada grupo
    com o `activity_id` da sessao que o gerou, na mesma ordem da lista plana.
    Sem ele, todas as `result.decisions` vao com o `activity_id` recebido (o
    `finish`, que avalia uma sessao so)."""
    group_ids = {
        muscle_group_id: save_group_state(conn, user_id, state)
        for muscle_group_id, state in sorted(result.group_states.items())
    }
    track_ids = {key: save_track(conn, user_id, track) for key, track in sorted(result.tracks.items())}
    if per_session:
        batches = [(batch.activity_id, batch.decisions) for batch in result.session_decisions]
    else:
        batches = [(activity_id, result.decisions)]
    written = 0
    for batch_activity_id, decisions in batches:
        for decision in decisions:
            group_state_id = decision.group_state_id or group_ids.get(decision.muscle_group_id)
            if group_state_id is None:
                continue
            resolved = replace(decision, group_state_id=group_state_id, track_id=decision.track_id)
            insert_decision(
                conn,
                user_id,
                resolved,
                kind="EVALUATION",
                activity_id=batch_activity_id,
                training_goal=training_goal,
            )
            written += 1
    return {"groups": len(group_ids), "tracks": len(track_ids), "decisions": written}


def persist_goal_evaluation(
    conn: Any,
    user_id: int,
    evaluation: GoalEvaluation,
    *,
    activity_id: Optional[int] = None,
    per_session: bool = False,
) -> dict[str, int]:
    """`persist_result` de cada objetivo avaliado (M97 F1), somando as
    contagens. Um objetivo so = exatamente o `persist_result` de antes."""
    totals = {"groups": 0, "tracks": 0, "decisions": 0}
    for goal, result in evaluation.results.items():
        counts = persist_result(
            conn, user_id, result, activity_id=activity_id, per_session=per_session, training_goal=goal
        )
        for name in totals:
            totals[name] += counts[name]
    return totals


# ---------------------------------------------------------------------------
# Espelho observavel das decisoes (secao 14)
# ---------------------------------------------------------------------------


def log_decision_events(
    user_id: int, activity_id: Optional[int], decisions: Sequence[ProgressionDecision]
) -> None:
    """Emite `progression.transition` e `progression.blocked` das decisoes de
    UMA sessao. Nao e regra de negocio: o que vai para o log ja esta em
    `progression_decisions` (fases e `blocked_by`); aqui so fica pesquisavel no
    CloudWatch sem consultar o banco.

    **Volume.** Uma decisao por GRUPO MUSCULAR tocado na sessao (D2: a fase e do
    grupo, nao da trilha) - tipicamente 2 a 4 grupos, no limite 6 num full body.
    E `transition` so sai quando a fase MUDA de fato, o que e raro por
    construcao (exige plato confirmado ou acao pendente efetivada). O teto real
    por sessao e, portanto, "grupos tocados" linhas, e o caso comum e zero.

    **Conteudo (secao 14).** So id numerico de usuario/atividade/grupo, fases,
    `engine_version` e codigos. O `reason` da decisao e texto livre e por isso
    NAO entra; `evidence` tambem nao (tem sinal de dor por trilha). Ambos
    continuam no banco.

    O replay (`rebuild_for_user`) NAO passa por aqui de proposito: reprocessar
    a historia inteira de todos os usuarios produziria milhares de linhas que
    nada dizem sobre o que aconteceu hoje.
    """
    for decision in decisions:
        if decision.phase_before != decision.phase_after:
            log_event(
                EVENT_PROGRESSION_TRANSITION,
                user_id=int(user_id),
                activity_id=None if activity_id is None else int(activity_id),
                muscle_group_id=decision.muscle_group_id,
                phase_before=decision.phase_before,
                phase_after=decision.phase_after,
                engine_version=ENGINE_VERSION,
                reason_code=decision.decision,
            )
        if decision.blocked_by:
            log_event(
                EVENT_PROGRESSION_BLOCKED,
                user_id=int(user_id),
                activity_id=None if activity_id is None else int(activity_id),
                muscle_group_id=decision.muscle_group_id,
                phase=decision.phase_after,
                engine_version=ENGINE_VERSION,
                reason_code=decision.blocked_by[0],
                blocked_by=list(decision.blocked_by),
            )


# ---------------------------------------------------------------------------
# Avaliador no finish (modo sombra)
# ---------------------------------------------------------------------------


def run_session_evaluation(
    conn: Any,
    user_id: int,
    activity_id: Optional[int] = None,
    *,
    readiness: Optional[Mapping[int, ReadinessContract]] = None,
) -> Optional[dict[str, Any]]:
    """Avalia a ULTIMA sessao concluida do usuario (ou `activity_id`, se ela
    estiver na janela) e grava o resultado. Sem `try` aqui: quem chama no
    `finish` e `on_session_finish`, que e quem engole a falha.

    A sessao e avaliada contra a ficha CONGELADA daquele dia
    (`workout_plan_exercises.prescription_json`, lido por
    `training_history.SESSIONS_SQL`): dose exigida = series da ficha, alvo e
    teto = os da ficha. `readiness` so pesa nas execucoes sem ficha
    congelada (ver `build_session_inputs`)."""
    history = TrainingHistoryRepository(conn)
    sessions = history.fetch_last_completed_sessions(user_id, SESSION_WINDOW)
    if not sessions:
        return None
    index = len(sessions) - 1
    if activity_id is not None:
        matches = [position for position, item in enumerate(sessions) if item.activity_id == int(activity_id)]
        if not matches:
            return None
        index = matches[-1]
    session = sessions[index]
    window = sessions[: index + 1]
    inputs = build_session_inputs(conn, user_id, window, readiness=readiness)
    # M97 F1: cada objetivo executado na sessao e avaliado so com as
    # execucoes e os estados dele.
    result = evaluate_session_by_goal(
        session,
        window,
        group_states=load_all_group_states(conn, user_id),
        tracks=load_tracks(conn, user_id),
        inputs=inputs,
    )
    counts = persist_goal_evaluation(conn, user_id, result, activity_id=session.activity_id)
    log_decision_events(user_id, session.activity_id, result.decisions)
    summary = {
        "activity_id": session.activity_id,
        "group_outcomes": dict(result.group_outcomes),
        **counts,
    }
    # M97 D: sessao de deload PULADA (decisao neutra; nada gravado, a trilha
    # e as fases ficam intactas). O motivo vai no resumo/log do `finish`.
    if result.skipped_reason:
        summary["skipped"] = result.skipped_reason
    return summary


def on_session_finish(conn: Any, user_id: int, activity_id: int) -> Optional[dict[str, Any]]:
    """Hook do `finish` em MODO SOMBRA. Nunca levanta: qualquer falha e
    logada como `progression.shadow_failed` e a finalizacao segue."""
    if not shadow_enabled():
        return None
    try:
        summary = run_session_evaluation(conn, user_id, activity_id)
    except Exception as exc:  # noqa: BLE001 - sombra nunca derruba o finish
        logger.warning(
            json.dumps(
                {
                    "event": "progression.shadow_failed",
                    "user_id": int(user_id),
                    "activity_id": int(activity_id),
                    "engine_version": ENGINE_VERSION,
                    "mode": engine_mode(),
                    "error_type": type(exc).__name__,
                },
                ensure_ascii=False,
            )
        )
        return None
    logger.info(
        json.dumps(
            {
                "event": "progression.shadow_evaluated",
                "user_id": int(user_id),
                "activity_id": int(activity_id),
                "engine_version": ENGINE_VERSION,
                "mode": engine_mode(),
                "summary": summary,
            },
            ensure_ascii=False,
            default=str,
        )
    )
    return summary


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def rebuild_for_user(
    conn: Any,
    user_id: int,
    *,
    window_size: int = SESSION_WINDOW,
    sessions: Optional[Sequence[SessionRecord]] = None,
) -> dict[str, Any]:
    """Reconstroi trilhas/fases do usuario a partir do historico (secao 5.11).

    Idempotente: apaga o estado e as decisoes DESTE usuario e reprocessa a
    historia inteira. `progression_decisions` e append-only por contrato, mas
    o replay e uma reconstrucao: apagar o que ele mesmo gerou e o que mantem
    "rodar duas vezes = mesmo estado".

    `sessions` permite que o chamador entregue o historico ja lido (em ordem
    cronologica crescente). E o que `app/replay_progression.py` faz para ler
    em paginas: no caminho da RDS Data API uma leitura unica do historico
    inteiro estouraria o teto de 1 MB da resposta. Sem o argumento, le tudo de
    uma vez, como antes.

    O replay usa o MESMO caminho do `finish`: cada sessao historica e avaliada
    contra a ficha congelada dela (`SESSIONS_SQL` le o snapshot), entao
    trilhas com `sets_count` inflado por series extras (antes do Lote 2/P3)
    voltam as series do Modelo/ficha.
    """
    if sessions is None:
        history = TrainingHistoryRepository(conn)
        sessions = history.fetch_last_completed_sessions(user_id, 10_000)
    inputs = build_session_inputs(conn, user_id, sessions)
    # M97 F1: um replay por objetivo presente no historico (um so = o de antes).
    result = evaluate_history_by_goal(sessions, inputs=inputs, window_size=window_size)
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {DECISIONS_TABLE} WHERE user_id = %s", (int(user_id),))
        request_memo.forget(TRACKS_MEMO)
        cur.execute(f"DELETE FROM {TRACKS_TABLE} WHERE user_id = %s", (int(user_id),))
        cur.execute(f"DELETE FROM {GROUP_STATES_TABLE} WHERE user_id = %s", (int(user_id),))
    # Cada decisao volta com a sessao que a gerou (antes ia com activity_id
    # NULL). O estado gravado (trilhas e grupos) e o mesmo de antes.
    counts = persist_goal_evaluation(conn, user_id, result, per_session=True)
    return {"user_id": int(user_id), "sessions": len(sessions), **counts}


def users_with_history(conn: Any) -> list[int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT a.user_id FROM activities a WHERE a.session_status = 'done' "
            "AND EXISTS (SELECT 1 FROM exercises e JOIN exercise_sets es ON es.exercise_id = e.id "
            f"AND {sql_set_confirmed('es.reps_pending')} "
            "WHERE e.activity_id = a.id) ORDER BY a.user_id"
        )
        return [int(row["user_id"]) for row in cur.fetchall() or []]


def _group_snapshot(state: GroupState) -> dict[str, Any]:
    return {
        "phase": state.phase,
        "phase_status": state.phase_status,
        "reps": [state.reps_min, state.reps_max],
        "pending_action": state.pending_action,
        "pending_payload": state.pending_payload,
        "technique_target_key": None if state.technique_target_key is None else list(state.technique_target_key),
    }


def state_snapshot(conn: Any, user_id: int) -> dict[str, Any]:
    """Estado comparavel entre duas execucoes do replay (sem ids nem datas de
    `updated_at`), usado pelo teste de idempotencia e pelo `fingerprint` do
    replay.

    M97 F1: `groups` sao os estados do objetivo DEFAULT (a forma de sempre);
    os de outro objetivo vao em `groups_by_goal`, que so aparece quando
    existe algum - historico so de hipertrofia tem o MESMO snapshot (e o
    mesmo `fingerprint`) de antes. As trilhas de outro objetivo ja tem rotulo
    proprio (`track_label`)."""
    all_groups = load_all_group_states(conn, user_id)
    tracks = load_tracks(conn, user_id)
    default_goal = DEFAULT_GOAL.value
    snapshot: dict[str, Any] = {
        "groups": {
            group_id: _group_snapshot(state)
            for (group_id, goal), state in sorted(all_groups.items())
            if goal == default_goal
        },
        "tracks": {
            track_label(key): {
                "load_status": track.load_status,
                "working_load_kg": None if track.working_load_kg is None else str(track.working_load_kg),
                "sets_count": track.sets_count,
                "technique_code": track.technique_code,
                "muscle_group_id": track.muscle_group_id,
            }
            for key, track in sorted(tracks.items())
        },
    }
    other: dict[str, dict[int, Any]] = {}
    for (group_id, goal), state in sorted(all_groups.items()):
        if goal != default_goal:
            other.setdefault(goal, {})[group_id] = _group_snapshot(state)
    if other:
        snapshot["groups_by_goal"] = other
    return snapshot


def retention_cleanup(conn: Any, *, months: int = 18) -> int:
    """D15: `progression_decisions` guarda 18 meses. Job a agendar no PR-20;
    aqui fica a funcao, executavel pelo script de replay com `--cleanup`."""
    with conn.cursor() as cur:
        cur.execute(
            f"DELETE FROM {DECISIONS_TABLE} WHERE created_at < DATE_SUB(NOW(), INTERVAL %s MONTH)",
            (int(months),),
        )
        return int(cur.rowcount or 0)
