"""Defaults de prescrição de um exercício (M76, Lote 4c — E8).

Dono ÚNICO dos valores que valem quando o Modelo, a IA ou o motor não
definiram reps, RPE ou descanso. Antes do M76 esses números estavam escritos
à mão em ~20 lugares (`"8-12"` ×7, `"8-9"` ×5, `min_rest_seconds=30` /
`max_rest_seconds=90` em 8 assinaturas, `round((min + max) / 2)` ×4, 60 s no
Modo Treino). Python puro, sem I/O; o frontend lê o espelho
`frontend/src/lib/motor-constants.generated.json` (seção `prescription`),
exportado daqui por `api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`.

Duas ideias que NÃO são a mesma coisa (e por isso têm nomes diferentes):

- **Default de prescrição** (`default_rest_seconds()`, 60 s): o descanso que a
  ficha GRAVA e o timer do Modo Treino usa quando ninguém definiu outro — o
  ponto médio do intervalo padrão de geração (30–90 s).
- **Descanso não definido na estimativa de tempo**: o "Tempo total de treino"
  do Modelo conta 0 s para exercício sem descanso e avisa "N sem descanso
  definido" (decisão da Fase 7, 2026-09-28). Essa regra é da estimativa e mora
  no dono dela, `periodization/time_estimate.py`
  (`REST_WHEN_UNDEFINED_SECONDS`); o complemento de volume da IA conta o teto
  do intervalo (`periodization/limits.COMPLEMENT_DEFAULT_REST_SECONDS`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Mapping, Optional, Sequence

from .progression_engine import STRENGTH_REPS as _STRENGTH_REPS
from .training_goal import TrainingGoal, read_goal
from .training_safety import STRENGTH_RPE_CEILING

# Alvo de repetições quando o Modelo/IA/motor não definiu (texto de faixa).
DEFAULT_REPS_TARGET: Final[str] = "8-12"
# Alvo de RPE quando ninguém definiu (texto de faixa; o teto de segurança do
# par corta depois em `effective_intensity.clamp_rpe_text`).
DEFAULT_RPE_TARGET: Final[str] = "8-9"

# Intervalo de descanso padrão pedido na geração da ficha
# (`WorkoutPlanGenerateIn.min_rest_seconds/max_rest_seconds`, formulários do FE).
DEFAULT_MIN_REST_SECONDS: Final[int] = 30
DEFAULT_MAX_REST_SECONDS: Final[int] = 90
# Limites de sanidade do intervalo (o FE limita os campos; a geração legada
# corta a resposta da IA neles).
REST_SECONDS_SANITY_MIN: Final[int] = 15
REST_SECONDS_SANITY_MAX: Final[int] = 240

# Séries quando o item não traz `sets_count` (geração legada e motor v1).
DEFAULT_SETS_COUNT: Final[int] = 3
# Piso de séries de qualquer item gravado (um item sem série não é trabalho).
# O Modo Treino usa o mesmo piso como alvo de séries de trabalho de um item sem
# `sets_count` e como nº de séries de aquecimento/ajuste configurados sem
# contagem (`buildAdvancedExerciseCounters`, núcleo do Modo Treino).
MIN_SETS_COUNT: Final[int] = 1

# Valores pré-preenchidos do registro da série no Modo Treino quando não há
# NENHUMA referência (sem sugestão da IA/motor, sem série anterior do exercício
# e sem alvo numérico na prescrição). Só o ponto de partida do campo: o aluno
# ajusta e o que vale é o que ele grava. Antes estavam escritos à mão nas telas
# (`?? 10` / `?? 20` em `PlanExecutionScreen.tsx` e `FreeformScreen.tsx`).
SET_LOGGER_FALLBACK_REPS: Final[int] = 10
SET_LOGGER_FALLBACK_LOAD_KG: Final[int] = 20


def midpoint_rest_seconds(min_rest_seconds: float, max_rest_seconds: float) -> int:
    """Descanso de um exercício sem descanso próprio: ponto médio do intervalo
    escolhido na geração. `round` do Python (o mesmo de todas as cópias que
    este helper substituiu), então o valor gravado não muda."""
    return round((min_rest_seconds + max_rest_seconds) / 2)


def default_rest_seconds() -> int:
    """Default de prescrição do descanso entre séries (60 s): ponto médio do
    intervalo padrão. É o que a ficha grava sem intervalo informado e o que o
    timer do Modo Treino usa quando o exercício não tem `rest_seconds`."""
    return midpoint_rest_seconds(DEFAULT_MIN_REST_SECONDS, DEFAULT_MAX_REST_SECONDS)


# O mesmo valor como constante, para default de assinatura (avaliado na definição).
DEFAULT_PRESCRIPTION_REST_SECONDS: Final[int] = default_rest_seconds()


# ---------------------------------------------------------------------------
# Perfil por objetivo de treino (M97 F2, 2026-10-25; mapa §66.2)
# ---------------------------------------------------------------------------
# O perfil de HIPERTROFIA e exatamente as constantes acima (byte-identico ao de
# antes do M97). O de FORCA e novo: valores a calibrar pelo usuario (PR da F2,
# "Valores a calibrar"), fonte RAG de forca (FOR-xxx, consolidado no plano):
# 3-5 reps (FOR: "3 a 5, ate 6"), RIR 1-3 = RPE 7-9, descanso 3-5 min e 2-3
# series (3 = teto da faixa da fonte).
# Os textos DERIVAM dos donos (nunca um segundo numero): faixa de forca da
# progressao (`progression_engine.STRENGTH_REPS`) e teto de RPE do objetivo
# (`training_safety.STRENGTH_RPE_CEILING`); o piso 7 (RIR 3) e o do texto da
# faixa da fonte, so de exibicao/ficha (o alvo de cobranca e o do dono da
# seguranca, `STRENGTH_TARGET_RPE`).
STRENGTH_RPE_FLOOR: Final[int] = 7
STRENGTH_REPS_TARGET: Final[str] = f"{_STRENGTH_REPS[0]}-{_STRENGTH_REPS[1]}"
STRENGTH_RPE_TARGET: Final[str] = f"{STRENGTH_RPE_FLOOR}-{STRENGTH_RPE_CEILING}"
STRENGTH_MIN_REST_SECONDS: Final[int] = 180
STRENGTH_MAX_REST_SECONDS: Final[int] = 300
STRENGTH_SETS_COUNT: Final[int] = 3
# Teto de sanidade do descanso de FORCA: nao pode cortar os 5 min da fonte.
STRENGTH_REST_SECONDS_SANITY_MAX: Final[int] = STRENGTH_MAX_REST_SECONDS


@dataclass(frozen=True)
class PrescriptionDefaults:
    """Defaults de prescricao de UM objetivo de treino (`defaults_for`)."""

    training_goal: str
    reps_target: str
    rpe_target: str
    min_rest_seconds: int
    max_rest_seconds: int
    sets_count: int
    rest_seconds_sanity_min: int
    rest_seconds_sanity_max: int

    @property
    def rest_seconds(self) -> int:
        """Descanso default do objetivo: ponto medio do intervalo dele."""
        return midpoint_rest_seconds(self.min_rest_seconds, self.max_rest_seconds)

    def to_dict(self) -> dict[str, object]:
        return {
            "training_goal": self.training_goal,
            "reps_target": self.reps_target,
            "rpe_target": self.rpe_target,
            "min_rest_seconds": self.min_rest_seconds,
            "max_rest_seconds": self.max_rest_seconds,
            "rest_seconds": self.rest_seconds,
            "sets_count": self.sets_count,
            "rest_seconds_sanity_min": self.rest_seconds_sanity_min,
            "rest_seconds_sanity_max": self.rest_seconds_sanity_max,
        }


HYPERTROPHY_DEFAULTS: Final[PrescriptionDefaults] = PrescriptionDefaults(
    training_goal=TrainingGoal.HYPERTROPHY.value,
    reps_target=DEFAULT_REPS_TARGET,
    rpe_target=DEFAULT_RPE_TARGET,
    min_rest_seconds=DEFAULT_MIN_REST_SECONDS,
    max_rest_seconds=DEFAULT_MAX_REST_SECONDS,
    sets_count=DEFAULT_SETS_COUNT,
    rest_seconds_sanity_min=REST_SECONDS_SANITY_MIN,
    rest_seconds_sanity_max=REST_SECONDS_SANITY_MAX,
)

STRENGTH_DEFAULTS: Final[PrescriptionDefaults] = PrescriptionDefaults(
    training_goal=TrainingGoal.STRENGTH.value,
    reps_target=STRENGTH_REPS_TARGET,
    rpe_target=STRENGTH_RPE_TARGET,
    min_rest_seconds=STRENGTH_MIN_REST_SECONDS,
    max_rest_seconds=STRENGTH_MAX_REST_SECONDS,
    sets_count=STRENGTH_SETS_COUNT,
    rest_seconds_sanity_min=REST_SECONDS_SANITY_MIN,
    rest_seconds_sanity_max=STRENGTH_REST_SECONDS_SANITY_MAX,
)

_DEFAULTS_BY_GOAL: Final[dict[TrainingGoal, PrescriptionDefaults]] = {
    TrainingGoal.HYPERTROPHY: HYPERTROPHY_DEFAULTS,
    TrainingGoal.STRENGTH: STRENGTH_DEFAULTS,
}


def defaults_for(training_goal: Any = None) -> PrescriptionDefaults:
    """Defaults de prescricao do objetivo (leitura tolerante do dono
    `training_goal`: ausente/desconhecido = default = hipertrofia)."""
    return _DEFAULTS_BY_GOAL[read_goal(training_goal)]


def rest_seconds_sanity_max(training_goal: Any = None) -> int:
    """Teto de sanidade do descanso do objetivo (hipertrofia 240 s; forca nao
    corta os 5 min da fonte)."""
    return defaults_for(training_goal).rest_seconds_sanity_max


def item_rest_seconds(
    training_goal: Any, min_rest_seconds: float, max_rest_seconds: float
) -> int:
    """Descanso de um item SEM descanso proprio na geracao da ficha.

    Hipertrofia (default): o ponto medio do intervalo PEDIDO na geracao
    (`WorkoutPlanGenerateIn.min/max_rest_seconds`) - exatamente o de antes do
    M97. Forca: o ponto medio do intervalo de FORCA (o formulario da geracao
    e de hipertrofia; 30-90 s cortaria o descanso de 3-5 min da fonte)."""
    if rest_follows_generation_interval(training_goal):
        return midpoint_rest_seconds(min_rest_seconds, max_rest_seconds)
    return defaults_for(training_goal).rest_seconds


def rest_follows_generation_interval(training_goal: Any = None) -> bool:
    """O descanso default do item do objetivo sai do intervalo PEDIDO na
    geração (hipertrofia) ou do perfil do objetivo (força)? O editor de Modelo
    mostra "da geração" ou o descanso do perfil (`GET /training-goals`)."""
    return defaults_for(training_goal) is HYPERTROPHY_DEFAULTS


def item_reps_target(training_goal: Any = None) -> str:
    """Alvo de reps quando ninguem definiu, pelo objetivo do item."""
    return defaults_for(training_goal).reps_target


def reps_target_fits_goal(reps_target: Any, training_goal: Any = None) -> bool:
    """O alvo de reps informado cabe na faixa do objetivo? (M97 F5, auto-fix
    da geracao por IA). Hipertrofia (default) aceita qualquer alvo, como
    sempre; sem alvo (None/vazio) cabe (o item usa `item_reps_target`). Fora
    do default: piso e teto informados dentro da faixa do perfil."""
    from .training_history import parse_reps_range

    if defaults_for(training_goal) is HYPERTROPHY_DEFAULTS:
        return True
    low, high = parse_reps_range(reps_target)
    if low is None or high is None:
        return True
    goal_low, goal_high = parse_reps_range(defaults_for(training_goal).reps_target)
    return goal_low <= low and high <= goal_high


# ---------------------------------------------------------------------------
# Normalizacao do item do Modelo ao objetivo NA GERACAO (fix M97, 2026-10-26;
# mapa §66.5)
# ---------------------------------------------------------------------------
# Decisao do usuario: o Modelo guarda o que foi digitado; ao gerar a ficha
# (Modos A e B, API e Lambda `musclefit-plan-generator`, que rodam a mesma
# geracao), o item de um objetivo FORA do default com valor incompativel com o
# perfil dele e ajustado aqui, e cada ajuste vira aviso auditado
# (`prescription.WARNING_GOAL_NORMALIZED` + `goal_adjustments` no
# `prescription_json` e no relatorio da geracao). Hipertrofia (default) nunca
# e normalizada: o item sai o mesmo objeto, sem ajuste.
#
# - descanso fora de [min, max] do perfil -> clamp para a faixa (abaixo = min,
#   acima = max); sem descanso no item, vale o default do objetivo
#   (`item_rest_seconds`), que ja esta na faixa;
# - reps fora da faixa do perfil (`reps_target_fits_goal`) -> o alvo default
#   do objetivo (`item_reps_target`); so em serie de repeticoes;
# - RPE acima do teto do objetivo (`training_safety.goal_rpe_ceiling`) -> o
#   corte UNICO de texto (`effective_intensity.clamp_rpe_text`);
# - series acima do teto do objetivo (`progression_engine.ladder_for(...)
#   .max_sets_count`) -> o teto, inclusive no Modo B (o Modelo manda nas
#   series, mas nao acima do teto do objetivo);
# - tecnica fora da politica do objetivo (`training_safety.goal_allows_technique`)
#   -> removida, com os parametros dela.
ADJUSTMENT_REST_SECONDS = "rest_seconds"
ADJUSTMENT_REPS_TARGET = "reps_target"
ADJUSTMENT_RPE_TARGET = "rpe_target"
ADJUSTMENT_SETS_COUNT = "sets_count"
ADJUSTMENT_TECHNIQUE = "advanced_technique"
# Parametros que saem junto com a tecnica removida.
_TECHNIQUE_PARAMETER_FIELDS = ("advanced_technique_applications", "advanced_technique_duration_seconds")


@dataclass(frozen=True)
class GoalAdjustment:
    """Um ajuste da geracao ao objetivo (auditoria)."""

    field: str
    original: Any
    adjusted: Any
    training_goal: str
    label: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "from": self.original,
            "to": self.adjusted,
            "training_goal": self.training_goal,
            "label": self.label,
        }


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return None


def normalize_item_for_goal(
    item: Mapping[str, Any], training_goal: Any = None
) -> tuple[Mapping[str, Any], tuple[GoalAdjustment, ...]]:
    """Item do Modelo ajustado ao perfil do objetivo + os ajustes feitos.

    Default (hipertrofia): o MESMO objeto e nenhum ajuste. Fora do default:
    uma copia com descanso, reps, RPE, series e tecnica dentro do perfil (ver
    o bloco acima). Nunca grava nada: o Modelo continua como foi digitado."""
    from .effective_intensity import clamp_rpe_text
    from .progression_engine import ladder_for
    from .set_measures import is_rep_based
    from .training_goal import goal_label, goal_value
    from .training_safety import goal_allows_technique, goal_rpe_ceiling

    profile = defaults_for(training_goal)
    if profile is HYPERTROPHY_DEFAULTS:
        return item, ()
    goal = goal_value(training_goal)
    label = goal_label(goal)
    out = dict(item)
    adjustments: list[GoalAdjustment] = []

    rest = _int_or_none(item.get("rest_seconds"))
    if rest is not None and rest > 0:
        clamped = min(max(rest, profile.min_rest_seconds), profile.max_rest_seconds)
        if clamped != rest:
            out["rest_seconds"] = clamped
            adjustments.append(
                GoalAdjustment(
                    ADJUSTMENT_REST_SECONDS,
                    rest,
                    clamped,
                    goal,
                    f"descanso {rest} s → {clamped} s (faixa de {label.lower()}: "
                    f"{profile.min_rest_seconds}-{profile.max_rest_seconds} s)",
                )
            )

    reps = item.get("reps_target")
    if reps not in (None, "") and is_rep_based(item.get("measure")) and not reps_target_fits_goal(reps, goal):
        target = item_reps_target(goal)
        out["reps_target"] = target
        adjustments.append(
            GoalAdjustment(
                ADJUSTMENT_REPS_TARGET,
                str(reps),
                target,
                goal,
                f"repetições {reps} → {target} (faixa de {label.lower()})",
            )
        )

    rpe = item.get("rpe_target")
    if rpe not in (None, ""):
        ceiling = goal_rpe_ceiling(goal)
        clamped_rpe, clamps = clamp_rpe_text(rpe, ceiling)
        if clamps:
            out["rpe_target"] = clamped_rpe
            adjustments.append(
                GoalAdjustment(
                    ADJUSTMENT_RPE_TARGET,
                    str(rpe),
                    clamped_rpe,
                    goal,
                    f"RPE {rpe} → {clamped_rpe} (teto de {label.lower()}: {ceiling})",
                )
            )

    sets = _int_or_none(item.get("sets_count"))
    max_sets = ladder_for(goal).max_sets_count
    if sets is not None and sets > max_sets:
        out["sets_count"] = max_sets
        adjustments.append(
            GoalAdjustment(
                ADJUSTMENT_SETS_COUNT,
                sets,
                max_sets,
                goal,
                f"séries {sets} → {max_sets} (teto de {label.lower()})",
            )
        )

    technique = (item.get("advanced_technique") or "").strip()
    if technique and not goal_allows_technique(technique, goal):
        out["advanced_technique"] = None
        for field_name in _TECHNIQUE_PARAMETER_FIELDS:
            if field_name in out:
                out[field_name] = None
        adjustments.append(
            GoalAdjustment(
                ADJUSTMENT_TECHNIQUE,
                technique,
                None,
                goal,
                f"técnica {technique} removida (fora da política de {label.lower()})",
            )
        )
    return out, tuple(adjustments)


def goal_adjustments_text(exercise_name: Any, adjustments: Sequence[GoalAdjustment]) -> Optional[str]:
    """Aviso do relatorio da geracao para os ajustes de UM item (None sem
    ajuste). O texto sai daqui, com os numeros do perfil interpolados."""
    if not adjustments:
        return None
    name = exercise_name or "Exercício"
    details = "; ".join(item.label for item in adjustments)
    return (
        f"{name}: ajustado ao objetivo na geração ({details}). "
        "O modelo continua salvo como foi digitado."
    )


def export_for_frontend() -> dict[str, object]:
    """Seção `prescription` do `motor-constants.generated.json`."""
    return {
        "default_reps_target": DEFAULT_REPS_TARGET,
        "default_rpe_target": DEFAULT_RPE_TARGET,
        "default_min_rest_seconds": DEFAULT_MIN_REST_SECONDS,
        "default_max_rest_seconds": DEFAULT_MAX_REST_SECONDS,
        "default_rest_seconds": default_rest_seconds(),
        "rest_seconds_sanity_min": REST_SECONDS_SANITY_MIN,
        "rest_seconds_sanity_max": REST_SECONDS_SANITY_MAX,
        "min_sets_count": MIN_SETS_COUNT,
        "set_logger_fallback_reps": SET_LOGGER_FALLBACK_REPS,
        "set_logger_fallback_load_kg": SET_LOGGER_FALLBACK_LOAD_KG,
    }


__all__ = [
    "DEFAULT_MAX_REST_SECONDS",
    "DEFAULT_MIN_REST_SECONDS",
    "DEFAULT_PRESCRIPTION_REST_SECONDS",
    "DEFAULT_REPS_TARGET",
    "DEFAULT_RPE_TARGET",
    "DEFAULT_SETS_COUNT",
    "ADJUSTMENT_REPS_TARGET",
    "ADJUSTMENT_REST_SECONDS",
    "ADJUSTMENT_RPE_TARGET",
    "ADJUSTMENT_SETS_COUNT",
    "ADJUSTMENT_TECHNIQUE",
    "GoalAdjustment",
    "HYPERTROPHY_DEFAULTS",
    "MIN_SETS_COUNT",
    "REST_SECONDS_SANITY_MAX",
    "REST_SECONDS_SANITY_MIN",
    "SET_LOGGER_FALLBACK_LOAD_KG",
    "SET_LOGGER_FALLBACK_REPS",
    "STRENGTH_DEFAULTS",
    "STRENGTH_MAX_REST_SECONDS",
    "STRENGTH_MIN_REST_SECONDS",
    "STRENGTH_REPS_TARGET",
    "STRENGTH_REST_SECONDS_SANITY_MAX",
    "STRENGTH_RPE_FLOOR",
    "STRENGTH_RPE_TARGET",
    "STRENGTH_SETS_COUNT",
    "PrescriptionDefaults",
    "default_rest_seconds",
    "defaults_for",
    "export_for_frontend",
    "goal_adjustments_text",
    "normalize_item_for_goal",
    "item_reps_target",
    "item_rest_seconds",
    "midpoint_rest_seconds",
    "reps_target_fits_goal",
    "rest_follows_generation_interval",
    "rest_seconds_sanity_max",
]
