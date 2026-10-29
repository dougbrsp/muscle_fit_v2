"""Preservação dos campos avançados de um exercício de ficha no update.

Módulo propositalmente sem dependências (nem DB, nem FastAPI) para ser
testável isoladamente — ver tests/unit/test_workout_plan_update_preserve.py.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

# Os 10 campos de técnica avançada/aquecimento/ajuste/pico de contração do
# modo 'equipment_series' (WorkoutPlanExerciseIn em models.py) e os 3 da
# medida da série. Não são
# editáveis na tela da ficha, então um cliente desatualizado pode omiti-los
# no PUT — sem esta proteção o UPDATE gravaria NULL por cima dos valores já
# persistidos.
ADVANCED_EXERCISE_FIELDS: tuple[str, ...] = (
    "advanced_technique",
    "advanced_technique_applications",
    "advanced_technique_duration_seconds",
    "warmup_sets_count",
    "warmup_reps",
    "warmup_rest_seconds",
    "feeler_set_sets_count",
    "feeler_set_reps",
    "feeler_set_rest_seconds",
    "peak_contraction_seconds",
    # Medida da série (Lote T2; colunas da 089): a tela da ficha não a edita,
    # então o cliente antigo que não a manda não pode apagar uma medida por
    # tempo / máximo de reps copiada do Modelo.
    "measure",
    "duration_seconds_target",
    "time_window_seconds",
    # Objetivo de treino (M97 F0; coluna da 093): resolvido na geracao; a
    # tela da ficha nao o edita, entao o cliente que nao o manda nao pode
    # devolver ao default um objetivo copiado do Modelo.
    "training_goal",
)


def merge_unsent_advanced_fields(
    incoming: Mapping[str, Any], existing_row: Optional[Mapping[str, Any]]
) -> dict[str, Any]:
    """Devolve uma cópia de `incoming` com os campos avançados AUSENTES
    (chave não enviada pelo cliente) preenchidos a partir de `existing_row`.

    - Chave presente em `incoming` (inclusive com valor None) prevalece:
      `null` explícito continua limpando o campo.
    - `existing_row` None (exercício novo naquela sequence): nada a preservar.
    - `incoming` deve vir de `model_dump(exclude_unset=True)` para que
      "não enviado" seja distinguível de "enviado como null".
    """
    merged = dict(incoming)
    if existing_row is None:
        return merged
    for field in ADVANCED_EXERCISE_FIELDS:
        if field not in merged and field in existing_row:
            merged[field] = existing_row[field]
    return merged
