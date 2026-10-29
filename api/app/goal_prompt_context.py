"""Texto de prompt por OBJETIVO de treino e por DELOAD (M97 F5; mapa §68).

Os prompts de IA do treino (`workout_summary_ai`, `exercise_set_evaluation_ai`,
`exercise_summary_ai`, `insights_prompt`) recebem o objetivo e o deload PRONTOS
(do snapshot congelado da ficha, de `exercises.training_goal`, de
`activities.deload_source` ou de `engine_decision_context.PairEngineContext`) e
pedem a este módulo:

- `focus_text(goals)`: o foco da persona ("hipertrofia" quando só há o objetivo
  default: a persona de sempre, letra por letra; senão os rótulos do dono
  `training_goal.GOAL_LABELS`);
- `goal_section(goals, ...)` e `deload_section(...)`: o bloco de regras do
  objetivo não-default e o do deload, com TODO número interpolado dos donos
  (`prescription_defaults.defaults_for`, `training_safety.goal_*`,
  `deload.deload_reason_label`/`DELOAD_SETS_REDUCTION_FRACTION`). Objetivo
  default e sessão normal = `""`: o prompt de hipertrofia não muda;
- `prompt_version(base, goals, deload)`: a versão do prompt com sufixo só nos
  ramos novos (`+goal-v1`, `+deload-v2`), no padrão do `STRENGTH_ENGINE_VERSION`
  da progressão. Hipertrofia sem deload = a versão de sempre (snapshots, dedupe
  do resumo e eval inalterados).

A prosa dos blocos mora no repositório privado (M103 P2,
`api/privado/prompts/goal_context/`); aqui ficam os ramos e os valores.

Nenhum ramo por objetivo aqui (guarda `objetivo_treino_ramo`): o texto é o mesmo
para todo objetivo não-default, preenchido pelas funções do dono. Nenhuma regra
nova: só composição de texto.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from . import prompt_store
from .domain.deload import DELOAD_ALLOWED_TECHNIQUES, DELOAD_SETS_REDUCTION_FRACTION, deload_reason_label
from .domain.prescription_defaults import defaults_for
from .domain.training_goal import DEFAULT_GOAL, goal_label, goals_in, is_default
from .domain.training_safety import goal_disallowed_techniques, goal_rpe_ceiling, goal_target_rpe

GOAL_PROMPT_VERSION = "goal-v1"
DELOAD_PROMPT_VERSION = "deload-v2"

# M103 P2: a PROSA dos dois blocos mora no repositório privado,
# `api/privado/prompts/goal_context/<versão>[.parte].md` (`prompt_store`); aqui
# ficam a composição, os ramos e os valores (todos lidos dos donos).
PROMPT_FEATURE_DIR = "goal_context"


def _goal_text(part: str, values: Mapping[str, str]) -> str:
    return prompt_store.render(PROMPT_FEATURE_DIR, f"{GOAL_PROMPT_VERSION}{part}", values)


def _deload_text(part: str, values: Mapping[str, str]) -> str:
    return prompt_store.render(PROMPT_FEATURE_DIR, f"{DELOAD_PROMPT_VERSION}{part}", values)

SCOPE_SESSION = "session"
SCOPE_WINDOW = "window"


def _as_goals(goals: Any) -> tuple[str, ...]:
    """Objetivos distintos (leitura tolerante do dono; ordem do vocabulário).
    Aceita um objetivo, uma coleção ou `None` (= default)."""
    if goals is None:
        return (DEFAULT_GOAL.value,)
    if isinstance(goals, (str, bytes)) or not isinstance(goals, Iterable):
        goals = [goals]
    values = goals_in(goals)
    return values or (DEFAULT_GOAL.value,)


def non_default_goals(goals: Any) -> tuple[str, ...]:
    return tuple(goal for goal in _as_goals(goals) if not is_default(goal))


def focus_text(goals: Any) -> str:
    """Foco da persona: os rótulos dos objetivos, em minúsculas, separados por
    vírgula ("hipertrofia"; "força"; "hipertrofia, força")."""
    return ", ".join(goal_label(goal).lower() for goal in _as_goals(goals))


def goal_label_suffix(goal: Any) -> str:
    """Marca de UM exercício na lista do prompt: `""` no default (a linha de
    sempre), senão ` — objetivo: <rótulo>`."""
    return "" if is_default(goal) else f" — objetivo: {goal_label(goal)}"


def _techniques_text(codes: Iterable[str], technique_names: Optional[Mapping[str, str]]) -> str:
    """Técnicas vetadas pelo objetivo, pelo NOME do catálogo `advanced_techniques`
    (M95c). Código sem nome resolvido (catálogo indisponível ou código fora
    dele) nunca vaza cru: vira a menção genérica."""
    codes = sorted(codes)
    if not codes:
        return ""
    names = technique_names or {}
    resolved = [names[code] for code in codes if names.get(code)]
    if not resolved:
        return _goal_text(".tecnicas_genericas", {})
    tail = _goal_text(".tecnicas_outras", {}) if len(resolved) < len(codes) else ""
    return _goal_text(".tecnicas", {"tecnicas": ", ".join(resolved), "outras": tail})


def _goal_rules(goal: str, technique_names: Optional[Mapping[str, str]]) -> str:
    label = goal_label(goal)
    defaults = defaults_for(goal)
    ceiling = goal_rpe_ceiling(goal)
    ceiling_text = _goal_text(".teto", {"teto": f"{ceiling}"}) if ceiling is not None else ""
    return _goal_text(
        ".regra",
        {
            "objetivo": label,
            "reps": f"{defaults.reps_target}",
            "rpe": f"{defaults.rpe_target}",
            "rpe_alvo": f"{goal_target_rpe(goal)}",
            "teto": ceiling_text,
            "descanso_min": f"{defaults.min_rest_seconds}",
            "descanso_max": f"{defaults.max_rest_seconds}",
            "series": f"{defaults.sets_count}",
            "tecnicas": _techniques_text(goal_disallowed_techniques(goal), technique_names),
        },
    )


def goal_section(goals: Any, technique_names: Optional[Mapping[str, str]] = None) -> str:
    """Bloco de regras dos objetivos NÃO-default presentes (`""` se só há o
    default). `technique_names` = `{code: nome}` do catálogo `advanced_techniques`
    (`advanced_techniques.technique_display_names`); ausente, menção genérica,
    nunca o código cru (M95c)."""
    extra = non_default_goals(goals)
    if not extra:
        return ""
    default_label = goal_label(DEFAULT_GOAL)
    lines = "\n".join(_goal_rules(goal, technique_names) for goal in extra)
    return _goal_text(
        "",
        {"objetivo_default": default_label, "regras": lines, "objetivo_default_minusculo": default_label.lower()},
    )


def deload_section(is_deload: bool, *, scope: str = SCOPE_SESSION, prescription: Any = None) -> str:
    """Bloco do deload (`""` fora dele). `scope`: a sessão é de deload
    (`SCOPE_SESSION`) ou a janela analisada tem deload (`SCOPE_WINDOW`).
    `prescription`: `deload.DeloadPrescription` do exercício, quando há."""
    if not is_deload:
        return ""
    fraction = DELOAD_SETS_REDUCTION_FRACTION
    rule = _deload_text(
        ".regra",
        {
            "motivo": deload_reason_label(),
            "fracao_numerador": f"{fraction.numerator}",
            "fracao_denominador": f"{fraction.denominator}",
        },
    )
    item = ""
    if prescription is not None:
        parts = []
        if getattr(prescription, "load_kg", None) is not None:
            parts.append(f"{prescription.load_kg} kg")
        if getattr(prescription, "reps_target", None) is not None:
            parts.append(f"{prescription.reps_target} reps")
        if getattr(prescription, "sets_count", None) is not None:
            parts.append(f"{prescription.sets_count} série(s)")
        parts.append(f"RPE-alvo {prescription.target_rpe}")
        item = _deload_text(".prescricao", {"prescricao": ", ".join(parts)})
    if scope == SCOPE_WINDOW:
        return _deload_text(".janela", {"regra": rule})
    return _deload_text("", {"regra": rule, "prescricao": item, "tecnicas": _deload_techniques_text()})


def _deload_techniques_text() -> str:
    """Técnicas na sessão de deload, do dono (`DELOAD_ALLOWED_TECHNIQUES`)."""
    if not DELOAD_ALLOWED_TECHNIQUES:
        return _deload_text(".sem_tecnicas", {})
    return _deload_text(".tecnicas", {"tecnicas": ", ".join(sorted(DELOAD_ALLOWED_TECHNIQUES))})


def prompt_section(
    goals: Any,
    is_deload: bool = False,
    *,
    technique_names: Optional[Mapping[str, str]] = None,
    deload_scope: str = SCOPE_SESSION,
    deload_prescription: Any = None,
) -> str:
    """Os dois blocos prontos para COLAR depois de uma linha do prompt:
    `""` (objetivo default, sessão normal) ou `"\\n\\n<blocos>"`."""
    blocks = [
        block
        for block in (
            goal_section(goals, technique_names),
            deload_section(is_deload, scope=deload_scope, prescription=deload_prescription),
        )
        if block
    ]
    return "".join(f"\n\n{block}" for block in blocks)


def prompt_version(base: str, goals: Any = None, is_deload: bool = False) -> str:
    """`base` no ramo de sempre; com sufixo nos ramos novos."""
    version = base
    if non_default_goals(goals):
        version += f"+{GOAL_PROMPT_VERSION}"
    if is_deload:
        version += f"+{DELOAD_PROMPT_VERSION}"
    return version


__all__ = [
    "DELOAD_PROMPT_VERSION",
    "GOAL_PROMPT_VERSION",
    "SCOPE_SESSION",
    "SCOPE_WINDOW",
    "deload_section",
    "focus_text",
    "goal_label_suffix",
    "goal_section",
    "non_default_goals",
    "prompt_section",
    "prompt_version",
]
