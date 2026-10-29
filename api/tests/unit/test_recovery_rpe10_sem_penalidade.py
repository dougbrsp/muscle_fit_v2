"""D4 revisada (2026-09-14): RPE 10 (inclusive antes da ultima serie) nao
penaliza a recuperacao, para TODOS os usuarios.

- `failure_penalty` (0,25 x max(0, n_RPE10 - 1) somado a dose) foi removida.
- `rpe_anticipated` nao dispara por serie em RPE 10 em nenhuma posicao.
- Complemento (2026-09-14, PR seguinte ao #70): nem por RPE >= alvo do plano
  + 1 - o campo ficou deprecado e e sempre False.

`VolumeService.get_muscle_group_overview` roda sem banco: os fetches sao
substituidos por linhas em memoria no mesmo formato de
`_fetch_window_sets`/`_fetch_full_history_sets`."""

from datetime import datetime, timedelta

import pytest

from app.intelligence.muscle_recovery_service import MuscleRecoveryService as MRS
from app.intelligence.volume_service import VolumeService

PLAN_ID = 77


def _rows(sets, *, plan_id=PLAN_ID):
    trained_at = (datetime.now() - timedelta(hours=6)).replace(microsecond=0)
    return [
        {
            "set_id": n,
            "exercise_id": 1,
            "activity_id": 500,
            "set_number": n,
            "reps": reps,
            "weight_kg": weight,
            "rpe": rpe,
            "set_type": "working",
            "local_date": trained_at.date().isoformat(),
            "trained_at": trained_at.isoformat(),
            "mapping_id": None,
            "original_name": "Supino reto",
            "primary_group": "Peitoral",
            "secondary_group": None,
            "primary_group_id": None,
            "secondary_group_id": None,
            "secondary_participation": None,
            "muscle_pain": None,
            "joint_pain": 0,
            "activity_workout_plan_id": plan_id,
            "raw_payload_workout_plan_id": None,
        }
        for n, (weight, reps, rpe) in enumerate(sets, start=1)
    ]


def _overview(sets):
    rows = _rows(sets)
    service = VolumeService()
    service._fetch_window_sets = lambda days, user_id=None: rows
    service._fetch_full_history_sets = lambda user_id=None, history_days=None: rows

    def _no_db(*_args, **_kwargs):
        # Tambem garante que o antigo lookup de rpe_target do plano
        # (workout_plan_exercises) nao e mais consultado.
        raise AssertionError("teste unitario nao pode tocar o banco")

    service.fetch_all = _no_db
    (item,) = service.get_muscle_group_overview(days=14, limit=10, user_id=1, now_local=datetime.now())
    return item


def _expected_dose_without_failure_penalty(sets):
    """Soma por serie dos fatores que continuam valendo (sessao unica: exercicio
    novo -> 1,05; sem referencia de e1RM -> load_spike 1,0; PRIMARY -> 1,0)."""
    return sum(
        MRS.effort_points(rpe) * 1.0 * MRS.repetition_factor(reps) * MRS.set_type_factor("working") * 1.0
        * MRS.exercise_factor(True)
        for _weight, reps, rpe in sets
    )


def test_failure_penalty_nao_existe_mais():
    assert not hasattr(MRS, "failure_penalty")


@pytest.mark.parametrize(
    "sets",
    [
        [(80, 10, 8), (80, 10, 8), (80, 8, 10)],  # 1 serie em RPE 10 (ultima)
        [(80, 8, 10), (80, 8, 10), (80, 8, 10)],  # 3 series em RPE 10 (antes: +0,5 na dose)
    ],
    ids=["1xRPE10", "3xRPE10"],
)
def test_dose_sem_acrescimo_por_series_em_rpe10(sets):
    item = _overview(sets)
    assert item["session_dose"] == pytest.approx(_expected_dose_without_failure_penalty(sets))


def test_dose_por_serie_rpe10_e_a_mesma_com_1_ou_3_series():
    """Cada serie em RPE 10 contribui exatamente o mesmo, sem acrescimo que
    cresca com a quantidade de series em falha."""
    one = _overview([(80, 8, 10)])["session_dose"]
    three = _overview([(80, 8, 10), (80, 8, 10), (80, 8, 10)])["session_dose"]
    assert three == pytest.approx(3 * one)


@pytest.mark.parametrize(
    "rpes",
    [(10, 10, 9), (10, 9, 9), (10, 10, 10), (9, 9, 10)],
)
def test_rpe_anticipated_nao_dispara_por_rpe10_em_nenhuma_posicao(rpes):
    # Antes do #70, com alvo '9', RPE 10 antes da ultima marcava True
    # (usuario nao avancado).
    item = _overview([(80, 8, rpe) for rpe in rpes])
    assert item["rpe_anticipated"] is False


@pytest.mark.parametrize(
    "rpes",
    [(9, 8, 8), (9, 9, 9), (9.5, 9.5, 9), (9, 9.5, 10)],
)
def test_rpe_anticipated_nunca_true_para_rpe_acima_do_alvo(rpes):
    # Antes (apos #70): alvo '8' -> limiar 9, qualquer serie em RPE 9/9,5
    # marcava True. Agora (2026-09-14) o campo e deprecado e sempre False.
    item = _overview([(80, 8, rpe) for rpe in rpes])
    assert item["rpe_anticipated"] is False


def test_lookup_de_rpe_target_do_plano_foi_removido():
    for attr in ("_fetch_plan_rpe_targets", "_parse_rpe_target", "_extract_workout_plan_id"):
        assert not hasattr(VolumeService, attr), attr


def test_rpe_anticipated_nao_consulta_flag_de_usuario_avancado():
    assert not hasattr(VolumeService, "_is_advanced_user")
