"""Lote 3 da investigacao RPE 10 (2026-10-11): P4 (qualificacao pela melhor
serie + regra de excesso) e P5 (carga da trilha e efetivacao condicionada).

Regra "Motores: fonte unica da verdade" (`docs/motores/FONTES_DA_VERDADE.md`
§3.3; guarda `test_guarda_fontes_da_verdade.py`): tudo aqui chama o dono
(`domain/progression_engine.py`, aplicado por `progression_evaluator`).
Cenarios ponta a ponta (U, A, B, H, H2, R) em `test_cenarios_rpe10_progressao.py`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

from app.domain import progression_engine as pe
from app.domain import progression_evaluator as ev
from app.domain.training_history import PrescribedSpec
from app.domain.training_safety import REDUCED_DOSE
from tests.support import progression_fixtures as fx

NOW = datetime(2026, 9, 10, 18, 0)
SAFETY = fx.safety(max_safe_rpe=10)


def _outcome(set_list, *, track=None, rpe_target=10):
    return pe.evaluate_track_session(
        fx.execution(set_list=set_list, prescribed=PrescribedSpec(sets=3, reps_min=10, reps_max=12, rpe_target=rpe_target)),
        track or fx.track(),
        reps_min=10,
        reps_max=12,
        ceiling=10,
        failure_allowed=True,
    )


# ---------------------------------------------------------------------------
# P4: regra de excesso (sinal da avaliacao da trilha)
# ---------------------------------------------------------------------------


def test_excesso_exige_reps_max_mais_3_na_melhor_serie_no_rpe_alvo():
    assert pe.EXCESS_REPS_FAST_TRACK == 3
    excesso = _outcome(fx.sets(reps=[17, 17, 11], rpe=10))
    assert (excesso.outcome, excesso.excess_reps) == (pe.QUALIFY, True)
    assert excesso.to_evidence()["excess_reps"] is True
    # 14 = reps_max + 2: qualifica sem excesso
    assert _outcome(fx.sets(reps=[14, 12, 12], rpe=10)).excess_reps is False
    # melhor serie abaixo do RPE alvo (15@8 com alvo 10): sem excesso
    assert _outcome(fx.sets(reps=[15, 12, 12], rpe=[8, 9, 10])).excess_reps is False
    # melhor serie sem RPE: sem excesso
    assert _outcome(fx.sets(reps=[15, 12, 12], rpe=[None, 10, 10])).excess_reps is False


def test_excesso_so_vale_em_sessao_qualificante_e_trilha_com_carga():
    # piso quebrado: RESET, mesmo com 18 reps na melhor serie
    reset = _outcome(fx.sets(reps=[18, 12, 9], rpe=10))
    assert (reset.outcome, reset.excess_reps) == (pe.RESET, False)
    # peso corporal: qualifica, sem aumento de carga
    corpo = _outcome(
        fx.sets(reps=[16, 15, 15], rpe=10, weight=None), track=fx.track(load=None, load_status=pe.LOAD_NOT_APPLICABLE)
    )
    assert (corpo.outcome, corpo.excess_reps) == (pe.QUALIFY, False)


def test_armar_o_aumento_da_trilha_e_um_passo_e_mantem_o_instante_ao_rearmar():
    track = fx.track(load=40)  # isolado, passo default 2,5
    excesso = _outcome(fx.sets(reps=[16, 14, 12], rpe=10))
    armada = pe.arm_excess_reps_fast_track(track, excesso, now=NOW)
    assert (armada.pending_load_kg, armada.pending_load_since) == (Decimal("42.5"), NOW)
    # rearmar a MESMA carga alvo nao renova a janela de expiracao
    depois = pe.arm_excess_reps_fast_track(armada, excesso, now=NOW + timedelta(days=7))
    assert depois.pending_load_since == NOW
    # sessao sem excesso nao arma
    assert pe.arm_excess_reps_fast_track(track, _outcome(fx.sets(reps=12, rpe=10)), now=NOW) == track


def test_prescribe_sobe_a_carga_da_trilha_sem_plato_e_sem_mudar_a_fase():
    track = replace(fx.track(load=40), pending_load_kg=Decimal("42.5"), pending_load_since=NOW)
    group = fx.group(phase=pe.F2)
    decision = pe.prescribe(track, group, SAFETY, now=NOW + timedelta(days=3))
    assert decision.decision == pe.INCREASE_LOAD
    assert decision.suggested_load_kg == 42.5
    assert (decision.suggested_reps_min, decision.suggested_reps_max) == (10, 12)  # volta ao piso na carga nova
    assert decision.phase_after == pe.F2
    assert decision.evidence[pe.EVIDENCE_EXCESS_REPS_FAST_TRACK]["target_kg"] == 42.5
    assert decision.evidence["load_increment_kg"] == 2.5
    assert "Excesso de repeticoes" in decision.reason


def test_prescribe_difere_o_aumento_da_trilha_em_dose_reduzida_e_ignora_o_expirado():
    track = replace(fx.track(load=40), pending_load_kg=Decimal("42.5"), pending_load_since=NOW)
    reduzida = pe.ReadinessContract(muscle_group_id=10, status=REDUCED_DOSE)
    diferida = pe.prescribe(track, fx.group(), SAFETY, reduzida, now=NOW)
    assert (diferida.decision, diferida.suggested_load_kg) == (pe.HOLD, 40.0)
    expirada = pe.prescribe(track, fx.group(), SAFETY, now=NOW + timedelta(days=pe.GROUP_EFFECTIVATION_DAYS))
    assert (expirada.decision, expirada.suggested_load_kg) == (pe.HOLD, 40.0)


def test_aumento_da_trilha_sai_quando_a_carga_alvo_e_executada_ou_expira():
    track = replace(fx.track(load=40), pending_load_kg=Decimal("42.5"), pending_load_since=NOW)
    feita = fx.execution(set_list=fx.sets(reps=10, rpe=10, weight="42.5"))
    antiga = fx.execution(set_list=fx.sets(reps=12, rpe=10, weight=40))
    executou = ev.update_track_execution(track, feita, now=NOW + timedelta(days=4), reps_min=10)
    assert (executou.pending_load_kg, executou.working_load_kg) == (None, Decimal("42.5"))
    segue = ev.update_track_execution(track, antiga, now=NOW + timedelta(days=4), reps_min=10)
    assert segue.pending_load_kg == Decimal("42.5")
    expirou = ev.update_track_execution(track, antiga, now=NOW + timedelta(days=14), reps_min=10)
    assert (expirou.pending_load_kg, expirou.working_load_kg) == (None, Decimal("40"))
    assert pe.track_pending_load_status(track, Decimal("42.5"), now=NOW) == pe.PENDING_LOAD_APPLIED
    assert pe.track_pending_load_status(track, Decimal("40"), now=NOW + timedelta(days=14)) == pe.PENDING_LOAD_EXPIRED


def test_trilha_de_outro_contexto_nao_herda_o_aumento_pendente():
    track = replace(
        fx.track(load=40), pending_load_kg=Decimal("42.5"), pending_load_since=NOW, last_executed_at=NOW
    )
    resolved, own = pe.resolve_track({track.key: track}, 1, 1, "pre_fatigued")
    assert own is False and resolved.pending_load_kg is None and resolved.pending_load_since is None


def test_avaliador_arma_o_aumento_e_registra_na_evidencia_do_grupo():
    track = fx.track(exercise_id=1, equipment_id=1, muscle_group_id=10, load=40)
    group = fx.group(muscle_group_id=10, phase=pe.F2, phase_entered_at=NOW - timedelta(days=30))
    session = fx.session(activity_id=1, day="2026-09-10", executions=[fx.execution(set_list=fx.sets(reps=[17, 17, 11], rpe=10))])
    result = ev.evaluate_session(
        session, [session], group_states={10: group}, tracks={track.key: track}, inputs=fx.inputs(tracks=[track])
    )
    novo = result.tracks[track.key]
    assert novo.pending_load_kg == Decimal("42.5")
    evidence = result.decisions[0].evidence
    assert evidence[pe.EVIDENCE_EXCESS_REPS_FAST_TRACK] == {
        "1:1": {"status": "armed", "from_kg": 40.0, "target_kg": 42.5}
    }
    # ajuste da trilha: o grupo nao muda de fase nem ganha acao pendente
    assert result.group_states[10].phase == pe.F2
    assert result.group_states[10].pending_action is None


# ---------------------------------------------------------------------------
# P5: efetivacao do aumento condicionada a carga executada. Desde o Lote 4/P6
# (2026-10-11) o aumento por plato tambem e DA TRILHA (mesmo mecanismo); o
# `INCREASE_LOAD` do grupo so existe como acao legada (ver
# test_progression_grupo_d2.py).
# ---------------------------------------------------------------------------


def _trilha_com_aumento_por_plato(since=NOW):
    return replace(
        fx.track(load=40),
        pending_load_kg=Decimal("42.5"),
        pending_load_since=since,
        pending_load_source=pe.PENDING_LOAD_SOURCE_PLATEAU,
    )


def test_execucao_na_carga_antiga_nao_marca_o_aumento_por_plato_como_feito():
    track = _trilha_com_aumento_por_plato()
    depois = NOW + timedelta(days=3)
    antiga = ev.update_track_execution(track, fx.execution(set_list=fx.sets(weight=40)), now=depois, reps_min=10)
    assert antiga.plateau_load_applied_at is None
    assert antiga.pending_load_kg == Decimal("42.5")
    nova = ev.update_track_execution(track, fx.execution(set_list=fx.sets(weight="42.5")), now=depois, reps_min=10)
    assert nova.plateau_load_applied_at == depois
    assert (nova.pending_load_kg, nova.pending_load_source) == (None, None)
    # expirado sem subir: sai sem a marca
    expirou = ev.update_track_execution(
        track, fx.execution(set_list=fx.sets(weight=40)), now=NOW + timedelta(days=14), reps_min=10
    )
    assert (expirou.pending_load_kg, expirou.plateau_load_applied_at) == (None, None)


def test_lote7_d1_serie_isolada_na_carga_alvo_nao_efetiva_o_aumento():
    """Lote 7/D1: a efetivacao (carga executada >= alvo) usa a mesma carga da
    sessao - uma serie isolada na carga alvo nao a efetiva; duas de tres sim."""
    track = _trilha_com_aumento_por_plato()
    depois = NOW + timedelta(days=3)
    isolada = fx.execution(set_list=fx.sets(reps=12, rpe=10, weight=[40, 40, "42.5"]))
    segue = ev.update_track_execution(track, isolada, now=depois, reps_min=10)
    assert (segue.pending_load_kg, segue.plateau_load_applied_at) == (Decimal("42.5"), None)
    assert segue.working_load_kg == Decimal("40")
    repetida = fx.execution(set_list=fx.sets(reps=12, rpe=10, weight=[40, "42.5", "42.5"]))
    feita = ev.update_track_execution(track, repetida, now=depois, reps_min=10)
    assert (feita.pending_load_kg, feita.plateau_load_applied_at) == (None, depois)
    assert feita.working_load_kg == Decimal("42.5")


def test_lote7_d1_back_off_leve_nao_zera_e_serie_pesada_isolada_nao_sobe_a_regua():
    """A checagem `load_below_track` olha so a carga da sessao: back-off mais
    leve depois das series na carga da trilha nao zera; uma serie isolada acima
    nao sobe a regua."""
    track = fx.track(load=40)
    back_off = _outcome(fx.sets(reps=[12, 12, 12], rpe=[9, 10, 10], weight=[40, 40, 35]), track=track)
    assert back_off.outcome == pe.QUALIFY and back_off.working_load_kg == Decimal("40")
    pesada = _outcome(fx.sets(reps=[12, 12, 12], rpe=[9, 10, 10], weight=[40, 40, 50]), track=track)
    assert pesada.outcome == pe.QUALIFY and pesada.working_load_kg == Decimal("40")
    abaixo = _outcome(fx.sets(reps=[12, 12, 12], rpe=[9, 10, 10], weight=[35, 35, 40]), track=track)
    assert abaixo.reasons == (pe.REASON_LOAD_BELOW_TRACK,)


def test_plato_da_trilha_rearma_depois_de_expirar_com_instante_novo():
    """Cenario H no nivel da trilha: 14 dias com a ficha de carga nova e a
    pessoa na carga antiga - o aumento expira sem promover a fase e o plato da
    trilha (ainda confirmado) o arma de novo."""
    track = replace(_trilha_com_aumento_por_plato(datetime(2026, 9, 1, 18, 0)), exercise_id=1, equipment_id=1, muscle_group_id=10)
    group = fx.group(muscle_group_id=10, phase=pe.F2, phase_entered_at=datetime(2026, 8, 1, 18, 0))
    qualifica = fx.sets(reps=12, rpe=10, weight=40)
    sessions = [
        fx.session(activity_id=1, day="2026-09-01", executions=[fx.execution(set_list=qualifica)]),
        fx.session(activity_id=2, day="2026-09-08", executions=[fx.execution(set_list=qualifica)]),
        fx.session(activity_id=3, day="2026-09-15", executions=[fx.execution(set_list=qualifica)]),
    ]
    result = ev.evaluate_session(
        sessions[-1], sessions, group_states={10: group}, tracks={track.key: track}, inputs=fx.inputs(tracks=[track])
    )
    novo = result.tracks[track.key]
    assert (novo.pending_load_kg, novo.pending_load_since) == (Decimal("42.5"), datetime(2026, 9, 15, 18, 0))
    assert result.group_states[10].phase == pe.F2
    assert result.group_states[10].pending_action is None
    assert result.decisions[0].evidence[pe.EVIDENCE_TRACK_PLATEAU_LOAD]["1:1"]["status"] == "armed"


def test_plato_que_rearma_o_mesmo_aumento_mantem_o_instante_original():
    since = datetime(2026, 9, 8, 18, 0)
    track = replace(_trilha_com_aumento_por_plato(since), exercise_id=1, equipment_id=1, muscle_group_id=10)
    group = fx.group(muscle_group_id=10, phase=pe.F2, phase_entered_at=datetime(2026, 8, 1, 18, 0))
    qualifica = fx.sets(reps=12, rpe=10, weight=40)
    sessions = [
        fx.session(activity_id=1, day="2026-09-01", executions=[fx.execution(set_list=qualifica)]),
        fx.session(activity_id=2, day="2026-09-08", executions=[fx.execution(set_list=qualifica)]),
        fx.session(activity_id=3, day="2026-09-12", executions=[fx.execution(set_list=qualifica)]),
    ]
    result = ev.evaluate_session(
        sessions[-1], sessions, group_states={10: group}, tracks={track.key: track}, inputs=fx.inputs(tracks=[track])
    )
    novo = result.tracks[track.key]
    assert (novo.pending_load_kg, novo.pending_load_since) == (Decimal("42.5"), since)
    assert pe.EVIDENCE_TRACK_PLATEAU_LOAD not in result.decisions[0].evidence


def test_plato_vence_o_excesso_como_origem_na_mesma_carga_alvo():
    excesso = replace(
        fx.track(load=40),
        pending_load_kg=Decimal("42.5"),
        pending_load_since=NOW,
        pending_load_source=pe.PENDING_LOAD_SOURCE_EXCESS,
    )
    plato = pe.Plateau(confirmed=True)
    armada = pe.arm_track_plateau_load(excesso, fx.group(phase=pe.F2), plato, now=NOW + timedelta(days=3))
    assert (armada.pending_load_since, pe.pending_load_source(armada)) == (NOW, pe.PENDING_LOAD_SOURCE_PLATEAU)
    # e o excesso depois nao rebaixa a origem
    rearmada = pe.arm_track_load_increase(armada, now=NOW + timedelta(days=5), source=pe.PENDING_LOAD_SOURCE_EXCESS)
    assert pe.pending_load_source(rearmada) == pe.PENDING_LOAD_SOURCE_PLATEAU
    # linha anterior a 072 (sem origem) = excesso
    assert pe.pending_load_source(replace(excesso, pending_load_source=None)) == pe.PENDING_LOAD_SOURCE_EXCESS


def test_prescribe_do_aumento_por_plato_cita_o_plato_da_trilha():
    track = _trilha_com_aumento_por_plato()
    decision = pe.prescribe(track, fx.group(phase=pe.F2), SAFETY, now=NOW + timedelta(days=3))
    assert (decision.decision, decision.suggested_load_kg) == (pe.INCREASE_LOAD, 42.5)
    assert decision.evidence[pe.EVIDENCE_TRACK_PLATEAU_LOAD]["target_kg"] == 42.5
    assert pe.EVIDENCE_EXCESS_REPS_FAST_TRACK not in decision.evidence
    assert "Plato do exercicio" in decision.reason


def test_trilha_que_ja_subiu_nao_recebe_segundo_aumento_com_acao_legada_do_grupo():
    group = fx.group(
        phase=pe.F2,
        pending_action=pe.INCREASE_LOAD,
        pending_payload={pe.PENDING_SINCE_KEY: NOW.isoformat()},
        phase_entered_at=NOW - timedelta(days=20),
    )
    subiu = replace(fx.track(load="42.5"), pending_action_applied_at=NOW + timedelta(days=2))
    decision = pe.prescribe(subiu, group, SAFETY, now=NOW + timedelta(days=4))
    assert (decision.decision, decision.suggested_load_kg) == (pe.HOLD, 42.5)


def test_acao_legada_sem_instante_usa_a_entrada_na_fase():
    legado = fx.group(pending_action=pe.INCREASE_LOAD, phase_entered_at=NOW)
    assert pe.group_pending_since(legado) == NOW


def test_d10_continua_exigindo_todas_as_series_abaixo_do_piso():
    """A troca do predicado (P4) nao mexe na D10: uma serie abaixo do piso da
    RESET `reps_below_reps_min`, mas nao conta para a regressao."""
    uma = _outcome(fx.sets(reps=[12, 12, 9], rpe=10))
    assert (uma.outcome, uma.reason, uma.below_reps_min) == (pe.RESET, pe.REASON_REPS_BELOW_MIN, False)
    assert pe.regression_triggered([uma, uma]) is False
    todas = _outcome(fx.sets(reps=[9, 8, 8], rpe=10))
    assert pe.regression_triggered([todas, todas]) is True
