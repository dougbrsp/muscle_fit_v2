"""Coerencia do par exercicio x equipamento (D13) na ESCRITA.

Regra unica deste modulo: um `gym_equipment` so pode ser gravado como
equipamento de um item se ele serve AQUELE `exercise_id`. Nunca "outro
equipamento do mesmo grupo muscular" - essa substituicao morreu com a D13
(PLANO_20260912.md secao 6.2 passo 6 e decisao D13) e o PR-12 a removeu do
motor do Modo B (`_recommend_for_group`).

Por que o modulo existe: a decisao da D13 estava certa no motor
(`generation/mode_b.resolve_item_equipment`), mas o par torto era montado
DEPOIS dele, na camada de gravacao - `services._replace_plan_exercises` via
`preferred_equipment_id` vazio (caminho (b): item que exige escolha do
usuario) e "ajudava", escolhendo o melhor equipamento do GRUPO primario do
exercicio. Resultado em producao (ficha 112): "Remada curvada no orbital row"
entregue com foto e etiqueta de "Estacao de polia / Pulley". Com a regra
morando em UM lugar, qualquer caminho de escrita - motor novo, edicao do
cliente, congelamento - responde a mesma pergunta da mesma forma.

Contrato:

- `is_pair_coherent` responde sim/nao (runtime: degrada para
  `requires_user_choice`);
- `assert_pair_coherent` LEVANTA `IncoherentEquipmentPairError` (testes e
  qualquer motor novo: par incoerente e ERRO DO MOTOR, nao dado do usuario).

Os ids dos dois lados passam por `exercise_legacy_id_map` antes da comparacao:
tanto o `exercise_id` recebido quanto o `gym_equipment.mapping_id` gravado no
cache podem ser ids antigos absorvidos pela consolidacao do catalogo canonico
(mesma regra de `services.is_gym_equipment_pair_active`). Sem isso um par
legitimo seria lido como incoerente.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional


class IncoherentEquipmentPairError(RuntimeError):
    """`gym_equipment` que NAO serve o `exercise_id` do item.

    Sempre um erro do motor que montou o par: o catalogo nunca produz esse
    vinculo sozinho."""

    def __init__(self, exercise_id: Any, gym_equipment_id: Any, found_exercise_id: Any = None) -> None:
        self.exercise_id = exercise_id
        self.gym_equipment_id = gym_equipment_id
        self.found_exercise_id = found_exercise_id
        super().__init__(
            f"Par incoerente (D13): gym_equipment {gym_equipment_id} serve o exercicio "
            f"{found_exercise_id!r}, nao o exercicio {exercise_id!r} do item."
        )


# ---------------------------------------------------------------------------
# Acesso a dados (somente SELECT)
# ---------------------------------------------------------------------------


def canonical_exercise_ids(cur: Any, exercise_ids: Iterable[Any]) -> dict[int, int]:
    """Ids antigos -> id ATIVO em `exercise_catalog`. Id que nunca mudou
    aparece mapeado para si mesmo."""
    wanted = sorted({int(value) for value in exercise_ids if value})
    if not wanted:
        return {}
    placeholders = ", ".join(["%s"] * len(wanted))
    cur.execute(
        f"SELECT old_exercise_id, canonical_exercise_id FROM exercise_legacy_id_map "
        f"WHERE old_exercise_id IN ({placeholders})",
        tuple(wanted),
    )
    resolved = {int(row["old_exercise_id"]): int(row["canonical_exercise_id"]) for row in cur.fetchall() or []}
    return {value: resolved.get(value, value) for value in wanted}


def exercise_ids_by_gym_equipment(cur: Any, gym_equipment_ids: Iterable[Any]) -> dict[int, Optional[int]]:
    """`gym_equipment.id` -> `exercise_id` CANONICO que ele serve (`None`
    quando a linha nao existe mais)."""
    wanted = sorted({int(value) for value in gym_equipment_ids if value})
    if not wanted:
        return {}
    placeholders = ", ".join(["%s"] * len(wanted))
    cur.execute(
        f"""
        SELECT ge.id, COALESCE(elm.canonical_exercise_id, ge.mapping_id) AS exercise_id
        FROM gym_equipment ge
        LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = ge.mapping_id
        WHERE ge.id IN ({placeholders})
        """,
        tuple(wanted),
    )
    found: dict[int, Optional[int]] = {}
    for row in cur.fetchall() or []:
        found[int(row["id"])] = int(row["exercise_id"]) if row["exercise_id"] is not None else None
    return {value: found.get(value) for value in wanted}


# ---------------------------------------------------------------------------
# A regra
# ---------------------------------------------------------------------------


def is_pair_coherent(cur: Any, exercise_id: Any, gym_equipment_id: Any) -> bool:
    """`True` quando `gym_equipment_id` serve `exercise_id`.

    Par incompleto (um dos lados ausente) NAO e incoerente: e simplesmente
    nao resolvido, e quem trata isso e a D13 caminho (b)."""
    if not exercise_id or not gym_equipment_id:
        return True
    found = exercise_ids_by_gym_equipment(cur, [gym_equipment_id]).get(int(gym_equipment_id))
    if found is None:
        return False
    canonical = canonical_exercise_ids(cur, [exercise_id, found])
    return canonical[int(exercise_id)] == canonical[int(found)]


def assert_pair_coherent(cur: Any, exercise_id: Any, gym_equipment_id: Any) -> None:
    """Igual a `is_pair_coherent`, mas LEVANTA. Use onde um par torto e bug e
    nao pode passar em silencio (testes, motores novos)."""
    if is_pair_coherent(cur, exercise_id, gym_equipment_id):
        return
    found = exercise_ids_by_gym_equipment(cur, [gym_equipment_id]).get(int(gym_equipment_id))
    raise IncoherentEquipmentPairError(exercise_id, gym_equipment_id, found)
