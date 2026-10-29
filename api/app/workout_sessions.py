"""Sessões de treino incrementais (gravação ao vivo pelo app Zepp OS).

Diferente de POST /activities (upsert com delete+recreate, pensado para
importação completa), estes endpoints gravam série a série durante o treino:

  POST /workout-sessions/start                          -> cria activity 'in_progress'
  POST /workout-sessions/{id}/exercises                 -> inicia um exercício
  POST /workout-sessions/{id}/exercises/{ex_id}/sets    -> grava uma série
  POST /workout-sessions/{id}/finish                    -> encerra e consolida

Idempotência por `client_key` (Evolução ZEPP, PR C, migração 087): o relógio
grava de uma fila offline e reenvia quando a resposta se perde. Com a mesma
chave no mesmo pai (a sessão para o exercício, o exercício para a série), a
repetição devolve o registro já gravado com `duplicate: true` e não grava nada
(nem agregados nem eventos de observabilidade). Sem a chave (celular,
importadores) nada muda.

Os agregados (sets_count, volume_kg, avg_rpe...) são recalculados a cada série
para que os analytics existentes funcionem mesmo com a sessão em andamento.
POST /activities permanece intacto para os importadores existentes.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from .advanced_techniques import ensure_valid_codes
from .db import is_duplicate_key_error, transaction
from .domain import effective_intensity
from .domain.intensity_status_labels import intensity_status_label, reps_position_label, safety_event_label
from .domain.progression_engine import SAFETY_ALERT_EVENTS
from .domain.calories_source import source_on_manual_write
from .domain import technique_accounting
from .domain.training_goal import SQL_PLAN_EXERCISE_GOAL, plan_exercise_goal_params
from . import deload_periods
from .domain.deload import (
    DELOAD_SOURCE_COLUMN,
    DeloadSource,
    DeloadToggleLocked,
    can_toggle_deload,
    deload_allows_technique,
    deload_effort_target,
    deload_source_value,
    is_deload_session,
    session_deload_source,
    toggled_deload_source,
)
from .domain.next_set_load import (
    DEFAULT_RULESET,
    planned_ramp_rpe,
    ruleset_for_goal,
    ruleset_uses_fatigue,
    ruleset_uses_published,
)
from .engine_decision_context import (
    PairEngineContext,
    equipment_step_kg,
    load_deload_prescription,
    load_pair_step_context,
    next_set_load_for,
    safe_read,
)
from .intelligence.dashboard.time_windows import user_now, user_timezone
from .intelligence.session_fatigue_reader import load_session_prior_fatigue
from .next_set_load_rules import load_published_ruleset
from .observability import EVENT_SAFETY_CLAMP, EVENT_SAFETY_RPE_ABOVE_CEILING, log_event
from .progression_state import on_session_finish
from .security import Identity, get_current_identity
from .domain.session_loads import LOAD_ADJUST_SOURCES
from .domain.set_measures import SetMeasure, is_rep_based, normalize_measure
from .domain.set_sides import SIDE_FIELDS, apply_canonical_to_model, sides_text
from .domain.training_safety import RPE_TARGET_SOURCE_PROTOCOL, RPE_TARGET_SOURCES
from .exercise_set_update_merge import merge_set_patch
from .set_measure_input import ensure_valid_set_measure, set_measure_columns
from .laboral_catalog import load_laboral_ids
from .set_types import (
    LABORAL,
    TECHNIQUE_APPLICATION_SET_TYPES,
    WORKING,
    is_laboral_set,
    is_pending_set,
    is_preparatory_set,
    normalize_set_type,
    sql_is_working,
    sql_set_confirmed,
)
from .services import (
    compute_exercise_stats,
    resolve_equipment_id,
    resolve_gym_id,
    resolve_mapping_id,
    source_id,
)
from .timezones import DEFAULT_TIMEZONE

router = APIRouter(tags=["workout-sessions"], dependencies=[Depends(get_current_identity)])

# `exercises.client_key`/`exercise_sets.client_key` (migração 087:
# VARCHAR(64) ascii_bin). Identificador opaco gerado pelo cliente; o charset
# simples evita surpresa de collation e de log.
CLIENT_KEY_MAX_LENGTH = 64
CLIENT_KEY_PATTERN = r"^[A-Za-z0-9._:-]+$"


def _client_key_field():
    return Field(default=None, min_length=1, max_length=CLIENT_KEY_MAX_LENGTH, pattern=CLIENT_KEY_PATTERN)


def _to_user_wall_clock(value: Optional[datetime], timezone_name: str) -> Optional[datetime]:
    """Normaliza um datetime para a convencao do banco: hora de PAREDE do
    usuario, NAIVE (`activities.started_at`/`ended_at` sao DATETIME sem fuso;
    ver `intervals_hr_match.py` e `time_windows.user_now`).

    - aware (ex.: `2026-10-02T12:00:00Z`) -> convertido para o fuso do
      usuario e sem tzinfo;
    - naive -> mantido como esta (o cliente ja mandou a hora local, como a
      tela /registro-exercicios faz).

    Fuso invalido cai em `DEFAULT_TIMEZONE`, como em `user_now`.
    """
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return value
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
        zone = ZoneInfo(DEFAULT_TIMEZONE)
    return value.astimezone(zone).replace(tzinfo=None)

class SessionStartIn(BaseModel):
    name: str = "Treino (Zepp)"
    workout_plan_id: Optional[int] = None
    gym_id: Optional[int] = None
    gym_name: Optional[str] = None
    # Tipo de Treinamento (training_types) que originou este treino, motor de
    # geracao de ficha v2. Opcional; ver models.ActivityIn.training_type_id.
    training_type_id: Optional[int] = None
    conducted_by: Optional[str] = None
    local_date: Optional[date] = None
    started_at: Optional[datetime] = None
    notes: Optional[str] = None
    source_name: str = "amazfit-zepp"
    activity_type: Optional[str] = None
    # M97 D: marca AVULSA de deload no início do Modo Treino. Sem ela, a
    # sessão ainda vira deload se a data dela cair num período ativo
    # (`/deload-periods`). A origem é carimbada em `activities.deload_source`.
    deload: bool = False


class SessionExerciseIn(BaseModel):
    original_name: str
    sequence: Optional[int] = None
    mapping_id: Optional[int] = None
    equipment_id: Optional[int] = None
    equipment_name: Optional[str] = None
    # Vincula este exercício lançado a um item específico da ficha planejada
    # (workout_plan_exercises.id), quando a activity tiver workout_plan_id.
    workout_plan_exercise_id: Optional[int] = None
    notes: Optional[str] = None
    # Sinais subjetivos opcionais (mesma semantica de exercises.muscle_pain/
    # joint_pain via POST /activities e PUT /exercises/logs/{id}); o app Zepp
    # OS nao os envia hoje, mas a tela /registro-exercicios precisa.
    muscle_pain: Optional[int] = Field(default=None, ge=0, le=10)
    joint_pain: Optional[bool] = None
    # Chave de idempotência do cliente (fila do relógio). Repetir a chave na
    # mesma sessão devolve o exercício já criado (`duplicate: true`).
    client_key: Optional[str] = _client_key_field()


class SessionSetIn(BaseModel):
    set_number: Optional[int] = None
    set_type: str = "working"
    reps: Optional[int] = None
    weight_kg: Optional[Decimal] = None
    rpe: Optional[Decimal] = None
    rest_seconds: Optional[int] = None
    hr_avg_bpm: Optional[Decimal] = None
    hr_max_bpm: Optional[Decimal] = None
    notes: Optional[str] = None
    # Dor muscular/articular por SERIE (tela /modo-treino) — mesma semantica
    # de exercises.muscle_pain/joint_pain, agora na granularidade da serie.
    # exercises.muscle_pain/joint_pain viram um rollup derivado destes
    # valores, recalculado em _refresh_exercise_aggregates.
    muscle_pain: Optional[int] = Field(default=None, ge=0, le=10)
    joint_pain: Optional[bool] = None
    # Fase 5: vínculo da aplicação de técnica (mini-bloco, drop, reps além da
    # falha) com o catálogo e com a série de trabalho que a originou. Vazios
    # numa aplicação (Zepp, cliente antigo) -> derivados do `set_type`, da
    # técnica do item da ficha e da última série de trabalho do exercício.
    technique_code: Optional[str] = Field(default=None, max_length=20)
    anchor_set_number: Optional[int] = Field(default=None, ge=1)
    # Chave de idempotência do cliente (fila do relógio). Repetir a chave no
    # mesmo exercício da sessão devolve a série já gravada (`duplicate: true`).
    client_key: Optional[str] = _client_key_field()
    # Medidas de série (Lote T3, colunas da 089). Ausente = `reps`: o relógio
    # antigo só manda reps, e a medida NUNCA é deduzida do item da ficha.
    # `duration`: segundos em `duration_seconds`, reps nulas; `max_reps`: reps
    # obrigatórias e `duration_seconds` = janela usada. Forma pelo dono
    # (`set_measures.validate_set_measure`) -> 422.
    measure: Optional[str] = Field(default=None, max_length=16)
    duration_seconds: Optional[int] = None
    # Modo Intensivo (colunas da 092). `reps_pending=true`: série concluída
    # com um toque, reps a confirmar depois (PATCH da série); exige `reps`
    # vazias e medida de repetições. Até a confirmação nenhum motor a vê
    # (`set_types.sql_set_confirmed`). `completed_at`: hora real da conclusão
    # (aware -> hora de parede do usuário, como `started_at`).
    # `planned_weight_kg`: carga planejada para a série depois dos ajustes
    # (`weight_kg` = executada). `load_adjust_source`: protocol | fatigue |
    # manual (`session_loads.LOAD_ADJUST_SOURCES`). `rpe_target_source=
    # 'protocol'`: o servidor grava em `rpe_target_effective` o RPE-alvo do
    # protocolo da técnica (`training_safety.protocol_target_rpe`, pelo teto
    # do par); o cliente nunca manda o número, e `rpe` segue só o relatado.
    reps_pending: bool = False
    completed_at: Optional[datetime] = None
    planned_weight_kg: Optional[Decimal] = Field(default=None, ge=0)
    load_adjust_source: Optional[str] = Field(default=None, max_length=16)
    rpe_target_source: Optional[str] = Field(default=None, max_length=16)
    # M102 (colunas da 100): reps/RPE por lado em exercício unilateral. Com
    # algum lado, `reps`/`rpe` oficiais = pior lado pelo dono
    # (`domain/set_sides`); o relógio e a fila offline não mandam os lados e
    # nada muda para eles.
    reps_left: Optional[int] = Field(default=None, ge=0)
    reps_right: Optional[int] = Field(default=None, ge=0)
    rpe_left: Optional[Decimal] = Field(default=None, ge=0, le=10)
    rpe_right: Optional[Decimal] = Field(default=None, ge=0, le=10)

    @model_validator(mode="after")
    def _apply_sides(self):
        # Antes das demais validações: elas veem o canônico (pior lado).
        apply_canonical_to_model(self)
        return self

    @model_validator(mode="after")
    def _check_intensive(self):
        _ensure_known_source("load_adjust_source", self.load_adjust_source, LOAD_ADJUST_SOURCES)
        _ensure_known_source("rpe_target_source", self.rpe_target_source, RPE_TARGET_SOURCES)
        rep_measure = normalize_measure(self.measure) is SetMeasure.REPS
        if self.reps_pending:
            if self.reps is not None:
                raise ValueError("reps_pending exige reps vazias (as reps chegam pelo PATCH da série).")
            if not rep_measure:
                raise ValueError("reps_pending só vale para série de repetições.")
        # `reps` NULL sem a marca continua aceita como antes (a tela
        # /registro-exercicios pode lançar série sem reps): só a marca
        # `reps_pending` tira a série dos motores.
        return self

    @model_validator(mode="after")
    def _check_measure(self):
        # Relogio antigo que mande um `duration_seconds` solto (sem medida)
        # continua gravando reps (a chave era ignorada antes do T3): sem 422
        # que travaria a fila offline.
        if self.measure is None:
            self.duration_seconds = None
        ensure_valid_set_measure(self.measure, self.reps, self.duration_seconds, set_number=self.set_number)
        return self


def _ensure_known_source(field: str, value: Optional[str], allowed: tuple[str, ...]) -> None:
    if value is not None and value not in allowed:
        raise ValueError(f"{field} deve ser um de {', '.join(allowed)}.")


class SessionSetPatchIn(BaseModel):
    """PATCH de UMA série (Modo Intensivo): confirmar reps pendentes ou
    corrigir carga/reps/RPE sem o apaga-e-recria do `PUT /exercises/logs`.
    Campo ausente mantém o gravado; presente vale (`exercise_set_update_merge.
    merge_set_patch`). Informar `reps` confirma a série (`reps_pending` = 0)."""

    reps: Optional[int] = Field(default=None, ge=0)
    weight_kg: Optional[Decimal] = Field(default=None, ge=0)
    rpe: Optional[Decimal] = Field(default=None, ge=0, le=10)
    # M102: lados da série unilateral (mesma regra "presente vale"); com lado,
    # `reps`/`rpe` oficiais são recalculados pelo dono depois da mescla.
    reps_left: Optional[int] = Field(default=None, ge=0)
    reps_right: Optional[int] = Field(default=None, ge=0)
    rpe_left: Optional[Decimal] = Field(default=None, ge=0, le=10)
    rpe_right: Optional[Decimal] = Field(default=None, ge=0, le=10)

    @model_validator(mode="after")
    def _check_not_empty(self):
        if not self.model_fields_set:
            raise ValueError("Informe ao menos um de reps, weight_kg ou rpe.")
        return self


class SessionFinishIn(BaseModel):
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    calories_kcal: Optional[Decimal] = None
    avg_hr_bpm: Optional[Decimal] = None
    max_hr_bpm: Optional[Decimal] = None
    notes: Optional[str] = None


def _get_live_session(cur, activity_id: int, user_id: int) -> dict[str, Any]:
    """Usada por add_session_exercise/add_session_set para validar a activity
    antes de gravar um exercício/série novo.

    Aceita tanto session_status='in_progress' quanto NULL (treinos "antigos"
    ou lançados por outro fluxo — Telegram/MCP/tela web manual — que nunca
    passaram por POST /workout-sessions/start). Esses endpoints são os únicos
    que a tela /registro-exercicios usa para lançar QUALQUER exercício novo
    numa activity existente, não só durante uma sessão ao vivo do relógio;
    exigir estritamente 'in_progress' bloqueava permanentemente o lançamento
    em qualquer treino legado. Só bloqueia (409) quando o treino já foi
    finalizado ('done'). Diferente de _get_finishable_activity, que aceita
    também 'done' porque finalizar é idempotente: lançar série num treino
    encerrado, não.

    Não enfraquece a proteção do app Zepp: o relógio sempre grava
    session_status='in_progress' em POST /workout-sessions/start e só muda
    para 'done' em finish, então o app Zepp nunca encontra uma sessão sua
    com session_status NULL; a combinação NULL só existe para treinos
    legados/manuais criados fora do fluxo de sessão ao vivo.
    """
    row = _get_owned_session(cur, activity_id, user_id)
    _ensure_not_finished(row)
    return row


def _get_owned_session(cur, activity_id: int, user_id: int) -> dict[str, Any]:
    """A activity do usuário (404 se não for dele), sem olhar o status.

    Usada direto só no caminho com `client_key`: a repetição de um lançamento
    que JÁ foi gravado devolve o registro mesmo que o treino tenha sido
    finalizado depois (a fila do relógio pode reenviar a série após o
    `finish`); um lançamento NOVO continua passando por `_ensure_not_finished`.
    """
    cur.execute(
        "SELECT id, gym_id, session_status, started_at FROM activities WHERE id = %s AND user_id = %s",
        (activity_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Sessão de treino não encontrada.")
    return row


def _ensure_not_finished(session: dict[str, Any]) -> None:
    if session.get("session_status") == "done":
        raise HTTPException(status_code=409, detail="Este treino já foi finalizado.")


def _find_exercise_by_client_key(cur, session_id: int, client_key: str, *, locking: bool = False):
    """Exercício já gravado com esta chave nesta sessão (`uq_exercises_activity_client_key`).

    `locking=True` depois de um INSERT recusado pela UNIQUE: a leitura
    travante enxerga a linha que a outra transação acabou de confirmar (a
    leitura comum ficaria no snapshot do REPEATABLE READ, anterior a ela).
    """
    cur.execute(
        "SELECT id, sequence, mapping_id, equipment_id, workout_plan_exercise_id FROM exercises "
        "WHERE activity_id = %s AND client_key = %s" + (" FOR UPDATE" if locking else ""),
        (session_id, client_key),
    )
    return cur.fetchone()


# Colunas do Modo Intensivo (092) devolvidas na resposta da série.
_INTENSIVE_SET_COLUMNS = "reps_pending, completed_at, planned_weight_kg, load_adjust_source, rpe_target_source"
# M102 (colunas da 100): lados da série unilateral, na ordem do dono.
_SIDE_SET_COLUMNS = ", ".join(SIDE_FIELDS)


def _find_set_by_client_key(cur, exercise_id: int, client_key: str, *, locking: bool = False):
    """Série já gravada com esta chave neste exercício (`uq_exercise_sets_exercise_client_key`).
    `locking`: ver `_find_exercise_by_client_key`."""
    cur.execute(
        "SELECT set_number, set_type, reps, weight_kg, rpe, muscle_pain, joint_pain, "
        "rpe_target_effective, safety_event, technique_code, anchor_set_number, measure, duration_seconds, "
        f"{_INTENSIVE_SET_COLUMNS}, {_SIDE_SET_COLUMNS} "
        "FROM exercise_sets "
        "WHERE exercise_id = %s AND client_key = %s" + (" FOR UPDATE" if locking else ""),
        (exercise_id, client_key),
    )
    return cur.fetchone()


def _get_finishable_activity(cur, activity_id: int, user_id: int) -> dict[str, Any]:
    """Variante permissiva de _get_live_session usada apenas por finish_session.

    Diferente de _get_live_session (exigida por add_session_exercise/
    add_session_set, que só fazem sentido durante uma sessão ao vivo
    'in_progress'), finalizar um treino também deve funcionar para
    activities "antigas" com session_status NULL — treinos criados antes da
    migração v7 ou importados fora do fluxo de sessão ao vivo (Telegram/MCP),
    que nunca passaram por POST /workout-sessions/start mas ainda são
    treinos válidos que o usuário pode querer marcar como concluídos.
    Diferente de _get_live_session, NÃO bloqueia um treino já 'done':
    finalizar é idempotente (ver finish_session). Só 404 para activity
    inexistente ou de outro usuário.
    """
    cur.execute(
        """
        SELECT id, gym_id, session_status, started_at, ended_at, duration_seconds, workout_plan_id
        FROM activities WHERE id = %s AND user_id = %s
        """,
        (activity_id, user_id),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Sessão de treino não encontrada.")
    return row


def _refresh_exercise_aggregates(cur, exercise_id: int) -> None:
    cur.execute(
        f"""
        SELECT set_type, reps, weight_kg, rpe, measure FROM exercise_sets
        WHERE exercise_id = %s AND {sql_set_confirmed("reps_pending")} ORDER BY set_number
        """,
        (exercise_id,),
    )
    # Série com reps pendentes (Modo Intensivo) fica fora de tudo abaixo até a
    # confirmação, inclusive do rollup de dor.
    # compute_exercise_stats ignora warmup/feeler nos agregados
    # (sets_count/reps_total/volume_kg/min/best_weight/avg_rpe). O rollup de
    # dor abaixo continua considerando TODAS as series.
    stats = compute_exercise_stats(cur.fetchall())

    # Rollup de dor por serie -> exercises.muscle_pain/joint_pain (decisao de
    # arquitetura do plano "Modo de Treino - previa/dor por serie": mantem os
    # campos legados de exercises sempre sincronizados com o agregado das
    # series reais, para NAO precisar alterar volume_service.py/
    # muscle_recovery_service.py/workout_plan_ai.py, que continuam lendo
    # exclusivamente de exercises.muscle_pain/joint_pain. MAX() ignora NULL;
    # se nenhuma serie deste exercicio tiver dor informada, o rollup fica
    # NULL e o COALESCE abaixo preserva o valor gravado diretamente no
    # exercicio (ex: fluxo /registro-exercicios, sem dor por serie).
    cur.execute(
        "SELECT MAX(muscle_pain) AS muscle_pain, MAX(joint_pain) AS joint_pain FROM exercise_sets "
        f"WHERE exercise_id = %s AND {sql_set_confirmed('reps_pending')}",
        (exercise_id,),
    )
    pain_rollup = cur.fetchone() or {}

    cur.execute(
        """
        UPDATE exercises
        SET sets_count = %s, reps_total = %s, volume_kg = %s,
            min_weight_kg = %s, best_weight_kg = %s, avg_rpe = %s,
            muscle_pain = COALESCE(%s, muscle_pain),
            joint_pain = COALESCE(%s, joint_pain)
        WHERE id = %s
        """,
        (
            stats["sets_count"],
            stats["reps_total"],
            stats["volume_kg"],
            stats["min_weight_kg"],
            stats["best_weight_kg"],
            stats["avg_rpe"],
            pain_rollup.get("muscle_pain"),
            pain_rollup.get("joint_pain"),
            exercise_id,
        ),
    )


def _iso_or_none(value: Any) -> Optional[str]:
    """`ended_at` como string ISO na resposta. Aceita datetime (os dois
    backends devolvem datetime hoje) e string sem quebrar."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _refresh_activity_avg_rpe(cur, activity_id: int) -> None:
    cur.execute(
        "SELECT AVG(avg_rpe) AS avg_rpe FROM exercises WHERE activity_id = %s AND avg_rpe IS NOT NULL",
        (activity_id,),
    )
    row = cur.fetchone()
    cur.execute(
        "UPDATE activities SET avg_rpe = %s WHERE id = %s",
        (row["avg_rpe"], activity_id),
    )


@router.post("/workout-sessions/start")
def start_session(payload: SessionStartIn, identity: Identity = Depends(get_current_identity)):
    ext_id = f"zepp-session-{uuid.uuid4().hex}"

    with transaction() as conn:
        # `started_at` na hora de PAREDE do usuario, nunca `datetime.now()`:
        # a Lambda roda em UTC, e um started_at em UTC (bug de 15/09/2026,
        # atividades 219+) fica 3 h fora da janela de 3600 s do casamento de
        # FC com o Intervals (`intervals_hr_match`) e ainda empurra treino
        # depois das 21 h para o `local_date` do dia seguinte.
        timezone_name = user_timezone(conn, identity.user_id)
        started_at = _to_user_wall_clock(payload.started_at, timezone_name) or user_now(timezone_name)
        local_date = payload.local_date or started_at.date()
        sid = source_id(conn, payload.source_name, "wearable")
        with conn.cursor() as cur:
            gym_id = resolve_gym_id(cur, payload.gym_id, payload.gym_name)

            if payload.workout_plan_id:
                cur.execute(
                    "SELECT id FROM workout_plans WHERE id = %s AND user_id = %s",
                    (payload.workout_plan_id, identity.user_id),
                )
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="Plano de treino não encontrado.")

            # M97 D: origem do deload pelo dono (período ativo na data da
            # sessão, no fuso do usuário, vence a marca avulsa). Sem deload, o
            # INSERT é o de sempre (a coluna da 094 só entra quando há valor).
            period = deload_periods.active_period_on_safe(cur, identity.user_id, local_date)
            deload_source = session_deload_source(manual=payload.deload, period_active=period is not None)
            deload_column, deload_value, deload_params = (
                (", deload_source", ",%s", (deload_source,)) if deload_source is not None else ("", "", ())
            )
            cur.execute(
                f"""
                INSERT INTO activities (
                  source_id, external_id, user_id, gym_id, training_type_id, local_date, started_at,
                  activity_type, name, notes, conducted_by, workout_plan_id, session_status{deload_column}
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'in_progress'{deload_value})
                """,
                (
                    sid,
                    ext_id,
                    identity.user_id,
                    gym_id,
                    payload.training_type_id,
                    local_date,
                    started_at,
                    payload.activity_type or "musculacao",
                    payload.name,
                    payload.notes,
                    payload.conducted_by,
                    payload.workout_plan_id,
                    *deload_params,
                ),
            )
            session_id = cur.lastrowid

    return {
        "status": "ok",
        "session_id": session_id,
        "external_id": ext_id,
        "gym_id": gym_id,
        "training_type_id": payload.training_type_id,
        "started_at": started_at.isoformat(),
        # M97 D: deload da sessão (ativo, origem e período) para o Modo Treino.
        "deload": deload_periods.deload_status(deload_source, period),
    }


class SessionMetaIn(BaseModel):
    name: Optional[str] = None
    workout_plan_id: Optional[int] = None
    gym_id: Optional[int] = None
    gym_name: Optional[str] = None
    training_type_id: Optional[int] = None
    conducted_by: Optional[str] = None
    local_date: Optional[date] = None
    started_at: Optional[datetime] = None
    # Nutrição L0 (2026-10-15): kcal gasta informada à mão pelo dono do treino.
    # Ausente = não mexe; `null` EXPLÍCITO limpa kcal e origem (o sync do
    # Intervals volta a preencher por COALESCE). A origem gravada vem do dono
    # (`domain/calories_source.source_on_manual_write`).
    calories_kcal: Optional[Decimal] = Field(default=None, ge=0)
    # M97 F3: liga (`true`) ou desliga (`false`) o deload de uma sessao JA
    # iniciada. Ausente/`null` = nao mexe. So muda enquanto a sessao nao tem
    # serie gravada (regra no dono `domain/deload.toggled_deload_source`;
    # senao 409 `deload_locked`).
    deload: Optional[bool] = None


# Series gravadas na sessao (qualquer tipo): a trava do deload (`can_toggle_deload`).
SESSION_SAVED_SETS_SQL = (
    "SELECT COUNT(*) AS n FROM exercise_sets s JOIN exercises e ON e.id = s.exercise_id WHERE e.activity_id = %s"
)
SESSION_DELOAD_SQL = (
    f"SELECT a.{DELOAD_SOURCE_COLUMN} AS deload_source, a.local_date AS local_date FROM activities a "
    "WHERE a.id = %s AND a.user_id = %s"
)


def _session_deload_row(cur, session_id: int, user_id: int) -> dict[str, Any]:
    cur.execute(SESSION_DELOAD_SQL, (session_id, user_id))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Sessão de treino não encontrada.")
    cur.execute(SESSION_SAVED_SETS_SQL, (session_id,))
    saved = (cur.fetchone() or {}).get("n") or 0
    return {**row, "saved_sets": int(saved)}


def _session_deload_status(cur, user_id: int, row: dict[str, Any], source: Optional[str]) -> dict[str, Any]:
    """Bloco `deload` da sessao (o mesmo do `start`) + se ainda pode mudar.
    O periodo so aparece quando e ele a origem gravada (desligado = sessao normal)."""
    period = None
    if source == DeloadSource.PERIOD.value and row.get("local_date") is not None:
        period = deload_periods.active_period_on_safe(cur, user_id, row["local_date"])
    status = deload_periods.deload_status(source, period)
    status["can_toggle"] = can_toggle_deload(row.get("saved_sets"))
    return status


@router.get("/workout-sessions/{session_id}/deload")
def get_session_deload(session_id: int, identity: Identity = Depends(get_current_identity)):
    """M97 F3: deload da sessao em andamento (origem gravada no `start` ou no
    PATCH meta) e se ainda pode ser ligado/desligado (sem serie gravada)."""
    with transaction() as conn:
        with conn.cursor() as cur:
            row = _session_deload_row(cur, session_id, identity.user_id)
            source = deload_source_value(row.get("deload_source"))
            status = _session_deload_status(cur, identity.user_id, row, source)
    return {"session_id": session_id, "deload": status}


@router.patch("/workout-sessions/{session_id}/meta")
def update_session_meta(session_id: int, payload: SessionMetaIn, identity: Identity = Depends(get_current_identity)):
    """Edita apenas os metadados do cabeçalho do treino (nome, academia, data/hora,
    condutor, ficha vinculada). Não mexe em exercises/exercise_sets. Diferente de
    PUT /activities/{id} (delete+recreate de exercícios via ActivityIn), este endpoint
    é seguro para editar um treino já com exercícios lançados. Funciona mesmo depois
    de "Finalizar treino" (session_status='done') — corrigir metadados de um treino
    já concluído é um caso de uso válido.
    """
    fields = payload.model_dump(exclude_unset=True)

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, gym_id FROM activities WHERE id = %s AND user_id = %s",
                (session_id, identity.user_id),
            )
            existing = cur.fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="Sessão de treino não encontrada.")

            updates: dict[str, Any] = {}

            if "name" in fields and fields["name"] is not None:
                updates["name"] = fields["name"]

            if "gym_id" in fields or "gym_name" in fields:
                gym_id = resolve_gym_id(cur, fields.get("gym_id"), fields.get("gym_name"))
                if gym_id is not None:
                    updates["gym_id"] = gym_id

            if "training_type_id" in fields:
                updates["training_type_id"] = fields["training_type_id"]

            if "conducted_by" in fields and fields["conducted_by"] is not None:
                updates["conducted_by"] = fields["conducted_by"]

            if "local_date" in fields and fields["local_date"] is not None:
                updates["local_date"] = fields["local_date"]

            if "started_at" in fields and fields["started_at"] is not None:
                started_at = fields["started_at"]
                if started_at.tzinfo is not None:
                    # Com fuso -> hora de parede do usuario (convencao do
                    # banco). Naive segue como veio; so consulta o fuso aqui.
                    started_at = _to_user_wall_clock(started_at, user_timezone(conn, identity.user_id))
                updates["started_at"] = started_at

            if "calories_kcal" in fields:
                kcal = fields["calories_kcal"]
                cur.execute(
                    "SELECT calories_kcal FROM activity_intervals_link WHERE activity_id = %s",
                    (session_id,),
                )
                link = cur.fetchone()
                updates["calories_kcal"] = kcal
                updates["calories_source"] = source_on_manual_write(kcal, link["calories_kcal"] if link else None)

            if "workout_plan_id" in fields:
                workout_plan_id = fields["workout_plan_id"]
                if workout_plan_id is not None:
                    cur.execute(
                        "SELECT id FROM workout_plans WHERE id = %s AND user_id = %s",
                        (workout_plan_id, identity.user_id),
                    )
                    if not cur.fetchone():
                        raise HTTPException(status_code=404, detail="Plano de treino não encontrado.")
                updates["workout_plan_id"] = workout_plan_id

            deload_block: Optional[dict[str, Any]] = None
            if fields.get("deload") is not None:
                # M97 F3: liga/desliga o deload so sem serie gravada (dono
                # `domain/deload.toggled_deload_source`). Ligar segue a regra
                # do `start`: periodo ativo na data da sessao vence a marca avulsa.
                deload_row = _session_deload_row(cur, session_id, identity.user_id)
                local_date = updates.get("local_date") or deload_row.get("local_date")
                period = (
                    deload_periods.active_period_on_safe(cur, identity.user_id, local_date)
                    if fields["deload"] and local_date is not None
                    else None
                )
                current = deload_source_value(deload_row.get("deload_source"))
                try:
                    new_source = toggled_deload_source(
                        current,
                        requested=bool(fields["deload"]),
                        saved_sets=deload_row["saved_sets"],
                        period_active=period is not None,
                    )
                except DeloadToggleLocked as exc:
                    raise HTTPException(
                        status_code=409, detail={"error": "deload_locked", "message": str(exc)}
                    ) from exc
                if new_source != current:
                    updates[DELOAD_SOURCE_COLUMN] = new_source
                deload_block = _session_deload_status(
                    cur, identity.user_id, {**deload_row, "local_date": local_date}, new_source
                )

            if updates:
                set_clause = ", ".join(f"{column} = %s" for column in updates)
                cur.execute(
                    f"UPDATE activities SET {set_clause} WHERE id = %s",
                    (*updates.values(), session_id),
                )

    out: dict[str, Any] = {"status": "ok", "session_id": session_id, "updated_fields": list(updates.keys())}
    if deload_block is not None:
        out["deload"] = deload_block
    return out


def _exercise_response(session_id: int, row: dict[str, Any], client_key: Optional[str], duplicate: bool) -> dict[str, Any]:
    return {
        "status": "ok",
        "session_id": session_id,
        "exercise_id": row["id"],
        "sequence": row["sequence"],
        "mapping_id": row["mapping_id"],
        "equipment_id": row["equipment_id"],
        "workout_plan_exercise_id": row["workout_plan_exercise_id"],
        "client_key": client_key,
        # True = esta chamada repetiu uma `client_key` já gravada: nada foi
        # criado e o registro devolvido é o da primeira gravação.
        "duplicate": duplicate,
    }


def _client_key_insert_parts(client_key: Optional[str]) -> tuple[str, str, tuple]:
    """(coluna, placeholder, parâmetro) da chave no INSERT. Sem chave, tudo
    vazio: o INSERT é o de antes e não depende da coluna da 087 (se o deploy
    subir o código sem a migração, só o caminho com chave falha)."""
    if client_key is None:
        return "", "", ()
    return ", client_key", ",%s", (client_key,)


def _existing_after_unique_violation(exc: Exception, client_key: Optional[str], find) -> Optional[dict[str, Any]]:
    """Corrida entre dois envios da mesma chave: o INSERT do segundo bate na
    UNIQUE da 087. Relê (leitura travante) e devolve o registro do primeiro;
    qualquer outro erro, ou violação de outra UNIQUE (ex.: `set_number`
    repetido com chave diferente), devolve None e o chamador relança."""
    if client_key is None or not is_duplicate_key_error(exc):
        return None
    return find()


@router.post("/workout-sessions/{session_id}/exercises")
def add_session_exercise(
    session_id: int,
    payload: SessionExerciseIn,
    identity: Identity = Depends(get_current_identity),
):
    client_key = payload.client_key
    with transaction() as conn:
        with conn.cursor() as cur:
            if client_key is None:
                session = _get_live_session(cur, session_id, identity.user_id)
            else:
                session = _get_owned_session(cur, session_id, identity.user_id)
                existing = _find_exercise_by_client_key(cur, session_id, client_key)
                if existing is not None:
                    return _exercise_response(session_id, existing, client_key, duplicate=True)
                _ensure_not_finished(session)

            sequence = payload.sequence
            if sequence is None:
                cur.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 AS seq FROM exercises WHERE activity_id = %s",
                    (session_id,),
                )
                sequence = cur.fetchone()["seq"]

            mapping_id = resolve_mapping_id(payload.original_name, None, payload.mapping_id, cur=cur)
            equipment_id = resolve_equipment_id(
                cur,
                mapping_id,
                session["gym_id"],
                payload.equipment_id,
                payload.equipment_name,
                user_id=identity.user_id,
            )

            # Sem chave, o INSERT nem cita a coluna da 087 (o de antes).
            key_column, key_value, key_param = _client_key_insert_parts(client_key)
            try:
                cur.execute(
                    f"""
                    INSERT INTO exercises (
                      activity_id, sequence, original_name, mapping_id, workout_plan_exercise_id,
                      equipment_id, sets_count, notes, muscle_pain, joint_pain, source_row_hash,
                      training_goal{key_column}
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,{SQL_PLAN_EXERCISE_GOAL}{key_value})
                    """,
                    (
                        session_id,
                        sequence,
                        payload.original_name,
                        mapping_id,
                        payload.workout_plan_exercise_id,
                        equipment_id,
                        payload.notes,
                        payload.muscle_pain,
                        payload.joint_pain,
                        f"zepp-live-{uuid.uuid4().hex}",
                        # Objetivo de treino (M97 F0): o da linha da ficha de
                        # origem; treino livre = default (dono `training_goal`).
                        *plan_exercise_goal_params(payload.workout_plan_exercise_id),
                        *key_param,
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - só a corrida da mesma chave vira resposta
                existing = _existing_after_unique_violation(
                    exc, client_key, lambda: _find_exercise_by_client_key(cur, session_id, client_key, locking=True)
                )
                if existing is None:
                    raise
                return _exercise_response(session_id, existing, client_key, duplicate=True)
            exercise_id = cur.lastrowid

    return _exercise_response(
        session_id,
        {
            "id": exercise_id,
            "sequence": sequence,
            "mapping_id": mapping_id,
            "equipment_id": equipment_id,
            "workout_plan_exercise_id": payload.workout_plan_exercise_id,
        },
        client_key,
        duplicate=False,
    )


def _session_fatigue_band(conn, user_id: Any, session_id: int, exercise_id: int):
    """Faixa de fadiga prévia do exercício na sessão REAL (dono
    `domain/prior_fatigue.py`, leitor `session_fatigue_reader`): a de ANTES
    da 1ª série do exercício, a mesma chave da trilha. Falha de leitura ->
    None (faixa desconhecida: só regras "qualquer fadiga" casam)."""
    prior = safe_read(
        load_session_prior_fatigue,
        conn,
        user_id,
        [session_id],
        default={},
        event="add_session_set.prior_fatigue_failed",
    )
    item = (prior or {}).get(int(exercise_id))
    return item.band if item is not None else None


def _evaluate_set(
    conn,
    cur,
    exercise_id: int,
    exercise_row: dict[str, Any],
    intensity,
    *,
    user_id: Any,
    session_id: int,
    set_type: str,
    set_number: int,
    reps: Any,
    weight_kg: Any,
    rpe: Any,
    joint_pain: Any,
    muscle_pain: Any,
    measure: Any = None,
    reps_pending: bool = False,
    protocol_target: Any = None,
):
    """Leitura do motor para UMA série: status/reps contra a ficha
    (`effective_intensity.set_intensity_reading`) e carga da próxima série
    (`next_set_load`). Nada aqui grava: a série nova e a repetição por
    `client_key` (com as entradas GRAVADAS) chamam a mesma função, então as
    duas respostas saem das mesmas regras dos donos.

    Medidas de série (Lote T3): série por tempo ou por máximo de reps
    (`set_measures.is_rep_based` falso) sai com `reps_position` e
    `next_set_load` nulos - os dois donos recebem a medida e decidem; o status
    de esforço pelo RPE continua.

    M96 (veredito único no card da série): o status é cobrado contra o alvo
    DESTA série - o degrau da rampa planejada (o mesmo da carga da próxima
    série) ou, com `rpe_target_source='protocol'` (`protocol_target`), o alvo
    do protocolo gravado na série - e a carga da próxima série recebe esse
    status para escolher o motivo do MANTER."""
    # Carga da PROXIMA serie (regra do usuario de 2026-10-12, dono
    # `domain/next_set_load.py`): passo e convencao do par lidos aqui;
    # falha de leitura -> passo default/`total` (nunca derruba a serie).
    # Nao e persistida: e derivavel da serie gravada.
    # Fatos da serie para o conjunto de regras (R1, 2026-10-20): a ordem desta
    # serie entre as series de trabalho ja gravadas do exercicio (1-based), a
    # rampa PLANEJADA de RPE (`next_set_load.planned_ramp_rpe`: ficha congelada,
    # senao a coluna da ficha; sem ficha, sem rampa), a faixa de fadiga previa
    # do exercicio e o teto efetivo da serie. Serie que nao e de trabalho fica
    # fora da regra: nada e lido para ela.
    # R2 (migracao 088): o conjunto PUBLICADO (`next_set_load_rules`, 1 SELECT;
    # sem linha/falha -> `DEFAULT_RULESET`), e a faixa de fadiga so e lida se
    # alguma regra dele a exige (`ruleset_uses_fatigue`, do dono): o padrao nao
    # exige, entao a leitura da fadiga por serie do R1 sai do caminho.
    working_set_index = None
    planned_rpe = None
    fatigue_band = None
    ruleset = DEFAULT_RULESET
    # Modo Intensivo: serie com reps PENDENTES nao alimenta motor nenhum ate
    # a confirmacao - sem reps nao ha carga da proxima serie a decidir.
    # Lote T3: serie fora de `reps` nao tem carga da proxima serie (o contexto
    # devolve None) - nada e lido para ela.
    if not reps_pending and set_type == WORKING and is_rep_based(measure):
        # M97 F2: o conjunto do OBJETIVO do item em execucao (dono
        # `next_set_load.ruleset_for_goal`): hipertrofia = o publicado, como
        # sempre; forca = o conjunto de forca (sem leitura do publicado).
        goal = effective_intensity.plan_training_goal(exercise_row.get("prescription_json"))
        published = (
            safe_read(
                load_published_ruleset, conn, default=DEFAULT_RULESET, event="add_session_set.next_set_ruleset_failed"
            )
            if ruleset_uses_published(goal)
            else None
        )
        ruleset = ruleset_for_goal(goal, published)
        # Posicao ORDINAL da serie entre as de trabalho (rampa): conta tambem
        # as pendentes, que ocupam a posicao delas no bloco (nao le reps/carga).
        cur.execute(
            "SELECT COUNT(*) AS n FROM exercise_sets "
            f"WHERE exercise_id = %s AND set_number < %s AND {sql_is_working('set_type')}",
            (exercise_id, set_number),
        )
        previous_working = int((cur.fetchone() or {}).get("n") or 0)
        working_set_index = previous_working + 1
        plan_rpe = effective_intensity.plan_rpe_target(
            exercise_row.get("prescription_json"), exercise_row.get("plan_rpe_target")
        )
        if plan_rpe is not None:
            planned_rpe = planned_ramp_rpe(plan_rpe, previous_working)
        if ruleset_uses_fatigue(ruleset):
            fatigue_band = _session_fatigue_band(conn, user_id, session_id, exercise_id)
    # RPE 10 Lote 6/P9: status da serie pelo motor (mesma regra da
    # sessao), devolvido ao Modo Treino junto do evento. Nao e
    # persistido: e derivavel do que ja fica gravado na serie
    # (`rpe`, `rpe_target_effective`, `safety_event`).
    # set-eval-v5: + reps da serie contra a faixa da ficha (motor).
    # M96: alvo do protocolo (Modo Intensivo) vale como esta; sem ele, o
    # degrau da rampa planejada restringe o alvo (`set_effort_target`).
    # M97 D: sessao de deload - o esforco e cobrado contra o RPE-alvo do
    # deload (dono `domain/deload.py`). Fix 2026-10-26: o alvo do protocolo
    # so vale para tecnica que o deload permite (hoje nenhuma).
    deload = is_deload_session(exercise_row.get("deload_source"))
    if deload:
        planned_rpe = None
        protocol_target = deload_effort_target(protocol_target, exercise_row.get("plan_technique"))
    reading = effective_intensity.set_intensity_reading(
        set_type,
        rpe,
        effective_intensity.with_protocol_target(intensity, protocol_target),
        reps=reps,
        reps_range=effective_intensity.plan_reps_range(
            exercise_row.get("prescription_json"), exercise_row.get("plan_reps_target")
        ),
        measure=measure,
        planned_rpe=planned_rpe if protocol_target is None else None,
    )
    # M100: serie de exercicio LABORAL nao tem avaliacao nem carga da proxima
    # serie (so conta no tempo de treino) - a leitura acima ja e neutra para
    # serie que nao e de trabalho.
    if reps_pending or is_laboral_set(set_type):
        return reading, None
    pair = safe_read(
        load_pair_step_context,
        conn,
        exercise_row.get("mapping_id"),
        exercise_row.get("gym_equipment_id"),
        event="add_session_set.next_set_context_failed",
    )
    if deload:
        # M97 D: a carga da proxima serie vem do dono do deload (60% da trilha).
        # Falha ao ler o par -> passo default (como a regra normal).
        pair = pair or PairEngineContext(step_kg=equipment_step_kg(None))
        pair = replace(
            pair,
            deload=load_deload_prescription(
                conn,
                user_id,
                exercise_row.get("mapping_id"),
                exercise_row.get("gym_equipment_id"),
                exercise_row.get("prescription_json"),
                pair.step_kg,
                exercise_row.get("plan_reps_target"),
                plan_row=exercise_row,
            ),
        )
    next_set = next_set_load_for(
        {
            "set_type": set_type,
            "reps": reps,
            "weight_kg": weight_kg,
            "rpe": rpe,
            "joint_pain": joint_pain,
            "muscle_pain": muscle_pain,
            "measure": measure,
        },
        pair,
        planned_rpe=planned_rpe,
        working_set_index=working_set_index,
        fatigue_band=fatigue_band,
        rpe_ceiling=intensity.ceiling,
        ruleset=ruleset,
        intensity_status=reading.status,
    )
    return reading, next_set


def _set_response(
    session_id: int,
    exercise_id: int,
    *,
    set_number: int,
    technique_code: Optional[str],
    anchor_set_number: Optional[int],
    rpe_target_effective: Any,
    safety_event: Optional[str],
    intensity,
    reading,
    next_set,
    client_key: Optional[str],
    duplicate: bool,
    intensive: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    intensive = intensive or {}
    return {
        "status": "ok",
        "session_id": session_id,
        "exercise_id": exercise_id,
        "set_number": set_number,
        "technique_code": technique_code,
        "anchor_set_number": anchor_set_number,
        # O cliente ve o que o servidor decidiu sobre a intensidade desta
        # serie (secao 5.7: o evento aparece na UI).
        "rpe_target_effective": rpe_target_effective,
        "safety_event": safety_event,
        "safety_event_label": safety_event_label(safety_event),
        "effective_rpe_ceiling": intensity.ceiling,
        "failure_allowed": intensity.failure_allowed,
        # RPE 10 Lote 6/P9: o status do motor para ESTA serie e o seu rotulo
        # (dono `domain/intensity_status_labels.py`). `None` em serie
        # preparatoria/aplicacao de tecnica e em treino livre sem alvo.
        "intensity_status": reading.status,
        "intensity_status_label": intensity_status_label(reading.status),
        # set-eval-v5: reps contra a faixa da ficha (`progression_engine.
        # set_reps_position`); `None` sem faixa ou em serie nao-working.
        "reps_position": reading.reps_position,
        "reps_position_label": reps_position_label(reading.reps_position),
        # Carga da proxima serie pelo motor (direcao, carga no passo, motivo):
        # o Modo Treino mostra e pre-preenche com ela, com ou sem IA. `None`
        # fora da regra (serie nao-working, sem carga, assistido/peso corporal).
        "next_set_load": next_set.to_dict() if next_set is not None else None,
        "client_key": client_key,
        # True = esta chamada repetiu uma `client_key` já gravada: nada foi
        # gravado de novo (série, agregados, eventos de segurança).
        "duplicate": duplicate,
        # Modo Intensivo (092): o GRAVADO da série, para a retomada.
        **_intensive_fields(intensive),
        # M102: reps/RPE OFICIAIS gravados (pior lado quando há lados) e os
        # lados; a tela usa estes, nunca recalcula.
        **_logged_values(intensive),
    }


def _number_or_none(value: Any) -> Optional[float]:
    return float(value) if value is not None else None


def _logged_values(row: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {"reps": row.get("reps"), "rpe": _number_or_none(row.get("rpe"))}
    for field in SIDE_FIELDS:
        value = row.get(field)
        values[field] = _number_or_none(value) if field.startswith("rpe") else value
    values["sides_text"] = sides_text(row)
    return values


def _intensive_fields(row: dict[str, Any]) -> dict[str, Any]:
    planned = row.get("planned_weight_kg")
    return {
        "reps_pending": is_pending_set(row),
        "completed_at": _iso_or_none(row.get("completed_at")),
        "planned_weight_kg": float(planned) if planned is not None else None,
        "load_adjust_source": row.get("load_adjust_source"),
        "rpe_target_source": row.get("rpe_target_source"),
    }


def _duplicate_set_response(
    conn, cur, session_id: int, exercise_id: int, exercise_row, stored, client_key: str, *, user_id: Any
):
    """Resposta da série repetida por `client_key`, sem gravar nada (nem
    agregados, nem `log_event` de clamp/segurança, que já saíram na 1ª vez).

    - `set_number`, `technique_code`, `anchor_set_number`,
      `rpe_target_effective` e `safety_event` (e o rótulo) são os GRAVADOS;
    - `intensity_status`, `reps_position` e `next_set_load` saem do motor
      (`_evaluate_set`) com as entradas GRAVADAS da série (tipo, número,
      reps, carga, RPE, dor), não com o corpo reenviado;
    - a intensidade do par é relida agora de `effective_intensity.
      resolve_for_logged_exercise`, como na primeira chamada.

    Se algo mudou entre o envio e a repetição, podem diferir da 1ª resposta:
    teto, `failure_allowed`, `intensity_status`, `reps_position` e
    `next_set_load`. As causas são recuperação/overlay do dia, prescrição ou
    ficha, passo/convenção do par, e séries de trabalho de número menor
    gravadas depois (rampa). `safety_event` é sempre o gravado e pode não
    bater com o status relido (mapa §58).
    """
    intensity = effective_intensity.resolve_for_logged_exercise(conn, exercise_id)
    reading, next_set = _evaluate_set(
        conn,
        cur,
        exercise_id,
        exercise_row,
        intensity,
        user_id=user_id,
        session_id=session_id,
        set_type=stored["set_type"],
        set_number=stored["set_number"],
        reps=stored["reps"],
        weight_kg=stored["weight_kg"],
        rpe=stored["rpe"],
        joint_pain=stored["joint_pain"],
        muscle_pain=stored["muscle_pain"],
        measure=stored.get("measure"),
        reps_pending=is_pending_set(stored),
        protocol_target=_stored_protocol_target(stored),
    )
    return _set_response(
        session_id,
        exercise_id,
        set_number=stored["set_number"],
        technique_code=stored["technique_code"],
        anchor_set_number=stored["anchor_set_number"],
        rpe_target_effective=stored["rpe_target_effective"],
        safety_event=stored["safety_event"],
        intensity=intensity,
        reading=reading,
        next_set=next_set,
        client_key=client_key,
        duplicate=True,
        intensive=stored,
    )


def _session_exercise_row(cur, session_id: int, exercise_id: int) -> dict[str, Any]:
    """O exercício da sessão com o item da ficha (técnica, faixa, prescrição
    congelada), o par e a origem do deload da sessão (M97 D); 404 fora da
    sessão."""
    cur.execute(
        """
        SELECT e.id, wpe.advanced_technique AS plan_technique,
               wpe.reps_target AS plan_reps_target, wpe.prescription_json,
               wpe.rpe_target AS plan_rpe_target,
               wpe.mapping_id AS plan_mapping_id, wpe.preferred_equipment_id AS plan_gym_equipment_id,
               wpe.training_goal AS plan_training_goal,
               COALESCE(e.mapping_id, wpe.mapping_id) AS mapping_id,
               e.equipment_id AS gym_equipment_id,
               a.deload_source AS deload_source
        FROM exercises e
        LEFT JOIN workout_plan_exercises wpe ON wpe.id = e.workout_plan_exercise_id
        JOIN activities a ON a.id = e.activity_id
        WHERE e.id = %s AND e.activity_id = %s
        """,
        (exercise_id, session_id),
    )
    exercise_row = cur.fetchone()
    if not exercise_row:
        raise HTTPException(status_code=404, detail="Exercício não encontrado nesta sessão.")
    return exercise_row


def _stored_protocol_target(stored: dict[str, Any]) -> Any:
    """Alvo do protocolo GRAVADO numa série com `rpe_target_source='protocol'`
    (repetição por `client_key` e confirmação das reps pendentes): o status é
    relido contra ele, como no POST (M96). Outra origem: None."""
    if stored.get("rpe_target_source") != RPE_TARGET_SOURCE_PROTOCOL:
        return None
    return stored.get("rpe_target_effective")


def _protocol_rpe_target(conn, exercise_row: dict[str, Any], set_type: str, technique_code: Optional[str]) -> int:
    """`rpe_target_effective` de uma série com `rpe_target_source='protocol'`:
    só em série de técnica (código enviado/derivado ou técnica do item da
    ficha), nunca em preparatória. O número é do dono
    (`training_safety.protocol_target_rpe`, via `effective_intensity`)."""
    if is_preparatory_set(set_type):
        raise HTTPException(status_code=422, detail="Série preparatória não tem RPE-alvo de protocolo.")
    if not (technique_code or (exercise_row.get("plan_technique") or "").strip()):
        raise HTTPException(status_code=422, detail="RPE-alvo de protocolo exige uma técnica avançada.")
    return effective_intensity.protocol_target_for_logged_exercise(
        conn, exercise_row.get("mapping_id"), exercise_row.get("gym_equipment_id"), exercise_row.get("prescription_json")
    )


@router.post("/workout-sessions/{session_id}/exercises/{exercise_id}/sets")
def add_session_set(
    session_id: int,
    exercise_id: int,
    payload: SessionSetIn,
    identity: Identity = Depends(get_current_identity),
):
    technique_code = (payload.technique_code or "").strip() or None
    # Fora da transação: o catálogo tem cache próprio e abre a sua leitura.
    ensure_valid_codes([technique_code])
    client_key = payload.client_key

    with transaction() as conn:
        with conn.cursor() as cur:
            if client_key is None:
                session = _get_live_session(cur, session_id, identity.user_id)
            else:
                session = _get_owned_session(cur, session_id, identity.user_id)

            exercise_row = _session_exercise_row(cur, session_id, exercise_id)

            if client_key is not None:
                stored = _find_set_by_client_key(cur, exercise_id, client_key)
                if stored is not None:
                    return _duplicate_set_response(
                        conn, cur, session_id, exercise_id, exercise_row, stored, client_key, user_id=identity.user_id
                    )
                _ensure_not_finished(session)

            set_number = payload.set_number
            if set_number is None:
                cur.execute(
                    "SELECT COALESCE(MAX(set_number), 0) + 1 AS n FROM exercise_sets WHERE exercise_id = %s",
                    (exercise_id,),
                )
                set_number = cur.fetchone()["n"]

            set_type = normalize_set_type(payload.set_type)
            # M100: serie de exercicio LABORAL e sempre `laboral` (preparatoria),
            # venha de onde vier (relogio, cliente antigo): o servidor nao deixa
            # ela entrar em volume, recorde ou progressao como `working`.
            if exercise_row.get("mapping_id") and int(exercise_row["mapping_id"]) in safe_read(
                load_laboral_ids, conn, [exercise_row["mapping_id"]], default=frozenset(),
                event="add_session_set.laboral_read_failed",
            ):
                set_type = LABORAL
            anchor_set_number = payload.anchor_set_number
            if set_type in TECHNIQUE_APPLICATION_SET_TYPES:
                if technique_code is None:
                    technique_code = technique_accounting.derive_technique_code(
                        set_type, exercise_row.get("plan_technique")
                    )
                if anchor_set_number is None:
                    cur.execute(
                        "SELECT set_number, set_type FROM exercise_sets WHERE exercise_id = %s",
                        (exercise_id,),
                    )
                    anchor_set_number = technique_accounting.derive_anchor_set_number(
                        set_number, ((row["set_number"], row["set_type"]) for row in cur.fetchall() or [])
                    )
            # PR-14 (secao 5.7): a intensidade EFETIVA da serie e do servidor,
            # nunca do cliente - prescricao congelada (com overlay de hoje),
            # ficha legada clampada ao vivo ou so a seguranca do par.
            intensity = effective_intensity.resolve_for_logged_exercise(conn, exercise_id)
            # Modo Intensivo: o alvo do PROTOCOLO da técnica substitui o da
            # prescrição nesta série (o `rpe` relatado continua do usuário).
            # M96: resolvido ANTES da leitura do motor, que cobra o status
            # contra ele (o mesmo número gravado e mostrado na tela).
            rpe_target_source = payload.rpe_target_source
            if (
                rpe_target_source == RPE_TARGET_SOURCE_PROTOCOL
                and is_deload_session(exercise_row.get("deload_source"))
                and not deload_allows_technique(technique_code or exercise_row.get("plan_technique"))
            ):
                # Fix M97 (2026-10-26): sessao de deload nao tem tecnica (dono
                # `domain/deload.DELOAD_ALLOWED_TECHNIQUES`); a serie e cobrada
                # e gravada contra o RPE-alvo do deload, sem protocolo.
                rpe_target_source = None
            protocol_target = (
                _protocol_rpe_target(conn, exercise_row, set_type, technique_code)
                if rpe_target_source == RPE_TARGET_SOURCE_PROTOCOL
                else None
            )
            reading, next_set = _evaluate_set(
                conn,
                cur,
                exercise_id,
                exercise_row,
                intensity,
                user_id=identity.user_id,
                session_id=session_id,
                set_type=set_type,
                set_number=set_number,
                reps=payload.reps,
                weight_kg=payload.weight_kg,
                rpe=payload.rpe,
                joint_pain=payload.joint_pain,
                muscle_pain=payload.muscle_pain,
                measure=payload.measure,
                reps_pending=payload.reps_pending,
                protocol_target=protocol_target,
            )
            safety_event = reading.safety_event
            # Lote T3: medida ausente = `reps` (já validada pelo modelo).
            measure, duration_seconds = set_measure_columns(payload.model_dump())
            # Aquecimento/ajuste nao tem alvo de esforco a cobrar: a coluna
            # fica NULL neles (serie preparatoria nunca alimenta intensidade).
            rpe_target_effective = None if is_preparatory_set(set_type) else intensity.rpe_target_effective
            if protocol_target is not None:
                rpe_target_effective = protocol_target
            elif rpe_target_effective is not None and is_deload_session(exercise_row.get("deload_source")):
                # M97 D: o alvo gravado é o do deload (o mesmo cobrado no status).
                rpe_target_effective = deload_effort_target(None)
            completed_at = None
            if payload.completed_at is not None:
                completed_at = _to_user_wall_clock(payload.completed_at, user_timezone(conn, identity.user_id))
            intensive = {
                "reps_pending": 1 if payload.reps_pending else 0,
                "completed_at": completed_at,
                "planned_weight_kg": payload.planned_weight_kg,
                "load_adjust_source": payload.load_adjust_source,
                "rpe_target_source": rpe_target_source,
                # M102: o gravado (canônico do dono + lados) vai na resposta.
                "reps": payload.reps,
                "rpe": payload.rpe,
                **{field: getattr(payload, field) for field in SIDE_FIELDS},
            }

            # Sem chave, o INSERT nem cita a coluna da 087 (o de antes).
            key_column, key_value, key_param = _client_key_insert_parts(client_key)
            try:
                cur.execute(
                    f"""
                    INSERT INTO exercise_sets (
                      exercise_id, set_number, set_type, reps, weight_kg, rpe,
                      rest_seconds, hr_avg_bpm, hr_max_bpm, muscle_pain, joint_pain, notes,
                      rpe_target_effective, safety_event, technique_code, anchor_set_number,
                      measure, duration_seconds, {_INTENSIVE_SET_COLUMNS}, {_SIDE_SET_COLUMNS}{key_column}
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s{key_value})
                    """,
                    (
                        exercise_id,
                        set_number,
                        set_type,
                        payload.reps,
                        payload.weight_kg,
                        payload.rpe,
                        payload.rest_seconds,
                        payload.hr_avg_bpm,
                        payload.hr_max_bpm,
                        payload.muscle_pain,
                        payload.joint_pain,
                        payload.notes,
                        rpe_target_effective,
                        safety_event,
                        technique_code,
                        anchor_set_number,
                        measure,
                        duration_seconds,
                        intensive["reps_pending"],
                        intensive["completed_at"],
                        intensive["planned_weight_kg"],
                        intensive["load_adjust_source"],
                        intensive["rpe_target_source"],
                        *(getattr(payload, field) for field in SIDE_FIELDS),
                        *key_param,
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - só a corrida da mesma chave vira resposta
                stored = _existing_after_unique_violation(
                    exc, client_key, lambda: _find_set_by_client_key(cur, exercise_id, client_key, locking=True)
                )
                if stored is None:
                    raise
                return _duplicate_set_response(
                    conn, cur, session_id, exercise_id, exercise_row, stored, client_key, user_id=identity.user_id
                )

            for clamp in intensity.clamps:
                log_event(
                    EVENT_SAFETY_CLAMP,
                    user_id=int(identity.user_id),
                    activity_id=session_id,
                    exercise_id=exercise_id,
                    set_number=set_number,
                    field=clamp.get("field"),
                    from_value=clamp.get("from"),
                    to_value=clamp.get("to"),
                    reason_code=clamp.get("reason"),
                )
            # Lote 1/P1: o log de observabilidade e do evento de SEGURANCA (par
            # que proibe a falha). O informativo (so a recuperacao negou a
            # falha) fica gravado na serie, sem alarme.
            if safety_event in SAFETY_ALERT_EVENTS:
                log_event(
                    EVENT_SAFETY_RPE_ABOVE_CEILING,
                    user_id=int(identity.user_id),
                    activity_id=session_id,
                    exercise_id=exercise_id,
                    set_number=set_number,
                    rpe=float(payload.rpe) if payload.rpe is not None else None,
                    effective_rpe_ceiling=intensity.ceiling,
                    reason_code=safety_event,
                    failure_denied_by=intensity.failure_denied_by,
                )

            _refresh_exercise_aggregates(cur, exercise_id)
            _refresh_activity_avg_rpe(cur, session_id)

    return _set_response(
        session_id,
        exercise_id,
        set_number=set_number,
        technique_code=technique_code,
        anchor_set_number=anchor_set_number,
        rpe_target_effective=rpe_target_effective,
        safety_event=safety_event,
        intensity=intensity,
        reading=reading,
        next_set=next_set,
        client_key=client_key,
        duplicate=False,
        intensive=intensive,
    )


@router.patch("/workout-sessions/{session_id}/exercises/{exercise_id}/sets/{set_number}")
def update_session_set(
    session_id: int,
    exercise_id: int,
    set_number: int,
    payload: SessionSetPatchIn,
    identity: Identity = Depends(get_current_identity),
):
    """Confirma reps pendentes ou corrige carga/reps/RPE de UMA série
    (Modo Intensivo), sem o apaga-e-recria do `PUT /exercises/logs`.

    - mesma regra de mescla da edição (`exercise_set_update_merge.
      merge_set_patch`: presente vale, ausente mantém) e mesma validação de
      medida (`ensure_valid_set_measure`) -> 422;
    - informar `reps` confirma (`reps_pending` = 0); `reps: null` numa série
      de repetições -> 422 (a série nunca volta a ficar pendente);
    - `safety_event` recalculado pelo RPE mesclado (mesma regra do POST);
      `rpe_target_effective`/`rpe_target_source` não mudam;
    - agregados do exercício e RPE médio da sessão recalculados; treino
      finalizado -> 409 (como o POST de série nova). Idempotente."""
    sent = payload.model_dump(exclude_unset=True)
    with transaction() as conn:
        with conn.cursor() as cur:
            _get_live_session(cur, session_id, identity.user_id)
            exercise_row = _session_exercise_row(cur, session_id, exercise_id)
            cur.execute(
                "SELECT id, set_number, set_type, reps, weight_kg, rpe, muscle_pain, joint_pain, "
                "rpe_target_effective, safety_event, technique_code, anchor_set_number, measure, duration_seconds, "
                f"client_key, {_INTENSIVE_SET_COLUMNS}, {_SIDE_SET_COLUMNS} "
                "FROM exercise_sets WHERE exercise_id = %s AND set_number = %s FOR UPDATE",
                (exercise_id, set_number),
            )
            stored = cur.fetchone()
            if not stored:
                raise HTTPException(status_code=404, detail="Série não encontrada neste exercício.")
            merged = merge_set_patch(stored, sent)
            if "reps" in sent and sent["reps"] is None and normalize_measure(merged.get("measure")) is SetMeasure.REPS:
                raise HTTPException(status_code=422, detail="Informe as repetições da série.")
            ensure_valid_set_measure(
                merged.get("measure"), merged.get("reps"), merged.get("duration_seconds"), set_number=set_number
            )
            set_type = normalize_set_type(merged.get("set_type"))
            intensity = effective_intensity.resolve_for_logged_exercise(conn, exercise_id)
            reading, next_set = _evaluate_set(
                conn,
                cur,
                exercise_id,
                exercise_row,
                intensity,
                user_id=identity.user_id,
                session_id=session_id,
                set_type=set_type,
                set_number=set_number,
                reps=merged.get("reps"),
                weight_kg=merged.get("weight_kg"),
                rpe=merged.get("rpe"),
                joint_pain=merged.get("joint_pain"),
                muscle_pain=merged.get("muscle_pain"),
                measure=merged.get("measure"),
                reps_pending=bool(merged["reps_pending"]),
                protocol_target=_stored_protocol_target(stored),
            )
            safety_event = reading.safety_event
            cur.execute(
                "UPDATE exercise_sets SET reps = %s, weight_kg = %s, rpe = %s, reps_pending = %s, safety_event = %s, "
                "reps_left = %s, reps_right = %s, rpe_left = %s, rpe_right = %s "
                "WHERE id = %s",
                (
                    merged.get("reps"),
                    merged.get("weight_kg"),
                    merged.get("rpe"),
                    merged["reps_pending"],
                    safety_event,
                    *(merged.get(field) for field in SIDE_FIELDS),
                    stored["id"],
                ),
            )
            if safety_event in SAFETY_ALERT_EVENTS and safety_event != stored.get("safety_event"):
                log_event(
                    EVENT_SAFETY_RPE_ABOVE_CEILING,
                    user_id=int(identity.user_id),
                    activity_id=session_id,
                    exercise_id=exercise_id,
                    set_number=set_number,
                    rpe=float(merged["rpe"]) if merged.get("rpe") is not None else None,
                    effective_rpe_ceiling=intensity.ceiling,
                    reason_code=safety_event,
                    failure_denied_by=intensity.failure_denied_by,
                )
            _refresh_exercise_aggregates(cur, exercise_id)
            _refresh_activity_avg_rpe(cur, session_id)

    return _set_response(
        session_id,
        exercise_id,
        set_number=set_number,
        technique_code=stored.get("technique_code"),
        anchor_set_number=stored.get("anchor_set_number"),
        rpe_target_effective=stored.get("rpe_target_effective"),
        safety_event=safety_event,
        intensity=intensity,
        reading=reading,
        next_set=next_set,
        client_key=stored.get("client_key"),
        duplicate=False,
        intensive=merged,
    )


@router.post("/workout-sessions/{session_id}/finish")
def finish_session(session_id: int, payload: SessionFinishIn, identity: Identity = Depends(get_current_identity)):
    with transaction() as conn:
        with conn.cursor() as cur:
            session = _get_finishable_activity(cur, session_id, identity.user_id)

            # Finalizar é IDEMPOTENTE. Bug real (atividade 230, 2026-09-25):
            # a primeira chamada EFETIVOU o encerramento no servidor
            # (`ended_at`/`session_status='done'` gravados) e só a RESPOSTA
            # não voltou — o API Gateway tem teto de 29 s e devolve
            # `{"message":"Endpoint request timed out"}`, que no browser
            # aparece como erro de API/CORS (mesmo precedente do PR #188). A
            # segunda tentativa do usuário levava 409 "Este treino já foi
            # finalizado.": uma faixa vermelha de erro num treino
            # perfeitamente encerrado, sem saída pela tela.
            #
            # Repetir agora devolve 200 com o MESMO estado e
            # `already_finished: true`, e NADA é reescrito: nem `ended_at`
            # (seria a hora da 2ª tentativa, distorcendo duração e
            # recuperação muscular), nem a ficha, nem o motor de progressão
            # em sombra, que já rodou na primeira chamada.
            if session.get("session_status") == "done":
                cur.execute(
                    "SELECT COUNT(*) AS exercises FROM exercises WHERE activity_id = %s",
                    (session_id,),
                )
                return {
                    "status": "ok",
                    "session_id": session_id,
                    "session_status": "done",
                    "ended_at": _iso_or_none(session.get("ended_at")),
                    "duration_seconds": session.get("duration_seconds"),
                    "exercises": cur.fetchone()["exercises"],
                    "workout_plan_marked_done": session.get("workout_plan_id") is not None,
                    "already_finished": True,
                }

            # ended_at só ganha um valor "agora" quando isso é a finalização
            # genuína de uma sessão ao vivo (session_status era 'in_progress'
            # antes desta chamada). Para activities "antigas"/legadas com
            # session_status NULL, marcar como concluída não deve inventar um
            # ended_at que nunca existiu — distorceria recuperação muscular
            # (que usa COALESCE(ended_at, started_at, created_at)). Se o
            # cliente enviar ended_at explicitamente, esse valor sempre
            # prevalece, independente do session_status anterior.
            #
            # Hora de PAREDE do usuario (mesma base do `started_at`), nunca
            # `datetime.now()` da Lambda em UTC: senao a duracao calculada
            # abaixo sai deslocada pelo offset do fuso. O fuso so e consultado
            # neste ramo (o caminho idempotente acima nao paga consulta extra).
            was_live_session = session.get("session_status") == "in_progress"
            payload_ended_at = payload.ended_at
            if payload_ended_at is not None and payload_ended_at.tzinfo is not None:
                payload_ended_at = _to_user_wall_clock(payload_ended_at, user_timezone(conn, identity.user_id))
            if payload_ended_at is not None:
                ended_at = payload_ended_at
            elif was_live_session:
                ended_at = user_now(user_timezone(conn, identity.user_id))
            else:
                ended_at = None

            duration = payload.duration_seconds
            if duration is None and ended_at is not None and session.get("started_at"):
                duration = max(0, int((ended_at - session["started_at"]).total_seconds()))

            cur.execute(
                """
                UPDATE activities
                SET ended_at = COALESCE(%s, ended_at),
                    duration_seconds = COALESCE(%s, duration_seconds),
                    calories_kcal = COALESCE(%s, calories_kcal),
                    avg_hr_bpm = COALESCE(%s, avg_hr_bpm),
                    max_hr_bpm = COALESCE(%s, max_hr_bpm),
                    notes = COALESCE(%s, notes),
                    session_status = 'done'
                WHERE id = %s
                """,
                (
                    ended_at,
                    duration,
                    payload.calories_kcal,
                    payload.avg_hr_bpm,
                    payload.max_hr_bpm,
                    payload.notes,
                    session_id,
                ),
            )
            _refresh_activity_avg_rpe(cur, session_id)

            # Se este treino foi lançado a partir de uma ficha (workout_plans),
            # finalizá-lo também marca a ficha como concluída. Idempotente:
            # mesmo que a ficha já esteja 'done', sobrescrever com 'done' de
            # novo não tem efeito colateral.
            workout_plan_marked_done = False
            workout_plan_id = session.get("workout_plan_id")
            if workout_plan_id is not None:
                cur.execute(
                    "UPDATE workout_plans SET status = 'done' WHERE id = %s",
                    (workout_plan_id,),
                )
                workout_plan_marked_done = True

            cur.execute(
                "SELECT COUNT(*) AS exercises FROM exercises WHERE activity_id = %s",
                (session_id,),
            )
            exercises_count = cur.fetchone()["exercises"]

            cur.execute(
                "SELECT ended_at FROM activities WHERE id = %s",
                (session_id,),
            )
            final_ended_at = cur.fetchone()["ended_at"]

        # Motor de Progressão v2 em MODO SOMBRA (PR-10, plano §5.11): avalia a
        # sessão que acabou de fechar e grava o que decidiria em
        # progression_group_states/tracks/decisions. Não altera nenhuma ficha,
        # prescrição nem a resposta abaixo, e só roda com
        # PROGRESSION_ENGINE=shadow|v2 (default `v1` = desligado, então o
        # deploy não muda comportamento). Falha aqui NUNCA derruba o finish:
        # on_session_finish captura, loga estruturado e segue.
        on_session_finish(conn, identity.user_id, session_id)

    return {
        "status": "ok",
        "session_id": session_id,
        "session_status": "done",
        "ended_at": _iso_or_none(final_ended_at),
        "duration_seconds": duration,
        "exercises": exercises_count,
        "workout_plan_marked_done": workout_plan_marked_done,
        "already_finished": False,
    }
