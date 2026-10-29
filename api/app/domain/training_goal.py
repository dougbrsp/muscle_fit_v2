"""Objetivo de treino (M97, F0): hipertrofia ou força.

Dono ÚNICO do vocabulário (`TrainingGoal`), do default (`DEFAULT_GOAL`), da
precedência (`resolve_goal`: override do item do slot > Modelo > default) e do
predicado SQL (`sql_goal_is`). Fora daqui ninguém escreve os literais
`'hypertrophy'`/`'strength'` (guarda `objetivo_treino_literal` em
`tests/unit/test_guarda_fontes_da_verdade.py`; mapa
`docs/motores/FONTES_DA_VERDADE.md` §66).

Onde o objetivo é gravado (migração 093; F0 só grava, nenhum motor lê):

- `workout_plan_templates` / `workout_plan_template_versions`: o do Modelo
  (cabeçalho da versão, como `methodology_intent`);
- `workout_plan_template_slot_items`: override do item (NULL = herda);
- `workout_plan_exercises` (+ `prescription_json`): o resolvido na geração;
- `exercises`: carimbado no registro a partir da linha da ficha de origem;
  treino livre, relógio e importação ficam no default;
- `progression_tracks` / `progression_group_states` (na chave única) e
  `progression_decisions`: hoje sempre o default (F1 passa a separar).

Python puro: sem banco, sem FastAPI.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Iterable, Mapping, Optional


class TrainingGoal(str, Enum):
    HYPERTROPHY = "hypertrophy"
    STRENGTH = "strength"


# Todo dado anterior à 093 (e todo registro sem ficha de origem) é hipertrofia:
# é o que o app sempre prescreveu. É também o DEFAULT das colunas da 093.
DEFAULT_GOAL = TrainingGoal.HYPERTROPHY

GOAL_VALUES: tuple[str, ...] = tuple(goal.value for goal in TrainingGoal)

TRAINING_GOAL_COLUMN = "training_goal"

# Rótulo de exibição de cada objetivo (M97 F3): a tela lê de `GET /training-goals`
# e nunca escreve o nome do objetivo à mão.
GOAL_LABELS: dict[TrainingGoal, str] = {
    TrainingGoal.HYPERTROPHY: "Hipertrofia",
    TrainingGoal.STRENGTH: "Força",
}


def goal_label(value: Any) -> str:
    """Rótulo do objetivo (leitura tolerante: ausente/desconhecido = default)."""
    return GOAL_LABELS[read_goal(value)]


# Pasta do material de RAG PRÓPRIO de cada objetivo (M97 F5), relativa a
# `rag_chunking.GOAL_RAG_DIRNAME`. `None` = o objetivo não tem material próprio:
# a hipertrofia (default) é a base de sempre da raiz de `rag/markdown`, e o
# prompt dela não muda. O material fica FORA da raiz de propósito: a descoberta
# automática de `select_rag_text` injetaria um `.md` da raiz em todo prompt.
GOAL_RAG_FOLDERS: dict[TrainingGoal, Optional[str]] = {
    TrainingGoal.HYPERTROPHY: None,
    TrainingGoal.STRENGTH: "FORCA",
}


def goal_rag_folder(value: Any) -> Optional[str]:
    """Pasta do RAG próprio do objetivo (leitura tolerante; `None` = sem)."""
    return GOAL_RAG_FOLDERS[read_goal(value)]


class InvalidTrainingGoal(ValueError):
    """Objetivo fora do vocabulário (a API devolve 422 com esta mensagem)."""


def _text(value: Any) -> str:
    if isinstance(value, TrainingGoal):
        return value.value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    return "" if value is None else str(value).strip().lower()


def parse_goal(value: Any) -> TrainingGoal:
    """Leitura ESTRITA (entrada da API): valor fora do vocabulário, vazio ou
    `None` -> `InvalidTrainingGoal`. Quem aceita "não informado" trata o `None`
    antes (ver `optional_goal_value`)."""
    text = _text(value)
    try:
        return TrainingGoal(text)
    except ValueError as exc:
        aceitos = ", ".join(GOAL_VALUES)
        raise InvalidTrainingGoal(f"training_goal inválido: {value!r}. Valores aceitos: {aceitos}.") from exc


def optional_goal_value(value: Any) -> Optional[str]:
    """Validador da API para campo opcional: `None`/vazio passa como `None`
    ("não informado"); qualquer outro valor passa por `parse_goal` (estrito)."""
    if value is None or _text(value) == "":
        return None
    return parse_goal(value).value


def read_goal(value: Any) -> TrainingGoal:
    """Leitura TOLERANTE (banco, snapshot, payload interno): `None`, vazio ou
    desconhecido -> `DEFAULT_GOAL`. Linha anterior à 093 lê hipertrofia."""
    try:
        return TrainingGoal(_text(value))
    except ValueError:
        return DEFAULT_GOAL


def read_override(value: Any) -> Optional[TrainingGoal]:
    """Override do item do slot: `None`/vazio/desconhecido = herda do Modelo."""
    try:
        return TrainingGoal(_text(value))
    except ValueError:
        return None


def goal_value(value: Any) -> str:
    """VALOR a gravar numa coluna NOT NULL (`read_goal(...).value`)."""
    return read_goal(value).value


def override_value(value: Any) -> Optional[str]:
    """VALOR a gravar na coluna NULL do item do slot (`None` = herda)."""
    goal = read_override(value)
    return None if goal is None else goal.value


def is_default(value: Any) -> bool:
    return read_goal(value) is DEFAULT_GOAL


def goals_in(values: Iterable[Any]) -> tuple[str, ...]:
    """VALORES distintos presentes (leitura tolerante: ausente = default), na
    ORDEM do vocabulário (`TrainingGoal`, default primeiro). É a ordem em que a
    progressão avalia uma sessão híbrida (`progression_evaluator`), então o
    resultado é determinístico."""
    present = {goal_value(value) for value in values}
    return tuple(goal for goal in GOAL_VALUES if goal in present)


def resolve_goal(item_override: Any, template_goal: Any) -> TrainingGoal:
    """Objetivo de uma linha da ficha: override do item do slot > objetivo do
    Modelo > `DEFAULT_GOAL`."""
    override = read_override(item_override)
    if override is not None:
        return override
    return read_goal(template_goal)


def resolved_goal_value(item_override: Any, template_goal: Any) -> str:
    """VALOR de `resolve_goal` (o que vai para a linha da ficha)."""
    return resolve_goal(item_override, template_goal).value


def goal_by_group(items: Iterable[Any], template_goal: Any) -> dict[int, str]:
    """Modo A (itens por grupo muscular): `muscle_group_id -> VALOR resolvido`.
    O exercício escolhido para um grupo herda o override do PRIMEIRO item do
    slot daquele grupo (ordem de `item_order`); grupo sem item = o do Modelo
    (quem consulta usa `resolved_goal_value(None, template_goal)`)."""
    goals: dict[int, str] = {}
    ordered = sorted(items or (), key=lambda item: _item_get(item, "item_order") or 0)
    for item in ordered:
        group_id = _item_get(item, "muscle_group_id")
        if group_id is None or int(group_id) in goals:
            continue
        goals[int(group_id)] = resolved_goal_value(_item_get(item, TRAINING_GOAL_COLUMN), template_goal)
    return goals


def _item_get(item: Any, key: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


# ---------------------------------------------------------------------------
# SQL (um lugar só)
# ---------------------------------------------------------------------------

_SQL_ALIAS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SQL_PARAM_RE = re.compile(r"^(?:%s|%\([A-Za-z_][A-Za-z0-9_]*\)s)$")


def sql_goal_is(alias: str, param: str = "%s") -> str:
    """Predicado SQL "a linha `alias` é do objetivo do parâmetro":
    `<alias>.training_goal = <param>`. O valor vai SEMPRE como parâmetro
    (`goal_value(...)`), nunca como literal. `alias` e `param` são fragmentos
    fixos do código, validados como em `set_types.sql_set_confirmed`."""
    if not _SQL_ALIAS_RE.match(alias or ""):
        raise ValueError(f"Alias SQL invalido para training_goal: {alias!r}")
    if not _SQL_PARAM_RE.match(param or ""):
        raise ValueError(f"Parametro SQL invalido para training_goal: {param!r}")
    return f"{alias}.{TRAINING_GOAL_COLUMN} = {param}"


_SQL_NUMBER_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def sql_goal_case(alias: str, goal: Any, then_sql: str, else_sql: str) -> str:
    """Expressão SQL "se a linha `alias` é do objetivo `goal`, `then_sql`;
    senão, `else_sql`" (M97 F4): base dos pesos por objetivo que os donos
    aplicam DENTRO de agregações (`SUM(... * <peso>)`), onde um parâmetro
    posicional embaralharia a ordem dos demais. O valor do objetivo sai do
    vocabulário (`parse_goal`, estrito), nunca de entrada externa; `then_sql`
    e `else_sql` são números do código do dono (validados)."""
    if not _SQL_ALIAS_RE.match(alias or ""):
        raise ValueError(f"Alias SQL invalido para training_goal: {alias!r}")
    for fragment in (then_sql, else_sql):
        if not _SQL_NUMBER_RE.match(str(fragment)):
            raise ValueError(f"Fragmento SQL invalido (numero esperado): {fragment!r}")
    value = parse_goal(goal).value
    return f"(CASE WHEN {alias}.{TRAINING_GOAL_COLUMN} = '{value}' THEN {then_sql} ELSE {else_sql} END)"


def sql_goal_join(left_alias: str, right_alias: str) -> str:
    """Condição de JOIN "as duas linhas são do MESMO objetivo" (M97 F1):
    `<left>.training_goal = <right>.training_goal` (ex.: a trilha e o estado
    do grupo dela). Aliases validados como em `sql_goal_is`."""
    for alias in (left_alias, right_alias):
        if not _SQL_ALIAS_RE.match(alias or ""):
            raise ValueError(f"Alias SQL invalido para training_goal: {alias!r}")
    return f"{left_alias}.{TRAINING_GOAL_COLUMN} = {right_alias}.{TRAINING_GOAL_COLUMN}"


# Objetivo de uma linha de `exercises` no registro: o da linha da ficha de
# origem; sem ficha (treino livre) ou linha inexistente, o default. Subconsulta
# escalar no VALUES do INSERT (nenhum statement a mais). Dois parâmetros:
# `plan_exercise_goal_params`.
SQL_PLAN_EXERCISE_GOAL = (
    "COALESCE((SELECT wpe_goal.training_goal FROM workout_plan_exercises wpe_goal "
    "WHERE wpe_goal.id = %s), %s)"
)


def plan_exercise_goal_params(workout_plan_exercise_id: Optional[int]) -> tuple[Optional[int], str]:
    return (None if workout_plan_exercise_id is None else int(workout_plan_exercise_id), DEFAULT_GOAL.value)
