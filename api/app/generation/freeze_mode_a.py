"""Congelamento DETERMINISTICO da prescricao de uma ficha do Modo A (PR-13).

Mesmo motivo do `freeze_mode_b`: a prescricao e do SERVIDOR. Entre gerar o
rascunho e salvar a ficha passa o cliente, e o PR-11 tornou
`prescription_json` read-only para ele. Aqui a prescricao e re-derivada do que
e confiavel - a LINHA SALVA (exercicio, equipamento e series que o usuario
aceitou) e o estado da progressao v2 -, pelas mesmas funcoes puras de
`generation/deterministic_prescription.py` que o rascunho usou.

Duas diferencas em relacao ao Modo B, as duas vindas da secao 6.1:

1. **Nao existe item de modelo a casar.** O modelo do Modo A declara grupos e
   percentuais, nao exercicios: quem escolheu o par foi o motor, e ele esta na
   propria linha da ficha. Qualquer linha com `mapping_id` +
   `preferred_equipment_id` e um item do Modo A.
2. **As series vem da progressao** (`model_fixed_sets=False`), mas o snapshot
   grava as series da LINHA. Motivo: a alocacao do passo 6 pode ter reduzido as
   series para nao estourar o teto de um grupo secundario, e o usuario pode ter
   editado o rascunho antes de salvar - em ambos os casos o que vale e o que
   esta na ficha. A decisao da progressao vai inteira no snapshot
   (`decision`), entao o motivo continua auditavel.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from ..domain.prescription import WARNING_EQUIPMENT_UNRESOLVED, build_prescription
from ..domain.training_goal import goal_value
from ..domain.training_safety import TrainingSafety
from .deterministic_prescription import MODE_A_ENGINE_VERSION, PairRequest, prescribe_pairs
from .freeze_mode_b import coherent_equipment_id, equipment_by_gym_equipment, projected_readiness

logger = logging.getLogger("fitness.generation")


def _plan_context(conn: Any, plan_id: int) -> Optional[dict[str, Any]]:
    """Ficha + modelo. `None` = o caminho deterministico do Modo A nao se
    aplica a esta ficha (sem modelo, modelo de outro `volume_mode`, ou modelo
    sem Tipo de Treinamento - que e o mesmo gate do motor)."""
    with conn.cursor() as cur:
        # 066: `volume_mode`/`training_type_id` da versao da FICHA (ou da
        # vigente, em ficha antiga) - o mesmo statement de antes, com o JOIN.
        cur.execute(
            "SELECT p.id, p.user_id, p.plan_date, p.plan_time, p.template_id, p.template_slot_sequence, "
            "       p.template_version_id, v.id AS version_id, "
            "       CASE WHEN v.id IS NULL THEN t.volume_mode ELSE v.volume_mode END AS volume_mode, "
            "       CASE WHEN v.id IS NULL THEN t.training_type_id ELSE v.training_type_id END AS training_type_id "
            "FROM workout_plans p LEFT JOIN workout_plan_templates t ON t.id = p.template_id "
            "LEFT JOIN workout_plan_template_versions v ON v.template_id = p.template_id "
            "  AND (v.id = p.template_version_id "
            "       OR (p.template_version_id IS NULL AND v.current_template_id = p.template_id)) "
            "WHERE p.id = %s",
            (int(plan_id),),
        )
        plan = cur.fetchone()
    if not plan or not plan.get("template_id"):
        return None
    if plan.get("volume_mode") != "percent_budget" or not plan.get("training_type_id"):
        return None
    return plan


def freeze_mode_a(
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

    equipment_by_gym = equipment_by_gym_equipment(conn, [row.get("preferred_equipment_id") for row in rows])
    readiness = projected_readiness(owner_id, plan.get("plan_date"), plan.get("plan_time"))

    requests: list[PairRequest] = []
    per_row: dict[int, dict[str, Any]] = {}
    for sequence, row in enumerate(rows, start=1):
        mapping_id = row.get("mapping_id")
        if not mapping_id:
            # Linha sem exercicio resolvido (edicao manual do rascunho): fica
            # com o caminho legado, como qualquer linha fora do motor.
            continue
        # D13: vinculo que nao serve o exercicio da linha nao vira par (o
        # item cai para `requires_user_choice`, com auditoria).
        equipment_id = coherent_equipment_id(conn, equipment_by_gym, row, int(plan_id))
        request = PairRequest(
            exercise_id=int(mapping_id),
            equipment_id=equipment_id,
            muscle_group_id=row.get("muscle_group_id"),
            model_sets_count=row.get("sets_count"),
            model_technique=None,  # o modelo do Modo A nao fixa tecnica
            model_rpe_target=row.get("rpe_target"),
            sequence=sequence,
            # M97 F1: o objetivo carimbado na linha (`plan_goal_stamp`)
            training_goal=goal_value(row.get("training_goal")),
        )
        requests.append(request)
        per_row[int(row["workout_plan_exercise_id"])] = {"request": request, "row": row}

    if not requests:
        return None

    prescriptions = prescribe_pairs(
        conn, int(owner_id), requests, readiness=readiness, model_fixed_sets=False, session_order=True
    )

    snapshots: dict[int, dict[str, Any]] = {}
    for row_id, ctx in per_row.items():
        request: PairRequest = ctx["request"]
        row = ctx["row"]
        prescription = prescriptions.get(request.key)
        if prescription is None:
            continue

        warnings = list(prescription.warnings)
        requires_user_choice = False
        if request.equipment_id is None:
            # Sem par resolvido nao ha seguranca do par: o usuario escolhe o
            # equipamento no modo treino antes de iniciar.
            warnings.append(WARNING_EQUIPMENT_UNRESOLVED)
            requires_user_choice = True

        decision = prescription.decision.to_dict()
        exercise = {
            **dict(row),
            "reps_target": prescription.reps_target or row.get("reps_target"),
            "load_target": row.get("load_target"),
            "rpe_target": row.get("rpe_target"),
            "advanced_technique": prescription.technique,
            # Series da LINHA (ver docstring): a alocacao do passo 6 e a edicao
            # do usuario mandam nelas, nao a trilha.
            "sets_count": row.get("sets_count") or decision.get("suggested_sets"),
            "suggested_load_kg": prescription.suggested_load_kg,
            "suggested_reps": decision.get("suggested_reps_max"),
            "progression": None,
            "progression_note": decision.get("reason"),
        }
        snapshots[row_id] = build_prescription(
            exercise,
            safety=safety_by_row.get(row_id) or prescription.safety,
            source=source,
            engine_version=MODE_A_ENGINE_VERSION,
            frozen_at=frozen_at,
            decision=decision,
            warnings=warnings,
            requires_user_choice=requires_user_choice,
        )

    logger.info(
        json.dumps(
            {
                "event": "generation.completed",
                "user_id": int(owner_id),
                "plan_id": int(plan_id),
                "template_id": plan.get("template_id"),
                "engine_version": MODE_A_ENGINE_VERSION,
                "frozen": len(snapshots),
                "llm_used": False,
            },
            ensure_ascii=False,
        )
    )
    return snapshots
