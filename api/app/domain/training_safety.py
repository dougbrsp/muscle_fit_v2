"""Resolvedor UNICO de seguranca de treino (PR-07 da refatoracao dos motores;
PLANO_20260912.md secoes 4, 5.7 e 5.9; decisoes D3, D11 e D12).

Pergunta que este modulo responde, para um par exercicio x equipamento:
qual e o RPE maximo seguro, a falha concentrica e permitida, quais tecnicas
avancadas podem ser usadas - e de onde veio cada regra.

Precedencia: `exercise_equipment_override` (linha do par em
`equipment_exercises` / `equipment_exercise_technique_overrides`) vence
`equipment_default` (`equipment` / `equipment_technique_compatibility`), que
vence o default conservador D3:
- `max_safe_rpe` NULL (ou fora de 6-10) -> 9, origem `None`;
- tecnica sem marcacao -> incompativel.

`is_advanced_user` NAO participa de nenhuma funcao daqui (D11: experiencia so
entra na elegibilidade de tecnica da progressao, nunca na seguranca).

Organizacao: tudo acima de "Acesso a dados" e funcao PURA (sem banco, sem
FastAPI), coberta por tests/unit/test_training_safety.py. O acesso a dados
fica em `load_safety_inputs()`; `resolve_training_safety()` so junta os dois.

Consumidores: o recomendador de equipamento (PR-08, leitura em lote via
`resolve_training_safety_bulk`, so INFORMA - filtra apenas com
`required_technique` explicito). Geracao de ficha, progressao, IA de serie e
Modo Treino entram nos PRs 12, 13 e 14.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from dataclasses import field as dc_field
from functools import lru_cache
from typing import Any, Iterable, Mapping, Optional

from .training_goal import TrainingGoal, read_goal

MIN_SAFE_RPE = 6
MAX_SAFE_RPE = 10
DEFAULT_MAX_SAFE_RPE = 9  # D3: desconhecido -> 9 (falha nao permitida)
FAILURE_RPE = 10  # RPE 10 = falha concentrica

# D4 revisada em 2026-09-18 (PR #121): o TETO de seguranca nunca vira PISO de
# exigencia. Sem RPE prescrito, o alvo e este default, cortado pelo teto -
# nunca o proprio teto. Fonte UNICA do valor: `progression_engine`
# (DEFAULT_EFFORT_TARGET_RPE) deriva daqui.
DEFAULT_TARGET_RPE = 8

SOURCE_OVERRIDE = "exercise_equipment_override"
SOURCE_EQUIPMENT_DEFAULT = "equipment_default"

# clamps[].reason
REASON_EQUIPMENT_SAFETY = "EQUIPMENT_SAFETY"
REASON_RECOVERY = "RECOVERY"
REASON_LEGACY_PLAN = "LEGACY_PLAN"
REASON_AI_SUGGESTION = "AI_SUGGESTION"

# validate_technique().reason / blocked_techniques[].reason
TECHNIQUE_UNKNOWN = "TECHNIQUE_UNKNOWN"
TECHNIQUE_NOT_COMPATIBLE = "TECHNIQUE_NOT_COMPATIBLE"
FAILURE_REQUIRED_NOT_ALLOWED = "FAILURE_REQUIRED_NOT_ALLOWED"

# `advanced_techniques.failure_requirement` que exige chegar a falha.
FAILURE_REQUIREMENTS_NEEDING_FAILURE = frozenset({"requires_failure", "beyond_failure"})

READY = "READY"
REDUCED_DOSE = "REDUCED_DOSE"
NO_DIRECT_WORK = "NO_DIRECT_WORK"

# Origem da negacao da falha concentrica (`IntensityComposition.failure_denied_by`,
# Lote 1/P1 da investigacao RPE 10, 2026-10-11). `PAIR`: o par exercicio x
# equipamento proibe a falha (`max_safe_rpe < 10`) - RPE 10 ali e evento de
# SEGURANCA (laranja). `RECOVERY`: o par permite a falha e so o contrato de
# recuperacao do dia a negou - RPE 10 ali e INFORMATIVO (acima do alvo do dia).
# Empate/ambos = PAIR (a restricao permanente vence).
FAILURE_DENIED_BY_PAIR = "PAIR"
FAILURE_DENIED_BY_RECOVERY = "RECOVERY"

# RPE-alvo do PROTOCOLO de uma tecnica avancada (Modo Intensivo, 2026-10-24;
# decisao do usuario): FST-7, drops, rest-pause e a familia "alem da falha"
# sao levados ao limite seguro do par. Falha permitida pelo teto EFETIVO do
# par (`max_safe_rpe` = 10) -> 10; qualquer outro teto, inclusive desconhecido
# -> 9. NAO e rebaixado pela recuperacao do dia (decisao do usuario): o
# protocolo e do par, o contrato do dia continua valendo para o alvo normal.
PROTOCOL_TARGET_RPE_WITH_FAILURE = FAILURE_RPE
PROTOCOL_TARGET_RPE_WITHOUT_FAILURE = 9
# `exercise_sets.rpe_target_source` (092): o `rpe_target_effective` da serie
# veio do protocolo da tecnica (e nao da prescricao). O `rpe` (relatado)
# continua so do usuario.
RPE_TARGET_SOURCE_PROTOCOL = "protocol"
RPE_TARGET_SOURCES: tuple[str, ...] = (RPE_TARGET_SOURCE_PROTOCOL,)


# --- politica por objetivo de treino (M97 F2, 2026-10-25; mapa §66.2) -------
# HIPERTROFIA (default): nada muda - alvo `DEFAULT_TARGET_RPE`, sem teto
# proprio do objetivo (so o do par e o da recuperacao), todas as tecnicas que o
# par permite. FORCA (RAG FOR-010: falha nao e obrigatoria e e evitada nos
# compostos pesados; RIR 1-3): alvo 8 e teto 9 (a falha nunca e prescrita), e
# as tecnicas METABOLICAS (FOR-045/046/048: drop-set, rest-pause, FST-7, GVT;
# bi-set nem existe no catalogo `advanced_techniques`) ficam fora. A familia
# "alem da falha" ja cai pelo teto 9 (`FAILURE_REQUIREMENTS_NEEDING_FAILURE`).
# Valores a calibrar (PR da F2).
STRENGTH_TARGET_RPE = 8
STRENGTH_RPE_CEILING = FAILURE_RPE - 1
STRENGTH_DISALLOWED_TECHNIQUES: frozenset[str] = frozenset({"drop_set", "rest_pause", "fst7", "gvt"})

# clamps[].reason quando o TETO DO OBJETIVO segurou o valor prescrito.
REASON_TRAINING_GOAL = "TRAINING_GOAL"
# blocked_techniques[].reason / validate_technique().reason: tecnica fora da
# politica do objetivo (forca).
TECHNIQUE_NOT_ALLOWED_FOR_GOAL = "TECHNIQUE_NOT_ALLOWED_FOR_GOAL"
# blocked_techniques[].source da politica do objetivo.
SOURCE_TRAINING_GOAL = "training_goal"

_GOAL_TARGET_RPE: Mapping[TrainingGoal, int] = {
    TrainingGoal.HYPERTROPHY: DEFAULT_TARGET_RPE,
    TrainingGoal.STRENGTH: STRENGTH_TARGET_RPE,
}
# None = o objetivo nao tem teto proprio.
_GOAL_RPE_CEILING: Mapping[TrainingGoal, Optional[int]] = {
    TrainingGoal.HYPERTROPHY: None,
    TrainingGoal.STRENGTH: STRENGTH_RPE_CEILING,
}
_GOAL_DISALLOWED_TECHNIQUES: Mapping[TrainingGoal, frozenset[str]] = {
    TrainingGoal.HYPERTROPHY: frozenset(),
    TrainingGoal.STRENGTH: STRENGTH_DISALLOWED_TECHNIQUES,
}


def goal_target_rpe(training_goal: Any = None) -> int:
    """RPE-alvo default do objetivo (sem RPE prescrito)."""
    return _GOAL_TARGET_RPE[read_goal(training_goal)]


def goal_rpe_ceiling(training_goal: Any = None) -> Optional[int]:
    """Teto de RPE do OBJETIVO (None = sem teto proprio; forca = 9)."""
    return _GOAL_RPE_CEILING[read_goal(training_goal)]


def goal_disallowed_techniques(training_goal: Any = None) -> frozenset[str]:
    """Codigos do catalogo `advanced_techniques` fora da politica do objetivo."""
    return _GOAL_DISALLOWED_TECHNIQUES[read_goal(training_goal)]


def goal_allows_technique(code: Any, training_goal: Any = None) -> bool:
    return (code or "").strip() not in goal_disallowed_techniques(training_goal)


# ---------------------------------------------------------------------------
# Tipos
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TechniquePermission:
    code: str
    source: str  # SOURCE_OVERRIDE | SOURCE_EQUIPMENT_DEFAULT

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "source": self.source}


@dataclass(frozen=True)
class BlockedTechnique:
    """Tecnica marcada compativel na matriz mas invalida neste par."""

    code: str
    source: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "source": self.source, "reason": self.reason}


@dataclass(frozen=True)
class TrainingSafety:
    exercise_id: Optional[int]
    equipment_id: Optional[int]
    equipment_exercise_id: Optional[int]
    max_safe_rpe: int  # efetivo (6-10), ja com o default D3
    failure_allowed: bool  # max_safe_rpe == 10
    safety_source: Optional[str]  # origem de max_safe_rpe; None = default D3
    allowed_techniques: tuple[TechniquePermission, ...] = ()
    blocked_techniques: tuple[BlockedTechnique, ...] = ()
    # failure_requirement das tecnicas ativas do catalogo (para validate_technique)
    technique_failure_requirements: Mapping[str, str] = dc_field(default_factory=dict)

    @property
    def allowed_codes(self) -> frozenset[str]:
        return frozenset(item.code for item in self.allowed_techniques)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exercise_id": self.exercise_id,
            "equipment_id": self.equipment_id,
            "equipment_exercise_id": self.equipment_exercise_id,
            "max_safe_rpe": self.max_safe_rpe,
            "failure_allowed": self.failure_allowed,
            "safety_source": self.safety_source,
            "allowed_advanced_techniques": [item.to_dict() for item in self.allowed_techniques],
            "blocked_advanced_techniques": [item.to_dict() for item in self.blocked_techniques],
        }


@dataclass(frozen=True)
class Readiness:
    """Parte do contrato de Recuperacao (secao 5.8) que limita intensidade."""

    status: str
    max_rpe: int
    allow_failure: bool


@lru_cache(maxsize=1)
def _readiness_table() -> dict[str, Readiness]:
    """Teto de RPE e falha por estado de prontidao, LIDOS do dono
    (`MuscleRecoveryService._RECOMMENDATIONS`: `maximum_rpe` e
    `allow_failure_on_last_set`). M76 (C10): antes era uma copia 10/9/8 aqui,
    mantida em paridade so por teste. Import tardio: o pacote `intelligence` e
    pesado e importa o `domain`; aqui ele so e lido na primeira composicao."""
    from ..intelligence.muscle_recovery_service import MuscleRecoveryService

    return {
        status: Readiness(status, int(rec["maximum_rpe"]), bool(rec["allow_failure_on_last_set"]))
        for status, rec in MuscleRecoveryService._RECOMMENDATIONS.items()
    }


def readiness_for_status(status: str) -> Readiness:
    try:
        return _readiness_table()[status]
    except KeyError:
        raise ValueError(f"status de readiness desconhecido: {status!r}") from None


@dataclass(frozen=True)
class Clamp:
    field: str
    from_value: Any
    to_value: Any
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "from": self.from_value, "to": self.to_value, "reason": self.reason}


@dataclass(frozen=True)
class IntensityComposition:
    ceiling: int
    failure_allowed: bool
    target: int
    clamps: tuple[Clamp, ...] = ()
    # None quando a falha e permitida; senao FAILURE_DENIED_BY_PAIR/RECOVERY.
    failure_denied_by: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "effective_rpe_ceiling": self.ceiling,
            "failure_allowed": self.failure_allowed,
            "failure_denied_by": self.failure_denied_by,
            "rpe_target": self.target,
            "clamps": [clamp.to_dict() for clamp in self.clamps],
        }


@dataclass(frozen=True)
class TechniqueValidation:
    code: str
    valid: bool
    reason: Optional[str] = None
    source: Optional[str] = None


# ---------------------------------------------------------------------------
# Funcoes puras
# ---------------------------------------------------------------------------


def normalize_max_safe_rpe(value: Any) -> Optional[int]:
    """Inteiro em 6-10 ou None. Valor sujo (fora da faixa, nao inteiro, bool)
    vira None = desconhecido: nunca e lido como permissao."""
    if value is None or isinstance(value, bool):
        return None
    try:
        as_float = float(value)
        number = int(as_float)  # nan -> ValueError, inf -> OverflowError
    except (TypeError, ValueError, OverflowError):
        return None
    if as_float != number:
        return None
    return number if MIN_SAFE_RPE <= number <= MAX_SAFE_RPE else None


def protocol_target_rpe(max_safe_rpe: Any) -> int:
    """RPE-alvo do protocolo da tecnica pelo teto EFETIVO do par (override >
    default do equipamento; ver `resolve_max_safe_rpe`). 10 so quando o par
    libera a falha; 9 em qualquer outro caso - inclusive teto desconhecido ou
    sujo. Nunca olha a recuperacao (decisao do usuario de 2026-10-24)."""
    if normalize_max_safe_rpe(max_safe_rpe) == FAILURE_RPE:
        return PROTOCOL_TARGET_RPE_WITH_FAILURE
    return PROTOCOL_TARGET_RPE_WITHOUT_FAILURE


def effective_protocol_target_rpe(live_max_safe_rpe: Any, frozen_target_rpe: Any = None) -> int:
    """Alvo do protocolo gravado na serie: o do teto do par AO VIVO, cortado
    pelo congelado na prescricao (`advanced_technique_parameters.target_rpe`)
    quando existir - mesmo principio do overlay de seguranca (so restringe:
    teto que caiu desde o congelamento derruba o alvo; teto que subiu nao o
    eleva). Congelado fora de 9/10 e ignorado."""
    live = protocol_target_rpe(live_max_safe_rpe)
    frozen = normalize_max_safe_rpe(frozen_target_rpe)
    if frozen in (PROTOCOL_TARGET_RPE_WITHOUT_FAILURE, PROTOCOL_TARGET_RPE_WITH_FAILURE):
        return min(live, frozen)
    return live


def resolve_max_safe_rpe(equipment_max_safe_rpe: Any, override_max_safe_rpe: Any) -> tuple[int, Optional[str]]:
    """(valor efetivo, origem). override > default do equipamento > 9 (D3)."""
    override = normalize_max_safe_rpe(override_max_safe_rpe)
    if override is not None:
        return override, SOURCE_OVERRIDE
    default = normalize_max_safe_rpe(equipment_max_safe_rpe)
    if default is not None:
        return default, SOURCE_EQUIPMENT_DEFAULT
    return DEFAULT_MAX_SAFE_RPE, None


def merge_technique_marks(
    equipment_marks: Mapping[str, Any], override_marks: Mapping[str, Any]
) -> dict[str, tuple[bool, str]]:
    """code -> (is_compatible, origem). A linha do par vence a do equipamento,
    inclusive para NEGAR (`is_compatible = 0`) um default compativel."""
    merged = {code: (bool(value), SOURCE_EQUIPMENT_DEFAULT) for code, value in equipment_marks.items()}
    merged.update({code: (bool(value), SOURCE_OVERRIDE) for code, value in override_marks.items()})
    return merged


def build_training_safety(
    *,
    exercise_id: Optional[int] = None,
    equipment_id: Optional[int] = None,
    equipment_exercise_id: Optional[int] = None,
    equipment_max_safe_rpe: Any = None,
    override_max_safe_rpe: Any = None,
    equipment_techniques: Optional[Mapping[str, Any]] = None,
    override_techniques: Optional[Mapping[str, Any]] = None,
    technique_failure_requirements: Optional[Mapping[str, str]] = None,
) -> TrainingSafety:
    """Monta o `TrainingSafety` a partir dos dados crus. `technique_failure_requirements`
    = {code: failure_requirement} das tecnicas ATIVAS do catalogo; tecnica
    marcada mas fora dele (inativa/removida) e ignorada."""
    requirements = dict(technique_failure_requirements or {})
    max_safe_rpe, source = resolve_max_safe_rpe(equipment_max_safe_rpe, override_max_safe_rpe)
    failure_allowed = max_safe_rpe == FAILURE_RPE

    allowed: list[TechniquePermission] = []
    blocked: list[BlockedTechnique] = []
    marks = merge_technique_marks(equipment_techniques or {}, override_techniques or {})
    for code in sorted(marks):
        compatible, mark_source = marks[code]
        if not compatible or code not in requirements:
            continue
        if requirements[code] in FAILURE_REQUIREMENTS_NEEDING_FAILURE and not failure_allowed:
            blocked.append(BlockedTechnique(code, mark_source, FAILURE_REQUIRED_NOT_ALLOWED))
        else:
            allowed.append(TechniquePermission(code, mark_source))

    return TrainingSafety(
        exercise_id=exercise_id,
        equipment_id=equipment_id,
        equipment_exercise_id=equipment_exercise_id,
        max_safe_rpe=max_safe_rpe,
        failure_allowed=failure_allowed,
        safety_source=source,
        allowed_techniques=tuple(allowed),
        blocked_techniques=tuple(blocked),
        technique_failure_requirements=requirements,
    )


def failure_denial_origin(failure_allowed: Any, pair_max_safe_rpe: Any) -> Optional[str]:
    """Quem negou a falha (regra UNICA, usada por `compose` e pelos snapshots
    antigos sem o campo - `prescription.failure_denied_by_of`).

    Falha permitida -> None. Par com teto < 10 (ou desconhecido) -> PAIR: a
    proibicao e do equipamento, mesmo que a recuperacao tambem negasse
    (empate/ambos = PAIR). Par com teto 10 -> RECOVERY: so o contrato do dia
    tirou a falha."""
    if failure_allowed:
        return None
    if normalize_max_safe_rpe(pair_max_safe_rpe) == FAILURE_RPE:
        return FAILURE_DENIED_BY_RECOVERY
    return FAILURE_DENIED_BY_PAIR


def compose(
    safety: TrainingSafety,
    readiness: Readiness,
    prescribed: Optional[int] = None,
    training_goal: Any = None,
) -> IntensityComposition:
    """Composicao de intensidade (regra unica, secao 4):

        ceiling         = min(safety.max_safe_rpe, recovery.max_rpe)
        failure_allowed = ceiling == 10 and recovery.allow_failure
        target          = min(prescrito ou DEFAULT_TARGET_RPE, ceiling)

    Com a falha negada pela recuperacao e ceiling 10, o ceiling cai para 9
    (secao 5.7: sem falha permitida a ficha nunca mostra 10).

    `prescribed` ausente -> alvo = `min(DEFAULT_TARGET_RPE, ceiling)`, SEM
    clamp registrado: nao houve valor prescrito para clampar (D4 revisada em
    2026-09-18 - o teto de seguranca corta o alvo para baixo e nunca exige
    nada). O clamp aponta o limite que de fato segurou o valor prescrito;
    empate -> EQUIPMENT_SAFETY (regra permanente).

    `failure_denied_by` (Lote 1/P1): origem da negacao da falha, pela mesma
    regra de empate (`failure_denial_origin`).

    M97 F2: `training_goal` (ausente = hipertrofia: o calculo acima, sem
    nenhuma mudanca). Forca: o teto do objetivo (`goal_rpe_ceiling`, 9) entra
    no `min` do ceiling - a falha nunca e prescrita, e a origem da negacao
    segue a regra do par (`failure_denial_origin`) -, e o alvo default e o do
    objetivo (`goal_target_rpe`). Clamp segurado so pelo teto do objetivo sai
    com `REASON_TRAINING_GOAL`."""
    goal_ceiling = goal_rpe_ceiling(training_goal)
    if goal_ceiling is not None:
        return _compose_with_goal_ceiling(safety, readiness, prescribed, training_goal, goal_ceiling)
    ceiling = min(safety.max_safe_rpe, readiness.max_rpe)
    recovery_binds = readiness.max_rpe < safety.max_safe_rpe
    if ceiling == FAILURE_RPE and not readiness.allow_failure:
        ceiling = FAILURE_RPE - 1
        recovery_binds = True
    failure_allowed = ceiling == FAILURE_RPE and readiness.allow_failure
    denied_by = failure_denial_origin(failure_allowed, safety.max_safe_rpe)

    if prescribed is None:
        return IntensityComposition(
            ceiling=ceiling,
            failure_allowed=failure_allowed,
            target=min(DEFAULT_TARGET_RPE, ceiling),
            failure_denied_by=denied_by,
        )

    target = min(int(prescribed), ceiling)
    clamps: tuple[Clamp, ...] = ()
    if target != prescribed:
        reason = REASON_RECOVERY if recovery_binds else REASON_EQUIPMENT_SAFETY
        clamps = (Clamp("rpe_target", prescribed, target, reason),)
    return IntensityComposition(
        ceiling=ceiling, failure_allowed=failure_allowed, target=target, clamps=clamps, failure_denied_by=denied_by
    )


def _compose_with_goal_ceiling(
    safety: TrainingSafety,
    readiness: Readiness,
    prescribed: Optional[int],
    training_goal: Any,
    goal_ceiling: int,
) -> IntensityComposition:
    """`compose` de um objetivo com teto proprio (forca). Mesma composicao do
    par x recuperacao; o teto do objetivo corta o ceiling e a falha."""
    base = compose(safety, readiness, None)
    ceiling = min(base.ceiling, goal_ceiling)
    failure_allowed = base.failure_allowed and ceiling == FAILURE_RPE
    denied_by = None if failure_allowed else (base.failure_denied_by or failure_denial_origin(False, safety.max_safe_rpe))
    if prescribed is None:
        return IntensityComposition(
            ceiling=ceiling,
            failure_allowed=failure_allowed,
            target=min(goal_target_rpe(training_goal), ceiling),
            failure_denied_by=denied_by,
        )
    target = min(int(prescribed), ceiling)
    clamps: tuple[Clamp, ...] = ()
    if target != prescribed:
        if goal_ceiling < base.ceiling:
            reason = REASON_TRAINING_GOAL
        else:
            # o par/recuperacao segurou: o motivo e o da composicao sem objetivo
            reason = compose(safety, readiness, prescribed).clamps[0].reason
        clamps = (Clamp("rpe_target", prescribed, target, reason),)
    return IntensityComposition(
        ceiling=ceiling, failure_allowed=failure_allowed, target=target, clamps=clamps, failure_denied_by=denied_by
    )


def safety_for_goal(safety: TrainingSafety, training_goal: Any = None) -> TrainingSafety:
    """O `TrainingSafety` do par com a politica de tecnicas do OBJETIVO: as
    tecnicas fora dela (`goal_disallowed_techniques`) saem de
    `allowed_techniques` e entram em `blocked_techniques` com
    `TECHNIQUE_NOT_ALLOWED_FOR_GOAL`. Hipertrofia (nada fora da politica):
    devolve o MESMO objeto, intocado."""
    disallowed = goal_disallowed_techniques(training_goal)
    if not disallowed or not any(item.code in disallowed for item in safety.allowed_techniques):
        return safety
    allowed = tuple(item for item in safety.allowed_techniques if item.code not in disallowed)
    blocked = tuple(safety.blocked_techniques) + tuple(
        BlockedTechnique(item.code, SOURCE_TRAINING_GOAL, TECHNIQUE_NOT_ALLOWED_FOR_GOAL)
        for item in safety.allowed_techniques
        if item.code in disallowed
    )
    return replace(safety, allowed_techniques=allowed, blocked_techniques=blocked)


def validate_technique(
    code: str, safety: TrainingSafety, *, failure_allowed: Optional[bool] = None
) -> TechniqueValidation:
    """Validacao TERMINAL e defensiva de tecnica (secao 5.9).

    `failure_allowed` = o valor ja composto com a recuperacao
    (`compose().failure_allowed`); ausente, usa o do par. Nunca amplia: falha
    proibida no par continua proibida mesmo se o chamador passar True.

    Tecnica `requires_failure`/`beyond_failure` num par sem falha permitida e
    invalida MESMO se marcada compativel na matriz."""
    requirement = safety.technique_failure_requirements.get(code)
    if requirement is None:
        return TechniqueValidation(code, False, TECHNIQUE_UNKNOWN)

    effective_failure = safety.failure_allowed and (failure_allowed is None or bool(failure_allowed))
    blocked = {item.code: item for item in safety.blocked_techniques}
    allowed = {item.code: item for item in safety.allowed_techniques}
    if code in blocked:
        return TechniqueValidation(code, False, blocked[code].reason, blocked[code].source)
    if code not in allowed:
        return TechniqueValidation(code, False, TECHNIQUE_NOT_COMPATIBLE)
    if requirement in FAILURE_REQUIREMENTS_NEEDING_FAILURE and not effective_failure:
        return TechniqueValidation(code, False, FAILURE_REQUIRED_NOT_ALLOWED, allowed[code].source)
    return TechniqueValidation(code, True, None, allowed[code].source)


def clamp_ai_suggestion(
    suggestion: Mapping[str, Any], composition: IntensityComposition
) -> tuple[dict[str, Any], list[Clamp]]:
    """Limita a sugestao da IA de serie (`{"reps", "load_kg", "rpe"}`) ao
    ceiling ja composto. Devolve (copia ajustada, clamps). So o RPE e
    limitado; reps/carga passam como vieram. RPE ausente/nao numerico nao gera
    clamp (nada a limitar)."""
    result = dict(suggestion)
    raw = result.get("rpe")
    if raw is None or isinstance(raw, bool):
        return result, []
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return result, []
    if value <= composition.ceiling:
        return result, []
    result["rpe"] = composition.ceiling
    return result, [Clamp("rpe", raw, composition.ceiling, REASON_AI_SUGGESTION)]


# ---------------------------------------------------------------------------
# Acesso a dados (somente SELECT)
# ---------------------------------------------------------------------------


def load_safety_inputs_bulk(conn: Any, pairs: Iterable[tuple[int, int]]) -> dict[tuple[int, int], dict[str, Any]]:
    """Le, para VARIOS pares `(exercise_id, equipment_id)`, os argumentos de
    `build_training_safety` - numero FIXO de SELECTs (5), independente da
    quantidade de pares (recomendador, PR-08: sem N+1). Equipamento
    inexistente ou par sem vinculo em `equipment_exercises` nao e erro: caem
    nos defaults D3 (o chamador decide se o par e utilizavel)."""
    wanted = sorted({(int(exercise_id), int(equipment_id)) for exercise_id, equipment_id in pairs})
    if not wanted:
        return {}
    from ..catalog_reads import active_technique_rows, equipment_rows

    equipment_ids = sorted({equipment_id for _, equipment_id in wanted})
    marks = ", ".join(["%s"] * len(equipment_ids))
    params = tuple(equipment_ids)
    # `equipment` e `advanced_techniques`: leitura compartilhada na requisicao
    # com `progression_state` (PERF-04) - mesmas linhas, colunas unidas.
    equipment = {
        equipment_id: row["max_safe_rpe"] for equipment_id, row in equipment_rows(conn, equipment_ids).items()
    }
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, equipment_id, exercise_id, max_safe_rpe_override FROM equipment_exercises "
            f"WHERE equipment_id IN ({marks})",
            params,
        )
        links = {(int(row["exercise_id"]), int(row["equipment_id"])): row for row in cur.fetchall() or []}
        cur.execute(
            "SELECT equipment_id, technique_code, is_compatible FROM equipment_technique_compatibility "
            f"WHERE equipment_id IN ({marks})",
            params,
        )
        equipment_techniques: dict[int, dict[str, int]] = {}
        for row in cur.fetchall() or []:
            equipment_techniques.setdefault(int(row["equipment_id"]), {})[row["technique_code"]] = int(
                row["is_compatible"] or 0
            )
        cur.execute(
            "SELECT o.equipment_exercise_id, o.technique_code, o.is_compatible "
            "FROM equipment_exercise_technique_overrides o "
            "JOIN equipment_exercises ee ON ee.id = o.equipment_exercise_id "
            f"WHERE ee.equipment_id IN ({marks})",
            params,
        )
        override_techniques: dict[int, dict[str, int]] = {}
        for row in cur.fetchall() or []:
            override_techniques.setdefault(int(row["equipment_exercise_id"]), {})[row["technique_code"]] = int(
                row["is_compatible"] or 0
            )
    requirements = {row["code"]: row["failure_requirement"] for row in active_technique_rows(conn)}

    result: dict[tuple[int, int], dict[str, Any]] = {}
    for exercise_id, equipment_id in wanted:
        link = links.get((exercise_id, equipment_id))
        link_id = int(link["id"]) if link else None
        result[(exercise_id, equipment_id)] = {
            "exercise_id": exercise_id,
            "equipment_id": equipment_id,
            "equipment_exercise_id": link_id,
            "equipment_max_safe_rpe": equipment.get(equipment_id),
            "override_max_safe_rpe": link["max_safe_rpe_override"] if link else None,
            "equipment_techniques": dict(equipment_techniques.get(equipment_id, {})),
            "override_techniques": dict(override_techniques.get(link_id, {})) if link_id is not None else {},
            "technique_failure_requirements": requirements,
        }
    return result


def load_safety_inputs(conn: Any, exercise_id: int, equipment_id: int) -> dict[str, Any]:
    """Um par: mesmo caminho do lote (uma unica implementacao de leitura)."""
    key = (int(exercise_id), int(equipment_id))
    inputs = load_safety_inputs_bulk(conn, [key])[key]
    return {**inputs, "exercise_id": exercise_id, "equipment_id": equipment_id}


def resolve_training_safety_bulk(conn: Any, pairs: Iterable[tuple[int, int]]) -> dict[tuple[int, int], TrainingSafety]:
    """`resolve_training_safety` para varios pares, com as MESMAS regras."""
    return {key: build_training_safety(**inputs) for key, inputs in load_safety_inputs_bulk(conn, pairs).items()}


def resolve_training_safety(exercise_id: int, equipment_id: int, *, conn: Any = None) -> TrainingSafety:
    """Funcao UNICA de seguranca do par exercicio x equipamento. Com `conn`,
    reusa a conexao/transacao do chamador; sem, abre uma so para a leitura."""
    if conn is not None:
        return build_training_safety(**load_safety_inputs(conn, exercise_id, equipment_id))
    from ..db import transaction

    with transaction() as own_conn:
        return build_training_safety(**load_safety_inputs(own_conn, exercise_id, equipment_id))
