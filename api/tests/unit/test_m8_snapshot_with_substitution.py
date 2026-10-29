"""M8 - `prescription.with_substitution`: o bloco auditavel da troca manual
de equipamento entra no snapshot re-derivado, seja qual for a origem dele."""

from __future__ import annotations

from app.domain import prescription as p
from app.generation import exercise_substitution as subst


def _audit():
    return subst.equipment_substitution_audit(
        reason=subst.REASON_USER_CHOICE, exercise_id=7, from_equipment_id=1, to_equipment_id=2, to_gym_equipment_id=99
    )


def test_adiciona_bloco_e_aviso_sem_tocar_no_resto():
    snapshot = p.build_prescription({"sets_count": 3, "reps_target": "8-12", "rpe_target": "8"})
    result = p.with_substitution(snapshot, _audit())
    assert result["substitution"]["reason"] == "USER_CHOICE"
    assert p.WARNING_EQUIPMENT_SUBSTITUTED in result["warnings"]
    # Aviso do par desconhecido (D3) preservado; ordem determinística.
    assert result["warnings"] == sorted({p.WARNING_EQUIPMENT_SUBSTITUTED, p.WARNING_EQUIPMENT_UNRESOLVED})
    for key in ("sets_count", "reps_target", "effective_rpe_target", "engine_version", "frozen_at"):
        assert result[key] == snapshot[key]
    assert snapshot["substitution"] is None  # a entrada nao e mutada


def test_a_decisao_do_usuario_substitui_bloco_anterior():
    earlier = {"kind": "EQUIPMENT", "reason": "EQUIPMENT_UNAVAILABLE_IN_GYM"}
    snapshot = p.build_prescription({"rpe_target": "8"}, substitution=earlier)
    assert snapshot["warnings"].count(p.WARNING_EQUIPMENT_SUBSTITUTED) == 1
    result = p.with_substitution(snapshot, _audit())
    assert result["substitution"]["reason"] == "USER_CHOICE"
    assert result["warnings"].count(p.WARNING_EQUIPMENT_SUBSTITUTED) == 1
