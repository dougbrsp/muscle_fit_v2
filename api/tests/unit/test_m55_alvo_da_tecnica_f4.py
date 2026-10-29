"""M55: a F4 guarda QUAL trilha foi o alvo da tecnica.

`progression_tracks.technique_code` e gravado pela execucao. Antes do M55 o
filtro `track_technique_in_force` era por GRUPO: com o grupo em F4-F6 e
`technique_applied_at` preenchido, TODA trilha dele com tecnica gravada (so
executada, herdada de outro Modelo) voltava a receber a tecnica. Agora a
efetivacao do `APPLY_TECHNIQUE` grava o par alvo em
`GroupState.technique_target_key` e so ele recebe a tecnica.

Funcoes puras, sem banco. A persistencia (migracao 063, dois backends) esta
em `tests/integration/dataapi/test_m55_alvo_tecnica_f4.py`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from app.domain.progression_engine import track_key
from app.domain import progression_engine as pe
from app.domain import progression_evaluator as ev
from tests.support import progression_fixtures as fx

APPLIED_AT = datetime(2026, 9, 20, 18, 0)
ALVO = (1, 1)
OUTRA = (2, 1)

# alvo da F4 + uma trilha do MESMO grupo com tecnica so executada
TRILHA_ALVO = fx.track(exercise_id=1, equipment_id=1, technique_code="rest_pause", sequence=2)
TRILHA_EXECUTADA = fx.track(exercise_id=2, equipment_id=1, technique_code="drop_set", sequence=1)


def _grupo(phase: str, *, alvo=ALVO, applied=APPLIED_AT) -> pe.GroupState:
    return replace(fx.group(phase=phase, technique_applied_at=applied), technique_target_key=alvo)


# ---------------------------------------------------------------------------
# Filtro
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phase", [pe.F4, pe.F5, pe.F6])
def test_grupo_em_f4_ou_alem_so_a_trilha_alvo_recebe_a_tecnica(phase):
    grupo = _grupo(phase)
    assert pe.track_technique_in_force(TRILHA_ALVO, grupo) == "rest_pause"
    assert pe.track_technique_in_force(TRILHA_EXECUTADA, grupo) is None


@pytest.mark.parametrize("phase", [pe.F1, pe.F2, pe.F3])
@pytest.mark.parametrize("alvo", [ALVO, None])
def test_grupo_antes_da_f4_nenhuma_trilha_recebe_tecnica_executada(phase, alvo):
    grupo = _grupo(phase, alvo=alvo, applied=None)
    assert pe.track_technique_in_force(TRILHA_ALVO, grupo) is None
    assert pe.track_technique_in_force(TRILHA_EXECUTADA, grupo) is None


def test_f5_alcancada_pulando_a_f4_nao_prescreve_tecnica_executada():
    grupo = _grupo(pe.F5, alvo=None, applied=None)
    assert pe.track_technique_in_force(TRILHA_EXECUTADA, grupo) is None


def test_trilha_alvo_sem_tecnica_gravada_nao_inventa_tecnica():
    sem_tecnica = replace(TRILHA_ALVO, technique_code=None)
    assert pe.track_technique_in_force(sem_tecnica, _grupo(pe.F5)) is None


def test_alvo_desconhecido_em_f4_mantem_o_comportamento_anterior():
    """Legado da migracao 063: F4 efetivada antes do M55 nao tem alvo
    gravado (NULL). Sem fonte segura do alvo, vale o filtro por grupo de
    antes - nenhuma prescricao atual muda com o deploy."""
    grupo = _grupo(pe.F5, alvo=None)
    assert pe.track_technique_in_force(TRILHA_ALVO, grupo) == "rest_pause"
    assert pe.track_technique_in_force(TRILHA_EXECUTADA, grupo) == "drop_set"


def test_dado_gravado_nao_e_apagado():
    pe.track_technique_in_force(TRILHA_EXECUTADA, _grupo(pe.F5))
    assert TRILHA_EXECUTADA.technique_code == "drop_set"


# ---------------------------------------------------------------------------
# Prescricao: o filtro chega a `prescribe`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "trilha, esperado",
    [(TRILHA_ALVO, "rest_pause"), (TRILHA_EXECUTADA, None)],
)
def test_prescribe_em_f5_so_poe_tecnica_na_trilha_alvo(trilha, esperado):
    decision = pe.prescribe(trilha, _grupo(pe.F5), fx.safety(max_safe_rpe=10), catalog=fx.CATALOG)
    assert decision.advanced_technique == esperado


def test_tecnica_do_modelo_continua_mandando_na_trilha_que_nao_e_alvo():
    decision = pe.prescribe(
        TRILHA_EXECUTADA,
        _grupo(pe.F5),
        fx.safety(max_safe_rpe=10),
        catalog=fx.CATALOG,
        model_technique="drop_set",
    )
    assert decision.advanced_technique == "drop_set"


# ---------------------------------------------------------------------------
# Efetivacao grava o alvo
# ---------------------------------------------------------------------------

APPLY = {"pending_action": pe.APPLY_TECHNIQUE, "pending_payload": {"track_key": [1, 1], "technique_code": "rest_pause"}}


def test_efetivacao_do_apply_technique_grava_o_alvo_do_payload():
    grupo = fx.group(phase=pe.F3, **APPLY)
    efetivado = pe.apply_effective(grupo, pe.F4, now=APPLIED_AT, reps=(10, 12))
    assert efetivado.technique_target_key == ALVO
    assert efetivado.technique_applied_at == APPLIED_AT
    assert efetivado.pending_payload is None  # o payload some; o alvo fica


@pytest.mark.parametrize("payload", [None, {}, {"track_key": [1]}, {"track_key": ["x", 1]}, {"track_key": "1:1"}])
def test_payload_sem_alvo_valido_nao_grava_alvo(payload):
    grupo = fx.group(phase=pe.F3, pending_action=pe.APPLY_TECHNIQUE, pending_payload=payload)
    assert pe.apply_effective(grupo, pe.F4, now=APPLIED_AT, reps=(10, 12)).technique_target_key is None


@pytest.mark.parametrize("acao, fase", [(pe.ADD_SET, pe.F5), (pe.INCREASE_LOAD, pe.F6), (pe.DECREASE_LOAD, pe.F6)])
def test_efetivacoes_seguintes_preservam_o_alvo(acao, fase):
    grupo = replace(
        fx.group(phase=pe.F4, pending_action=acao, pending_payload={"track_key": [2, 1]}, technique_applied_at=APPLIED_AT),
        technique_target_key=ALVO,
    )
    assert pe.apply_effective(grupo, fase, now=datetime(2026, 9, 30), reps=(10, 12)).technique_target_key == ALVO


def test_avaliacao_da_sessao_efetiva_a_f4_e_guarda_o_alvo():
    """Caminho do avaliador (`evaluate_session`): sessao com as duas trilhas
    efetiva o `APPLY_TECHNIQUE` pendente; o grupo sai em F4 com o alvo do
    payload, e a trilha que so tinha tecnica executada fica sem tecnica."""
    alvo = replace(TRILHA_ALVO, technique_code=None)
    tracks = [alvo, TRILHA_EXECUTADA]
    grupo = fx.group(phase=pe.F3, **APPLY)
    groups, track_map = fx.state_maps(tracks, [grupo])
    sessao = fx.session(
        day="2026-09-20",
        executions=[fx.execution(exercise_id=2, sequence=1), fx.execution(exercise_id=1, sequence=2)],
    )
    result = ev.evaluate_session(
        sessao, [sessao], group_states=groups, tracks=track_map, inputs=fx.inputs(tracks=tracks)
    )
    estado = result.group_states[10]
    assert estado.phase == pe.F4
    assert estado.technique_applied_at is not None
    assert estado.technique_target_key == ALVO
    # a execucao seguinte grava a tecnica no alvo: so ele recebe
    alvo_com_tecnica = replace(result.tracks[track_key(*ALVO, "rested")], technique_code="rest_pause")
    assert pe.track_technique_in_force(alvo_com_tecnica, estado) == "rest_pause"
    assert pe.track_technique_in_force(result.tracks[track_key(*OUTRA, "rested")], estado) is None
