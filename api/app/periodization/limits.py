"""Tetos e pisos de tamanho de sessão do Modelo e da ficha (M76, Lote 4c — E9).

Dono ÚNICO dos limites de exercícios por slot/sessão, séries por exercício,
itens priorizados e tempo por slot. Antes estavam espalhados e pareciam
contraditórios (exercícios/sessão 6–8–12 em `context.py` × 9 em
`volume_complement.py` × 12 em `abc_volume_reinforcement.py`; séries/exercício
4 × 6 × 9; o prompt dizia "{alvo}-9" com o 9 escrito à mão).

Os valores divergem por MOTIVO LEGÍTIMO — cada um responde a uma pergunta
diferente, decidida pelo usuário — e por isso cada um tem um nome próprio.
NENHUM número mudou neste lote; só passou a ter um dono:

| Pergunta | Constante | Valor | Quem consome |
|---|---|---|---|
| Menos que isto é esboço, não sessão (único número que o validador confere) | `SESSION_EXERCISES_MIN` | 6 | prompt + `SLOT_TOO_FEW_EXERCISES` |
| Alvo que o prompt pede quando o catálogo permite | `SESSION_EXERCISES_TARGET_MIN` | 8 | prompt |
| Folga do prompt (não orçamento; a faixa por grupo tem precedência) | `SESSION_EXERCISES_MAX` | 12 | prompt |
| Até onde o complemento DETERMINÍSTICO enche um slot (M46) | `COMPLEMENT_MAX_EXERCISES_PER_SLOT` | 9 | complemento + prompt ("tipicamente 8-9") |
| Séries por exercício: ideal (EXG-004) | `methodology_volume._SETS_PER_EXERCISE_MIN/MAX` | 3–4 | prompt, complemento (o MIN é também o piso do corte de excesso, `VOLUME_REDUCED`), validador (dono: `metrics_registry`) |
| Séries por exercício: teto do ramo (c) do complemento (M46) | `COMPLEMENT_MAX_SETS_PER_EXERCISE` | 6 | complemento |
| Tempo máximo de um slot no complemento (M46) | `SLOT_TIME_LIMIT_SECONDS` | 90 min | complemento |
| Descanso das séries sem descanso NA CONTA do complemento (2026-10-05) | `COMPLEMENT_DEFAULT_REST_SECONDS` | 90 s (teto do intervalo padrão) | complemento |
| Itens `prioritize` por slot | `MAX_PRIORITIZED_PER_SLOT` | 3 | save do Modelo (422), validador da IA, prompt |
| Modo A determinístico (passo 6): exercícios por grupo no slot | `MODE_A_MAX_EXERCISES_PER_GROUP` | 3 | `generation/mode_a.py` |
| Motor de ficha v2 legado: teto de séries por exercício ("sessão compacta") | `LEGACY_V2_MAX_SETS_PER_EXERCISE` | 6 | `template_volume_validator` (Lote 5 apaga) |
| Reforço da ficha legada ABC/motor v1: teto suave de exercícios do dia | `LEGACY_REINFORCEMENT_MAX_EXERCISES_PER_DAY` | 12 | `abc_volume_reinforcement` (Lote 5 apaga) |
| Reforço da ficha legada: teto rígido de séries por exercício | `LEGACY_REINFORCEMENT_MAX_SETS_PER_EXERCISE` | 9 | `abc_volume_reinforcement` (Lote 5 apaga) |

Python puro, sem I/O e sem importar o resto do pacote `periodization` (só o
dono dos defaults de prescrição), para que validador, prompt, complemento e o
save do Modelo leiam a mesma constante sem ciclo de import.
"""

from __future__ import annotations

from typing import Any, Final, Iterable, Mapping, Optional

from ..domain.prescription_defaults import DEFAULT_MAX_REST_SECONDS, defaults_for
from ..domain.training_goal import TrainingGoal, read_goal, resolve_goal

# --- Sessão completa do Modo B (prompt da Periodização) -------------------
# O PISO é o único número que o validador confere (`SLOT_TOO_FEW_EXERCISES`) e
# por isso NÃO muda - é o que separa uma sessão de um esboço. O TETO subiu de
# 9 para 12 no prompt v3 (medição de 2026-09-28: com teto 9 a LLM tratava
# "6-9 exercícios" como orçamento do DIA e repartia entre os grupos). O teto do
# prompt é folga, não orçamento.
SESSION_EXERCISES_MIN: Final[int] = 6
SESSION_EXERCISES_TARGET_MIN: Final[int] = 8
SESSION_EXERCISES_MAX: Final[int] = 12

# --- Complemento determinístico de volume (M46, decisões do usuário) ------
COMPLEMENT_MAX_EXERCISES_PER_SLOT: Final[int] = 9
# O ideal continua 4 séries (EXG-004); 6 é o teto do ramo (c).
COMPLEMENT_MAX_SETS_PER_EXERCISE: Final[int] = 6
SLOT_TIME_LIMIT_MINUTES: Final[int] = 90
SLOT_TIME_LIMIT_SECONDS: Final[int] = SLOT_TIME_LIMIT_MINUTES * 60
# Decisão do usuário (2026-10-05): na conta dos 90 min DO COMPLEMENTO, série de
# trabalho sem `rest_seconds` (a IA sempre o deixa nulo) descansa o TETO do
# intervalo padrão de descanso (90 s) - conservador: o complemento nunca enche
# um slot contando um descanso menor do que o aluno pode fazer. A tela do
# Modelo continua contando 0 s (`time_estimate.REST_WHEN_UNDEFINED_SECONDS`).
COMPLEMENT_DEFAULT_REST_SECONDS: Final[int] = DEFAULT_MAX_REST_SECONDS
# Corte de excesso (`VOLUME_REDUCED`, 2026-10-16): teto RIGIDO de avaliacoes do
# motor por chamada do complemento (cortes aceitos + rejeitados), orcamento
# proprio, separado dos passos do acrescimo. Estourou = o excesso restante fica
# como aviso. Medicao nas 20 respostas reais: ver FONTES_DA_VERDADE §43.
COMPLEMENT_REDUCTION_MAX_EVALUATIONS: Final[int] = 200

# O prompt diz "tipicamente {alvo}-N exercícios" num slot com um grupo de
# faixa alta e baixa frequência: N é até onde o complemento leva um slot.
SESSION_EXERCISES_TYPICAL_MAX: Final[int] = COMPLEMENT_MAX_EXERCISES_PER_SLOT

# --- Objetivo de treino FORÇA (M97 F2, 2026-10-25; mapa §66.2) -------------
# Sessão de força: 2-4 compostos pesados + isoladores em faixa moderada, com
# 3-5 min de descanso (RAG FOR-042/023) - cabe menos exercício no mesmo tempo.
# O piso de uma sessão completa de força é menor que o de hipertrofia. Valor a
# calibrar (PR da F2). O complemento conta o TETO do intervalo de descanso do
# objetivo do item (força: 300 s; hipertrofia: os 90 s de sempre).
STRENGTH_SESSION_EXERCISES_MIN: Final[int] = 4

_SESSION_EXERCISES_MIN_BY_GOAL: Final[Mapping[TrainingGoal, int]] = {
    TrainingGoal.HYPERTROPHY: SESSION_EXERCISES_MIN,
    TrainingGoal.STRENGTH: STRENGTH_SESSION_EXERCISES_MIN,
}


def session_exercises_min(training_goal: Any = None) -> int:
    """Piso de exercícios de uma sessão completa do objetivo (hipertrofia: 6)."""
    return _SESSION_EXERCISES_MIN_BY_GOAL[read_goal(training_goal)]


def slot_items_goal(items: Iterable[Any], template_goal: Any = None) -> TrainingGoal:
    """Objetivo de um slot para os limites de sessão: força só quando TODOS os
    itens de equipamento resolvem para força (override do item > objetivo do
    Modelo, `training_goal.resolve_goal`). Slot misto ou sem item = hipertrofia,
    o piso de sempre."""
    goals = {
        resolve_goal(_item_field(item, "training_goal"), template_goal) for item in items or () if _is_equipment(item)
    }
    if goals == {TrainingGoal.STRENGTH}:
        return TrainingGoal.STRENGTH
    return TrainingGoal.HYPERTROPHY


def slot_session_goal(items: Iterable[Any], template_goal: Any = None, declared_goal: Any = None) -> TrainingGoal:
    """Objetivo do slot para o piso de exercícios: o DECLARADO no pedido da
    geração por IA (objetivo do dia, `PeriodizationDraftIn.day_goals`, M97 F5)
    vence; sem declaração, o dos itens (`slot_items_goal`). Um dia de força tem
    isoladores com override de hipertrofia e continua sendo dia de força."""
    if declared_goal is not None:
        return read_goal(declared_goal)
    return slot_items_goal(items, template_goal)


# --- Composição do dia por objetivo (M97 F5; RAG FOR-042/FOR-023) -----------
# Dia de força na geração por IA: 2-4 compostos pesados no objetivo do dia; os
# isoladores vão em faixa moderada (override de hipertrofia), não em carga
# máxima. Hipertrofia não tem faixa de compostos (None) e nada muda nela.
STRENGTH_DAY_HEAVY_COMPOUNDS_MIN: Final[int] = 2
STRENGTH_DAY_HEAVY_COMPOUNDS_MAX: Final[int] = 4

_HEAVY_COMPOUNDS_BY_GOAL: Final[Mapping[TrainingGoal, Optional[tuple[int, int]]]] = {
    TrainingGoal.HYPERTROPHY: None,
    TrainingGoal.STRENGTH: (STRENGTH_DAY_HEAVY_COMPOUNDS_MIN, STRENGTH_DAY_HEAVY_COMPOUNDS_MAX),
}
# Objetivo do ISOLADOR num dia do objetivo (o composto fica no do dia).
_ISOLATION_GOAL_BY_DAY_GOAL: Final[Mapping[TrainingGoal, TrainingGoal]] = {
    TrainingGoal.HYPERTROPHY: TrainingGoal.HYPERTROPHY,
    TrainingGoal.STRENGTH: TrainingGoal.HYPERTROPHY,
}


def heavy_compound_range(day_goal: Any = None) -> Optional[tuple[int, int]]:
    """Faixa de compostos pesados de um dia do objetivo (`None` = sem faixa)."""
    return _HEAVY_COMPOUNDS_BY_GOAL[read_goal(day_goal)]


def day_item_goal(day_goal: Any, is_compound: bool) -> TrainingGoal:
    """Objetivo de um item de equipamento num dia do objetivo `day_goal`:
    composto = o do dia; isolador = `_ISOLATION_GOAL_BY_DAY_GOAL` (força ->
    hipertrofia). Hipertrofia devolve hipertrofia para os dois."""
    goal = read_goal(day_goal)
    return goal if is_compound else _ISOLATION_GOAL_BY_DAY_GOAL[goal]


def complement_default_rest_seconds(item: Any, template_goal: Any = None) -> int:
    """Descanso de uma série de trabalho SEM `rest_seconds` na conta de tempo
    do complemento: o teto do intervalo do objetivo do item (override > Modelo;
    hipertrofia = `COMPLEMENT_DEFAULT_REST_SECONDS`, o de sempre)."""
    goal = resolve_goal(_item_field(item, "training_goal"), template_goal)
    if goal is TrainingGoal.HYPERTROPHY:
        return COMPLEMENT_DEFAULT_REST_SECONDS
    return defaults_for(goal).max_rest_seconds


def _item_field(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _is_equipment(item: Any) -> bool:
    return (_item_field(item, "item_kind") or "equipment") == "equipment"


# --- Priorização -----------------------------------------------------------
MAX_PRIORITIZED_PER_SLOT: Final[int] = 3

# --- Geração de ficha do Modo A (motor determinístico, passo 6) -----------
MODE_A_MAX_EXERCISES_PER_GROUP: Final[int] = 3

# --- Motor de ficha v2 legado (USE_TEMPLATE_VOLUME_ENGINE_V2; Lote 5 apaga) -
# Deliberadamente MENOR que o teto do reforço ABC (9): sessão compacta.
LEGACY_V2_MAX_SETS_PER_EXERCISE: Final[int] = 6

# --- Reforço da ficha legada (ABC / templates_engine v1; Lote 5 apaga) ----
LEGACY_REINFORCEMENT_MAX_EXERCISES_PER_DAY: Final[int] = 12
LEGACY_REINFORCEMENT_MAX_SETS_PER_EXERCISE: Final[int] = 9


def export_for_frontend() -> dict[str, int]:
    """Seção `periodization_limits` do `motor-constants.generated.json`:
    rótulos do relatório do complemento e a validação de priorizados do
    builder. Séries ideais por exercício vêm de EXG-004 (`methodology_volume`)."""
    from ..intelligence.methodology_volume import _SETS_PER_EXERCISE_MAX

    return {
        "max_prioritized_per_slot": MAX_PRIORITIZED_PER_SLOT,
        "complement_max_exercises_per_slot": COMPLEMENT_MAX_EXERCISES_PER_SLOT,
        "complement_max_sets_per_exercise": COMPLEMENT_MAX_SETS_PER_EXERCISE,
        "slot_time_limit_minutes": SLOT_TIME_LIMIT_MINUTES,
        "sets_per_exercise_ideal_max": int(_SETS_PER_EXERCISE_MAX),
    }


__all__ = [
    "COMPLEMENT_DEFAULT_REST_SECONDS",
    "COMPLEMENT_MAX_EXERCISES_PER_SLOT",
    "COMPLEMENT_MAX_SETS_PER_EXERCISE",
    "LEGACY_REINFORCEMENT_MAX_EXERCISES_PER_DAY",
    "LEGACY_REINFORCEMENT_MAX_SETS_PER_EXERCISE",
    "LEGACY_V2_MAX_SETS_PER_EXERCISE",
    "MAX_PRIORITIZED_PER_SLOT",
    "MODE_A_MAX_EXERCISES_PER_GROUP",
    "SESSION_EXERCISES_MAX",
    "SESSION_EXERCISES_MIN",
    "SESSION_EXERCISES_TARGET_MIN",
    "SESSION_EXERCISES_TYPICAL_MAX",
    "SLOT_TIME_LIMIT_MINUTES",
    "SLOT_TIME_LIMIT_SECONDS",
    "STRENGTH_DAY_HEAVY_COMPOUNDS_MAX",
    "STRENGTH_DAY_HEAVY_COMPOUNDS_MIN",
    "STRENGTH_SESSION_EXERCISES_MIN",
    "complement_default_rest_seconds",
    "day_item_goal",
    "export_for_frontend",
    "heavy_compound_range",
    "session_exercises_min",
    "slot_items_goal",
    "slot_session_goal",
]
