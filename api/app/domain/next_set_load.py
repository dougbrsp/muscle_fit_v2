"""Carga da PRÓXIMA série dentro da sessão (Modo Treino) - regra do usuário de
2026-10-12, generalizada em CONJUNTO DE REGRAS em 2026-10-20 (PR R1). Dono
único (mapa `docs/motores/FONTES_DA_VERDADE.md` §36, §36.1 e §59; padrões
`carga_proxima_serie`, `carga_percentual` e `regra_carga_cadastrada` da guarda).

Regra (literal do usuário): "se para atingir o RPE 10 a série precisou passar
de 15 repetições ou se parou em 12 repetições com RPE < 9 então a carga
precisa ser aumentada. Se o RPE 10 veio antes de 6 repetições então a carga
deve ser reduzida."

Leitura aplicada (premissas do TL, na ordem de prevalência):

1. **dor** - dor articular relatada ou dor muscular acima do limiar do motor de
   recuperação (`muscle_recovery_service.muscle_pain_blocks_direct_work`,
   `MUSCLE_PAIN_BLOCK_ABOVE`) -> REDUZIR um passo, acima de tudo;
2. **mais de `NEXT_SET_INCREASE_ABOVE_REPS` reps** (com qualquer RPE, inclusive
   sem RPE: 16+ reps já dizem que a carga está leve) -> AUMENTAR um passo;
3. **`NEXT_SET_INCREASE_MIN_REPS_LOW_RPE` reps ou mais com RPE abaixo de
   `NEXT_SET_LOW_RPE_BELOW`** (RPE informado) -> AUMENTAR um passo;
4. **RPE de falha (`training_safety.FAILURE_RPE`) com menos de
   `NEXT_SET_DECREASE_BELOW_REPS` reps** -> REDUZIR um passo;
5. senão -> MANTER a carga executada.

Rampa PLANEJADA de RPE (decisão do usuário de 2026-10-12, "trate a rampa, desde
que planejada como exceção"): quando o `rpe_target` da ficha é uma rampa
(`"8-9-10"`, parser único `training_history.parse_rpe_ramp`, degrau da série
por `PrescribedSpec.rpe_target_for_set`), o limiar da condição 3 passa a ser
`min(NEXT_SET_LOW_RPE_BELOW, RPE planejado da série)`: série 1 de "8-9-10"
(planejada 8) com 12 reps em RPE 8 -> MANTER (motivo "RPE dentro da rampa
planejada"); em RPE 7 -> AUMENTAR; série 2 (planejada 9) com 12 @ 8 -> AUMENTAR.
Alvo único (`"9"`) não é rampa: regra sem exceção. As condições 1, 2 e 4 não
mudam. De onde vem a rampa: no salvamento da série, da ficha congelada
(`prescription_json.rpe_target`, senão `workout_plan_exercises.rpe_target`) e
da ordem da série entre as séries de trabalho do exercício; na IA por série, do
`rpe_target` e das séries anteriores do payload (o que o Modo Treino mostrou).
Treino livre (sem ficha): sem rampa.

Escopo: só série `working` com carga (> 0) e reps. Fora (sem sugestão,
`None`): aquecimento/ajuste/aplicação de técnica, treino sem carga e as
convenções `assisted` e `bodyweight` (`load_conventions`). `assisted` NÃO é
invertido de propósito: lá "mais carga" é mais assistência (mais fácil), e o
rótulo "Aumentar carga" diria o contrário do que a pessoa deve fazer; nenhuma
série `assisted` existe no histórico do usuário 1 (medição de 2026-10-12).
`bodyweight` não tem a carga movida na série. `bodyweight_plus` (lastro) vale.

Passo: o do equipamento (`progression_engine.load_increment`, D12), a carga
nova arredondada ao passo (`progression_engine.round_to_equipment_step`).
Manter devolve a carga EXECUTADA exata (alcançável por definição). Reduzir
abaixo do primeiro passo não existe: direção REDUZIR sem carga (`load_kg`
None).

Esta regra é INTRA-SESSÃO. A prescrição da próxima SESSÃO continua sendo do
motor de progressão (qualificação, excesso de reps, platô) - nada aqui a toca.

CONJUNTO DE REGRAS (R1, decisões do usuário de 2026-10-20)
----------------------------------------------------------

A regra acima passa a ser o `DEFAULT_RULESET`: um `RuleSet` com 3 `Rule`s
ordenadas, avaliadas por `evaluate_ruleset(facts, ruleset)`; a PRIMEIRA que
casa vence. Cada regra combina, em "e": posição da série entre as séries de
trabalho do exercício (`SetPosition`, 1-based), contexto de fadiga prévia do
exercício (`FatigueCondition` = faixas de `domain/prior_fatigue.py`, lida
ANTES da 1ª série do exercício, a mesma chave da trilha), condição de reps
(`Condition` com número ou os tokens `floor`/`ceiling` = `reps_floor`/
`reps_ceiling` do conjunto), condição de RPE (número ou o token `max` = teto
efetivo "RPE máximo" da série, `effective_intensity.EffectiveIntensity.ceiling`),
`respect_planned_ramp` (o limiar superior de RPE vira `min(limiar, RPE
planejado da série)` quando há rampa) e a ação (`HOLD`/`INCREASE_LOAD`/
`DECREASE_LOAD`) com a magnitude (`steps` n = n passos do equipamento;
`percent` p = carga × (1 ± p/100) arredondada ao passo, garantindo pelo menos
um passo na direção). O percentual é aplicado SÓ AQUI: tela, prompt, SQL e
lambdas recebem a carga em kg e o texto prontos (`NextSetLoad`,
`describe_rule`). A dor (condição 1) NÃO é configurável: vale antes de qualquer
regra. Nenhuma regra casando -> MANTER (`NEXT_SET_WITHIN_PLANNED_RAMP` se
alguma regra só não casou por causa da rampa, senão `NEXT_SET_WITHIN_RULE`).
Regra cadastrada sem `reason_code` -> `NEXT_SET_RULE_MATCHED`, com o rótulo
igual a `describe_rule`. O conjunto PUBLICADO (migração 088, R2) chega pelo
loader `app/next_set_load_rules.py` (I/O: só converte e grava; sem linha ou
falha de leitura -> `DEFAULT_RULESET`); `ruleset_uses_fatigue` diz ao chamador
se a faixa de fadiga precisa ser lida. A tela de cadastro vem no R4.

Consumidores (não recalculam): `engine_decision_context.next_set_load_for`
(monta os `SetFacts`), `bound_ai_load` (a carga final da IA por série É a
desta regra), o prompt da IA por série (`exercise_set_evaluation_ai`,
set-eval-v7), o salvamento da série (`workout_sessions.add_session_set`, campo
`next_set_load`) e o Modo Treino (códigos, rótulos e vocabulário via
`motor-constants.generated.json`, bloco `next_set_load`).

Python puro, sem I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable, Final, Mapping, Optional, Union

from ..load_conventions import LoadConvention, normalize_load_convention
from ..set_types import WORKING, normalize_set_type
from .exercise_briefing import DECISION_LABELS
from .intensity_status_labels import intensity_status_label
from .prior_fatigue import PRIOR_FATIGUE_BAND_LABELS, PriorFatigueBand
from .progression_engine import (
    DECREASE_LOAD,
    HOLD,
    INCREASE_LOAD,
    INTENSITY_ABOVE_SAFE_CEILING,
    INTENSITY_BELOW_TARGET,
    EXCESS_REPS_FAST_TRACK,
    STRENGTH_REPS,
    load_increment,
    round_to_equipment_step,
)
from .training_goal import TrainingGoal, read_goal
from .set_measures import is_rep_based
from .training_history import PrescribedSpec, parse_rpe_ramp
from .training_safety import FAILURE_RPE, STRENGTH_TARGET_RPE

# --- limiares (regra do usuário, 2026-10-12) ---------------------------------
NEXT_SET_INCREASE_ABOVE_REPS: Final[int] = 15  # 16+ reps: carga leve, sobe
NEXT_SET_INCREASE_MIN_REPS_LOW_RPE: Final[int] = 12  # 12+ reps ...
NEXT_SET_LOW_RPE_BELOW: Final[int] = 9  # ... com RPE abaixo de 9: sobe
NEXT_SET_DECREASE_BELOW_REPS: Final[int] = 6  # RPE de falha antes de 6 reps: desce
NEXT_SET_FAILURE_RPE: Final[int] = FAILURE_RPE  # RPE 10 = falha (dono: training_safety)

# Convenções em que a regra não se aplica (ver docstring).
NEXT_SET_EXCLUDED_CONVENTIONS: Final[frozenset[LoadConvention]] = frozenset(
    {LoadConvention.ASSISTED, LoadConvention.BODYWEIGHT}
)

# --- motivos (códigos estáveis; rótulos em `reason_labels`) ------------------
REASON_PAIN = "NEXT_SET_PAIN"
REASON_REPS_ABOVE_LIMIT = "NEXT_SET_REPS_ABOVE_LIMIT"
REASON_LOW_RPE_AT_REPS = "NEXT_SET_LOW_RPE_AT_REPS"
REASON_FAILURE_BEFORE_MIN_REPS = "NEXT_SET_FAILURE_BEFORE_MIN_REPS"
REASON_WITHIN_RULE = "NEXT_SET_WITHIN_RULE"
REASON_WITHIN_PLANNED_RAMP = "NEXT_SET_WITHIN_PLANNED_RAMP"
# R1: regra CADASTRADA (sem código próprio) casou; o rótulo é `describe_rule`.
REASON_RULE_MATCHED = "NEXT_SET_RULE_MATCHED"
# M96 (veredito único no card da série, 2026-10-25): nenhuma regra casou
# (MANTER), mas o status de ESFORÇO da série (`progression_engine.
# set_intensity_status`, que o card mostra ao lado) não é "atingido" - o motivo
# não pode dizer "RPE dentro do esperado". A carga continua MANTIDA (regra do
# usuário intacta); só o motivo passa a derivar do mesmo status.
REASON_HOLD_EFFORT_BELOW_TARGET = "NEXT_SET_HOLD_EFFORT_BELOW_TARGET"
REASON_HOLD_ABOVE_SAFE_CEILING = "NEXT_SET_HOLD_ABOVE_SAFE_CEILING"
# M97 D: sessao de deload - a carga da proxima serie vem do dono
# `domain/deload.py` (`deload_next_set_load`), nao das regras abaixo.
REASON_DELOAD = "NEXT_SET_DELOAD"
NEXT_SET_REASONS: Final[tuple[str, ...]] = (
    REASON_PAIN,
    REASON_REPS_ABOVE_LIMIT,
    REASON_LOW_RPE_AT_REPS,
    REASON_FAILURE_BEFORE_MIN_REPS,
    REASON_WITHIN_RULE,
    REASON_WITHIN_PLANNED_RAMP,
    REASON_RULE_MATCHED,
    REASON_HOLD_EFFORT_BELOW_TARGET,
    REASON_HOLD_ABOVE_SAFE_CEILING,
    REASON_DELOAD,
)

# Status de esforço da série (código do dono `progression_engine`) -> motivo
# do MANTER quando nenhuma regra casou. Status ausente ou atingido: o motivo
# de sempre ("dentro do esperado" / "dentro da rampa planejada").
_HOLD_REASON_BY_INTENSITY_STATUS: Final[Mapping[str, str]] = {
    INTENSITY_BELOW_TARGET: REASON_HOLD_EFFORT_BELOW_TARGET,
    INTENSITY_ABOVE_SAFE_CEILING: REASON_HOLD_ABOVE_SAFE_CEILING,
}

# Motivo gravado em `load_adjustments[]` da IA por série quando a carga da IA
# é trocada pela desta regra.
REASON_NEXT_SET_RULE = "NEXT_SET_LOAD_RULE"

# R2 (migração 088): origem do conjunto avaliado e versão do motor gravada com
# o conjunto cadastrado (`next_set_load_rule_sets.engine_version`).
RULESET_SOURCE_DEFAULT: Final[str] = "default"
RULESET_SOURCE_PUBLISHED: Final[str] = "published"
RULESET_SOURCE_DRAFT: Final[str] = "draft"
RULESET_ENGINE_VERSION: Final[str] = "next-set-load-rules-v1"


def _muscle_pain_block_above() -> int:
    # Import tardio: o pacote `intelligence` importa o `domain`.
    from ..intelligence.muscle_recovery_service import MUSCLE_PAIN_BLOCK_ABOVE

    return MUSCLE_PAIN_BLOCK_ABOVE


def _muscle_pain_blocks(muscle_pain: Any) -> bool:
    from ..intelligence.muscle_recovery_service import muscle_pain_blocks_direct_work

    return muscle_pain_blocks_direct_work(muscle_pain)


def _deload_reason_label() -> str:
    # Import tardio: `domain/deload.py` importa este modulo (NextSetLoad).
    from .deload import deload_reason_label

    return deload_reason_label()


def reason_labels() -> Mapping[str, str]:
    """Rótulo de cada motivo, com os limiares interpolados das constantes."""
    return {
        REASON_PAIN: f"Dor articular ou dor muscular acima de {_muscle_pain_block_above()}/10",
        REASON_REPS_ABOVE_LIMIT: f"Mais de {NEXT_SET_INCREASE_ABOVE_REPS} repetições: carga leve",
        REASON_LOW_RPE_AT_REPS: (
            f"{NEXT_SET_INCREASE_MIN_REPS_LOW_RPE} ou mais repetições com RPE abaixo de "
            f"{NEXT_SET_LOW_RPE_BELOW}: carga leve"
        ),
        REASON_FAILURE_BEFORE_MIN_REPS: (
            f"RPE {NEXT_SET_FAILURE_RPE} antes de {NEXT_SET_DECREASE_BELOW_REPS} repetições: carga pesada"
        ),
        REASON_WITHIN_RULE: "Repetições e RPE dentro do esperado: carga adequada",
        REASON_WITHIN_PLANNED_RAMP: "RPE dentro da rampa planejada: carga adequada",
        REASON_RULE_MATCHED: "Regra de carga cadastrada",
        # M96: o rótulo do status vem do dono (`intensity_status_labels`), o
        # mesmo que o card mostra ao lado.
        REASON_HOLD_EFFORT_BELOW_TARGET: (
            f"{intensity_status_label(INTENSITY_BELOW_TARGET)}: mantenha a carga e busque o RPE-alvo"
        ),
        REASON_HOLD_ABOVE_SAFE_CEILING: (
            f"{intensity_status_label(INTENSITY_ABOVE_SAFE_CEILING)}: mantenha a carga e pare antes da falha"
        ),
        # M97 D: rotulo do dono do deload (numeros interpolados de la).
        REASON_DELOAD: _deload_reason_label(),
    }


def reason_label(code: Optional[str]) -> Optional[str]:
    return reason_labels().get(code) if code else None


# ---------------------------------------------------------------------------
# Vocabulário do conjunto de regras (R1)
# ---------------------------------------------------------------------------


class SetPosition(str, Enum):
    """Posição da série avaliada entre as séries de TRABALHO do exercício
    (1-based). `any` casa qualquer posição, inclusive índice desconhecido."""

    ANY = "any"
    FIRST = "first"
    SECOND = "second"
    THIRD_PLUS = "third_plus"


SET_POSITION_LABELS: Final[Mapping[SetPosition, str]] = {
    SetPosition.ANY: "qualquer",
    SetPosition.FIRST: "1ª série",
    SetPosition.SECOND: "2ª série",
    SetPosition.THIRD_PLUS: "3ª série em diante",
}
# Índice 1-based (mínimo, máximo ou None = sem teto) coberto por cada posição.
_SET_POSITION_RANGE: Final[Mapping[SetPosition, tuple[int, Optional[int]]]] = {
    SetPosition.FIRST: (1, 1),
    SetPosition.SECOND: (2, 2),
    SetPosition.THIRD_PLUS: (3, None),
}


class FatigueCondition(str, Enum):
    """Contexto de fadiga prévia exigido pela regra: `any` ou uma faixa de
    `PriorFatigueBand` (mesmos valores). Faixa desconhecida só casa `any`."""

    ANY = "any"
    RESTED = PriorFatigueBand.RESTED.value
    PRE_FATIGUED = PriorFatigueBand.PRE_FATIGUED.value
    HEAVILY_FATIGUED = PriorFatigueBand.HEAVILY_FATIGUED.value


def fatigue_condition_labels() -> dict[str, str]:
    """Rótulos das faixas, os do dono da fadiga prévia + "qualquer"."""
    labels = {FatigueCondition.ANY.value: "qualquer"}
    for band in PriorFatigueBand:
        labels[band.value] = PRIOR_FATIGUE_BAND_LABELS[band]
    return labels


class ConditionOp(str, Enum):
    GT = ">"
    GTE = ">="
    LT = "<"
    LTE = "<="
    BETWEEN = "between"


CONDITION_OP_LABELS: Final[Mapping[ConditionOp, str]] = {
    ConditionOp.GT: ">",
    ConditionOp.GTE: "≥",
    ConditionOp.LT: "<",
    ConditionOp.LTE: "≤",
    ConditionOp.BETWEEN: "entre",
}

# Tokens: valores simbólicos resolvidos na avaliação (reps pelo conjunto, RPE
# pelo teto efetivo da série).
REPS_TOKEN_FLOOR: Final[str] = "floor"
REPS_TOKEN_CEILING: Final[str] = "ceiling"
RPE_TOKEN_MAX: Final[str] = "max"
REPS_TOKENS: Final[tuple[str, ...]] = (REPS_TOKEN_FLOOR, REPS_TOKEN_CEILING)
RPE_TOKENS: Final[tuple[str, ...]] = (RPE_TOKEN_MAX,)
REPS_TOKEN_LABELS: Final[Mapping[str, str]] = {REPS_TOKEN_FLOOR: "piso", REPS_TOKEN_CEILING: "teto"}
RPE_TOKEN_LABELS: Final[Mapping[str, str]] = {RPE_TOKEN_MAX: "máximo"}


class MagnitudeKind(str, Enum):
    STEPS = "steps"
    PERCENT = "percent"


MAGNITUDE_KIND_LABELS: Final[Mapping[MagnitudeKind, str]] = {
    MagnitudeKind.STEPS: "passos do equipamento",
    MagnitudeKind.PERCENT: "percentual da carga",
}

# Ações que uma regra pode tomar (códigos de ação do motor).
RULE_ACTIONS: Final[tuple[str, ...]] = (HOLD, INCREASE_LOAD, DECREASE_LOAD)

# --- limites do cadastro (validação; a tela só os exibe) --------------------
RULESET_MAX_RULES: Final[int] = 20
MAGNITUDE_PERCENT_MIN: Final[int] = 1
MAGNITUDE_PERCENT_MAX: Final[int] = 50
MAGNITUDE_STEPS_MIN: Final[int] = 1
MAGNITUDE_STEPS_MAX: Final[int] = 4
REPS_FLOOR_MIN: Final[int] = 1
REPS_CEILING_MAX: Final[int] = 30
RULE_RPE_MIN: Final[int] = 1
RULE_RPE_MAX: Final[int] = FAILURE_RPE

# Códigos de validação (`validate_ruleset`); os de aviso começam com o prefixo.
RULESET_WARNING_PREFIX: Final[str] = "WARN_"
RULESET_EMPTY = "RULESET_EMPTY"
RULESET_TOO_MANY_RULES = "RULESET_TOO_MANY_RULES"
RULESET_POSITIONS_NOT_SEQUENTIAL = "RULESET_POSITIONS_NOT_SEQUENTIAL"
REPS_FLOOR_BELOW_MIN = "REPS_FLOOR_BELOW_MIN"
REPS_CEILING_ABOVE_MAX = "REPS_CEILING_ABOVE_MAX"
REPS_FLOOR_NOT_BELOW_CEILING = "REPS_FLOOR_NOT_BELOW_CEILING"
RULE_ACTION_UNKNOWN = "RULE_ACTION_UNKNOWN"
RULE_WITHOUT_CONDITION = "RULE_WITHOUT_CONDITION"
RULE_HOLD_WITH_MAGNITUDE = "RULE_HOLD_WITH_MAGNITUDE"
RULE_MAGNITUDE_MISSING = "RULE_MAGNITUDE_MISSING"
RULE_MAGNITUDE_OUT_OF_RANGE = "RULE_MAGNITUDE_OUT_OF_RANGE"
RULE_STEPS_NOT_INTEGER = "RULE_STEPS_NOT_INTEGER"
RULE_REPS_TOKEN_UNKNOWN = "RULE_REPS_TOKEN_UNKNOWN"
RULE_REPS_OUT_OF_RANGE = "RULE_REPS_OUT_OF_RANGE"
RULE_RPE_TOKEN_UNKNOWN = "RULE_RPE_TOKEN_UNKNOWN"
RULE_RPE_OUT_OF_RANGE = "RULE_RPE_OUT_OF_RANGE"
RULE_BETWEEN_INVALID = "RULE_BETWEEN_INVALID"
RULE_RAMP_WITHOUT_RPE = "RULE_RAMP_WITHOUT_RPE"
RULE_REASON_CODE_UNKNOWN = "RULE_REASON_CODE_UNKNOWN"
WARN_NO_DECREASE_RULE = RULESET_WARNING_PREFIX + "NO_DECREASE_RULE"
WARN_RULE_UNREACHABLE = RULESET_WARNING_PREFIX + "RULE_UNREACHABLE"


# ---------------------------------------------------------------------------
# Contrato
# ---------------------------------------------------------------------------

NumberOrToken = Union[Decimal, str]


@dataclass(frozen=True)
class Condition:
    """`<campo> <op> <valor>`; `between` usa `value` (mínimo) e `value_to`
    (máximo), fechado nos dois lados. `value` pode ser um token."""

    op: ConditionOp
    value: NumberOrToken
    value_to: Optional[NumberOrToken] = None


@dataclass(frozen=True)
class Magnitude:
    kind: MagnitudeKind
    value: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "value": _number(self.value)}


ONE_STEP: Final[Magnitude] = Magnitude(MagnitudeKind.STEPS, Decimal(1))


@dataclass(frozen=True)
class Rule:
    position: int
    set_position: SetPosition = SetPosition.ANY
    fatigue: FatigueCondition = FatigueCondition.ANY
    reps: Optional[Condition] = None
    rpe: Optional[Condition] = None
    respect_planned_ramp: bool = False
    action: str = HOLD
    magnitude: Optional[Magnitude] = None
    # Motivo próprio (as regras padrão têm os `NEXT_SET_*` de sempre); None =
    # `REASON_RULE_MATCHED` com `describe_rule` como rótulo.
    reason_code: Optional[str] = None


@dataclass(frozen=True)
class RuleSet:
    reps_floor: int
    reps_ceiling: int
    rules: tuple[Rule, ...]
    version: int = 0  # 0 = o conjunto padrão em código
    source: str = RULESET_SOURCE_DEFAULT


@dataclass(frozen=True)
class SetFacts:
    """Tudo que a avaliação precisa saber da série executada. Quem monta
    (`engine_decision_context.next_set_load_for`) só lê; nada aqui é regra."""

    set_type: Any = WORKING
    working_set_index: Optional[int] = None  # 1-based entre as `working` do exercício
    fatigue_band: Any = None  # `PriorFatigueBand`/valor; None = desconhecida
    reps: Any = None
    rpe: Any = None
    rpe_ceiling: Any = None  # teto efetivo da série ("RPE máximo")
    planned_rpe: Any = None  # RPE planejado da série pela rampa da ficha
    weight_kg: Any = None
    step_kg: Any = None
    load_convention: Any = None
    joint_pain: Any = None
    muscle_pain: Any = None
    # Lote T3 (medidas de serie): medida da serie (ausente = reps).
    measure: Any = None
    # M96: status de ESFORÇO da série pelo dono (`progression_engine.
    # set_intensity_status`, o que o card mostra). Só escolhe o motivo do
    # MANTER; None = desconhecido (motivo de sempre).
    intensity_status: Optional[str] = None


@dataclass(frozen=True)
class NextSetLoad:
    """Sugestão de carga da próxima série: direção (código de ação do motor),
    carga (no passo; None = reduzir sem passo abaixo) e motivo."""

    direction: str
    load_kg: Optional[float]
    reason: str
    executed_kg: float
    step_kg: float
    # RPE planejado DESTA série pela rampa da ficha (None = sem rampa).
    planned_rpe: Optional[float] = None
    # R1: a regra que casou (None = dor ou nenhuma), o seu texto, a versão do
    # conjunto (0 = padrão em código) e a magnitude aplicada.
    rule_position: Optional[int] = None
    rule_text: Optional[str] = None
    ruleset_version: int = 0
    magnitude: Optional[Magnitude] = None

    @property
    def direction_label(self) -> str:
        return DECISION_LABELS[self.direction]

    @property
    def reason_label(self) -> str:
        if self.reason == REASON_RULE_MATCHED and self.rule_text:
            return self.rule_text
        if self.reason == REASON_WITHIN_PLANNED_RAMP and self.planned_rpe is not None:
            return f"RPE dentro da rampa planejada (alvo {_format_rpe(self.planned_rpe)} nesta série)"
        return reason_labels()[self.reason]

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "direction_label": self.direction_label,
            "load_kg": self.load_kg,
            "executed_kg": self.executed_kg,
            "step_kg": self.step_kg,
            "reason": self.reason,
            "reason_label": self.reason_label,
            "planned_rpe": self.planned_rpe,
            "rule_position": self.rule_position,
            "rule_text": self.rule_text,
            "ruleset_version": self.ruleset_version,
            "magnitude": self.magnitude.to_dict() if self.magnitude is not None else None,
        }


# ---------------------------------------------------------------------------
# Conjunto padrão = a regra de 2026-10-12 (com a rampa de 36.1)
# ---------------------------------------------------------------------------

DEFAULT_RULESET: Final[RuleSet] = RuleSet(
    reps_floor=NEXT_SET_DECREASE_BELOW_REPS,
    reps_ceiling=NEXT_SET_INCREASE_MIN_REPS_LOW_RPE,
    rules=(
        Rule(
            position=1,
            reps=Condition(ConditionOp.GT, Decimal(NEXT_SET_INCREASE_ABOVE_REPS)),
            action=INCREASE_LOAD,
            magnitude=ONE_STEP,
            reason_code=REASON_REPS_ABOVE_LIMIT,
        ),
        Rule(
            position=2,
            reps=Condition(ConditionOp.GTE, Decimal(NEXT_SET_INCREASE_MIN_REPS_LOW_RPE)),
            rpe=Condition(ConditionOp.LT, Decimal(NEXT_SET_LOW_RPE_BELOW)),
            respect_planned_ramp=True,
            action=INCREASE_LOAD,
            magnitude=ONE_STEP,
            reason_code=REASON_LOW_RPE_AT_REPS,
        ),
        Rule(
            position=3,
            reps=Condition(ConditionOp.LT, Decimal(NEXT_SET_DECREASE_BELOW_REPS)),
            rpe=Condition(ConditionOp.GTE, Decimal(NEXT_SET_FAILURE_RPE)),
            action=DECREASE_LOAD,
            magnitude=ONE_STEP,
            reason_code=REASON_FAILURE_BEFORE_MIN_REPS,
        ),
    ),
)


# ---------------------------------------------------------------------------
# Conjunto de FORCA (M97 F2, 2026-10-25; mapa §66.2) - versionado em codigo
# ---------------------------------------------------------------------------
# Regras analogas as de hipertrofia, na faixa de forca da progressao
# (`progression_engine.STRENGTH_REPS`, piso/teto do conjunto) e no RPE-alvo de
# forca (`training_safety.STRENGTH_TARGET_RPE`):
#   1. mais de `STRENGTH_NEXT_SET_INCREASE_ABOVE_REPS` reps (bem acima do teto:
#      carga leve para forca) -> AUMENTAR `STRENGTH_NEXT_SET_LARGE_INCREASE_PERCENT`%;
#   2. teto da faixa ou mais com RPE abaixo do alvo de forca (sobrou reserva;
#      respeita a rampa planejada) -> AUMENTAR `STRENGTH_NEXT_SET_INCREASE_PERCENT`%;
#   3. RPE de falha antes do piso -> REDUZIR `STRENGTH_NEXT_SET_DECREASE_PERCENT`%.
# Em carga alta (>= 80% de 1RM) um passo fixo do equipamento e pequeno demais
# no agachamento e grande demais no isolador: a magnitude e PERCENTUAL
# (`_percent_load`, ao menos um passo na direcao). O conjunto PUBLICADO (088,
# tela `/regras-carga`) continua sendo o de hipertrofia; a forca usa este, em
# codigo (`ruleset_for_goal`). Mudou uma regra/numero -> sobe
# `STRENGTH_RULESET_VERSION`. Valores a calibrar (PR da F2).
STRENGTH_RULESET_VERSION: Final[int] = 1
RULESET_SOURCE_STRENGTH: Final[str] = f"strength-v{STRENGTH_RULESET_VERSION}"
STRENGTH_NEXT_SET_INCREASE_ABOVE_REPS: Final[int] = STRENGTH_REPS[1] + EXCESS_REPS_FAST_TRACK
STRENGTH_NEXT_SET_LOW_RPE_BELOW: Final[int] = STRENGTH_TARGET_RPE
STRENGTH_NEXT_SET_LARGE_INCREASE_PERCENT: Final[int] = 10
STRENGTH_NEXT_SET_INCREASE_PERCENT: Final[int] = 5
STRENGTH_NEXT_SET_DECREASE_PERCENT: Final[int] = 10

STRENGTH_RULESET: Final[RuleSet] = RuleSet(
    reps_floor=STRENGTH_REPS[0],
    reps_ceiling=STRENGTH_REPS[1],
    rules=(
        Rule(
            position=1,
            reps=Condition(ConditionOp.GT, Decimal(STRENGTH_NEXT_SET_INCREASE_ABOVE_REPS)),
            action=INCREASE_LOAD,
            magnitude=Magnitude(MagnitudeKind.PERCENT, Decimal(STRENGTH_NEXT_SET_LARGE_INCREASE_PERCENT)),
        ),
        Rule(
            position=2,
            reps=Condition(ConditionOp.GTE, REPS_TOKEN_CEILING),
            rpe=Condition(ConditionOp.LT, Decimal(STRENGTH_NEXT_SET_LOW_RPE_BELOW)),
            respect_planned_ramp=True,
            action=INCREASE_LOAD,
            magnitude=Magnitude(MagnitudeKind.PERCENT, Decimal(STRENGTH_NEXT_SET_INCREASE_PERCENT)),
        ),
        Rule(
            position=3,
            reps=Condition(ConditionOp.LT, REPS_TOKEN_FLOOR),
            rpe=Condition(ConditionOp.GTE, Decimal(NEXT_SET_FAILURE_RPE)),
            action=DECREASE_LOAD,
            magnitude=Magnitude(MagnitudeKind.PERCENT, Decimal(STRENGTH_NEXT_SET_DECREASE_PERCENT)),
        ),
    ),
    version=0,
    source=RULESET_SOURCE_STRENGTH,
)


def ruleset_for_goal(training_goal: Any, published: Optional[RuleSet] = None) -> RuleSet:
    """Conjunto de regras da serie em execucao pelo OBJETIVO do item (leitura
    tolerante: ausente = hipertrofia). Hipertrofia: o PUBLICADO (`published`,
    do loader `next_set_load_rules`; None = `DEFAULT_RULESET`) - exatamente o
    de antes. Forca: `STRENGTH_RULESET`."""
    if not ruleset_uses_published(training_goal):
        return STRENGTH_RULESET
    return published or DEFAULT_RULESET


def ruleset_uses_published(training_goal: Any) -> bool:
    """O objetivo usa o conjunto PUBLICADO (088)? So hipertrofia: quem chama
    so le o publicado quando precisa."""
    return read_goal(training_goal) is not TrainingGoal.STRENGTH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_rpe(value: Any) -> str:
    return f"{float(value):g}".replace(".", ",")


def _number(value: Decimal) -> Union[int, float]:
    """Decimal -> número JSON (inteiro quando é inteiro)."""
    return int(value) if value == value.to_integral_value() else float(value)


def _number_text(value: Decimal) -> str:
    return str(_number(value)).replace(".", ",")


def planned_ramp_rpe(rpe_target: Any, working_set_index: Any) -> Optional[Decimal]:
    """RPE planejado da série de trabalho `working_set_index` (0-based) quando
    o `rpe_target` da ficha é uma RAMPA (dois ou mais degraus diferentes).
    Alvo único, sem alvo ou sem índice -> None (sem exceção de rampa)."""
    if working_set_index is None:
        return None
    ramp = parse_rpe_ramp(rpe_target)
    if len(set(ramp)) < 2:
        return None
    return PrescribedSpec(rpe_target=ramp[-1], rpe_targets=ramp).rpe_target_for_set(max(int(working_set_index), 0))


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _convention(value: Any) -> LoadConvention:
    try:
        return normalize_load_convention(value)
    except ValueError:
        return LoadConvention.TOTAL


def _rounded_in_direction(executed: Decimal, step: Decimal, target: Decimal, *, up: bool) -> Optional[float]:
    """`target` arredondado ao múltiplo do passo (`round_to_equipment_step`,
    meio para cima), desde que fique do lado certo da carga executada. Sem
    carga positiva/na direção: None."""
    if target <= 0:
        return None
    rounded = round_to_equipment_step(target, step)
    if rounded is None:
        return None
    value = Decimal(str(rounded))
    if value == executed or (value > executed) != up:
        return None
    return rounded


def _steps_load(executed: Decimal, step: Decimal, steps: int, *, up: bool) -> Optional[float]:
    """Carga executada ± `steps` passos, arredondada ao múltiplo do passo.
    Carga fora do passo (anilha em libra): 21,3 kg com passo 2,5 -> 23,8 -> 25
    / 18,8 -> 20. Sem carga positiva abaixo (reduzir a partir do primeiro
    passo): None."""
    delta = step * max(int(steps), 1)
    return _rounded_in_direction(executed, step, executed + delta if up else executed - delta, up=up)


def _one_step(executed: Decimal, step: Decimal, *, up: bool) -> Optional[float]:
    return _steps_load(executed, step, 1, up=up)


def _percent_load(executed_load: Decimal, step: Decimal, percent: Decimal, *, up: bool) -> Optional[float]:
    """Carga executada × (1 ± p/100), arredondada ao passo do equipamento.
    O arredondamento nunca anula a direção: se cair na própria carga (20 kg
    +5% = 21 -> 20 no passo 2,5), vale um passo inteiro (22,5). Reduzir sem
    passo abaixo: None. ÚNICO lugar do projeto onde um percentual vira kg."""
    signed_percent = percent if up else -percent
    target = executed_load * (1 + signed_percent / 100)
    rounded = _rounded_in_direction(executed_load, step, target, up=up)
    if rounded is None:
        return _steps_load(executed_load, step, 1, up=up)
    return rounded


# ---------------------------------------------------------------------------
# Avaliação
# ---------------------------------------------------------------------------


class _Outcome(Enum):
    MATCH = "match"
    NO_MATCH = "no_match"
    RAMP_BLOCKED = "ramp_blocked"  # casaria sem a rampa planejada


@dataclass(frozen=True)
class _Match:
    direction: str
    reason: str
    rule: Optional[Rule]
    magnitude: Optional[Magnitude]


def _resolve_reps_value(value: Optional[NumberOrToken], ruleset: RuleSet) -> Optional[Decimal]:
    if isinstance(value, str):
        if value == REPS_TOKEN_FLOOR:
            return Decimal(ruleset.reps_floor)
        if value == REPS_TOKEN_CEILING:
            return Decimal(ruleset.reps_ceiling)
        return None
    return _decimal(value)


def _resolve_rpe_value(value: Optional[NumberOrToken], facts: SetFacts) -> Optional[Decimal]:
    if isinstance(value, str):
        return _decimal(facts.rpe_ceiling) if value == RPE_TOKEN_MAX else None
    return _decimal(value)


def _compare(actual: Decimal, op: ConditionOp, threshold: Decimal, threshold_to: Optional[Decimal]) -> bool:
    if op is ConditionOp.GT:
        return actual > threshold
    if op is ConditionOp.GTE:
        return actual >= threshold
    if op is ConditionOp.LT:
        return actual < threshold
    if op is ConditionOp.LTE:
        return actual <= threshold
    return threshold_to is not None and threshold <= actual <= threshold_to


def _position_matches(position: SetPosition, working_set_index: Any) -> bool:
    if position is SetPosition.ANY:
        return True
    index = _int(working_set_index)
    if index is None:
        return False
    low, high = _SET_POSITION_RANGE[position]
    return index >= low and (high is None or index <= high)


def _fatigue_matches(condition: FatigueCondition, band: Any) -> bool:
    if condition is FatigueCondition.ANY:
        return True
    if band is None:
        return False
    value = band.value if isinstance(band, Enum) else str(band)
    return value == condition.value


def _reps_matches(condition: Optional[Condition], reps: Decimal, ruleset: RuleSet) -> bool:
    if condition is None:
        return True
    threshold = _resolve_reps_value(condition.value, ruleset)
    threshold_to = _resolve_reps_value(condition.value_to, ruleset) if condition.op is ConditionOp.BETWEEN else None
    if threshold is None or (condition.op is ConditionOp.BETWEEN and threshold_to is None):
        return False
    return _compare(reps, condition.op, threshold, threshold_to)


def _rpe_outcome(rule: Rule, facts: SetFacts, rpe: Optional[Decimal]) -> _Outcome:
    """Condição de RPE da regra. Sem condição: não exige RPE. Com condição e
    RPE ausente/teto ausente (token `max`): não casa. `respect_planned_ramp`
    com rampa: o limiar SUPERIOR (`<`, `<=`, máximo do `between`) vira
    `min(limiar, RPE planejado)`; se a regra só deixou de casar por isso,
    `RAMP_BLOCKED` (o motivo final vira "dentro da rampa planejada")."""
    condition = rule.rpe
    if condition is None:
        return _Outcome.MATCH
    if rpe is None:
        return _Outcome.NO_MATCH
    threshold = _resolve_rpe_value(condition.value, facts)
    threshold_to = _resolve_rpe_value(condition.value_to, facts) if condition.op is ConditionOp.BETWEEN else None
    if threshold is None or (condition.op is ConditionOp.BETWEEN and threshold_to is None):
        return _Outcome.NO_MATCH
    matched_without_ramp = _compare(rpe, condition.op, threshold, threshold_to)
    planned = _decimal(facts.planned_rpe) if rule.respect_planned_ramp else None
    if planned is None:
        return _Outcome.MATCH if matched_without_ramp else _Outcome.NO_MATCH
    if condition.op in (ConditionOp.LT, ConditionOp.LTE):
        threshold = min(threshold, planned)
    elif condition.op is ConditionOp.BETWEEN and threshold_to is not None:
        threshold_to = min(threshold_to, planned)
    if _compare(rpe, condition.op, threshold, threshold_to):
        return _Outcome.MATCH
    return _Outcome.RAMP_BLOCKED if matched_without_ramp else _Outcome.NO_MATCH


def _rule_outcome(rule: Rule, facts: SetFacts, ruleset: RuleSet, reps: Decimal, rpe: Optional[Decimal]) -> _Outcome:
    if not _position_matches(rule.set_position, facts.working_set_index):
        return _Outcome.NO_MATCH
    if not _fatigue_matches(rule.fatigue, facts.fatigue_band):
        return _Outcome.NO_MATCH
    if not _reps_matches(rule.reps, reps, ruleset):
        return _Outcome.NO_MATCH
    return _rpe_outcome(rule, facts, rpe)


def _classify(facts: SetFacts, ruleset: RuleSet) -> Optional[_Match]:
    """Dor -> regras em ordem (a primeira que casa vence) -> MANTER."""
    reps = _decimal(facts.reps)
    if reps is None or reps <= 0:
        return None
    rpe = _decimal(facts.rpe)
    if bool(facts.joint_pain) or _muscle_pain_blocks(facts.muscle_pain):
        return _Match(DECREASE_LOAD, REASON_PAIN, None, ONE_STEP)
    ramp_blocked = False
    for rule in ruleset.rules:
        outcome = _rule_outcome(rule, facts, ruleset, reps, rpe)
        if outcome is _Outcome.MATCH:
            # Magnitude APLICADA: manter não tem; aumentar/reduzir sem cadastro vale um passo.
            magnitude = None if rule.action == HOLD else (rule.magnitude or ONE_STEP)
            return _Match(rule.action, rule.reason_code or REASON_RULE_MATCHED, rule, magnitude)
        if outcome is _Outcome.RAMP_BLOCKED:
            ramp_blocked = True
    effort_reason = _HOLD_REASON_BY_INTENSITY_STATUS.get(facts.intensity_status or "")
    if effort_reason is not None:
        return _Match(HOLD, effort_reason, None, None)
    return _Match(HOLD, REASON_WITHIN_PLANNED_RAMP if ramp_blocked else REASON_WITHIN_RULE, None, None)


def _load_for(match: _Match, executed: Decimal, step: Decimal) -> Optional[float]:
    if match.direction == HOLD:
        return float(executed)
    up = match.direction == INCREASE_LOAD
    magnitude = match.magnitude or ONE_STEP
    if magnitude.kind is MagnitudeKind.PERCENT:
        return _percent_load(executed, step, magnitude.value, up=up)
    return _steps_load(executed, step, int(magnitude.value), up=up)


def evaluate_ruleset(facts: SetFacts, ruleset: RuleSet) -> Optional[NextSetLoad]:
    """Sugestão da próxima série pelo conjunto de regras, ou None quando a
    regra não se aplica (série que não é `working`, convenção excluída, sem
    carga/reps). A dor vale antes das regras; nenhuma casando -> MANTER.

    Lote T3 (medidas de serie): serie por tempo/maximo de reps
    (`set_measures.is_rep_based` falso) fica fora da regra - ela fala de
    reps x carga, e "maximo de reps em 30 s" nao e essa pergunta."""
    if normalize_set_type(facts.set_type) != WORKING:
        return None
    if not is_rep_based(facts.measure):
        return None
    if _convention(facts.load_convention) in NEXT_SET_EXCLUDED_CONVENTIONS:
        return None
    executed = _decimal(facts.weight_kg)
    if executed is None or executed <= 0:
        return None
    match = _classify(facts, ruleset)
    if match is None:
        return None
    step = load_increment(is_compound=False, load_increment_kg=_decimal(facts.step_kg)).step_kg
    planned = _decimal(facts.planned_rpe)
    return NextSetLoad(
        direction=match.direction,
        load_kg=_load_for(match, executed, step),
        reason=match.reason,
        executed_kg=float(executed),
        step_kg=float(step),
        planned_rpe=float(planned) if planned is not None else None,
        rule_position=match.rule.position if match.rule is not None else None,
        rule_text=describe_rule(match.rule, ruleset) if match.rule is not None else None,
        ruleset_version=ruleset.version,
        magnitude=match.magnitude,
    )


def classify_next_set(
    reps: Any, rpe: Any, *, joint_pain: Any = None, muscle_pain: Any = None, planned_rpe: Any = None
) -> Optional[tuple[str, str]]:
    """(direção, motivo) do conjunto PADRÃO para uma série de trabalho já
    executada (fachada sobre `evaluate_ruleset(DEFAULT_RULESET)`, sem carga).
    `planned_rpe`: RPE planejado da série pela rampa da ficha (`planned_ramp_rpe`).
    Sem reps válidas -> None."""
    facts = SetFacts(reps=reps, rpe=rpe, planned_rpe=planned_rpe, joint_pain=joint_pain, muscle_pain=muscle_pain)
    match = _classify(facts, DEFAULT_RULESET)
    return (match.direction, match.reason) if match is not None else None


def suggest_next_set_load(
    *,
    set_type: Any,
    reps: Any,
    weight_kg: Any,
    rpe: Any,
    joint_pain: Any = None,
    muscle_pain: Any = None,
    step_kg: Any = None,
    load_convention: Any = None,
    planned_rpe: Any = None,
    working_set_index: Any = None,
    fatigue_band: Any = None,
    rpe_ceiling: Any = None,
    ruleset: Optional[RuleSet] = None,
) -> Optional[NextSetLoad]:
    """Sugestão da próxima série, ou None quando a regra não se aplica
    (série que não é `working`, sem carga/reps, convenção excluída).
    `planned_rpe`: RPE planejado da série pela rampa da ficha (`planned_ramp_rpe`).
    R1: `working_set_index` (1-based), `fatigue_band`, `rpe_ceiling` e
    `ruleset` (None = `DEFAULT_RULESET`); sem eles, a chamada antiga vale igual."""
    facts = SetFacts(
        set_type=set_type,
        working_set_index=working_set_index,
        fatigue_band=fatigue_band,
        reps=reps,
        rpe=rpe,
        rpe_ceiling=rpe_ceiling,
        planned_rpe=planned_rpe,
        weight_kg=weight_kg,
        step_kg=step_kg,
        load_convention=load_convention,
        joint_pain=joint_pain,
        muscle_pain=muscle_pain,
    )
    return evaluate_ruleset(facts, ruleset or DEFAULT_RULESET)


# ---------------------------------------------------------------------------
# Texto (pt-BR) e validação do conjunto
# ---------------------------------------------------------------------------


def _reps_value_text(value: NumberOrToken, ruleset: RuleSet) -> str:
    if isinstance(value, str):
        resolved = _resolve_reps_value(value, ruleset)
        label = REPS_TOKEN_LABELS.get(value, value)
        return f"{label} ({_number_text(resolved)})" if resolved is not None else label
    return _number_text(value)


def _rpe_value_text(value: NumberOrToken) -> str:
    if isinstance(value, str):
        return RPE_TOKEN_LABELS.get(value, value)
    return _number_text(value)


def _condition_text(name: str, condition: Condition, value_text: Callable[[NumberOrToken], str]) -> str:
    if condition.op is ConditionOp.BETWEEN:
        upper = value_text(condition.value_to) if condition.value_to is not None else "?"
        return f"{name} entre {value_text(condition.value)} e {upper}"
    return f"{name} {CONDITION_OP_LABELS[condition.op]} {value_text(condition.value)}"


def _magnitude_text(magnitude: Optional[Magnitude]) -> str:
    magnitude = magnitude or ONE_STEP
    if magnitude.kind is MagnitudeKind.PERCENT:
        return f"{_number_text(magnitude.value)}% (no passo do equipamento)"
    steps = int(magnitude.value)
    return f"{steps} passo" if steps == 1 else f"{steps} passos"


def _action_text(rule: Rule) -> str:
    if rule.action == INCREASE_LOAD:
        return f"Aumentar {_magnitude_text(rule.magnitude)}"
    if rule.action == DECREASE_LOAD:
        return f"Reduzir {_magnitude_text(rule.magnitude)}"
    return "Manter a carga"


def describe_rule(rule: Rule, ruleset: RuleSet) -> str:
    """Texto pt-BR de uma regra, ex.: "2ª série · músculo descansado · reps >
    teto (12) · RPE < máximo · respeita a rampa → Aumentar 25% (no passo do
    equipamento)". Partes `any` ficam de fora; sem condição: "sempre"."""
    parts: list[str] = []
    if rule.set_position is not SetPosition.ANY:
        parts.append(SET_POSITION_LABELS[rule.set_position])
    if rule.fatigue is not FatigueCondition.ANY:
        parts.append(f"músculo {fatigue_condition_labels()[rule.fatigue.value]}")
    if rule.reps is not None:
        parts.append(_condition_text("reps", rule.reps, lambda value: _reps_value_text(value, ruleset)))
    if rule.rpe is not None:
        parts.append(_condition_text("RPE", rule.rpe, _rpe_value_text))
    if rule.respect_planned_ramp:
        parts.append("respeita a rampa")
    return f"{' · '.join(parts) or 'sempre'} → {_action_text(rule)}"


def describe_ruleset(ruleset: RuleSet) -> str:
    """Uma linha por regra, numerada, precedida do piso/teto de reps."""
    lines = [f"piso {ruleset.reps_floor} reps · teto {ruleset.reps_ceiling} reps"]
    lines.extend(f"{rule.position}. {describe_rule(rule, ruleset)}" for rule in ruleset.rules)
    return "\n".join(lines)


def describe_pain_rule() -> str:
    """Texto pt-BR da regra de dor, que vale ANTES do conjunto (não
    configurável; limiar do motor de recuperação, magnitude `ONE_STEP`)."""
    return (
        f"dor articular ou dor muscular acima de {_muscle_pain_block_above()}/10 → "
        f"Reduzir {_magnitude_text(ONE_STEP)}"
    )


def describe_matched_rule(next_set: NextSetLoad) -> Optional[str]:
    """O que casou para o texto da IA: a regra (`rule_text`), "nenhuma regra
    casou" no manter sem regra, ou None quando a dor decidiu antes das regras."""
    if next_set.rule_text:
        return f"Regra que casou: {next_set.rule_text}"
    if next_set.reason == REASON_PAIN:
        return None
    return "Nenhuma regra casou: manter a carga"


def _is_catch_all(rule: Rule) -> bool:
    return (
        rule.set_position is SetPosition.ANY
        and rule.fatigue is FatigueCondition.ANY
        and rule.reps is None
        and rule.rpe is None
    )


def _validate_condition(
    condition: Optional[Condition], *, tokens: tuple[str, ...], low: int, high: int
) -> tuple[bool, bool, bool]:
    """(token desconhecido, número fora da faixa, `between` inválido)."""
    if condition is None:
        return False, False, False
    unknown_token = False
    out_of_range = False
    numbers: list[Decimal] = []
    for value in (condition.value, condition.value_to):
        if value is None:
            continue
        if isinstance(value, str):
            unknown_token = unknown_token or value not in tokens
            continue
        number = _decimal(value)
        if number is None or not (low <= number <= high):
            out_of_range = True
        elif number is not None:
            numbers.append(number)
    between_invalid = False
    if condition.op is ConditionOp.BETWEEN:
        if condition.value_to is None:
            between_invalid = True
        elif len(numbers) == 2 and numbers[0] >= numbers[1]:
            between_invalid = True
    elif condition.value_to is not None:
        between_invalid = True
    return unknown_token, out_of_range, between_invalid


def validate_ruleset(ruleset: RuleSet) -> list[str]:
    """Códigos de erro e de aviso (prefixo `WARN_`) do conjunto; vazio = ok.
    Códigos por regra levam `:<position>`. Conjunto sem regra de redução é
    AVISO, não erro (a dor continua reduzindo acima de tudo)."""
    codes: list[str] = []
    if not ruleset.rules:
        codes.append(RULESET_EMPTY)
    if len(ruleset.rules) > RULESET_MAX_RULES:
        codes.append(RULESET_TOO_MANY_RULES)
    if ruleset.reps_floor < REPS_FLOOR_MIN:
        codes.append(REPS_FLOOR_BELOW_MIN)
    if ruleset.reps_ceiling > REPS_CEILING_MAX:
        codes.append(REPS_CEILING_ABOVE_MAX)
    if ruleset.reps_floor >= ruleset.reps_ceiling:
        codes.append(REPS_FLOOR_NOT_BELOW_CEILING)
    if [rule.position for rule in ruleset.rules] != list(range(1, len(ruleset.rules) + 1)):
        codes.append(RULESET_POSITIONS_NOT_SEQUENTIAL)
    catch_all_seen = False
    for rule in ruleset.rules:
        suffix = f":{rule.position}"
        if rule.action not in RULE_ACTIONS:
            codes.append(RULE_ACTION_UNKNOWN + suffix)
        if _is_catch_all(rule):
            codes.append(RULE_WITHOUT_CONDITION + suffix)
        elif catch_all_seen:
            codes.append(WARN_RULE_UNREACHABLE + suffix)
        catch_all_seen = catch_all_seen or _is_catch_all(rule)
        if rule.action == HOLD and rule.magnitude is not None:
            codes.append(RULE_HOLD_WITH_MAGNITUDE + suffix)
        if rule.action in (INCREASE_LOAD, DECREASE_LOAD):
            if rule.magnitude is None:
                codes.append(RULE_MAGNITUDE_MISSING + suffix)
            else:
                codes.extend(code + suffix for code in _validate_magnitude(rule.magnitude))
        unknown, out_of_range, between_invalid = _validate_condition(
            rule.reps, tokens=REPS_TOKENS, low=REPS_FLOOR_MIN, high=REPS_CEILING_MAX
        )
        if unknown:
            codes.append(RULE_REPS_TOKEN_UNKNOWN + suffix)
        if out_of_range:
            codes.append(RULE_REPS_OUT_OF_RANGE + suffix)
        unknown_rpe, out_of_range_rpe, between_invalid_rpe = _validate_condition(
            rule.rpe, tokens=RPE_TOKENS, low=RULE_RPE_MIN, high=RULE_RPE_MAX
        )
        if unknown_rpe:
            codes.append(RULE_RPE_TOKEN_UNKNOWN + suffix)
        if out_of_range_rpe:
            codes.append(RULE_RPE_OUT_OF_RANGE + suffix)
        if between_invalid or between_invalid_rpe:
            codes.append(RULE_BETWEEN_INVALID + suffix)
        if rule.respect_planned_ramp and rule.rpe is None:
            codes.append(RULE_RAMP_WITHOUT_RPE + suffix)
        if rule.reason_code is not None and rule.reason_code not in _RULE_REASON_CODES:
            codes.append(RULE_REASON_CODE_UNKNOWN + suffix)
    if ruleset.rules and not any(rule.action == DECREASE_LOAD for rule in ruleset.rules):
        codes.append(WARN_NO_DECREASE_RULE)
    return codes


# Motivos que uma regra pode carregar (`reason_code`): os das regras padrão e o
# genérico. A dor e "manter" não são motivo de regra.
_RULE_REASON_CODES: Final[frozenset[str]] = frozenset(
    {REASON_REPS_ABOVE_LIMIT, REASON_LOW_RPE_AT_REPS, REASON_FAILURE_BEFORE_MIN_REPS, REASON_RULE_MATCHED}
)


def ruleset_uses_fatigue(ruleset: RuleSet) -> bool:
    """Alguma regra exige um contexto de fadiga prévia (`fatigue != any`)?
    Sem nenhuma, a faixa não muda o resultado de `evaluate_ruleset` e o
    chamador pode deixar de lê-la (R2: corta a leitura por série do padrão)."""
    return any(rule.fatigue is not FatigueCondition.ANY for rule in ruleset.rules)


def _validate_magnitude(magnitude: Magnitude) -> list[str]:
    value = _decimal(magnitude.value)
    if value is None:
        return [RULE_MAGNITUDE_OUT_OF_RANGE]
    if magnitude.kind is MagnitudeKind.PERCENT:
        return [] if MAGNITUDE_PERCENT_MIN <= value <= MAGNITUDE_PERCENT_MAX else [RULE_MAGNITUDE_OUT_OF_RANGE]
    codes: list[str] = []
    if value != value.to_integral_value():
        codes.append(RULE_STEPS_NOT_INTEGER)
    if not (MAGNITUDE_STEPS_MIN <= value <= MAGNITUDE_STEPS_MAX):
        codes.append(RULE_MAGNITUDE_OUT_OF_RANGE)
    return codes


def ruleset_errors(ruleset: RuleSet) -> list[str]:
    """Só os erros de `validate_ruleset` (sem os avisos)."""
    return [code for code in validate_ruleset(ruleset) if not code.startswith(RULESET_WARNING_PREFIX)]


# ---------------------------------------------------------------------------
# JSON (persistência no R2; a tela no R4 monta este mesmo formato)
# ---------------------------------------------------------------------------


def _value_to_json(value: Optional[NumberOrToken]) -> Any:
    if value is None:
        return None
    return value if isinstance(value, str) else _number(value)


def _condition_to_dict(condition: Optional[Condition]) -> Optional[dict[str, Any]]:
    if condition is None:
        return None
    data: dict[str, Any] = {"op": condition.op.value, "value": _value_to_json(condition.value)}
    if condition.value_to is not None:
        data["value_to"] = _value_to_json(condition.value_to)
    return data


def rule_to_dict(rule: Rule) -> dict[str, Any]:
    data: dict[str, Any] = {
        "position": rule.position,
        "set_position": rule.set_position.value,
        "fatigue": rule.fatigue.value,
        "reps": _condition_to_dict(rule.reps),
        "rpe": _condition_to_dict(rule.rpe),
        "respect_planned_ramp": rule.respect_planned_ramp,
        "action": rule.action,
        "magnitude": rule.magnitude.to_dict() if rule.magnitude is not None else None,
    }
    if rule.reason_code is not None:
        data["reason_code"] = rule.reason_code
    return data


def ruleset_to_dict(ruleset: RuleSet) -> dict[str, Any]:
    return {
        "reps_floor": ruleset.reps_floor,
        "reps_ceiling": ruleset.reps_ceiling,
        "version": ruleset.version,
        "source": ruleset.source,
        "rules": [rule_to_dict(rule) for rule in ruleset.rules],
    }


def _value_from_json(value: Any, *, tokens: tuple[str, ...]) -> Optional[NumberOrToken]:
    if value is None:
        return None
    if isinstance(value, str):
        if value in tokens:
            return value
        number = _decimal(value)
        if number is None:
            raise ValueError(f"valor inválido na condição: {value!r}")
        return number
    number = _decimal(value)
    if number is None:
        raise ValueError(f"valor inválido na condição: {value!r}")
    return number


def _condition_from_dict(data: Any, *, tokens: tuple[str, ...]) -> Optional[Condition]:
    if data is None:
        return None
    if not isinstance(data, Mapping):
        raise ValueError("condição precisa ser um objeto {op, value, value_to?}")
    value = _value_from_json(data.get("value"), tokens=tokens)
    if value is None:
        raise ValueError("condição sem `value`")
    return Condition(
        op=ConditionOp(str(data.get("op"))),
        value=value,
        value_to=_value_from_json(data.get("value_to"), tokens=tokens),
    )


def _magnitude_from_dict(data: Any) -> Optional[Magnitude]:
    if data is None:
        return None
    if not isinstance(data, Mapping):
        raise ValueError("magnitude precisa ser um objeto {kind, value}")
    value = _decimal(data.get("value"))
    if value is None:
        raise ValueError("magnitude sem `value` numérico")
    return Magnitude(kind=MagnitudeKind(str(data.get("kind"))), value=value)


def rule_from_dict(data: Mapping[str, Any], *, position: Optional[int] = None) -> Rule:
    """Regra a partir do JSON; `position` explícito (ordem na lista) vence o do
    JSON. Valor de enum desconhecido -> `ValueError`."""
    resolved_position = position if position is not None else _int(data.get("position"))
    if resolved_position is None:
        raise ValueError("regra sem `position`")
    reason_code = data.get("reason_code")
    return Rule(
        position=resolved_position,
        set_position=SetPosition(str(data.get("set_position") or SetPosition.ANY.value)),
        fatigue=FatigueCondition(str(data.get("fatigue") or FatigueCondition.ANY.value)),
        reps=_condition_from_dict(data.get("reps"), tokens=REPS_TOKENS),
        rpe=_condition_from_dict(data.get("rpe"), tokens=RPE_TOKENS),
        respect_planned_ramp=bool(data.get("respect_planned_ramp")),
        action=str(data.get("action") or HOLD),
        magnitude=_magnitude_from_dict(data.get("magnitude")),
        reason_code=str(reason_code) if reason_code else None,
    )


def ruleset_from_dict(
    data: Mapping[str, Any], *, version: Optional[int] = None, source: Optional[str] = None
) -> RuleSet:
    """Conjunto a partir do JSON (`{"reps_floor", "reps_ceiling", "rules": [...]}`).
    As regras ficam na ordem da lista, com `position` = 1..n. `version`/`source`
    explícitos vencem os do JSON (0 / "default")."""
    if not isinstance(data, Mapping):
        raise ValueError("conjunto precisa ser um objeto")
    reps_floor = _int(data.get("reps_floor"))
    reps_ceiling = _int(data.get("reps_ceiling"))
    if reps_floor is None or reps_ceiling is None:
        raise ValueError("conjunto sem `reps_floor`/`reps_ceiling`")
    raw_rules = data.get("rules")
    if raw_rules is None:
        raw_rules = []
    if not isinstance(raw_rules, (list, tuple)):
        raise ValueError("`rules` precisa ser uma lista")
    rules = tuple(rule_from_dict(item, position=index) for index, item in enumerate(raw_rules, start=1))
    resolved_version = version if version is not None else (_int(data.get("version")) or 0)
    resolved_source = source if source is not None else str(data.get("source") or RULESET_SOURCE_DEFAULT)
    return RuleSet(reps_floor=reps_floor, reps_ceiling=reps_ceiling, rules=rules, version=resolved_version, source=resolved_source)


# ---------------------------------------------------------------------------
# Espelho do FE
# ---------------------------------------------------------------------------


def rule_vocabulary() -> dict[str, Any]:
    """Opções dos Selects da tela de cadastro (R4): valores e rótulos do dono."""
    fatigue = fatigue_condition_labels()
    return {
        "set_positions": [{"value": item.value, "label": SET_POSITION_LABELS[item]} for item in SetPosition],
        "fatigue_conditions": [{"value": item.value, "label": fatigue[item.value]} for item in FatigueCondition],
        "condition_ops": [{"value": item.value, "label": CONDITION_OP_LABELS[item]} for item in ConditionOp],
        "reps_tokens": [{"value": token, "label": REPS_TOKEN_LABELS[token]} for token in REPS_TOKENS],
        "rpe_tokens": [{"value": token, "label": RPE_TOKEN_LABELS[token]} for token in RPE_TOKENS],
        "actions": [{"value": action, "label": DECISION_LABELS[action]} for action in RULE_ACTIONS],
        "magnitude_kinds": [{"value": item.value, "label": MAGNITUDE_KIND_LABELS[item]} for item in MagnitudeKind],
    }


def rule_limits() -> dict[str, Any]:
    return {
        "max_rules": RULESET_MAX_RULES,
        "magnitude_percent_min": MAGNITUDE_PERCENT_MIN,
        "magnitude_percent_max": MAGNITUDE_PERCENT_MAX,
        "magnitude_steps_min": MAGNITUDE_STEPS_MIN,
        "magnitude_steps_max": MAGNITUDE_STEPS_MAX,
        "reps_floor_min": REPS_FLOOR_MIN,
        "reps_ceiling_max": REPS_CEILING_MAX,
        "rule_rpe_min": RULE_RPE_MIN,
        "rule_rpe_max": RULE_RPE_MAX,
    }


def export_for_frontend() -> dict[str, Any]:
    """Bloco `next_set_load` de `motor-constants.generated.json`."""
    return {
        "reasons": list(NEXT_SET_REASONS),
        "reason_labels": dict(reason_labels()),
        "load_adjustment_reason_rule": REASON_NEXT_SET_RULE,
        "reason_rule_matched": REASON_RULE_MATCHED,
        "rule_vocabulary": rule_vocabulary(),
        "rule_limits": rule_limits(),
        "default_ruleset": ruleset_to_dict(DEFAULT_RULESET),
    }
