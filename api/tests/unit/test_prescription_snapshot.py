"""`app/domain/prescription.py`: o que entra no snapshot congelado e o que o
overlay de seguranca ao vivo pode (e nao pode) fazer com ele. Modulo puro -
nenhum banco aqui."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.domain import prescription as pd
from app.domain import training_safety as ts

CATALOG = {"drop_set": "requires_failure", "gvt": "failure_not_required"}


def _safety(max_safe_rpe, *, techniques=None):
    return ts.build_training_safety(
        exercise_id=1,
        equipment_id=2,
        equipment_exercise_id=3,
        equipment_max_safe_rpe=max_safe_rpe,
        equipment_techniques=techniques or {},
        technique_failure_requirements=CATALOG,
    )


def _exercise(**kwargs):
    base = {
        "sets_count": 3,
        "reps_target": "10-12",
        "load_target": "40",
        "rpe_target": "9",
        "advanced_technique": None,
        "progression": "increase_load",
        "progression_note": "Subir 2,5 kg",
        "suggested_load_kg": "42.5",
        "suggested_reps": 10,
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# parse_rpe
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valor, esperado",
    [
        ("9", 9),
        # M76 (C4): o texto e uma RAMPA e o alvo e o ULTIMO degrau (D4), o
        # mesmo de `training_history.PrescribedSpec`; o teto corta depois.
        ("8-9", 9),
        ("8-9-10", 10),
        (9, 9),
        (9.0, 9),
        ("RPE 10", 10),
        ("8,5", 8),  # fracao arredonda para BAIXO
        ("9.5", 9),
        ("", None),
        (None, None),
        ("alto", None),
        (True, None),
    ],
)
def test_parse_rpe(valor, esperado):
    assert pd.parse_rpe(valor) == esperado


# ---------------------------------------------------------------------------
# build_prescription
# ---------------------------------------------------------------------------


def test_snapshot_guarda_o_prescrito_e_o_efetivo_com_o_clamp():
    snapshot = pd.build_prescription(_exercise(rpe_target="10"), safety=_safety(8))
    assert snapshot["rpe_target"] == "10"  # o que foi prescrito, preservado
    assert (snapshot["effective_rpe_target"], snapshot["effective_rpe_ceiling"]) == (8, 8)
    assert snapshot["clamps"] == [{"field": "rpe_target", "from": 10, "to": 8, "reason": "EQUIPMENT_SAFETY"}]
    assert snapshot["failure_allowed"] is False
    assert snapshot["safety_constraints"] == {"max_safe_rpe": 8, "source": "equipment_default"}


def test_sem_rpe_prescrito_o_alvo_e_o_default_e_nao_o_teto():
    """D4 revisada em 2026-09-18: o teto nunca vira piso."""
    snapshot = pd.build_prescription(_exercise(rpe_target=None), safety=_safety(10))
    assert (snapshot["effective_rpe_target"], snapshot["effective_rpe_ceiling"]) == (8, 10)
    assert snapshot["clamps"] == []


def test_par_desconhecido_cai_no_default_conservador_com_aviso():
    snapshot = pd.build_prescription(_exercise(rpe_target="10"), safety=None)
    assert snapshot["warnings"] == [pd.WARNING_EQUIPMENT_UNRESOLVED]
    assert snapshot["effective_rpe_ceiling"] == ts.DEFAULT_MAX_SAFE_RPE
    assert snapshot["safety_constraints"] == {"max_safe_rpe": ts.DEFAULT_MAX_SAFE_RPE, "source": None}


def test_tecnica_invalida_no_par_exige_escolha_do_usuario():
    # drop_set exige falha; teto 8 nao permite falha -> invalida.
    snapshot = pd.build_prescription(
        _exercise(advanced_technique="drop_set"), safety=_safety(8, techniques={"drop_set": 1})
    )
    assert snapshot["requires_user_choice"] is True
    assert snapshot["warnings"] == [pd.WARNING_MODEL_TECHNIQUE_INVALID]


def test_tecnica_valida_no_par_nao_exige_escolha():
    snapshot = pd.build_prescription(
        _exercise(advanced_technique="gvt"), safety=_safety(8, techniques={"gvt": 1})
    )
    assert snapshot["requires_user_choice"] is False and snapshot["warnings"] == []


def test_indicadores_do_motor_legado_entram_congelados():
    snapshot = pd.build_prescription(_exercise(), safety=_safety(9))
    assert snapshot["progression"] == "increase_load"
    assert snapshot["progression_note"] == "Subir 2,5 kg"
    assert snapshot["suggested_load_kg"] == 42.5 and snapshot["suggested_reps"] == 10
    assert snapshot["reason"] == "Subir 2,5 kg"


def test_origem_e_versao_ficam_gravadas_no_snapshot():
    quando = datetime(2026, 9, 18, 14, 30, 5, 123456)
    snapshot = pd.build_prescription(_exercise(), safety=_safety(9), frozen_at=quando)
    assert snapshot["schema_version"] == pd.SCHEMA_VERSION
    assert snapshot["engine_version"] == pd.LEGACY_ENGINE_VERSION
    assert snapshot["source"] == pd.SOURCE_PLAN_CREATED
    assert snapshot["frozen_at"] == "2026-09-18T14:30:05"  # sem microssegundos


def test_decisao_do_motor_v2_preenche_fase_e_motivo_sem_mudar_o_contrato():
    """PR-12/13 trocam so a ORIGEM: os campos ja existem no formato de hoje."""
    snapshot = pd.build_prescription(
        _exercise(),
        safety=_safety(9),
        engine_version="progression-v2.0.0",
        decision_id=7781,
        decision={"phase_after": "F3", "decision": "INCREASE_LOAD", "reason": "Platô superado."},
    )
    assert (snapshot["decision_id"], snapshot["phase"], snapshot["decision"]) == (7781, "F3", "INCREASE_LOAD")
    assert snapshot["engine_version"] == "progression-v2.0.0"
    assert snapshot["reason"] == "Platô superado."


# ---------------------------------------------------------------------------
# apply_safety_overlay
# ---------------------------------------------------------------------------


def test_overlay_restringe_quando_o_teto_de_hoje_e_menor():
    snapshot = pd.build_prescription(_exercise(rpe_target="9"), safety=_safety(9))
    shown, overlay = pd.apply_safety_overlay(snapshot, _safety(7))
    assert shown["effective_rpe_target"] == 7 and shown["effective_rpe_ceiling"] == 7
    assert overlay["clamps"] == [{"field": "rpe_target", "from": 9, "to": 7, "reason": "EQUIPMENT_SAFETY"}]
    assert snapshot["effective_rpe_target"] == 9  # o snapshot nao e mutado


def test_overlay_nunca_amplia_quando_o_teto_de_hoje_e_maior():
    snapshot = pd.build_prescription(_exercise(rpe_target="8"), safety=_safety(8))
    shown, overlay = pd.apply_safety_overlay(snapshot, _safety(10))
    assert shown["effective_rpe_target"] == 8
    assert shown["failure_allowed"] is False
    # M76 (B1/C5): o teto do OVERLAY tambem nunca passa do congelado - o
    # Modo Treino le `overlay ?? prescricao` e nao pode oferecer 10 aqui.
    assert overlay["clamps"] == [] and overlay["effective_rpe_ceiling"] == 8
    assert overlay["failure_allowed"] is False


def test_overlay_sem_par_conhecido_devolve_a_prescricao_como_esta():
    snapshot = pd.build_prescription(_exercise(), safety=_safety(9))
    shown, overlay = pd.apply_safety_overlay(snapshot, None)
    assert shown == snapshot and overlay is None


def test_ficha_legada_sem_snapshot_ainda_informa_a_seguranca_de_hoje():
    shown, overlay = pd.apply_safety_overlay(None, _safety(8))
    assert shown is None
    assert overlay["effective_rpe_ceiling"] == 8 and overlay["failure_allowed"] is False
