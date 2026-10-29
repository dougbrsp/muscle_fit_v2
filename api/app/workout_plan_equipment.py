"""Troca de EQUIPAMENTO de um exercicio numa ficha ja salva (M8).

Rotas em `workout_plans.py`:

- `GET  /workout-plans/{plan_id}/exercises/{exercise_id}/equipment-options`
- `POST /workout-plans/{plan_id}/exercises/{exercise_id}/substitute-equipment`

Vale para QUALQUER ficha - Modo A, Modo B, legada ou com `prescription_json`
NULL - porque o servidor e o dono da prescricao (PR-11): a troca grava
`preferred_equipment_id`/`equipment_name` na linha e RE-DERIVA a prescricao
dela pelo mesmo caminho do `refresh-prescription`
(`services._freeze_plan_prescriptions`, que despacha para `freeze_mode_b`,
`freeze_mode_a` ou o legado conforme a origem da ficha). Nenhum segundo
caminho de prescricao existe aqui.

Decisao do usuario (2026-09-21): a troca e SO de equipamento - o exercicio
(`mapping_id`) e o grupo muscular nao mudam. Por isso os candidatos sao
exatamente as associacoes ATIVAS da academia da ficha para o MESMO exercicio
(D13: `equipment_pair.assert_pair_coherent`), ranqueadas pelo ranking unico
do usuario (PR-08).

D13a vale aqui como na geracao: `max_safe_rpe` do par novo >= o do par atual
(se houver) e tecnica prescrita valida no par novo; o que nao passa e listado
com o motivo em `GET .../equipment-options` (`eligible=false`) e recusado com
422 no POST. A troca fica auditada em `prescription_json.substitution`
(`kind=EQUIPMENT`, `reason=USER_CHOICE`) + `EQUIPMENT_SUBSTITUTED`.

Carga e evolucao seguem o equipamento NOVO (pedido de 2026-10-24): a carga
escrita na linha e descartada antes do recongelamento e regravada com a
sugestao do motor v2 para o par novo (`suggested_load_kg` do snapshot, no
mesmo formato da geracao); sem historico do par, fica vazia. Progressao, nota,
decisao e teto ja vem do snapshot recongelado.

Estado: ficha nao finalizada (`status='planned'`, 409 fora disso) - a mesma
condicao de status do refresh; a data passada nao impede a troca (2026-10-24),
porque ficha `planned` nunca foi executada (`assert_plan_prescription_refreshable(
require_future_date=False)`).
"""

from __future__ import annotations

from typing import Any, Optional

from . import services
from .db import transaction
from .domain import equipment_pair
from .domain import prescription as prescription_domain
from .domain import training_safety as ts
from .equipment_recommender import PUBLIC_ASSETS_DOMAIN, rank_gym_equipment_rows
from .generation import exercise_substitution as subst

# `reason` de `PlanExerciseSubstitutionError` (status HTTP entre parenteses)
REASON_PLAN_WITHOUT_GYM = "plan_without_gym"  # 409
REASON_EXERCISE_WITHOUT_MAPPING = "exercise_without_mapping"  # 409
REASON_SAME_EQUIPMENT = "same_equipment"  # 409
REASON_EQUIPMENT_NOT_IN_GYM = "equipment_not_in_gym"  # 422
REASON_INCOHERENT_PAIR = "incoherent_pair"  # 422
REASON_MAX_SAFE_RPE_LOWER = "max_safe_rpe_lower"  # 422
REASON_TECHNIQUE_INCOMPATIBLE = "technique_incompatible"  # 422


class PlanExerciseSubstitutionError(Exception):
    """Troca recusada. `status` e o HTTP que o router devolve: 409 quando o
    ESTADO da ficha/linha impede; 422 quando o equipamento pedido nao serve."""

    def __init__(self, status: int, reason: str, message: str):
        super().__init__(message)
        self.status = status
        self.reason = reason
        self.message = message


# ---------------------------------------------------------------------------
# Leituras
# ---------------------------------------------------------------------------


def _load_plan_and_row(conn: Any, plan_id: int, exercise_id: int, user_id: Optional[int]) -> tuple[dict, dict]:
    with conn.cursor() as cur:
        if user_id is not None:
            cur.execute(
                "SELECT id, user_id, gym_id, status, plan_date, plan_type FROM workout_plans WHERE id = %s AND user_id = %s",
                (int(plan_id), int(user_id)),
            )
        else:
            cur.execute(
                "SELECT id, user_id, gym_id, status, plan_date, plan_type FROM workout_plans WHERE id = %s",
                (int(plan_id),),
            )
        plan = cur.fetchone()
        if not plan:
            raise services.PlanNotFoundError(plan_id)
        cur.execute(
            "SELECT id, sequence, exercise_name, mapping_id, equipment_name, preferred_equipment_id, "
            "advanced_technique, requires_user_choice "
            "FROM workout_plan_exercises WHERE id = %s AND plan_id = %s",
            (int(exercise_id), int(plan_id)),
        )
        row = cur.fetchone()
    if not row:
        raise services.PlanNotFoundError(plan_id)
    return plan, row


def _same_exercise_links(conn: Any, gym_id: int, mapping_id: int) -> list[dict[str, Any]]:
    """Associacoes ATIVAS da academia para o MESMO exercicio (canonico dos
    dois lados), com foto e `equipment.id` - mesma regra de
    `generation/mode_b._gym_equipment_for_exercise`."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT ge.id, ge.mapping_id, ge.equipment_id, eq.name AS equipment_name,
                   (CASE WHEN eq.photo_path IS NOT NULL THEN CONCAT('https://{PUBLIC_ASSETS_DOMAIN}/', eq.photo_path) ELSE NULL END) AS photo_url,
                   COALESCE(in_elm.canonical_exercise_id, %s) AS canonical_exercise_id
            FROM gym_equipment ge
            JOIN equipment eq ON eq.id = ge.equipment_id
            LEFT JOIN exercise_legacy_id_map ge_elm ON ge_elm.old_exercise_id = ge.mapping_id
            LEFT JOIN exercise_legacy_id_map in_elm ON in_elm.old_exercise_id = %s
            WHERE ge.gym_id = %s
              AND COALESCE(ge_elm.canonical_exercise_id, ge.mapping_id)
                  = COALESCE(in_elm.canonical_exercise_id, %s)
              AND ge.is_active = 1
            ORDER BY ge.id ASC
            """,
            (int(mapping_id), int(mapping_id), int(gym_id), int(mapping_id)),
        )
        return list(cur.fetchall() or [])


def _current_link(conn: Any, row: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Associacao atual da linha, ou `None` quando nao ha (item
    `requires_user_choice`) ou quando o vinculo gravado nao serve o exercicio
    (par torto de ficha antiga: tratado como "sem equipamento", igual a
    leitura da ficha)."""
    link_id = row.get("preferred_equipment_id")
    if not link_id:
        return None
    with conn.cursor() as cur:
        if not equipment_pair.is_pair_coherent(cur, row.get("mapping_id"), link_id):
            return None
        cur.execute("SELECT id, equipment_id, gym_id FROM gym_equipment WHERE id = %s", (int(link_id),))
        return cur.fetchone()


def _eligibility(
    safety: Optional[ts.TrainingSafety], *, original_max_safe_rpe: int, technique: Optional[str]
) -> Optional[str]:
    """`None` = elegivel; senao o motivo (mesmos codigos do M6)."""
    if safety is None:
        return subst.REJECT_MAX_SAFE_RPE_LOWER
    if int(safety.max_safe_rpe) < int(original_max_safe_rpe):
        return subst.REJECT_MAX_SAFE_RPE_LOWER
    if technique and not ts.validate_technique(technique, safety).valid:
        return subst.REJECT_TECHNIQUE_INCOMPATIBLE
    return None


def list_equipment_options(plan_id: int, exercise_id: int, user_id: Optional[int]) -> dict[str, Any]:
    """Payload de `GET .../equipment-options`: as associacoes da academia da
    ficha para o exercicio da linha, no ranking do DONO da ficha, cada uma
    com `eligible` + `reason` (D13a) e a seguranca do par."""
    with transaction() as conn:
        plan, row = _load_plan_and_row(conn, plan_id, exercise_id, user_id)
        if not plan.get("gym_id"):
            raise PlanExerciseSubstitutionError(
                409, REASON_PLAN_WITHOUT_GYM, "A ficha não tem academia definida; não há catálogo para escolher."
            )
        if not row.get("mapping_id"):
            raise PlanExerciseSubstitutionError(
                409,
                REASON_EXERCISE_WITHOUT_MAPPING,
                "Este exercício não está vinculado ao catálogo; vincule-o antes de trocar o equipamento.",
            )
        gym_id, mapping_id = int(plan["gym_id"]), int(row["mapping_id"])
        links = _same_exercise_links(conn, gym_id, mapping_id)
        with conn.cursor() as cur:
            ranked = rank_gym_equipment_rows(cur, links, gym_id=gym_id, user_id=plan.get("user_id"))
        current = _current_link(conn, row)
        pairs = [(int(link["canonical_exercise_id"]), int(link["equipment_id"])) for link in ranked]
        if current:
            pairs.append((mapping_id, int(current["equipment_id"])))
        safety_by_pair = ts.resolve_training_safety_bulk(conn, pairs) if pairs else {}
        current_safety = safety_by_pair.get((mapping_id, int(current["equipment_id"]))) if current else None
        original_max_safe_rpe = int(current_safety.max_safe_rpe) if current_safety else 0
        technique = row.get("advanced_technique") or None

    items = []
    for link in ranked:
        safety = safety_by_pair.get((int(link["canonical_exercise_id"]), int(link["equipment_id"])))
        reason = _eligibility(safety, original_max_safe_rpe=original_max_safe_rpe, technique=technique)
        items.append(
            {
                "gym_equipment_id": int(link["id"]),
                "equipment_id": int(link["equipment_id"]),
                "equipment_name": link.get("equipment_name"),
                "photo_url": link.get("photo_url"),
                "user_preference_stars": link.get("user_preference_stars"),
                "max_safe_rpe": safety.max_safe_rpe if safety else None,
                "failure_allowed": safety.failure_allowed if safety else None,
                "is_current": bool(current and int(current["id"]) == int(link["id"])),
                "eligible": reason is None,
                "reason": reason,
            }
        )
    return {
        "plan_id": int(plan_id),
        "workout_plan_exercise_id": int(row["id"]),
        "mapping_id": mapping_id,
        "exercise_name": row.get("exercise_name"),
        "current_gym_equipment_id": int(current["id"]) if current else None,
        "current_max_safe_rpe": original_max_safe_rpe if current else None,
        "required_technique": technique,
        "items": items,
    }


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------


def substitute_equipment(
    plan_id: int, exercise_id: int, gym_equipment_id: int, user_id: Optional[int]
) -> dict[str, Any]:
    """Troca o equipamento da linha e recongela SO a prescricao dela.
    Devolve `{"plan": <ficha lida de novo>, "exercise": <linha atualizada>}`."""
    with transaction() as conn:
        plan, row = _load_plan_and_row(conn, plan_id, exercise_id, user_id)
        services.assert_plan_prescription_refreshable(plan, require_future_date=False)
        if not plan.get("gym_id"):
            raise PlanExerciseSubstitutionError(
                409, REASON_PLAN_WITHOUT_GYM, "A ficha não tem academia definida; não há catálogo para escolher."
            )
        if not row.get("mapping_id"):
            raise PlanExerciseSubstitutionError(
                409,
                REASON_EXERCISE_WITHOUT_MAPPING,
                "Este exercício não está vinculado ao catálogo; vincule-o antes de trocar o equipamento.",
            )
        gym_id, mapping_id = int(plan["gym_id"]), int(row["mapping_id"])

        with conn.cursor() as cur:
            cur.execute(
                "SELECT ge.id, ge.gym_id, ge.equipment_id, ge.is_active, eq.name AS equipment_name "
                "FROM gym_equipment ge JOIN equipment eq ON eq.id = ge.equipment_id WHERE ge.id = %s",
                (int(gym_equipment_id),),
            )
            link = cur.fetchone()
            if not link or int(link["gym_id"]) != gym_id or not link.get("is_active"):
                raise PlanExerciseSubstitutionError(
                    422, REASON_EQUIPMENT_NOT_IN_GYM, "Este equipamento não está ativo na academia da ficha."
                )
            # D13 na escrita: o vinculo tem de servir o exercicio DESTA linha.
            try:
                equipment_pair.assert_pair_coherent(cur, mapping_id, int(gym_equipment_id))
            except equipment_pair.IncoherentEquipmentPairError as exc:
                raise PlanExerciseSubstitutionError(
                    422, REASON_INCOHERENT_PAIR, "Este equipamento não serve o exercício desta linha."
                ) from exc

        current = _current_link(conn, row)
        if current and int(current["id"]) == int(gym_equipment_id):
            raise PlanExerciseSubstitutionError(409, REASON_SAME_EQUIPMENT, "Este já é o equipamento da linha.")

        pairs = [(mapping_id, int(link["equipment_id"]))]
        if current:
            pairs.append((mapping_id, int(current["equipment_id"])))
        safety_by_pair = ts.resolve_training_safety_bulk(conn, pairs)
        new_safety = safety_by_pair.get((mapping_id, int(link["equipment_id"])))
        current_safety = safety_by_pair.get((mapping_id, int(current["equipment_id"]))) if current else None
        original_max_safe_rpe = int(current_safety.max_safe_rpe) if current_safety else 0
        technique = row.get("advanced_technique") or None
        reason = _eligibility(new_safety, original_max_safe_rpe=original_max_safe_rpe, technique=technique)
        if reason == subst.REJECT_MAX_SAFE_RPE_LOWER:
            raise PlanExerciseSubstitutionError(
                422,
                REASON_MAX_SAFE_RPE_LOWER,
                f"O equipamento escolhido tem teto de RPE {new_safety.max_safe_rpe if new_safety else '?'}, "
                f"abaixo do teto {original_max_safe_rpe} do equipamento atual. A troca nunca reduz a segurança.",
            )
        if reason == subst.REJECT_TECHNIQUE_INCOMPATIBLE:
            raise PlanExerciseSubstitutionError(
                422,
                REASON_TECHNIQUE_INCOMPATIBLE,
                f"O equipamento escolhido não é compatível com a técnica prescrita ({technique}).",
            )

        # A carga escrita na linha era do equipamento ANTIGO: sai antes de
        # recongelar (o snapshot nao herda carga de outro par) e volta, logo
        # abaixo, como a sugestao do motor para o par NOVO.
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE workout_plan_exercises SET preferred_equipment_id = %s, equipment_name = %s, "
                "load_target = NULL WHERE id = %s",
                (int(gym_equipment_id), link.get("equipment_name"), int(row["id"])),
            )
        audit = subst.equipment_substitution_audit(
            reason=subst.REASON_USER_CHOICE,
            exercise_id=mapping_id,
            from_equipment_id=int(current["equipment_id"]) if current else None,
            to_equipment_id=int(link["equipment_id"]),
            to_gym_equipment_id=int(gym_equipment_id),
            original_max_safe_rpe=original_max_safe_rpe if current else None,
            chosen_max_safe_rpe=new_safety.max_safe_rpe if new_safety else None,
        )
        # O MESMO caminho do refresh-prescription, restrito a esta linha.
        services._freeze_plan_prescriptions(
            conn,
            int(plan_id),
            user_id=plan.get("user_id"),
            plan_type=plan.get("plan_type"),
            exercise_ids=[int(row["id"])],
            source=prescription_domain.SOURCE_REFRESH,
            substitution_by_row={int(row["id"]): audit},
        )
        services.sync_load_target_from_frozen_suggestion(conn, int(row["id"]))

    fresh = services.get_workout_plan(int(plan_id), user_id=user_id)
    exercise = next(
        (item for item in (fresh or {}).get("exercises") or [] if int(item["workout_plan_exercise_id"]) == int(row["id"])),
        None,
    )
    return {"plan": fresh, "exercise": exercise}
