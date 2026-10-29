"""M77 (2026-10-11): a trilha adota a carga executada mesmo com TODAS as
series `working` abaixo do piso da faixa, quando ela e carga de trabalho.

Antes, `adopted_working_load` nunca adotava nesse caso ("territorio da D10:
adotar consolidaria uma carga alta demais"). Efeito: 70 kg x 8 na faixa 10-12
deixava a trilha presa numa carga antiga (35 kg, Remada horizontal) e a D10
ainda REDUZIA a partir dali. Agora a carga executada e adotada; a carga alta demais
continua tratada pela D10, a partir da carga EXECUTADA (HOLD para buscar o piso,
-1 incremento depois de duas sessoes abaixo dele). Teste de carga (poucas
repeticoes, longe do piso) continua ignorado.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

import app.domain.progression_engine as pe
import app.domain.progression_evaluator as ev
from tests.support import progression_fixtures as fx

REMADA = 8  # exercise_catalog.id "Remada horizontal na maquina"
ARTICULADA = 49  # equipment.id "Remada horizontal articulada"
DORSAL = 6  # muscle_group_id PRIMARY
KEY = pe.track_key(REMADA, ARTICULADA, "rested")
NOW = datetime(2026, 10, 11, 9, 0)


def _track(load=35):
    return fx.track(
        exercise_id=REMADA,
        equipment_id=ARTICULADA,
        muscle_group_id=DORSAL,
        load=load,
        sets_count=2,
        is_compound=True,
    )


def _execution(reps, weight, rpe=9):
    return fx.execution(
        exercise_id=REMADA,
        equipment_id=ARTICULADA,
        set_list=fx.sets(reps=reps, rpe=rpe, weight=weight),
    )


def _replay(executions, *, track=None, group=None):
    """Avalia as sessoes em ordem a partir de um estado F2 (faixa 10-12) com a
    trilha em 35 kg - cenario sintetico baseado na trilha antiga real da Remada
    horizontal."""
    track = track or _track()
    groups, tracks = fx.state_maps([track], [group or fx.group(muscle_group_id=DORSAL)])
    inputs = fx.inputs(tracks=[track])
    sessions = [
        fx.session(activity_id=index, day=f"2026-10-{index:02d}", executions=[item])
        for index, item in enumerate(executions, start=1)
    ]
    result = None
    for index, session in enumerate(sessions):
        result = ev.evaluate_session(
            session,
            sessions[: index + 1],
            group_states=groups,
            tracks=tracks,
            inputs=inputs,
        )
        groups, tracks = result.group_states, result.tracks
    return result


# ===========================================================================
# cenario sintetico baseado em caso real: 70 kg x 8 na faixa 10-12
# ===========================================================================


def test_70kg_x_8_abaixo_do_piso_e_adotado():
    updated = ev.update_track_execution(
        _track(), _execution([8, 8], 70), now=NOW, reps_min=10
    )
    assert updated.working_load_kg == Decimal("70")
    assert updated.last_load_change_at == NOW


def test_a_adocao_abaixo_do_piso_vai_para_a_evidencia_da_decisao():
    result = _replay([_execution([8, 8], 70)])
    decision = result.decisions[0]
    adopted = decision.evidence[pe.EVIDENCE_LOAD_ADOPTED_FROM_EXECUTION]
    assert adopted == {
        "8:49": {"from_kg": 35.0, "to_kg": 70.0, "source": pe.LOAD_ADOPTION_SOURCE}
    }


def test_depois_de_70x8_a_prescricao_mantem_70_e_nao_volta_para_35():
    """Uma sessao abaixo do piso nao regride: a prescricao seguinte mantem a
    carga REAL para buscar o piso (HOLD)."""
    result = _replay([_execution([8, 8], 70)])
    track = result.tracks[KEY]
    group = result.group_states[DORSAL]
    assert group.pending_action is None
    decision = pe.prescribe(
        track, group, fx.safety(exercise_id=REMADA, equipment_id=ARTICULADA)
    )
    assert decision.decision == pe.HOLD
    assert decision.suggested_load_kg == 70.0


def test_duas_sessoes_abaixo_do_piso_reduzem_um_passo_a_partir_da_carga_real():
    """D10 intacta, mas a partir da carga executada: 70 -> 70 - 1 incremento,
    nunca 35 - 1 incremento."""
    result = _replay([_execution([8, 8], 70), _execution([8, 7], 70)])
    track = result.tracks[KEY]
    group = result.group_states[DORSAL]
    assert track.working_load_kg == Decimal("70")
    assert group.pending_action == pe.DECREASE_LOAD
    assert group.pending_payload["rule"] == "D10"
    decision = pe.prescribe(
        track, group, fx.safety(exercise_id=REMADA, equipment_id=ARTICULADA)
    )
    increment = pe.load_increment(is_compound=True, load_increment_kg=None).delta_kg
    assert decision.decision == pe.DECREASE_LOAD
    assert Decimal(str(decision.suggested_load_kg)) == Decimal("70") - increment


def test_caso_real_exercicio_6_sai_da_carga_antiga_e_acompanha_a_carga_executada():
    """Cenario sintetico baseado em caso real (exercicio 6, faixa 10-12):
    trilha em 32,5 kg, depois 42,5 x 8/7 e 45 x 8/8. Antes a trilha ficava em
    32,5 kg; agora acompanha 42,5 e 45."""
    track = fx.track(
        exercise_id=6, equipment_id=35, muscle_group_id=7, load="32.5", sets_count=2
    )
    first = fx.execution(
        exercise_id=6,
        equipment_id=35,
        set_list=fx.sets(reps=[8, 7], rpe=9, weight="42.5"),
    )
    updated = ev.update_track_execution(track, first, now=NOW, reps_min=10)
    assert updated.working_load_kg == Decimal("42.5")
    second = fx.execution(
        exercise_id=6, equipment_id=35, set_list=fx.sets(reps=[8, 8], rpe=9, weight=45)
    )
    assert ev.update_track_execution(
        updated, second, now=NOW, reps_min=10
    ).working_load_kg == Decimal("45")


# ===========================================================================
# casos protegidos: teste de carga / carga atipica
# ===========================================================================


def test_teste_de_carga_com_poucas_repeticoes_nao_e_adotado():
    """100 kg x 3 na faixa 10-12 e teste (territorio de 1RM), nao carga de
    trabalho: a trilha fica na carga dela."""
    updated = ev.update_track_execution(
        _track(), _execution([3], 100, rpe=10), now=NOW, reps_min=10
    )
    assert updated.working_load_kg == Decimal("35")
    assert updated.last_load_change_at is None


@pytest.mark.parametrize(
    ("reps_min", "reps", "adopted"),
    [
        (10, [7, 6], True),  # melhor serie a 3 do piso: carga de trabalho
        (10, [6, 6], False),  # a 4 do piso: longe demais
        (6, [5, 4], True),  # faixa 6-8: 5 repeticoes ainda e trabalho
        (6, [4, 4], False),  # 4 repeticoes: teste, mesmo a 2 do piso
    ],
)
def test_limites_da_adocao_abaixo_do_piso(reps_min, reps, adopted):
    updated = ev.update_track_execution(
        _track(), _execution(reps, 70), now=NOW, reps_min=reps_min
    )
    assert updated.working_load_kg == (Decimal("70") if adopted else Decimal("35"))


def test_aquecimento_pesado_nao_conta_como_carga_executada():
    """Series preparatorias nunca entram: so as `working` decidem."""
    execution = fx.execution(
        exercise_id=REMADA,
        equipment_id=ARTICULADA,
        set_list=fx.sets(set_type="warmup", reps=5, rpe=None, weight=90, count=1)
        + fx.sets(reps=[12, 12], rpe=9, weight=35, start=2),
    )
    updated = ev.update_track_execution(_track(), execution, now=NOW, reps_min=10)
    assert updated.working_load_kg == Decimal("35")


def test_reducao_continua_sem_vir_da_execucao():
    """A assimetria segue: carga menor abaixo do piso nao derruba a trilha."""
    updated = ev.update_track_execution(
        _track(load=70), _execution([8, 8], 60), now=NOW, reps_min=10
    )
    assert updated.working_load_kg == Decimal("70")


def test_predicado_de_carga_de_trabalho_abaixo_do_piso():
    sets = fx.sets(reps=[8, 8], weight=70)
    assert pe.below_floor_load_is_working(sets, 10)
    assert not pe.below_floor_load_is_working(fx.sets(reps=[3], weight=100), 10)
    assert not pe.below_floor_load_is_working(sets, None)
    assert not pe.below_floor_load_is_working(fx.sets(reps=[None], weight=70), 10)
