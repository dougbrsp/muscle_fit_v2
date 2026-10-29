"""Decisão do motor de progressão v2 pronta para os prompts de IA do Modo
Treino (M76, Lote 1c; inventário C1/C11 em `docs/motores/FONTES_DA_VERDADE.md`).

Pergunta que este módulo responde: **o que o motor já decidiu** — ação, carga
e reps alvo, passo do equipamento — num formato que os três prompts do Modo
Treino recebem PRONTO:

- avaliação de série (`exercise_set_evaluation_ai`): a carga sugerida pela IA
  para a próxima série É a da regra de carga da próxima série
  (`domain/next_set_load.py`, set-eval-v6) quando ela se aplica; fora dela
  (série sem carga, `assisted`/`bodyweight`) cai no passo do equipamento e
  não sobe além da carga alvo do motor (ou da executada, se maior)
  (`bound_ai_load`);
- resumo de fim de exercício (`exercise_summary_ai`): a IA vê a decisão que
  prescreveu o exercício hoje e não sugere carga para a próxima vez;
- resumo de fim de treino (`workout_summary_ai`): a IA vê a decisão de hoje de
  cada exercício e a decisão POR GRUPO que o motor gravou ao finalizar ESTE
  treino.

De onde vem cada decisão (medido em produção em 2026-10-06):

- **por par exercício × equipamento**: o snapshot congelado da ficha
  (`workout_plan_exercises.prescription_json`: `decision`, `suggested_load_kg`,
  `reps_target`, `effective_rpe_target`, `sets_count`), gravado pelo motor ao
  gerar/congelar a ficha. É a única decisão por par que existe: as linhas de
  `progression_decisions` são do GRUPO muscular (`track_id` sempre NULL, sem
  carga) e a carga da PRÓXIMA sessão só nasce quando a próxima ficha for
  congelada;
- **por grupo, ao finalizar**: `progression_decisions` (`kind=EVALUATION`)
  com o `activity_id` do treino.

Nenhuma regra é recalculada aqui (princípio 2 do mapa: consumidor não
recalcula). O passo do equipamento e o arredondamento são os do motor:

- passo: `progression_engine.load_increment(...).step_kg` (D12: NULL ->
  `DEFAULT_LOAD_STEP_KG`), lido de `equipment.load_increment_kg` por
  `progression_state.load_equipment_steps`;
- arredondamento: `progression_engine.round_to_equipment_step` (helper
  público único do Lote 1a; regra em `domain/session_loads.round_to_step`,
  múltiplo do passo, meio para cima) — importado e reexportado aqui, nunca
  copiado;
- faixa de reps: `training_history.parse_reps_range`.

As leituras de banco são só SELECT e nunca derrubam a chamada de IA: sem
decisão (treino livre, ficha anterior ao motor, falha de leitura) o prompt diz
que não há decisão e a IA é instruída a não sugerir progressão de carga.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Optional

from .domain.deload import DeloadPrescription, deload_next_set_load, is_deload_session, item_deload_prescription
from .domain.exercise_briefing import DECISION_LABELS
from .domain.next_set_load import (
    DEFAULT_RULESET,
    REASON_NEXT_SET_RULE,
    NextSetLoad,
    RuleSet,
    SetFacts,
    evaluate_ruleset,
    planned_ramp_rpe,
)
from .domain.progression_engine import round_to_equipment_step  # noqa: F401 - helper público único (reexportado)
from .domain.progression_engine import load_increment
from .domain.training_goal import DEFAULT_GOAL
from .domain.training_history import parse_reps_range
from .db_helpers import rows as _fetchall
from .set_types import WORKING, normalize_set_type

logger = logging.getLogger(__name__)

# Motivo gravado em `load_adjustments[]` da avaliação de série. NÃO vai em
# `clamps[]`: a UI do Modo Treino mostra todo clamp como "RPE limitado por
# segurança" (SetLoggerCard.tsx).
REASON_EQUIPMENT_STEP = "EQUIPMENT_STEP"
REASON_ENGINE_TARGET = "ENGINE_TARGET_LOAD"


# ---------------------------------------------------------------------------
# Contrato
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EngineDecision:
    """Decisão do motor para um par exercício × equipamento (snapshot
    congelado da ficha), só com o que os prompts consomem."""

    decision: str
    load_kg: Optional[float] = None
    reps_min: Optional[int] = None
    reps_max: Optional[int] = None
    sets_count: Optional[int] = None
    rpe_target: Optional[int] = None

    @property
    def label(self) -> str:
        return DECISION_LABELS.get(self.decision, self.decision)


@dataclass(frozen=True)
class GroupDecision:
    """Decisão do motor para um grupo muscular, gravada ao finalizar o treino
    (`progression_decisions`, `kind=EVALUATION`)."""

    muscle_group: str
    decision: str
    phase_before: Optional[str] = None
    phase_after: Optional[str] = None
    reason: str = ""

    @property
    def label(self) -> str:
        return DECISION_LABELS.get(self.decision, self.decision)


@dataclass(frozen=True)
class PairEngineContext:
    """O que a avaliação de série precisa do motor para um par."""

    step_kg: Decimal
    decision: Optional[EngineDecision] = None
    # RPE 10 Lote 6/P9: `exercises.id` da sessao em andamento onde a serie foi
    # salva (o status da serie e resolvido contra a ficha dele).
    exercise_row_id: Optional[int] = None
    # Carga da próxima série (2026-10-12): convenção de carga do par
    # (`equipment_exercises.load_convention`; None = `total`). A regra não se
    # aplica a `assisted`/`bodyweight` (`domain/next_set_load.py`).
    load_convention: Optional[str] = None
    # M97 F2: objetivo do exercicio em andamento (snapshot congelado; sem
    # exercicio lancado = default). Escolhe o conjunto da proxima serie
    # (`next_set_load.ruleset_for_goal`).
    training_goal: str = DEFAULT_GOAL.value
    # M97 D: prescricao de deload do item quando a sessao em andamento e de
    # deload (dono `domain/deload.py`); None = sessao normal. Com ela, a carga
    # da proxima serie e a do deload (`next_set_load_for`).
    deload: Optional[DeloadPrescription] = None


# ---------------------------------------------------------------------------
# Passo do equipamento (regra do motor, só reexposta)
# ---------------------------------------------------------------------------


def equipment_step_kg(load_increment_kg: Any = None) -> Decimal:
    """Passo de carga do equipamento pela regra do motor (D12): o próprio
    `equipment.load_increment_kg`, ou o default do motor quando ausente."""
    return load_increment(is_compound=False, load_increment_kg=load_increment_kg).step_kg


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def evaluation_reps_range(engine: Optional[PairEngineContext], reps_target: Any) -> tuple[Optional[int], Optional[int]]:
    """Faixa de reps da série avaliada pela IA (set-eval-v5): a da decisão do
    motor (prescrição congelada), senão a faixa exibida no Modo Treino
    (`reps_target` do payload). Sem faixa -> (None, None). A posição das reps
    contra ela é do motor (`progression_engine.set_reps_position`)."""
    decision = engine.decision if engine is not None else None
    if decision is not None and decision.reps_min is not None and decision.reps_max is not None:
        return decision.reps_min, decision.reps_max
    return parse_reps_range(reps_target)


def bound_ai_load(
    ai_load_kg: Any,
    context: Optional[PairEngineContext],
    *,
    executed_kg: Any = None,
    rule: Optional[NextSetLoad] = None,
) -> tuple[Optional[float], list[dict[str, Any]]]:
    """Carga da próxima série sugerida pela IA -> carga aplicável.

    0. **regra da próxima série** (2026-10-12, dono `domain/next_set_load.py`):
       quando ela se aplica (`rule` não None), a carga final É a da regra -
       a IA não sobe, não desce e não sobe até o alvo do motor contra ela. A
       troca vai em `load_adjustments[]` com `REASON_NEXT_SET_RULE`. Direção
       REDUZIR sem passo abaixo -> sem carga (None);

    Fora da regra (série sem carga, `assisted`/`bodyweight`, payload sem reps):

    1. **passo**: a carga cai no passo do equipamento
       (`round_to_equipment_step`). Exceção: repetir a carga que a pessoa
       ACABOU de executar ou a carga alvo do motor — as duas são alcançáveis
       por definição (anilhas em libra, por exemplo, não caem no passo em kg);
    2. **direção do motor**: com carga alvo do motor para o par, a IA não sobe
       além do MAIOR entre a carga alvo e a carga recém-executada.

    Devolve `(carga, ajustes)`; cada ajuste é `{field, from, to, reason}`."""
    original = _decimal(ai_load_kg)
    if rule is not None:
        final = rule.load_kg
        same = original == (Decimal(str(final)) if final is not None else None)
        if same:
            return final, []
        source = float(original) if original is not None else None
        return final, [{"field": "load_kg", "from": source, "to": final, "reason": REASON_NEXT_SET_RULE}]
    if original is None:
        return None, []
    step = context.step_kg if context else equipment_step_kg(None)
    target = context.decision.load_kg if context and context.decision else None
    executed = _decimal(executed_kg)
    reachable = {float(v) for v in (_decimal(target), executed) if v is not None and v > 0}

    adjustments: list[dict[str, Any]] = []
    if float(original) in reachable:
        value: Optional[float] = float(original)
    else:
        value = round_to_equipment_step(original, step)
        if value is None:
            return None, [{"field": "load_kg", "from": float(original), "to": None, "reason": REASON_EQUIPMENT_STEP}]
        if Decimal(str(value)) != original:
            adjustments.append(
                {"field": "load_kg", "from": float(original), "to": value, "reason": REASON_EQUIPMENT_STEP}
            )
    if target is not None:
        limit = max(reachable)
        if value > limit:
            adjustments.append({"field": "load_kg", "from": value, "to": limit, "reason": REASON_ENGINE_TARGET})
            value = limit
    return value, adjustments


# ---------------------------------------------------------------------------
# Texto para os prompts (números interpolados da decisão, nunca à mão)
# ---------------------------------------------------------------------------


def format_kg(value: Any) -> str:
    parsed = _decimal(value)
    if parsed is None:
        return "-"
    text = f"{parsed.normalize():f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text.replace('.', ',')} kg"


def format_decision(decision: EngineDecision) -> str:
    """`Aumentar carga · carga alvo 62,5 kg · 10 a 12 reps · 3 séries · RPE alvo 9`."""
    parts = [decision.label]
    if decision.load_kg is not None:
        parts.append(f"carga alvo {format_kg(decision.load_kg)}")
    if decision.reps_min is not None and decision.reps_max is not None:
        reps = (
            f"{decision.reps_min} reps"
            if decision.reps_min == decision.reps_max
            else f"{decision.reps_min} a {decision.reps_max} reps"
        )
        parts.append(reps)
    if decision.sets_count is not None:
        parts.append(f"{decision.sets_count} séries")
    if decision.rpe_target is not None:
        parts.append(f"RPE alvo {decision.rpe_target}")
    return " · ".join(parts)


def format_group_decision(decision: GroupDecision) -> str:
    """`Peitoral: Manter (fase F1 → F2)`."""
    text = f"{decision.muscle_group}: {decision.label}"
    if decision.phase_before and decision.phase_after:
        text += (
            f" (fase {decision.phase_after})"
            if decision.phase_before == decision.phase_after
            else f" (fase {decision.phase_before} → {decision.phase_after})"
        )
    return text


# ---------------------------------------------------------------------------
# Conversão das linhas (funções puras)
# ---------------------------------------------------------------------------


def _int(value: Any) -> Optional[int]:
    parsed = _decimal(value)
    return None if parsed is None else int(parsed)


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def decision_from_snapshot(snapshot: Any) -> Optional[EngineDecision]:
    """`workout_plan_exercises.prescription_json` -> `EngineDecision`.

    Só snapshot que o MOTOR produziu (`decision` preenchida); o caminho legado
    (sem decisão) devolve `None`. Carga = `suggested_load_kg` do snapshot
    (a carga numérica que o motor prescreveu); faixa = `reps_target`."""
    data = _json(snapshot)
    decision = data.get("decision")
    if not decision:
        return None
    load = _decimal(data.get("suggested_load_kg"))
    reps_min, reps_max = parse_reps_range(data.get("reps_target"))
    return EngineDecision(
        decision=str(decision),
        load_kg=float(load) if load is not None and load > 0 else None,
        reps_min=reps_min,
        reps_max=reps_max,
        sets_count=_int(data.get("sets_count")),
        rpe_target=_int(data.get("effective_rpe_target")),
    )


def group_decisions_from_rows(rows: Iterable[Mapping[str, Any]]) -> list[GroupDecision]:
    """Linhas de `progression_decisions` do treino, em ordem -> a última
    decisão de cada grupo, na ordem em que os grupos apareceram."""
    by_group: dict[str, GroupDecision] = {}
    for row in rows:
        output = _json(row.get("output_json"))
        decision = output.get("decision") or row.get("decision")
        group = row.get("muscle_group")
        if not decision or not group:
            continue
        by_group[str(group)] = GroupDecision(
            muscle_group=str(group),
            decision=str(decision),
            phase_before=row.get("phase_before") or output.get("phase_before"),
            phase_after=row.get("phase_after") or output.get("phase_after"),
            reason=str(output.get("reason") or ""),
        )
    return list(by_group.values())


# ---------------------------------------------------------------------------
# Leitura (só SELECT)
# ---------------------------------------------------------------------------

# Snapshot do exercício da sessão EM ANDAMENTO do usuário para o par da série
# (a série acabou de ser salva nele). Sem sessão em andamento ou treino livre
# (sem linha da ficha) -> nenhuma linha -> sem decisão.
IN_PROGRESS_SNAPSHOT_SQL = """
SELECT e.id AS exercise_row_id, wpe.prescription_json, wpe.reps_target AS plan_reps_target, a.deload_source,
       wpe.mapping_id AS plan_mapping_id, wpe.preferred_equipment_id AS plan_gym_equipment_id,
       wpe.training_goal AS plan_training_goal
FROM exercises e
JOIN activities a ON a.id = e.activity_id
JOIN workout_plan_exercises wpe ON wpe.id = e.workout_plan_exercise_id
WHERE a.user_id = %s AND a.session_status = 'in_progress'
  AND COALESCE(e.mapping_id, wpe.mapping_id) = %s AND e.equipment_id = %s
ORDER BY e.id DESC
LIMIT 1
"""

LOGGED_EXERCISE_SNAPSHOT_SQL = """
SELECT wpe.prescription_json
FROM exercises e
JOIN activities a ON a.id = e.activity_id
JOIN workout_plan_exercises wpe ON wpe.id = e.workout_plan_exercise_id
WHERE e.id = %s AND a.user_id = %s
"""

ACTIVITY_SNAPSHOTS_SQL = """
SELECT e.id AS exercise_row_id, wpe.prescription_json
FROM exercises e
JOIN activities a ON a.id = e.activity_id
JOIN workout_plan_exercises wpe ON wpe.id = e.workout_plan_exercise_id
WHERE a.id = %s AND a.user_id = %s
"""

ACTIVITY_GROUP_DECISIONS_SQL = """
SELECT pd.decision, pd.output_json, pd.phase_before, pd.phase_after, mg.name AS muscle_group
FROM progression_decisions pd
JOIN progression_group_states gs ON gs.id = pd.group_state_id
LEFT JOIN muscle_groups mg ON mg.id = gs.muscle_group_id
WHERE pd.user_id = %s AND pd.activity_id = %s AND pd.kind = 'EVALUATION'
ORDER BY pd.created_at ASC, pd.id ASC
"""


def _fetchone(conn: Any, sql: str, params: tuple) -> Optional[Mapping[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _pair_step_kg(conn: Any, gym_equipment_id: Any) -> Decimal:
    from .domain.effective_intensity import equipment_id_of_gym_equipment
    from .progression_state import load_equipment_steps

    equipment_id = equipment_id_of_gym_equipment(conn, gym_equipment_id)
    raw_step = load_equipment_steps(conn, [equipment_id]).get(equipment_id) if equipment_id is not None else None
    return equipment_step_kg(raw_step)


def load_pair_step_context(conn: Any, mapping_id: Any, gym_equipment_id: Any) -> PairEngineContext:
    """Só o passo do equipamento e a convenção de carga do par (sem decisão):
    o que a regra da próxima série precisa no salvamento da série."""
    return PairEngineContext(
        step_kg=_pair_step_kg(conn, gym_equipment_id),
        load_convention=read_pair_load_convention(conn, mapping_id, gym_equipment_id),
    )


def load_pair_context(conn: Any, user_id: Any, mapping_id: Any, gym_equipment_id: Any) -> PairEngineContext:
    """Passo do equipamento + decisão de hoje do par da avaliação de série
    (`mapping_id` do catálogo, `gym_equipment.id`) + convenção de carga."""
    step_kg = _pair_step_kg(conn, gym_equipment_id)
    decision = None
    exercise_row_id = None
    training_goal = DEFAULT_GOAL.value
    deload = None
    ids = (_int(user_id), _int(mapping_id), _int(gym_equipment_id))
    if None not in ids:
        row = _fetchone(conn, IN_PROGRESS_SNAPSHOT_SQL, ids)
        decision = decision_from_snapshot(row.get("prescription_json")) if row else None
        exercise_row_id = _int(row.get("exercise_row_id")) if row else None
        if row:
            from .domain.effective_intensity import plan_training_goal

            training_goal = plan_training_goal(row.get("prescription_json"))
            if is_deload_session(row.get("deload_source")):
                deload = load_deload_prescription(
                    conn, ids[0], ids[1], ids[2], row.get("prescription_json"), step_kg, row.get("plan_reps_target"),
                    plan_row=row,
                )
    return PairEngineContext(
        step_kg=step_kg,
        decision=decision,
        exercise_row_id=exercise_row_id,
        training_goal=training_goal,
        load_convention=read_pair_load_convention(conn, mapping_id, gym_equipment_id),
        deload=deload,
    )


def load_deload_prescription(
    conn: Any,
    user_id: Any,
    mapping_id: Any,
    gym_equipment_id: Any,
    prescription_json: Any,
    step_kg: Any,
    plan_reps_target: Any = None,
    *,
    plan_row: Optional[Mapping[str, Any]] = None,
) -> DeloadPrescription:
    """Prescricao de deload do exercicio em andamento (M97 D): base = a carga
    sugerida pela faixa %1RM congelada (forca com e1RM), senao a de trabalho
    da TRILHA do par no objetivo do snapshot (sem trilha, a sugerida
    congelada), pelo dono `domain/deload.py`. So e lida em sessao de deload.

    M97-D2: par TROCADO no Modo Treino (`plan_row` com o par planejado do
    item, `deload.pair_swapped`): o snapshot congelado e do par ORIGINAL, entao
    a base e a do PAR NOVO, a mesma da 1a serie
    (`services.swapped_pair_deload_prescription`). Falha nessa leitura: a
    base de antes (logado)."""
    from . import deload_periods
    from .domain.deload import pair_swapped
    from .domain.effective_intensity import frozen_snapshot, plan_item_goal, plan_training_goal

    if plan_row is not None and pair_swapped(
        plan_row.get("plan_mapping_id"), plan_row.get("plan_gym_equipment_id"), mapping_id, gym_equipment_id
    ):
        try:
            from .services import swapped_pair_deload_prescription

            return swapped_pair_deload_prescription(
                conn,
                int(user_id),
                int(mapping_id),
                _int(gym_equipment_id),
                plan_item_goal(prescription_json, plan_row.get("plan_training_goal")),
            )
        except Exception:  # noqa: BLE001 - contexto opcional: a base de antes
            logger.exception("engine_decision_context.swapped_pair_deload_failed")
    goal = plan_training_goal(prescription_json)
    with conn.cursor() as cur:
        working = deload_periods.track_working_load_safe(cur, user_id, mapping_id, gym_equipment_id, goal)
    return item_deload_prescription(
        track_working_load_kg=working,
        snapshot=frozen_snapshot(prescription_json),
        plan_row={"reps_target": plan_reps_target, "training_goal": goal},
        step_kg=step_kg,
    )


def read_pair_load_convention(conn: Any, mapping_id: Any, gym_equipment_id: Any) -> Optional[str]:
    """Convenção de carga do par para a regra da próxima série. Sem par:
    None (= `total`). Falha de leitura: None + log (nunca derruba a série nem
    a IA; a regra então vale como `total`)."""
    from .load_conventions import pair_load_convention

    if _int(mapping_id) is None or _int(gym_equipment_id) is None:
        return None
    try:
        with conn.cursor() as cur:
            return pair_load_convention(cur, mapping_id, gym_equipment_id).value
    except Exception:  # noqa: BLE001 - contexto opcional
        logger.exception("engine_decision_context.load_convention_failed")
        return None


_NO_PLANNED_RPE = object()
_NO_SET_INDEX = object()


def payload_working_set_index(values: Mapping[str, Any]) -> int:
    """Ordem (0-based) da série avaliada entre as séries de TRABALHO do
    exercício na sessão: quantas `working` vieram antes em
    `previous_sets_this_session` (sem `set_type` = `working`)."""
    previous = values.get("previous_sets_this_session") or []
    return sum(1 for item in previous if normalize_set_type((item or {}).get("set_type")) == WORKING)


def next_set_load_for(
    values: Mapping[str, Any],
    context: Optional[PairEngineContext],
    *,
    planned_rpe: Any = _NO_PLANNED_RPE,
    working_set_index: Any = _NO_SET_INDEX,
    fatigue_band: Any = None,
    rpe_ceiling: Any = None,
    ruleset: Optional[RuleSet] = None,
    intensity_status: Optional[str] = None,
) -> Optional[NextSetLoad]:
    """Regra da próxima série (`domain/next_set_load.py`) para a série em
    `values` (payload da avaliação ou do salvamento: `set_type`, `reps`,
    `weight_kg`, `rpe`, `joint_pain`, `muscle_pain`), com o passo e a
    convenção do par. Só monta os `SetFacts`: a regra é do dono.

    Rampa planejada: `planned_rpe` explícito (salvamento da série: ficha
    congelada + ordem entre as séries de trabalho) ou, sem ele, o
    `rpe_target` do payload na ordem das séries anteriores
    (`payload_working_set_index`) - o que o Modo Treino mostrou à IA.

    R1 (2026-10-20): `working_set_index` (1-based; sem ele, a ordem no
    payload + 1), `fatigue_band` (faixa de fadiga prévia do exercício, lida
    por quem chama; None = desconhecida), `rpe_ceiling` (teto efetivo da
    série) e `ruleset` (None = `DEFAULT_RULESET`; o conjunto cadastrado chega
    pelo loader do R2).

    Medidas de série (Lote T3): `values["measure"]` vai para os fatos; o
    dono (`evaluate_ruleset`) devolve None fora de `reps`.

    M96: `intensity_status` é o status de esforço da MESMA série
    (`SetIntensityReading.status`, dono `progression_engine`); o dono da
    regra o usa só para o motivo do MANTER (veredito único no card).

    M97 D: com `context.deload` (sessão de deload), a carga da próxima série
    é a do dono do deload (`deload_next_set_load`), não a do conjunto."""
    if context is not None and context.deload is not None:
        return deload_next_set_load(
            values.get("weight_kg"),
            context.deload,
            context.step_kg,
            set_type=values.get("set_type"),
            measure=values.get("measure"),
        )
    payload_index = payload_working_set_index(values)
    if planned_rpe is _NO_PLANNED_RPE:
        planned_rpe = planned_ramp_rpe(values.get("rpe_target"), payload_index)
    if working_set_index is _NO_SET_INDEX:
        working_set_index = payload_index + 1
    facts = SetFacts(
        set_type=values.get("set_type") or WORKING,
        working_set_index=working_set_index,
        fatigue_band=fatigue_band,
        reps=values.get("reps"),
        rpe=values.get("rpe"),
        rpe_ceiling=rpe_ceiling,
        planned_rpe=planned_rpe,
        weight_kg=values.get("weight_kg"),
        step_kg=context.step_kg if context else None,
        load_convention=context.load_convention if context else None,
        joint_pain=values.get("joint_pain"),
        muscle_pain=values.get("muscle_pain"),
        measure=values.get("measure"),
        intensity_status=intensity_status,
    )
    return evaluate_ruleset(facts, ruleset or DEFAULT_RULESET)


def load_logged_exercise_decision(conn: Any, user_id: Any, exercise_row_id: Any) -> Optional[EngineDecision]:
    """Decisão de hoje de um exercício já lançado (`exercises.id`)."""
    ids = (_int(exercise_row_id), _int(user_id))
    if None in ids:
        return None
    row = _fetchone(conn, LOGGED_EXERCISE_SNAPSHOT_SQL, ids)
    return decision_from_snapshot(row.get("prescription_json")) if row else None


def load_activity_decisions(conn: Any, user_id: Any, activity_id: Any) -> dict[str, Any]:
    """Decisões do treino para o resumo de fim de treino:
    `{"exercises": {exercises.id: EngineDecision}, "groups": [GroupDecision]}`."""
    ids = (_int(activity_id), _int(user_id))
    if None in ids:
        return {"exercises": {}, "groups": []}
    exercises: dict[int, EngineDecision] = {}
    for row in _fetchall(conn, ACTIVITY_SNAPSHOTS_SQL, ids):
        decision = decision_from_snapshot(row.get("prescription_json"))
        if decision is not None and _int(row.get("exercise_row_id")) is not None:
            exercises[_int(row.get("exercise_row_id"))] = decision
    groups = group_decisions_from_rows(_fetchall(conn, ACTIVITY_GROUP_DECISIONS_SQL, (ids[1], ids[0])))
    return {"exercises": exercises, "groups": groups}


def safe_read(reader, *args, default=None, event: str = "engine_decision_context.read_failed"):
    """Leitura opcional: falha (tabela ausente, banco fora) vira `default` e
    um log — a IA segue sem a decisão e é instruída a não sugerir carga."""
    try:
        return reader(*args)
    except Exception:  # noqa: BLE001 - contexto opcional nunca derruba a IA
        logger.exception(event)
        return default
