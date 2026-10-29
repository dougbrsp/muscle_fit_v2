"""Congelamento DETERMINISTICO da prescricao de uma ficha do Modo B (PR-12).

Por que isto existe em vez de a geracao mandar o snapshot pronto: a
prescricao e do SERVIDOR. Entre gerar o rascunho e salvar a ficha passa o
cliente, que edita o que quiser - o PR-11 fez de `prescription_json` uma
coluna read-only para ele justamente por isso. Entao o congelamento re-deriva
a prescricao a partir do que e confiavel: a linha salva, o MODELO (que esta
no banco) e o estado da progressao v2.

O resultado tem de bater com o rascunho, e bate: os dois passam pelas mesmas
funcoes puras de `generation/deterministic_prescription.py`, com o mesmo
estado persistido.

Contrato: `freeze_mode_b()` devolve `None` quando o caminho deterministico
nao se aplica (flag desligada, ficha sem modelo, modelo que nao e
`equipment_series`). O chamador (`services._freeze_plan_prescriptions`) cai
no caminho legado - que continua sendo o default de producao.

M6: uma linha cujo exercicio foi SUBSTITUIDO pela geracao (outro exercicio do
mesmo grupo, `generation/exercise_substitution.py`) nao tem o `mapping_id`
de nenhum item do modelo. `match_rows_to_model_items` a casa pela POSICAO
(mesma condicao de grupo com que o M6 a escolheu), para que ela continue
sendo um item do Modo B - com as series/tecnica/RPE do modelo e com a troca
auditada em `prescription_json.substitution`.

M101b: o item de EXERCICIO LIVRE (so o grupo, sem par) tambem nao tem
`mapping_id`; a linha que a geracao escolheu para ele casa pela posicao e,
se o rascunho foi reordenado, pelo grupo PRIMARY na ordem. Sem auditoria de
substituicao (nada foi trocado): so o aviso `EXERCISE_AUTO_CHOSEN`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

# M101c: itens aqui ja sao `equipment` do Modo B (sem `item_kind`): `require_kind=False`.
from ..domain.free_exercise_item import is_free_exercise_item
from ..domain.prescription import (
    WARNING_EQUIPMENT_SUBSTITUTED,
    WARNING_EQUIPMENT_UNRESOLVED,
    WARNING_EXERCISE_AUTO_CHOSEN,
    build_prescription,
)
from ..domain.prescription_defaults import normalize_item_for_goal
from ..domain.set_measures import is_rep_based
from ..domain.training_goal import goal_value
from ..domain.training_safety import TrainingSafety
from .deterministic_prescription import (
    MODE_B_ENGINE_VERSION,
    PairRequest,
    prescribe_pairs,
    readiness_by_group,
)

logger = logging.getLogger("fitness.generation")


def _plan_context(conn: Any, plan_id: int) -> Optional[dict[str, Any]]:
    """Ficha + modelo + itens do slot. `None` = caminho deterministico nao se
    aplica a esta ficha."""
    with conn.cursor() as cur:
        # 066: a versao da FICHA (a que o usuario viu no rascunho) governa o
        # congelamento; ficha antiga (`template_version_id` NULL) usa a vigente.
        # O `volume_mode` vem dessa versao - um Modelo que mudou de modo depois
        # do rascunho nao muda o motor desta ficha.
        cur.execute(
            "SELECT p.id, p.user_id, p.plan_date, p.plan_time, p.template_id, p.template_slot_sequence, "
            "       p.template_version_id, v.id AS version_id, "
            "       CASE WHEN v.id IS NULL THEN t.volume_mode ELSE v.volume_mode END AS volume_mode "
            "FROM workout_plans p LEFT JOIN workout_plan_templates t ON t.id = p.template_id "
            "LEFT JOIN workout_plan_template_versions v ON v.template_id = p.template_id "
            "  AND (v.id = p.template_version_id "
            "       OR (p.template_version_id IS NULL AND v.current_template_id = p.template_id)) "
            "WHERE p.id = %s",
            (int(plan_id),),
        )
        plan = cur.fetchone()
        if not plan or not plan.get("template_id") or plan.get("volume_mode") != "equipment_series":
            return None
        cur.execute(
            "SELECT i.item_order, i.muscle_group_id, i.mapping_id, i.equipment_id, i.sets_count, "
            "       i.reps_target, i.rpe_target, i.advanced_technique, "
            "       i.advanced_technique_applications, i.advanced_technique_duration_seconds, "
            # Fix M97: o descanso do Modelo entra na auditoria dos ajustes ao
            # objetivo (`normalize_item_for_goal`); coluna da 058.
            "       i.rest_seconds "
            "FROM workout_plan_template_slot_items i "
            "JOIN workout_plan_template_slots s ON s.id = i.slot_id "
            # Slots da versao resolvida no SELECT acima (a da ficha, ou a
            # vigente) - fecha a divergencia rascunho x save (066).
            "WHERE s.template_id = %s AND s.version_id = %s AND s.sequence = %s "
            "AND i.item_kind = 'equipment' "
            "ORDER BY i.item_order ASC",
            (int(plan["template_id"]), plan.get("version_id"), plan.get("template_slot_sequence")),
        )
        plan["items"] = list(cur.fetchall() or [])
    return plan


def equipment_by_gym_equipment(conn: Any, ids: Sequence[int]) -> dict[int, int]:
    """`gym_equipment.id` -> `equipment.id` (a ficha guarda o primeiro; a
    seguranca e a progressao falam o segundo)."""
    wanted = sorted({int(value) for value in ids if value})
    if not wanted:
        return {}
    placeholders = ", ".join(["%s"] * len(wanted))
    with conn.cursor() as cur:
        cur.execute(f"SELECT id, equipment_id FROM gym_equipment WHERE id IN ({placeholders})", tuple(wanted))
        return {int(row["id"]): int(row["equipment_id"]) for row in cur.fetchall() or []}


def coherent_equipment_id(
    conn: Any, equipment_by_gym: Mapping[int, int], row: Mapping[str, Any], plan_id: int
) -> Optional[int]:
    """`equipment.id` do item, ou `None` quando o vinculo gravado nao serve o
    exercicio da linha.

    O congelamento NAO confia no `preferred_equipment_id` persistido: entre a
    geracao e a ficha salva passa a camada de escrita (e o cliente). Foi assim
    que a ficha 112 congelou uma prescricao para o par (Remada no orbital row
    x Estacao de polia), que nunca existiu - com `EQUIPMENT_SUBSTITUTED` no
    lugar de `EQUIPMENT_UNRESOLVED`. Par incoerente aqui vira D13 caminho (b),
    auditado: `requires_user_choice`, que e a verdade."""
    from ..domain.equipment_pair import is_pair_coherent

    gym_equipment_id = row.get("preferred_equipment_id")
    if not gym_equipment_id:
        return None
    with conn.cursor() as cur:
        if not is_pair_coherent(cur, row.get("mapping_id"), gym_equipment_id):
            logger.error(
                "D13: par incoerente ignorado no congelamento plan_id=%s sequence=%s "
                "mapping_id=%s gym_equipment_id=%s (item passa a exigir escolha do usuario)",
                plan_id,
                row.get("sequence"),
                row.get("mapping_id"),
                gym_equipment_id,
            )
            return None
    return equipment_by_gym.get(int(gym_equipment_id))


def projected_readiness(user_id: Optional[int], plan_date: Any, plan_time: Any) -> dict[int, Any]:
    """Contrato de recuperacao projetado para o instante do treino (secao
    5.8). Usa o servico oficial direto, sem passar pela rota interna."""
    if user_id is None:
        return {}
    from ..intelligence.muscle_recovery_service import MuscleRecoveryService
    from ..intelligence.recovery_projection import project_recovery_items

    service = MuscleRecoveryService()
    # Um unico "agora", na hora LOCAL do usuario, para o retrato
    # (`hours_since_trained`) e para a projecao: `plan_date`/`plan_time` sao
    # hora local, e `datetime.now()` e o relogio UTC da Lambda - misturar os
    # dois encolhia a antecedencia em 3h (ou a zerava, via `max`, com treino
    # proximo da criacao da ficha).
    now = service.user_now(int(user_id))
    # Janela e limite: os do modelo (M76 B3), os mesmos do rascunho e da tela.
    items = service.get_recovery_overview(user_id=int(user_id), now_local=now)
    target = _target_datetime(plan_date, plan_time) or now
    # O alvo nunca anda para tras: recuperacao projetada so olha para a
    # frente (ficha de hoje ja comecada, ou de uma data passada num refresh
    # que ja foi barrado antes de chegar aqui).
    return readiness_by_group(project_recovery_items(items, now, max(now, target)))


def base_sets_for_freeze(row_sets: Any, model_sets: Any) -> Any:
    """Séries de PARTIDA do congelamento de uma linha do Modo B.

    Desde o M76 B2 o rascunho já grava a dose reduzida (`mode_b.
    sets_for_recovery`): a linha de um grupo REDUCED_DOSE chega com
    `ceil(S × 0,75)` e não com as séries do Modelo. Partir dela reduziria DUAS
    vezes (5 -> 4 -> 3). Então, quando a linha tem as séries do Modelo ou a
    redução delas, a partida é o Modelo e o motor reaplica a regra única com a
    recuperação do instante do congelamento; se o usuário editou para outro
    número, vale a edição dele (como antes)."""
    if not model_sets:
        return row_sets
    if not row_sets:
        return model_sets
    from ..intelligence.muscle_recovery_service import MuscleRecoveryService

    if int(row_sets) in (int(model_sets), MuscleRecoveryService.reduced_sets(model_sets)):
        return model_sets
    return row_sets


def _target_datetime(plan_date: Any, plan_time: Any) -> Optional[datetime]:
    """`plan_date` + `plan_time` -> instante do treino. `plan_time` chega do
    MySQL como `timedelta` (coluna TIME) ou `time`; ausente = meia-noite."""
    from datetime import date as date_cls
    from datetime import time as time_cls
    from datetime import timedelta

    if isinstance(plan_date, datetime):
        return plan_date
    if not isinstance(plan_date, date_cls):
        return None
    if isinstance(plan_time, time_cls):
        return datetime.combine(plan_date, plan_time)
    if isinstance(plan_time, timedelta):
        return datetime.combine(plan_date, datetime.min.time()) + plan_time
    return datetime.combine(plan_date, datetime.min.time())


def match_rows_to_model_items(
    conn: Any, rows: Sequence[Mapping[str, Any]], items: Sequence[Mapping[str, Any]]
) -> dict[int, dict[str, Any]]:
    """`workout_plan_exercise_id` -> item do modelo que originou a linha.

    Duas passagens, nesta ordem:

    1. **por exercicio** - a linha tem o `mapping_id` de um item do modelo.
       Duplicatas do mesmo exercicio no slot sao consumidas na ordem
       (`item_order`), que e a ordem em que a geracao as emitiu e a ficha as
       guarda (`sequence`);
    2. **por posicao** (M6) - a linha tem um exercicio que NAO esta no modelo
       porque a geracao o substituiu por outro do mesmo grupo. O item e o da
       mesma posicao (`sequence` <-> `item_order`), desde que ainda nao tenha
       sido consumido e que o grupo PRIMARY do exercicio da linha seja o
       grupo do item - a mesma condicao com que o M6 o escolheu. Sem isso, a
       linha substituida cairia no caminho legado e perderia a prescricao
       deterministica e a auditoria da troca. Vale tambem para o item de
       exercicio livre (M101b, sem `mapping_id`);
    3. **por grupo** (M101b) - linha que sobrou casa com o 1o item de
       exercicio livre nao consumido cujo grupo e o PRIMARY do exercicio da
       linha (rascunho reordenado pelo usuario).

    Linha sem item (exercicio acrescentado a mao no rascunho) fica de fora."""
    from .. import progression_state as ps

    items = list(items)
    by_exercise: dict[int, list[int]] = {}
    for index, item in enumerate(items):
        if item.get("mapping_id"):
            by_exercise.setdefault(int(item["mapping_id"]), []).append(index)
    consumed: set[int] = set()
    used: dict[int, int] = {}
    matched: dict[int, dict[str, Any]] = {}
    unmatched: list[tuple[int, Mapping[str, Any]]] = []

    for sequence, row in enumerate(rows, start=1):
        mapping_id = row.get("mapping_id")
        if not mapping_id:
            continue
        candidates = by_exercise.get(int(mapping_id)) or []
        position = used.get(int(mapping_id), 0)
        used[int(mapping_id)] = position + 1
        if not candidates:
            unmatched.append((sequence, row))
            continue
        index = candidates[position] if position < len(candidates) else candidates[-1]
        consumed.add(index)
        matched[int(row["workout_plan_exercise_id"])] = dict(items[index])

    if unmatched:
        primary, _weights = ps.load_primary_groups(conn, [int(row["mapping_id"]) for _, row in unmatched])
        for sequence, row in unmatched:
            index = int(row.get("sequence") or sequence) - 1
            if not 0 <= index < len(items) or index in consumed:
                continue
            item = items[index]
            group_id = item.get("muscle_group_id")
            if group_id is None or (not item.get("mapping_id") and not is_free_exercise_item(item, require_kind=False)):
                continue
            if primary.get(int(row["mapping_id"])) != int(group_id):
                continue
            consumed.add(index)
            matched[int(row["workout_plan_exercise_id"])] = dict(item)
        free_indexes = [index for index, item in enumerate(items) if is_free_exercise_item(item, require_kind=False)]
        for _sequence, row in unmatched:
            if int(row["workout_plan_exercise_id"]) in matched:
                continue
            row_group = primary.get(int(row["mapping_id"]))
            index = next(
                (
                    index
                    for index in free_indexes
                    if index not in consumed and row_group == int(items[index]["muscle_group_id"])
                ),
                None,
            )
            if index is None:
                continue
            consumed.add(index)
            matched[int(row["workout_plan_exercise_id"])] = dict(items[index])
    return matched


def substitution_audit_for_row(
    model_item: Mapping[str, Any], request: PairRequest, row: Mapping[str, Any]
) -> Optional[dict[str, Any]]:
    """Bloco `substitution` do snapshot quando a linha difere do item do
    modelo: exercicio trocado (M6, `kind=EXERCISE`) ou so o equipamento
    (D13 (a), `kind=EQUIPMENT`). `None` = a linha e o que o modelo pediu.

    O congelamento re-deriva do que esta gravado, entao o `rejected[]` da
    geracao nao esta mais disponivel aqui; o rascunho o guarda em
    `raw_payload.generation_audit.substitutions[]`."""
    from . import exercise_substitution as subst

    model_exercise_id = int(model_item["mapping_id"]) if model_item.get("mapping_id") else None
    model_equipment_id = int(model_item["equipment_id"]) if model_item.get("equipment_id") else None
    if model_exercise_id is not None and model_exercise_id != int(request.exercise_id):
        return {
            "kind": subst.KIND_EXERCISE,
            "reason": subst.REASON_EXERCISE_UNAVAILABLE_IN_GYM,
            "from_exercise_id": model_exercise_id,
            "from_equipment_id": model_equipment_id,
            "to_exercise_id": int(request.exercise_id),
            "to_equipment_id": int(request.equipment_id) if request.equipment_id is not None else None,
            "to_gym_equipment_id": int(row["preferred_equipment_id"]) if row.get("preferred_equipment_id") else None,
        }
    if model_equipment_id is not None and request.equipment_id is not None and model_equipment_id != int(request.equipment_id):
        return subst.equipment_substitution_audit(
            reason=subst.REASON_EQUIPMENT_UNAVAILABLE_IN_GYM,
            exercise_id=int(request.exercise_id),
            from_equipment_id=model_equipment_id,
            to_equipment_id=int(request.equipment_id),
            to_gym_equipment_id=int(row["preferred_equipment_id"]) if row.get("preferred_equipment_id") else None,
        )
    return None


def reps_fields_for_measure(row: Mapping[str, Any]) -> dict[str, Any]:
    """Medida da serie (Lote T2): a da LINHA da ficha (copiada do Modelo na
    geracao). Fora de `reps` (`set_measures.is_rep_based`) o snapshot nao leva
    alvo nem sugestao de repeticoes; em `reps`, nada muda (dict vazio)."""
    if is_rep_based(row.get("measure")):
        return {}
    return {"reps_target": None, "suggested_reps": None}


def freeze_mode_b(
    conn: Any,
    plan_id: int,
    rows: Sequence[Mapping[str, Any]],
    *,
    user_id: Optional[int],
    safety_by_row: Mapping[int, TrainingSafety],
    source: str,
    frozen_at: datetime,
) -> Optional[dict[int, dict[str, Any]]]:
    """`workout_plan_exercise_id` -> snapshot, ou `None` (nao aplicavel)."""
    from . import deterministic_enabled

    if not deterministic_enabled():
        return None
    plan = _plan_context(conn, plan_id)
    if plan is None:
        return None
    owner_id = user_id if user_id is not None else plan.get("user_id")
    if owner_id is None:
        return None

    model_item_by_row = match_rows_to_model_items(conn, rows, plan["items"])

    equipment_by_gym = equipment_by_gym_equipment(
        conn, [row.get("preferred_equipment_id") for row in rows]
    )
    readiness = projected_readiness(owner_id, plan.get("plan_date"), plan.get("plan_time"))

    requests: list[PairRequest] = []
    per_row: dict[int, dict[str, Any]] = {}
    for sequence, row in enumerate(rows, start=1):
        mapping_id = row.get("mapping_id")
        if not mapping_id:
            continue
        model_item = model_item_by_row.get(int(row["workout_plan_exercise_id"]))
        if model_item is None:
            # Exercicio na ficha que nao esta no modelo (o usuario acrescentou
            # ao editar o rascunho): nao e item do Modo B, fica com o legado.
            continue
        equipment_id = coherent_equipment_id(conn, equipment_by_gym, row, int(plan_id))
        # Fix M97 (2026-10-26): o item do Modelo ajustado ao objetivo da linha,
        # a MESMA normalizacao do rascunho (`mode_b`); os ajustes vao para o
        # snapshot. Hipertrofia: o mesmo item, nenhum ajuste.
        normalized_item, goal_adjustments = normalize_item_for_goal(
            {**model_item, "measure": row.get("measure")}, row.get("training_goal")
        )
        if goal_adjustments:
            model_item = normalized_item
        request = PairRequest(
            exercise_id=int(mapping_id),
            equipment_id=equipment_id,
            muscle_group_id=model_item.get("muscle_group_id"),
            model_sets_count=base_sets_for_freeze(row.get("sets_count"), model_item.get("sets_count")),
            model_technique=model_item.get("advanced_technique"),
            model_rpe_target=model_item.get("rpe_target") or row.get("rpe_target"),
            model_technique_applications=model_item.get("advanced_technique_applications"),
            model_technique_duration_seconds=model_item.get("advanced_technique_duration_seconds"),
            sequence=sequence,
            # M97 F1: o objetivo carimbado na linha (`plan_goal_stamp`)
            training_goal=goal_value(row.get("training_goal")),
        )
        requests.append(request)
        per_row[int(row["workout_plan_exercise_id"])] = {
            "request": request,
            "model_item": model_item,
            "row": row,
            "goal_adjustments": goal_adjustments,
        }

    if not requests:
        return None

    # session_order: a ficha e UMA sessao; o contexto de fadiga de cada item
    # vem da posicao planejada (PR 2 da fadiga previa).
    prescriptions = prescribe_pairs(conn, int(owner_id), requests, readiness=readiness, session_order=True)

    snapshots: dict[int, dict[str, Any]] = {}
    for row_id, context in per_row.items():
        request: PairRequest = context["request"]
        model_item = context["model_item"]
        row = context["row"]
        prescription = prescriptions.get(request.key)
        if prescription is None:
            continue

        warnings = list(prescription.warnings)
        requires_user_choice = False
        substitution: Optional[dict[str, Any]] = None
        if request.equipment_id is None:
            # D13 caminho (b): nenhum equipamento seguro na academia.
            warnings.append(WARNING_EQUIPMENT_UNRESOLVED)
            requires_user_choice = True
        else:
            substitution = substitution_audit_for_row(model_item, request, row)
            if substitution:
                warnings.append(WARNING_EQUIPMENT_SUBSTITUTED)
            elif is_free_exercise_item(model_item, require_kind=False) and WARNING_EXERCISE_AUTO_CHOSEN not in warnings:
                # M101b: exercicio livre no Modelo, escolhido pela geracao
                # (`mode_b._resolve_free_exercise`). Aviso informativo, sem
                # auditoria de substituicao.
                warnings.append(WARNING_EXERCISE_AUTO_CHOSEN)

        decision = prescription.decision.to_dict()
        exercise = {
            **dict(row),
            # A progressao manda em reps e carga; o modelo, no resto.
            "reps_target": prescription.reps_target or row.get("reps_target"),
            "load_target": row.get("load_target"),
            "rpe_target": model_item.get("rpe_target") or row.get("rpe_target"),
            "advanced_technique": prescription.technique,
            "sets_count": decision.get("suggested_sets") or row.get("sets_count"),
            "suggested_load_kg": prescription.suggested_load_kg,
            "suggested_reps": decision.get("suggested_reps_max"),
            "progression": None,
            "progression_note": decision.get("reason"),
        }
        exercise.update(reps_fields_for_measure(row))
        snapshots[row_id] = build_prescription(
            exercise,
            safety=safety_by_row.get(row_id) or prescription.safety,
            source=source,
            engine_version=MODE_B_ENGINE_VERSION,
            frozen_at=frozen_at,
            decision=decision,
            warnings=warnings,
            requires_user_choice=requires_user_choice,
            substitution=substitution,
            goal_adjustments=[adjustment.to_dict() for adjustment in context["goal_adjustments"]],
        )

    logger.info(
        json.dumps(
            {
                "event": "generation.completed",
                "user_id": int(owner_id),
                "plan_id": int(plan_id),
                "template_id": plan.get("template_id"),
                "engine_version": MODE_B_ENGINE_VERSION,
                "frozen": len(snapshots),
                "llm_used": False,
            },
            ensure_ascii=False,
        )
    )
    return snapshots
