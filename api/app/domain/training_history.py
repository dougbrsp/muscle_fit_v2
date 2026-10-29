"""Historico de treino que o Motor de Progressao v2 consome (PR-10;
PLANO_20260912.md secao 5.2 e casos de borda da 5.10).

Uma pergunta, uma query: "quais foram as ultimas N sessoes CONCLUIDAS deste
usuario, com as series de cada exercicio e a prescricao que gerou cada uma?".

Regras que o SQL implementa (e por que):

- So `activities` concluidas (`session_status = 'done'`) e que tenham ao menos
  uma linha em `exercise_sets`. Historico so com agregados (importacoes
  antigas, sem series) e IGNORADO para platô (secao 5.10, ultima linha) - com
  ele nao ha como saber reps/RPE por serie.
- Series preparatorias (`warmup`, `feeler`) sao filtradas no `ON` do LEFT JOIN
  com `set_types.sql_not_preparatory()`, entao um exercicio que so teve
  aquecimento continua aparecendo, com zero series. A contagem das
  preparatorias vem em `preparatory_sets_count` (evidencia, nunca carga).
  O `dropset` legado nunca foi normalizado no banco: a normalizacao e em
  RUNTIME, e por isso os predicados de `set_types` (e nao literais) sao a
  unica forma aceita de filtrar `set_type` em SQL novo.
- Chave canonica do par: `exercise_id` = `exercise_catalog.id` (via
  `exercises.mapping_id`, com fallback na prescricao) e `equipment_id` =
  `equipment.id` (via `gym_equipment.equipment_id`) - nunca `gym_equipment.id`,
  que e cache por academia (secao 5.1). Treino avulso sem plano conta para a
  trilha se os dois resolverem (secao 5.10).
- SEM N+1: uma unica ida ao banco para as N sessoes, com as sessoes escolhidas
  numa subconsulta `LIMIT` e o restante por JOIN.
- Prescricao CONGELADA (Lote 2/P7 da investigacao RPE 10, 2026-10-11): alem
  das colunas editaveis de `workout_plan_exercises`, o SQL le do
  `prescription_json` - chave a chave (`JSON_EXTRACT`), nunca o JSON inteiro,
  que repetiria clamps/evidencias em cada linha de serie e pesaria na pagina
  da RDS Data API - as series, o alvo efetivo, o teto, a falha e a decisao
  com que a ficha daquele dia foi congelada. E a base contra a qual o
  fechamento avalia a sessao (`FrozenPrescription`).

Os DTOs abaixo sao imutaveis e nao tem nenhuma regra de progressao: quem
decide o que qualifica e o `progression_engine`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional, Sequence

from ..set_types import normalize_set_type, sql_is_working, sql_not_preparatory, sql_set_confirmed
from .set_measures import DEFAULT_MEASURE, SetMeasure, is_rep_based, normalize_measure
from .training_goal import DEFAULT_GOAL, goal_value, goals_in

DEFAULT_SESSION_WINDOW = 21

_REPS_RANGE_RE = re.compile(r"(\d+)\s*-\s*(\d+)")
_FIRST_NUMBER_RE = re.compile(r"(\d+(?:[.,]\d+)?)")
_ALL_NUMBERS_RE = re.compile(r"\d+(?:[.,]\d+)?")


# ---------------------------------------------------------------------------
# Conversores (texto livre das colunas legadas -> numero)
# ---------------------------------------------------------------------------


def parse_reps_range(value: Any) -> tuple[Optional[int], Optional[int]]:
    """`"10-12"` -> (10, 12); `"12"` -> (12, 12); vazio/sem numero -> (None, None)."""
    if value is None:
        return None, None
    text = str(value)
    match = _REPS_RANGE_RE.search(text)
    if match:
        return int(match.group(1)), int(match.group(2))
    single = _FIRST_NUMBER_RE.search(text)
    if single:
        number = int(float(single.group(1).replace(",", ".")))
        return number, number
    return None, None


def parse_rpe_ramp(value: Any) -> tuple[Decimal, ...]:
    """`workout_plan_exercises.rpe_target` e uma RAMPA por serie, nao um
    escalar: `"8-9-10"` = RPE 8 na 1a, 9 na 2a, 10 na 3a (formato dominante no
    banco; `"9"`, `"8-9"`, `"8-8.5-9-9.5-10"` tambem ocorrem).

    `"8-9-10"` -> (8, 9, 10); `"9"` -> (9,); vazio/sem numero -> ().
    """
    if value is None:
        return ()
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return (Decimal(str(value)),)
    return tuple(Decimal(item.replace(",", ".")) for item in _ALL_NUMBERS_RE.findall(str(value)))


def parse_int(value: Any) -> Optional[int]:
    """Primeiro inteiro de um texto livre (`"RPE 9"` -> 9). None se nao houver."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (float, Decimal)):
        return int(value)
    match = _FIRST_NUMBER_RE.search(str(value))
    return int(float(match.group(1).replace(",", "."))) if match else None


def parse_decimal(value: Any) -> Optional[Decimal]:
    """Decimal ou None. Texto livre (`"40 kg"`) tambem resolve."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    match = _FIRST_NUMBER_RE.search(str(value))
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", "."))
    except InvalidOperation:
        return None


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SetRecord:
    """Uma serie executada (secao 5.2). `set_type` JA normalizado."""

    set_number: int
    set_type: str
    reps: Optional[int] = None
    weight_kg: Optional[Decimal] = None
    rpe: Optional[Decimal] = None
    muscle_pain: Optional[int] = None
    joint_pain: Optional[bool] = None
    # Fase 5 (059): tecnica da linha e a serie de trabalho que a originou.
    # NULL em registro sem tecnica, legado anterior a 059 e Zepp.
    technique_code: Optional[str] = None
    anchor_set_number: Optional[int] = None
    # Lote T3 (medidas de serie, 089): `reps`/`duration`/`max_reps`, JA
    # normalizada (`set_measures.normalize_measure`; ausente = reps), e os
    # segundos da serie por tempo (janela em `max_reps`).
    measure: SetMeasure = DEFAULT_MEASURE
    duration_seconds: Optional[int] = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "SetRecord":
        joint_pain = row.get("set_joint_pain")
        technique_code = row.get("technique_code")
        return cls(
            set_number=int(row.get("set_number") or 0),
            set_type=normalize_set_type(row.get("set_type")),
            reps=parse_int(row.get("reps")),
            weight_kg=parse_decimal(row.get("weight_kg")),
            rpe=parse_decimal(row.get("rpe")),
            muscle_pain=parse_int(row.get("set_muscle_pain")),
            joint_pain=None if joint_pain is None else bool(joint_pain),
            technique_code=str(technique_code).strip() or None if technique_code else None,
            anchor_set_number=parse_int(row.get("anchor_set_number")),
            measure=normalize_measure(row.get("measure")),
            duration_seconds=parse_int(row.get("duration_seconds")),
        )


def parse_bool(value: Any) -> Optional[bool]:
    """Booleano tolerante: `True`/`1`/`"true"` (o que `JSON_UNQUOTE` devolve
    para um booleano JSON) -> True; `False`/`0`/`"false"` -> False; ausente,
    `"null"` ou lixo -> None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "1"):
        return True
    if text in ("false", "0"):
        return False
    return None


def _json_text(value: Any) -> Optional[str]:
    """Escalar de `JSON_UNQUOTE(JSON_EXTRACT(...))`: o JSON `null` volta como o
    texto `"null"` e conta como ausente."""
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text.lower() == "null" else text


# Colunas que `SESSIONS_SQL` extrai do `prescription_json` (prefixo `frozen_`)
# e a chave correspondente no snapshot (`domain/prescription.build_prescription`).
FROZEN_SNAPSHOT_KEYS: dict[str, str] = {
    "frozen_schema_version": "schema_version",
    "frozen_sets_count": "sets_count",
    "frozen_effective_rpe_target": "effective_rpe_target",
    "frozen_effective_rpe_ceiling": "effective_rpe_ceiling",
    "frozen_failure_allowed": "failure_allowed",
    "frozen_decision": "decision",
    # RPE 10 Lote 1/P1: origem da negacao da falha congelada e, para snapshot
    # antigo sem ela, o teto do par congelado (de onde ela e derivada).
    "frozen_failure_denied_by": "failure_denied_by",
    "frozen_pair_max_safe_rpe": "safety_constraints.max_safe_rpe",
    # Medida da serie (Lote T2): chave aditiva do snapshot; ausente = reps.
    "frozen_measure": "measure",
}


def frozen_columns_from_snapshot(snapshot: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Snapshot ja decodificado -> as colunas `frozen_*` que `SESSIONS_SQL`
    devolveria para ele. Para quem monta `SessionRecord` sem banco (testes,
    cenarios) passar pelo MESMO parser do caminho real."""
    snapshot = snapshot or {}
    return {column: _snapshot_path(snapshot, key) for column, key in FROZEN_SNAPSHOT_KEYS.items()}


def _snapshot_path(snapshot: dict[str, Any], key: str) -> Any:
    """`a.b` -> `snapshot["a"]["b"]` (mesma leitura do `JSON_EXTRACT('$.a.b')`)."""
    value: Any = snapshot
    for part in key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


@dataclass(frozen=True)
class FrozenPrescription:
    """O que a ficha CONGELADA daquele dia mandou (`prescription_json`, PR-11),
    so os campos que o fechamento usa para avaliar a sessao contra a ficha
    (Lote 2/P7). Sem regra: quem decide e o motor de progressao.

    `effective_rpe_target`/`effective_rpe_ceiling`/`failure_allowed` ja sao o
    par composto com o contrato de recuperacao PROJETADO no congelamento (a
    mesma fonte que o Modo Treino usa para gravar a serie,
    `effective_intensity.intensity_for_row`)."""

    sets_count: Optional[int] = None
    effective_rpe_target: Optional[int] = None
    effective_rpe_ceiling: Optional[int] = None
    failure_allowed: Optional[bool] = None
    decision: Optional[str] = None
    # RPE 10 Lote 1/P1: None | PAIR | RECOVERY (ausente em snapshot antigo) e o
    # teto do par congelado (`safety_constraints.max_safe_rpe`), que permite
    # derivar a origem nesses snapshots (`prescription.failure_denied_by_of`).
    failure_denied_by: Optional[str] = None
    pair_max_safe_rpe: Optional[int] = None
    # Medida da serie congelada (Lote T2; `set_measures`): ausente = reps.
    measure: SetMeasure = DEFAULT_MEASURE

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Optional["FrozenPrescription"]:
        """`None` quando a linha nao tem snapshot (ficha legada anterior ao
        PR-11, treino livre ou JSON sem as chaves) - o avaliador entao segue
        o caminho antigo (teto do par composto com READY)."""
        values = {column: _json_text(row.get(column)) for column in FROZEN_SNAPSHOT_KEYS}
        if not any(values.values()):
            return None
        frozen = cls(
            sets_count=parse_int(values["frozen_sets_count"]),
            effective_rpe_target=parse_int(values["frozen_effective_rpe_target"]),
            effective_rpe_ceiling=parse_int(values["frozen_effective_rpe_ceiling"]),
            failure_allowed=parse_bool(values["frozen_failure_allowed"]),
            decision=(values["frozen_decision"] or None),
            failure_denied_by=(values.get("frozen_failure_denied_by") or None),
            pair_max_safe_rpe=parse_int(values.get("frozen_pair_max_safe_rpe")),
            measure=normalize_measure(values.get("frozen_measure")),
        )
        if frozen.effective_rpe_ceiling is None and frozen.sets_count is None and frozen.decision is None:
            return None
        return frozen

    def intensity_snapshot(self) -> dict[str, Any]:
        """Os campos de intensidade no formato do snapshot, para
        `effective_intensity.from_prescription` (overlay de seguranca de hoje)."""
        return {
            "effective_rpe_target": self.effective_rpe_target,
            "effective_rpe_ceiling": self.effective_rpe_ceiling,
            "failure_allowed": bool(self.failure_allowed),
            "failure_denied_by": self.failure_denied_by,
            "safety_constraints": {"max_safe_rpe": self.pair_max_safe_rpe},
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "sets_count": self.sets_count,
            "effective_rpe_target": self.effective_rpe_target,
            "effective_rpe_ceiling": self.effective_rpe_ceiling,
            "failure_allowed": self.failure_allowed,
            "decision": self.decision,
            "failure_denied_by": self.failure_denied_by,
            "pair_max_safe_rpe": self.pair_max_safe_rpe,
            **_measure_entry(self.measure),
        }


def _measure_entry(measure: SetMeasure) -> dict[str, Any]:
    """`{"measure": ...}` so fora de `reps`: o contrato de serializacao anterior
    ao Lote T2 (evidencia gravada em `progression_decisions`) nao muda para
    toda serie de repeticoes."""
    return {} if is_rep_based(measure) else {"measure": measure.value}


@dataclass(frozen=True)
class PrescribedSpec:
    """`prescribed{...}` da secao 5.2: o que a ficha pedia naquela sessao."""

    sets: Optional[int] = None
    reps_min: Optional[int] = None
    reps_max: Optional[int] = None
    load: Optional[Decimal] = None
    rpe_target: Optional[Decimal] = None  # alvo da ULTIMA serie (topo da rampa)
    technique: Optional[str] = None
    rpe_targets: tuple[Decimal, ...] = ()  # rampa completa, serie a serie
    # Lote 2/P7: a ficha CONGELADA daquele dia (None = sem snapshot).
    frozen: Optional[FrozenPrescription] = None
    # Medida da serie que a ficha pedia (Lote T2; `set_measures`): a coluna
    # `prescribed_measure` quando a consulta a traz, senao a do snapshot
    # congelado; ausente = reps. Quem decide o que fazer com ela e o motor.
    measure: SetMeasure = DEFAULT_MEASURE

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "PrescribedSpec":
        reps_min, reps_max = parse_reps_range(row.get("prescribed_reps_target"))
        ramp = parse_rpe_ramp(row.get("prescribed_rpe_target"))
        frozen = FrozenPrescription.from_row(row)
        measure = row.get("prescribed_measure")
        if measure is None:
            measure = _json_text(row.get("frozen_measure"))
        return cls(
            sets=parse_int(row.get("prescribed_sets_count")),
            reps_min=reps_min,
            reps_max=reps_max,
            load=parse_decimal(row.get("prescribed_load_target")),
            # o alvo de esforco e o ULTIMO degrau da rampa: e o que a ficha
            # pede na serie onde o esforco de fato acontece (§5.4, D4/2026-09-18)
            rpe_target=ramp[-1] if ramp else None,
            technique=(row.get("prescribed_technique") or None),
            rpe_targets=ramp,
            frozen=frozen,
            measure=normalize_measure(measure),
        )

    def rpe_target_for_set(self, index: int) -> Optional[Decimal]:
        """Alvo da serie `index` (0-based). Rampa mais curta que a sessao
        executada repete o ultimo degrau."""
        if not self.rpe_targets:
            return self.rpe_target
        return self.rpe_targets[min(int(index), len(self.rpe_targets) - 1)]

    def to_dict(self) -> dict[str, Any]:
        """Contrato de serializacao anterior ao Lote 2 preservado: `frozen` so
        aparece quando a linha tem ficha congelada."""
        result: dict[str, Any] = {
            "sets": self.sets,
            "reps_min": self.reps_min,
            "reps_max": self.reps_max,
            "load": float(self.load) if self.load is not None else None,
            "rpe_target": float(self.rpe_target) if self.rpe_target is not None else None,
            "rpe_targets": [float(item) for item in self.rpe_targets],
            "technique": self.technique,
        }
        if self.frozen is not None:
            result["frozen"] = self.frozen.to_dict()
        result.update(_measure_entry(self.measure))
        return result


@dataclass(frozen=True)
class ExerciseExecution:
    """Um exercicio dentro de uma sessao, com suas series nao preparatorias."""

    exercise_row_id: int  # exercises.id
    sequence: int
    exercise_id: Optional[int]  # exercise_catalog.id (canonico)
    equipment_id: Optional[int]  # equipment.id (canonico)
    gym_equipment_id: Optional[int] = None
    workout_plan_exercise_id: Optional[int] = None
    prescription_snapshot_id: Optional[int] = None
    prescribed: PrescribedSpec = field(default_factory=PrescribedSpec)
    muscle_pain: Optional[int] = None
    joint_pain: Optional[bool] = None
    preparatory_sets_count: int = 0
    sets: tuple[SetRecord, ...] = ()
    # M97 F1: objetivo de treino da execucao (`exercises.training_goal`,
    # carimbado no registro a partir da linha da ficha; VALOR do dono
    # `domain/training_goal.py`, leitura tolerante: ausente = default).
    training_goal: str = DEFAULT_GOAL.value

    @property
    def pair(self) -> Optional[tuple[int, int]]:
        """Chave da trilha, ou None se o par nao resolveu (secao 5.10)."""
        if self.exercise_id is None or self.equipment_id is None:
            return None
        return (self.exercise_id, self.equipment_id)


@dataclass(frozen=True)
class SessionRecord:
    """Uma sessao concluida, em ordem cronologica de execucao."""

    activity_id: int
    local_date: date
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    workout_plan_id: Optional[int] = None
    exercises: tuple[ExerciseExecution, ...] = ()
    # M97 D: origem do deload da sessao (`activities.deload_source`, VALOR do
    # dono `domain/deload.py`; None = sessao normal).
    deload_source: Optional[str] = None

    @property
    def is_deload(self) -> bool:
        """Sessao de deload (dono `domain/deload.is_deload_session`): conta na
        recuperacao e no volume, mas a progressao a PULA."""
        from .deload import is_deload_session

        return is_deload_session(self.deload_source)

    def progression_view(self, training_goal: Any) -> "SessionRecord":
        """O que a PROGRESSAO de um objetivo ve desta sessao: as execucoes do
        objetivo (`only_goal`) ou, numa sessao de deload, nenhuma - a sessao
        fica na janela (o teto de ativacao le a sessao inteira), mas nao
        avanca, nao regride e nao conta para plato (M97 D)."""
        if self.is_deload:
            return self if not self.exercises else replace(self, exercises=())
        return self.only_goal(training_goal)

    @property
    def occurred_at(self) -> Optional[datetime]:
        return self.ended_at or self.started_at

    @property
    def training_goals(self) -> tuple[str, ...]:
        """Objetivos executados na sessao, na ordem do vocabulario do dono
        (`training_goal.goals_in`). Sessao hibrida (M97) tem mais de um."""
        return goals_in(execution.training_goal for execution in self.exercises)

    def only_goal(self, training_goal: Any) -> "SessionRecord":
        """A MESMA sessao so com as execucoes do objetivo pedido (M97 F1: a
        progressao de um objetivo so ve as exposicoes dele). Sessao de um
        objetivo so devolve a propria instancia quando ele e o pedido."""
        goal = goal_value(training_goal)
        exercises = tuple(item for item in self.exercises if goal_value(item.training_goal) == goal)
        if len(exercises) == len(self.exercises):
            return self
        return replace(self, exercises=exercises)


# ---------------------------------------------------------------------------
# Repositorio (somente SELECT)
# ---------------------------------------------------------------------------

_SESSION_ORDER = "COALESCE(a.ended_at, a.started_at, a.created_at)"

SESSIONS_SQL = f"""
SELECT
  s.activity_id,
  s.local_date,
  s.started_at,
  s.ended_at,
  s.workout_plan_id,
  s.deload_source,
  e.id                        AS exercise_row_id,
  e.sequence                  AS sequence,
  COALESCE(e.mapping_id, wpe.mapping_id) AS exercise_id,
  ge.equipment_id             AS equipment_id,
  e.equipment_id              AS gym_equipment_id,
  e.workout_plan_exercise_id  AS workout_plan_exercise_id,
  e.training_goal             AS training_goal,
  e.muscle_pain               AS exercise_muscle_pain,
  e.joint_pain                AS exercise_joint_pain,
  wpe.sets_count              AS prescribed_sets_count,
  wpe.reps_target             AS prescribed_reps_target,
  wpe.load_target             AS prescribed_load_target,
  wpe.rpe_target              AS prescribed_rpe_target,
  wpe.measure                 AS prescribed_measure,
  wpe.advanced_technique      AS prescribed_technique,
  JSON_UNQUOTE(JSON_EXTRACT(wpe.prescription_json, '$.schema_version'))        AS frozen_schema_version,
  JSON_UNQUOTE(JSON_EXTRACT(wpe.prescription_json, '$.sets_count'))            AS frozen_sets_count,
  JSON_UNQUOTE(JSON_EXTRACT(wpe.prescription_json, '$.effective_rpe_target'))  AS frozen_effective_rpe_target,
  JSON_UNQUOTE(JSON_EXTRACT(wpe.prescription_json, '$.effective_rpe_ceiling')) AS frozen_effective_rpe_ceiling,
  JSON_UNQUOTE(JSON_EXTRACT(wpe.prescription_json, '$.failure_allowed'))       AS frozen_failure_allowed,
  JSON_UNQUOTE(JSON_EXTRACT(wpe.prescription_json, '$.decision'))              AS frozen_decision,
  JSON_UNQUOTE(JSON_EXTRACT(wpe.prescription_json, '$.failure_denied_by'))     AS frozen_failure_denied_by,
  JSON_UNQUOTE(JSON_EXTRACT(wpe.prescription_json, '$.safety_constraints.max_safe_rpe')) AS frozen_pair_max_safe_rpe,
  JSON_UNQUOTE(JSON_EXTRACT(wpe.prescription_json, '$.measure'))               AS frozen_measure,
  (
    SELECT COUNT(*) FROM exercise_sets pes
    WHERE pes.exercise_id = e.id AND NOT ({sql_not_preparatory("pes.set_type")})
      AND {sql_set_confirmed('pes.reps_pending')}
  )                           AS preparatory_sets_count,
  es.id                       AS set_row_id,
  es.set_number               AS set_number,
  es.set_type                 AS set_type,
  es.reps                     AS reps,
  es.weight_kg                AS weight_kg,
  es.rpe                      AS rpe,
  es.muscle_pain              AS set_muscle_pain,
  es.joint_pain               AS set_joint_pain,
  es.technique_code           AS technique_code,
  es.anchor_set_number        AS anchor_set_number,
  es.measure                  AS measure,
  es.duration_seconds         AS duration_seconds
FROM (
  SELECT a.id AS activity_id, a.local_date, a.started_at, a.ended_at, a.workout_plan_id,
         a.deload_source,
         {_SESSION_ORDER} AS occurred_at
  FROM activities a
  WHERE a.user_id = %s
    AND a.session_status = 'done'
    AND EXISTS (
      SELECT 1 FROM exercises ex JOIN exercise_sets exs ON exs.exercise_id = ex.id AND {sql_set_confirmed('exs.reps_pending')}
      WHERE ex.activity_id = a.id
    )
  ORDER BY {_SESSION_ORDER} DESC, a.id DESC
  LIMIT %s OFFSET %s
) s
JOIN exercises e ON e.activity_id = s.activity_id
LEFT JOIN workout_plan_exercises wpe ON wpe.id = e.workout_plan_exercise_id
LEFT JOIN gym_equipment ge ON ge.id = e.equipment_id
LEFT JOIN exercise_sets es ON es.exercise_id = e.id AND {sql_not_preparatory("es.set_type")} AND {sql_set_confirmed('es.reps_pending')}
ORDER BY s.occurred_at ASC, s.activity_id ASC, e.sequence ASC, e.id ASC, es.set_number ASC, es.id ASC
"""

# Contagem de series de trabalho por exercicio, com o MESMO predicado do
# motor. Usada so por diagnostico/replay (`working_sets_count`).
WORKING_SETS_COUNT_SQL = f"""
SELECT es.exercise_id, COUNT(*) AS working_sets
FROM exercise_sets es
WHERE es.exercise_id IN ({{placeholders}}) AND {sql_is_working("es.set_type")}
  AND {sql_set_confirmed('es.reps_pending')}
GROUP BY es.exercise_id
"""


def build_sessions(rows: Iterable[dict[str, Any]]) -> list[SessionRecord]:
    """Agrupa as linhas planas da query em `SessionRecord`s (funcao PURA:
    testavel sem banco, e o unico lugar que sabe o formato do resultado)."""
    sessions: dict[int, dict[str, Any]] = {}
    for row in rows:
        activity_id = int(row["activity_id"])
        session = sessions.setdefault(
            activity_id,
            {
                "activity_id": activity_id,
                "local_date": row.get("local_date"),
                "started_at": row.get("started_at"),
                "ended_at": row.get("ended_at"),
                "workout_plan_id": row.get("workout_plan_id"),
                "deload_source": row.get("deload_source"),
                "exercises": {},
            },
        )
        exercise_row_id = int(row["exercise_row_id"])
        exercise = session["exercises"].get(exercise_row_id)
        if exercise is None:
            joint_pain = row.get("exercise_joint_pain")
            exercise = {
                "exercise_row_id": exercise_row_id,
                "sequence": int(row.get("sequence") or 0),
                "exercise_id": parse_int(row.get("exercise_id")),
                "equipment_id": parse_int(row.get("equipment_id")),
                "gym_equipment_id": parse_int(row.get("gym_equipment_id")),
                "workout_plan_exercise_id": parse_int(row.get("workout_plan_exercise_id")),
                "prescribed": PrescribedSpec.from_row(row),
                "muscle_pain": parse_int(row.get("exercise_muscle_pain")),
                "joint_pain": None if joint_pain is None else bool(joint_pain),
                "preparatory_sets_count": int(row.get("preparatory_sets_count") or 0),
                "training_goal": goal_value(row.get("training_goal")),
                "sets": [],
            }
            session["exercises"][exercise_row_id] = exercise
        if row.get("set_row_id") is not None:
            exercise["sets"].append(SetRecord.from_row(row))

    result: list[SessionRecord] = []
    for session in sessions.values():
        exercises = tuple(
            ExerciseExecution(
                exercise_row_id=item["exercise_row_id"],
                sequence=item["sequence"],
                exercise_id=item["exercise_id"],
                equipment_id=item["equipment_id"],
                gym_equipment_id=item["gym_equipment_id"],
                workout_plan_exercise_id=item["workout_plan_exercise_id"],
                prescription_snapshot_id=item["workout_plan_exercise_id"],
                prescribed=item["prescribed"],
                muscle_pain=item["muscle_pain"],
                joint_pain=item["joint_pain"],
                preparatory_sets_count=item["preparatory_sets_count"],
                sets=tuple(item["sets"]),
                training_goal=item["training_goal"],
            )
            for item in session["exercises"].values()
        )
        result.append(
            SessionRecord(
                activity_id=session["activity_id"],
                local_date=session["local_date"],
                started_at=session["started_at"],
                ended_at=session["ended_at"],
                workout_plan_id=parse_int(session["workout_plan_id"]),
                exercises=exercises,
                deload_source=_deload_source(session["deload_source"]),
            )
        )
    return result


def _deload_source(value: Any) -> Optional[str]:
    from .deload import deload_source_value

    return deload_source_value(value)


class TrainingHistoryRepository:
    """Leitura do historico de treino. Nenhuma escrita, nenhuma regra."""

    def __init__(self, conn: Any):
        self.conn = conn

    def fetch_last_completed_sessions(
        self, user_id: int, n: int = DEFAULT_SESSION_WINDOW, *, offset: int = 0
    ) -> list[SessionRecord]:
        """As `n` ultimas sessoes concluidas COM series, em ordem cronologica
        crescente (a mais antiga primeiro), numa unica query.

        `offset` pula as `offset` sessoes MAIS RECENTES antes de pegar as `n`
        seguintes (o `LIMIT/OFFSET` fatia a ordem decrescente da subconsulta).
        Serve ao replay, que le o historico inteiro em paginas porque a
        resposta da RDS Data API tem teto de 1 MB - ver
        `app/replay_progression.py`. Com o default `offset=0` o SQL e o mesmo
        de sempre.
        """
        limit = max(1, int(n))
        with self.conn.cursor() as cur:
            cur.execute(SESSIONS_SQL, (int(user_id), limit, max(0, int(offset))))
            rows = cur.fetchall() or []
        return build_sessions(rows)

    def working_sets_count(self, exercise_row_ids: Sequence[int]) -> dict[int, int]:
        """`exercises.id` -> nº de series `working` (mesmo predicado do motor)."""
        ids = sorted({int(value) for value in exercise_row_ids})
        if not ids:
            return {}
        placeholders = ", ".join(["%s"] * len(ids))
        with self.conn.cursor() as cur:
            cur.execute(WORKING_SETS_COUNT_SQL.format(placeholders=placeholders), tuple(ids))
            return {int(row["exercise_id"]): int(row["working_sets"]) for row in cur.fetchall() or []}
