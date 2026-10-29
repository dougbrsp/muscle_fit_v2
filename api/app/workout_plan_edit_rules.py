"""Regras de EDICAO de uma ficha salva em `/planos-treino` (sem DB, sem FastAPI).

Pedido do usuario (2026-10-24): numa ficha NAO finalizada da para editar
equipamento, series, reps, carga, RPE, descanso, tecnica avancada e os demais
parametros de cada exercicio - so o GRUPAMENTO MUSCULAR fica travado. Ficha
finalizada continua somente leitura nos exercicios.

Este modulo e o dono dessas regras de ciclo de vida da ficha (nao e regra de
motor: nenhum calculo de carga/prescricao mora aqui). O servidor aplica as
regras em `services.update_workout_plan` / `_replace_plan_exercises` e expoe
`editable` na leitura da ficha; a tela so consome o booleano.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

# Ficha "nao finalizada" = ainda planejada. `done` (fechada pelo Modo Treino),
# `completed` (marcada a mao) e `skipped` sao estados terminais. E a mesma
# condicao de status do refresh da prescricao e da troca de equipamento
# (`services.assert_plan_prescription_refreshable`).
# Ficha gerada e ainda nao executada (tambem a recencia "planejada" do
# exercicio livre, M101b: `equipment_recommender.load_user_recency`).
PLANNED_PLAN_STATUS = "planned"
EDITABLE_PLAN_STATUSES: frozenset[str] = frozenset({PLANNED_PLAN_STATUS})

REASON_PLAN_FINALIZED = "plan_finalized"  # 409
REASON_MUSCLE_GROUP_LOCKED = "muscle_group_locked"  # 422

# Campos de uma linha JA SALVA que, quando mudam no PUT, fazem o servidor
# re-congelar a prescricao dela: o exercicio, o equipamento e a tecnica sao o
# que o motor valida contra o par (seguranca, D13a, tecnica valida). Series,
# reps, carga e RPE escritos a mao NAO re-congelam (PR-11: a prescricao
# congelada so muda por ato explicito - refresh, troca de equipamento ou troca
# do proprio exercicio).
REFREEZE_ON_CHANGE_FIELDS: tuple[str, ...] = ("mapping_id", "preferred_equipment_id", "advanced_technique")


class PlanEditError(Exception):
    """Edicao recusada. `status` e o HTTP do router: 409 quando o ESTADO da
    ficha impede; 422 quando o pedido muda o que nao pode mudar."""

    def __init__(self, status: int, reason: str, message: str):
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.message = message


def is_plan_editable(status: Optional[str]) -> bool:
    return (status or "").strip().lower() in EDITABLE_PLAN_STATUSES


def assert_plan_editable(status: Optional[str]) -> None:
    if not is_plan_editable(status):
        raise PlanEditError(
            409,
            REASON_PLAN_FINALIZED,
            "Esta ficha já foi finalizada: os exercícios ficam somente leitura. "
            f"Status atual: '{status}'.",
        )


def normalize_group_name(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def assert_muscle_group_text_unchanged(
    *, persisted_group: Any, incoming_group: Any, exercise_name: Any
) -> None:
    """O texto do grupo de uma linha salva nao muda. Linha salva sem grupo
    (ficha antiga) aceita o primeiro preenchimento."""
    persisted = normalize_group_name(persisted_group)
    if persisted and normalize_group_name(incoming_group) != persisted:
        raise PlanEditError(
            422,
            REASON_MUSCLE_GROUP_LOCKED,
            f"O grupamento muscular de '{exercise_name}' não pode ser alterado "
            f"(atual: {persisted_group}).",
        )


def is_same_primary_group_swap(persisted_group_id: Optional[int], new_group_id: Optional[int]) -> bool:
    """Predicado UNICO da troca de exercicio numa linha salva: vale quando os
    dois grupos PRIMARY sao iguais ou quando um dos lados nao tem grupo
    conhecido. Usado pela validacao do save (`assert_same_primary_group`, 422)
    e pela lista de exercicios elegiveis da tela (`eligible_swap_exercise_ids`):
    a tela nunca oferece o que o servidor recusa, nem esconde o que ele aceita."""
    if persisted_group_id is None or new_group_id is None:
        return True
    return int(persisted_group_id) == int(new_group_id)


def eligible_swap_exercise_ids(
    *, persisted_group_id: Optional[int], candidate_groups: Mapping[int, Optional[int]]
) -> Optional[list[int]]:
    """Exercicios (ids canonicos) pelos quais a linha salva pode ser trocada.

    `persisted_group_id`: grupo PRIMARY do exercicio salvo na linha (dono:
    `progression_state.primary_groups_from_cursor`); `candidate_groups`: grupo
    PRIMARY de cada candidato pelo mesmo dono (None = sem grupo PRIMARY).
    Devolve None quando a linha nao tem grupo conhecido - o servidor nao
    restringe a troca, entao a tela tambem nao."""
    if persisted_group_id is None:
        return None
    return sorted(
        int(exercise_id)
        for exercise_id, group_id in candidate_groups.items()
        if is_same_primary_group_swap(persisted_group_id, group_id)
    )


def assert_same_primary_group(
    *, persisted_group_id: Optional[int], new_group_id: Optional[int], exercise_name: Any
) -> None:
    """Trocar o exercicio de uma linha salva so vale para outro do MESMO grupo
    PRIMARY (grupo do dono `progression_state.primary_groups_from_cursor`).
    Sem grupo conhecido de um dos lados nao ha o que comparar."""
    if not is_same_primary_group_swap(persisted_group_id, new_group_id):
        raise PlanEditError(
            422,
            REASON_MUSCLE_GROUP_LOCKED,
            f"'{exercise_name}' é de outro grupamento muscular. Na edição da ficha só é possível "
            "trocar por um exercício do mesmo grupamento.",
        )


def _comparable(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value.strip() or None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def refreeze_needed(existing_row: Optional[Mapping[str, Any]], new_values: Mapping[str, Any]) -> bool:
    """A linha existente mudou de exercicio, de equipamento ou de tecnica?"""
    if existing_row is None:
        return False
    return any(
        _comparable(existing_row.get(field)) != _comparable(new_values.get(field))
        for field in REFREEZE_ON_CHANGE_FIELDS
    )
