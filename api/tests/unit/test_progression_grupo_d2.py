"""Bloco "Progressao por grupo (D2)" da secao 13 do PLANO_20260912.md, sobre
`app/domain/progression_evaluator.py` (ainda sem banco).

A fase e do GRUPO; carga, series e tecnica sao da TRILHA. D2 revista no Lote
4/P6 (2026-10-11, decisao do usuario): o PLATO que sobe a carga e da TRILHA
(2 QUALIFY dela em >= 7 dias), o RESET de um exercicio nao zera o outro, e a
F2 -> F3 sai pela maioria (Lote 7/D2: dos EXERCICIOS, nao das trilhas)
(`docs/motores/FONTES_DA_VERDADE.md`; guarda `plato_por_trilha`). Estes testes olham o
avaliador de sessao ponta a ponta: janela de sessoes -> estado novo + decisoes.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.domain import progression_engine as pe
from app.domain import progression_evaluator as ev
from app.domain.training_history import FrozenPrescription, PrescribedSpec
from app.domain.training_safety import NO_DIRECT_WORK, REDUCED_DOSE
from tests.support import progression_fixtures as fx

PEITO, COSTAS = 10, 20
SUPINO, CRUCIFIXO, REMADA = 1, 2, 3
BANCO = 100


def _run(sessions, tracks, groups=(), **kwargs):
    """Avalia a ultima sessao da janela e devolve o `EvaluationResult`."""
    group_states, track_states = fx.state_maps(tracks, groups)
    return ev.evaluate_session(
        sessions[-1],
        sessions,
        group_states=group_states,
        tracks=track_states,
        inputs=fx.inputs(tracks=tracks, **kwargs),
    )


def _qualifying(exercise_id=SUPINO, *, sequence=1, rpe=10, reps=12, weight=40, count=3):
    return fx.execution(
        exercise_id=exercise_id,
        equipment_id=BANCO,
        sequence=sequence,
        set_list=fx.sets(reps=reps, rpe=rpe, weight=weight, count=count),
    )


# ===========================================================================
# platô no nivel do grupo
# ===========================================================================


def test_grupo_com_duas_trilhas_ambas_qualificam_cada_uma_arma_o_proprio_aumento():
    """D2 revista no Lote 4/P6 (2026-10-11): o plato que sobe a carga e DA
    TRILHA. Duas trilhas que qualificam nas duas sessoes armam CADA UMA o seu
    aumento (um passo da propria carga); o grupo nao ganha acao pendente.
    Antes: `INCREASE_LOAD` pendente no grupo."""
    tracks = [
        fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=1),
        fx.track(exercise_id=CRUCIFIXO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=2, load=20),
    ]
    sessions = [
        fx.session(
            activity_id=1,
            day="2026-09-01",
            executions=[_qualifying(SUPINO), _qualifying(CRUCIFIXO, sequence=2, weight=20)],
        ),
        fx.session(
            activity_id=2,
            day="2026-09-09",
            executions=[_qualifying(SUPINO), _qualifying(CRUCIFIXO, sequence=2, weight=20)],
        ),
    ]
    result = _run(sessions, tracks, [fx.group(muscle_group_id=PEITO, phase=pe.F2)])
    assert result.group_outcomes[PEITO] == pe.QUALIFY
    assert result.group_states[PEITO].pending_action is None
    assert result.decisions[0].decision == pe.INCREASE_LOAD
    assert result.tracks[tracks[0].key].pending_load_kg == Decimal("42.5")
    assert result.tracks[tracks[1].key].pending_load_kg == Decimal("22.5")
    assert {pe.pending_load_source(result.tracks[t.key]) for t in tracks} == {pe.PENDING_LOAD_SOURCE_PLATEAU}
    evidence = result.decisions[0].evidence
    assert evidence["span_days"] == 8
    assert evidence[pe.EVIDENCE_TRACK_PLATEAUS][f"{SUPINO}:{BANCO}"]["confirmed"] is True
    assert set(evidence[pe.EVIDENCE_TRACK_PLATEAU_LOAD]) == {f"{SUPINO}:{BANCO}", f"{CRUCIFIXO}:{BANCO}"}


def test_evidencia_grava_veredito_e_motivo_por_trilha():
    """Secao 14: `progression_decisions` precisa responder "por que bloqueou".
    Ate 2026-09-18 a evidencia saia como `{"tracks": {"1:100": "RESET"}}`."""
    tracks = [fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)]
    sessions = [fx.session(activity_id=1, day="2026-09-01", executions=[_qualifying(rpe=[10, 10, 7])])]
    result = _run(sessions, tracks, [fx.group(muscle_group_id=PEITO, phase=pe.F2)])
    gravado = result.decisions[0].evidence["tracks"][f"{SUPINO}:{BANCO}"]
    assert gravado["outcome"] == pe.RESET
    assert gravado["reason"] == pe.REASON_RPE_BELOW_TARGET
    assert gravado["effort_target_rpe"] == 10.0
    assert gravado["intensity_status"] == pe.INTENSITY_BELOW_TARGET
    # leitura tolerante ao formato antigo (decisoes ja gravadas em DEV)
    assert ev.track_evidence_outcome(gravado) == pe.RESET
    assert ev.track_evidence_outcome("NEUTRAL") == pe.NEUTRAL
    assert ev.track_evidence_reason("NEUTRAL") is None


def test_um_exercicio_que_reseta_nao_zera_a_sequencia_do_outro():
    """Lote 4/P6: o RESET do Crucifixo (11 reps) zera so a sequencia DELE. O
    Supino, com 2 QUALIFY em 8 dias, sobe a carga sem esperar. Antes: a
    sequencia do GRUPO zerava e ninguem subia."""
    tracks = [
        fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=1),
        fx.track(exercise_id=CRUCIFIXO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=2),
    ]
    sessions = [
        fx.session(activity_id=1, day="2026-09-01", executions=[_qualifying(SUPINO), _qualifying(CRUCIFIXO, sequence=2)]),
        fx.session(
            activity_id=2,
            day="2026-09-09",
            executions=[_qualifying(SUPINO), _qualifying(CRUCIFIXO, sequence=2, reps=11)],
        ),
    ]
    result = _run(sessions, tracks, [fx.group(muscle_group_id=PEITO, phase=pe.F2)])
    # o veredito de GRUPO continua RESET (vale para as acoes de fase sem carga)
    assert result.group_outcomes[PEITO] == pe.RESET
    assert result.group_states[PEITO].pending_action is None
    assert result.group_states[PEITO].phase == pe.F2
    assert result.tracks[tracks[0].key].pending_load_kg == Decimal("42.5")
    assert result.tracks[tracks[1].key].pending_load_kg is None
    plateaus = result.decisions[0].evidence[pe.EVIDENCE_TRACK_PLATEAUS]
    assert plateaus[f"{CRUCIFIXO}:{BANCO}"]["confirmed"] is False


def test_neutra_de_um_exercicio_nao_interrompe_a_sequencia_do_outro():
    """Lote 4/P6: sessoes NEUTRAS (sem RPE) no Crucifixo e no proprio Supino
    entre duas QUALIFY do Supino nao interrompem a sequencia dele."""
    tracks = [
        fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=1),
        fx.track(exercise_id=CRUCIFIXO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=2),
    ]
    sessions = [
        fx.session(activity_id=1, day="2026-09-01", executions=[_qualifying(SUPINO)]),
        fx.session(activity_id=2, day="2026-09-04", executions=[_qualifying(CRUCIFIXO, sequence=2, rpe=None)]),
        fx.session(activity_id=3, day="2026-09-05", executions=[_qualifying(SUPINO, rpe=None)]),
        fx.session(activity_id=4, day="2026-09-09", executions=[_qualifying(SUPINO)]),
    ]
    result = _run(sessions, tracks, [fx.group(muscle_group_id=PEITO, phase=pe.F2)])
    assert result.tracks[tracks[0].key].pending_load_kg == Decimal("42.5")


def test_trilha_em_calibracao_e_neutra_para_o_grupo():
    """Bloco D2: "trilha em calibracao e neutra"."""
    ativa = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=1)
    nova = fx.track(
        exercise_id=CRUCIFIXO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=2, load=None, load_status="CALIBRATION"
    )
    sessions = [
        fx.session(activity_id=1, day="2026-09-01", executions=[_qualifying(SUPINO), _qualifying(CRUCIFIXO, sequence=2, reps=6)]),
        fx.session(activity_id=2, day="2026-09-09", executions=[_qualifying(SUPINO), _qualifying(CRUCIFIXO, sequence=2, reps=6)]),
    ]
    result = _run(sessions, [ativa, nova], [fx.group(muscle_group_id=PEITO, phase=pe.F2)])
    # a trilha nova executou LONGE do teto, mas em calibracao ela e neutra:
    # o grupo continua qualificando pela trilha ativa
    assert result.group_outcomes[PEITO] == pe.QUALIFY
    # Lote 4/P6: o aumento e da trilha ativa; a calibrada nao arma nada
    assert result.group_states[PEITO].pending_action is None
    assert result.tracks[ativa.key].pending_load_kg == Decimal("42.5")
    assert result.tracks[nova.key].pending_load_kg is None
    # e a trilha nova sai da calibracao com L = minimo das `working`
    assert result.tracks[nova.key].load_status == pe.ACTIVE
    assert result.tracks[nova.key].working_load_kg == Decimal("40")


def test_todas_as_trilhas_neutras_deixam_o_grupo_neutro():
    track = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)
    sessions = [
        fx.session(activity_id=1, day="2026-09-01", executions=[_qualifying(SUPINO)]),
        fx.session(activity_id=2, day="2026-09-09", executions=[_qualifying(SUPINO, rpe=None)]),
    ]
    result = _run(sessions, [track], [fx.group(muscle_group_id=PEITO, phase=pe.F2)])
    assert result.group_outcomes[PEITO] == pe.NEUTRAL


def test_duas_sessoes_no_mesmo_dia_nao_confirmam_plato():
    """Matriz: "duas sessoes no mesmo dia"."""
    track = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)
    sessions = [
        fx.session(activity_id=1, day="2026-09-01", hour=9, executions=[_qualifying(SUPINO)]),
        fx.session(activity_id=2, day="2026-09-01", hour=19, executions=[_qualifying(SUPINO)]),
    ]
    result = _run(sessions, [track], [fx.group(muscle_group_id=PEITO, phase=pe.F2)])
    assert result.group_states[PEITO].pending_action is None
    assert result.decisions[0].evidence["span_days"] == 0


def test_exercicio_com_primary_em_outro_grupo_nao_afeta_a_fase():
    """Bloco D2: "exercicio com PRIMARY em outro grupo nao afeta a fase"."""
    peito = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=1)
    costas = fx.track(exercise_id=REMADA, equipment_id=BANCO, muscle_group_id=COSTAS, sequence=2)
    sessions = [
        fx.session(activity_id=1, day="2026-09-01", executions=[_qualifying(SUPINO), _qualifying(REMADA, sequence=2, reps=11)]),
        fx.session(activity_id=2, day="2026-09-09", executions=[_qualifying(SUPINO), _qualifying(REMADA, sequence=2, reps=11)]),
    ]
    result = _run(
        sessions,
        [peito, costas],
        [fx.group(muscle_group_id=PEITO, phase=pe.F2), fx.group(muscle_group_id=COSTAS, phase=pe.F2)],
    )
    # Costas executou 11 reps (abaixo do teto 12, mas NAO abaixo do minimo
    # 10, entao nao e regressao): a sessao zera a sequencia de Costas.
    # Peito qualifica e ganha platô; Costas (que executou longe do teto) zera -
    # cada grupo tem a sua propria sequencia
    # Lote 4/P6: o aumento e da trilha do Peito; nenhuma acao de grupo
    assert result.tracks[peito.key].pending_load_kg == Decimal("42.5")
    assert result.tracks[costas.key].pending_load_kg is None
    assert result.group_states[PEITO].pending_action is None
    assert result.group_states[COSTAS].pending_action is None


def test_grupo_sem_execucao_na_sessao_nao_e_avaliado():
    peito = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)
    costas = fx.track(exercise_id=REMADA, equipment_id=BANCO, muscle_group_id=COSTAS)
    groups = [fx.group(muscle_group_id=PEITO, phase=pe.F2), fx.group(muscle_group_id=COSTAS, phase=pe.F4)]
    sessions = [fx.session(activity_id=1, day="2026-09-01", executions=[_qualifying(SUPINO)])]
    result = _run(sessions, [peito, costas], groups)
    assert set(result.group_outcomes) == {PEITO}
    assert [decision.muscle_group_id for decision in result.decisions] == [PEITO]
    assert result.group_states[COSTAS].phase == pe.F4  # intacto


# ===========================================================================
# aplicacao e efetivacao das acoes
# ===========================================================================


def test_mudanca_de_faixa_vale_para_todas_as_trilhas_do_grupo():
    """Bloco D2: "mudanca de faixa aplicada a todas as trilhas"."""
    tracks = [
        fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=1),
        fx.track(exercise_id=CRUCIFIXO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=2),
    ]
    group = fx.group(muscle_group_id=PEITO, phase=pe.F1, reps=(6, 8), pending_action=pe.SET_REP_RANGE_10_12)
    safety = fx.safety(max_safe_rpe=10)
    for track in tracks:
        decision = pe.prescribe(track, group, safety)
        assert (decision.suggested_reps_min, decision.suggested_reps_max) == (10, 12)


def test_carga_efetivada_por_trilha_e_o_grupo_avanca_com_a_maioria():
    """Lote 4/P6: a trilha executa a carga alvo do aumento por plato dela
    (`plateau_load_applied_at`); com 1 de 1 trilha efetivada, o grupo vai a F3."""
    track = replace(
        fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO),
        pending_load_kg=Decimal("42.5"),
        pending_load_since=datetime(2026, 9, 1, 18, 0),
        pending_load_source=pe.PENDING_LOAD_SOURCE_PLATEAU,
    )
    group = fx.group(muscle_group_id=PEITO, phase=pe.F2, phase_entered_at=datetime(2026, 8, 1, 18, 0))
    sessions = [fx.session(activity_id=1, day="2026-09-05", executions=[_qualifying(SUPINO, weight="42.5")])]
    result = _run(sessions, [track], [group])
    novo = result.tracks[track.key]
    assert novo.plateau_load_applied_at == datetime(2026, 9, 5, 18, 0)
    assert (novo.pending_load_kg, novo.pending_load_source, novo.working_load_kg) == (None, None, Decimal("42.5"))
    state = result.group_states[PEITO]
    assert state.phase == pe.F3 and state.pending_action is None
    assert state.last_load_change_at is not None
    evidence = result.decisions[0].evidence
    assert evidence["effective"] is True
    assert evidence[pe.EVIDENCE_GROUP_PHASE_ADVANCE] == {
        "applied": 1,
        "total": 1,
        "share": 0.5,
        "unit": "exercise",
    }
    assert evidence[pe.EVIDENCE_TRACK_PLATEAU_LOAD] == {f"{SUPINO}:{BANCO}": {"status": "applied", "target_kg": 42.5}}


def test_aumento_pelo_excesso_nao_conta_para_a_fase_do_grupo():
    """O excesso (Lote 3/P4) sobe a carga da trilha, mas nao promove a fase:
    so o aumento por plato conta para a maioria."""
    track = replace(
        fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO),
        pending_load_kg=Decimal("42.5"),
        pending_load_since=datetime(2026, 9, 1, 18, 0),
        pending_load_source=pe.PENDING_LOAD_SOURCE_EXCESS,
    )
    group = fx.group(muscle_group_id=PEITO, phase=pe.F2, phase_entered_at=datetime(2026, 8, 1, 18, 0))
    sessions = [fx.session(activity_id=1, day="2026-09-05", executions=[_qualifying(SUPINO, weight="42.5")])]
    result = _run(sessions, [track], [group])
    assert result.tracks[track.key].plateau_load_applied_at is None
    assert result.group_states[PEITO].phase == pe.F2


@pytest.mark.parametrize(
    ("aplicadas", "total", "avanca"),
    [(1, 1, True), (1, 2, False), (2, 2, True), (1, 3, False), (2, 3, True), (2, 4, False), (3, 4, True)],
)
def test_fase_do_grupo_avanca_com_a_maioria_estrita_dos_exercicios(aplicadas, total, avanca):
    """Lote 4/P6 + Lote 7/D2: F2 -> F3 quando MAIS DA METADE dos exercicios com
    trilha ativa de carga efetivou o aumento por plato desde a entrada na fase
    (`GROUP_PHASE_ADVANCE_TRACK_SHARE`). Um exercicio por trilha aqui."""
    assert pe.GROUP_PHASE_ADVANCE_TRACK_SHARE == 0.5
    entrada = datetime(2026, 9, 1, 18, 0)
    group = fx.group(muscle_group_id=PEITO, phase=pe.F2, phase_entered_at=entrada)
    tracks = [
        replace(
            fx.track(exercise_id=index + 1, equipment_id=BANCO, muscle_group_id=PEITO),
            plateau_load_applied_at=entrada + timedelta(days=5) if index < aplicadas else None,
        )
        for index in range(total)
    ]
    progress = pe.group_load_phase_progress(group, tracks)
    assert (progress.applied, progress.total) == (aplicadas, total)
    assert pe.group_load_phase_complete(group, tracks) is avanca


def test_maioria_ignora_trilha_em_calibracao_peso_corporal_e_aumento_anterior_a_fase():
    entrada = datetime(2026, 9, 1, 18, 0)
    group = fx.group(muscle_group_id=PEITO, phase=pe.F2, phase_entered_at=entrada)
    subiu = replace(fx.track(exercise_id=1, muscle_group_id=PEITO), plateau_load_applied_at=entrada + timedelta(days=3))
    antes = replace(fx.track(exercise_id=2, muscle_group_id=PEITO), plateau_load_applied_at=entrada - timedelta(days=3))
    calibrando = fx.track(exercise_id=3, muscle_group_id=PEITO, load=None, load_status=pe.CALIBRATION)
    corpo = fx.track(exercise_id=4, muscle_group_id=PEITO, load=None, load_status=pe.LOAD_NOT_APPLICABLE)
    progress = pe.group_load_phase_progress(group, [subiu, antes, calibrando, corpo])
    assert (progress.applied, progress.total, progress.complete) == (1, 2, False)
    # INCREASE_LOAD legado do grupo: efetivo so pela mesma maioria
    legado = replace(group, pending_action=pe.INCREASE_LOAD)
    assert pe.group_pending_effective(legado, [subiu, antes], now=entrada + timedelta(days=30)) is False
    assert pe.group_pending_effective(legado, [subiu], now=entrada) is True


def test_lote7_d2_duas_trilhas_do_mesmo_exercicio_contam_uma_vez():
    """Lote 7/D2 (2026-10-11, decisao do usuario): a unidade da maioria e o
    EXERCICIO. Contextos de fadiga/equipamentos do mesmo exercicio somam UM no
    denominador - e um no numerador, se qualquer um deles subiu."""
    entrada = datetime(2026, 9, 1, 18, 0)
    group = fx.group(muscle_group_id=PEITO, phase=pe.F2, phase_entered_at=entrada)
    depois = entrada + timedelta(days=5)
    supino_descansado = replace(
        fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO), plateau_load_applied_at=depois
    )
    supino_pre = replace(
        fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO),
        fatigue_context="pre_fatigued",
        plateau_load_applied_at=depois,
    )
    crucifixo = fx.track(exercise_id=CRUCIFIXO, equipment_id=BANCO, muscle_group_id=PEITO)
    crucifixo_pre = replace(crucifixo, fatigue_context="pre_fatigued")
    # antes (Lote 4): 2 de 4 trilhas - sem maioria; agora 1 de 2 exercicios
    progress = pe.group_load_phase_progress(group, [supino_descansado, supino_pre, crucifixo, crucifixo_pre])
    assert (progress.applied, progress.total, progress.complete) == (1, 2, False)
    # so o supino ativo, em dois contextos que subiram: 1 de 1
    progress = pe.group_load_phase_progress(group, [supino_descansado, supino_pre])
    assert (progress.applied, progress.total, progress.complete) == (1, 1, True)
    assert progress.to_evidence()["unit"] == "exercise"


def test_lote7_d2_exercicio_com_uma_variacao_que_subiu_conta():
    """Basta UMA trilha do exercicio (qualquer equipamento/contexto) ter
    efetivado o aumento por plato para o exercicio contar como efetivado."""
    entrada = datetime(2026, 9, 1, 18, 0)
    group = fx.group(muscle_group_id=PEITO, phase=pe.F2, phase_entered_at=entrada)
    supino_banco = replace(
        fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO),
        plateau_load_applied_at=entrada + timedelta(days=5),
    )
    supino_smith = fx.track(exercise_id=SUPINO, equipment_id=BANCO + 1, muscle_group_id=PEITO)
    supino_pre = replace(supino_smith, fatigue_context="pre_fatigued")
    crucifixo = replace(
        fx.track(exercise_id=CRUCIFIXO, equipment_id=BANCO, muscle_group_id=PEITO),
        plateau_load_applied_at=entrada + timedelta(days=6),
    )
    crossover = fx.track(exercise_id=CRUCIFIXO + 50, equipment_id=BANCO, muscle_group_id=PEITO)
    # trilhas: 2 de 5 (sem maioria no Lote 4); exercicios: 2 de 3 -> avanca
    tracks = [supino_banco, supino_smith, supino_pre, crucifixo, crossover]
    progress = pe.group_load_phase_progress(group, tracks)
    assert (progress.applied, progress.total) == (2, 3)
    assert pe.group_load_phase_complete(group, tracks) is True
    # o aumento pelo EXCESSO continua nao contando (so `plateau_load_applied_at`)
    excesso = replace(crossover, pending_load_kg=Decimal("45"), pending_load_source=pe.PENDING_LOAD_SOURCE_EXCESS)
    assert pe.group_load_phase_progress(group, [supino_smith, excesso]).applied == 0


def test_increase_load_legado_do_grupo_sai_da_fila_e_o_plato_da_trilha_rearma():
    """Acao de grupo gravada antes do Lote 4: retirada sem promover a fase; o
    plato da propria trilha arma o aumento dela."""
    track = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)
    group = fx.group(
        muscle_group_id=PEITO,
        phase=pe.F2,
        pending_action=pe.INCREASE_LOAD,
        pending_payload={pe.PENDING_SINCE_KEY: "2026-09-01T18:00:00"},
        phase_entered_at=datetime(2026, 8, 1, 18, 0),
    )
    sessions = [
        fx.session(activity_id=1, day="2026-09-01", executions=[_qualifying(SUPINO)]),
        fx.session(activity_id=2, day="2026-09-09", executions=[_qualifying(SUPINO)]),
    ]
    result = _run(sessions, [track], [group])
    state = result.group_states[PEITO]
    assert (state.phase, state.pending_action) == (pe.F2, None)
    assert result.decisions[0].evidence[pe.EVIDENCE_GROUP_INCREASE_BY_TRACK] is True
    assert result.tracks[track.key].pending_load_kg == Decimal("42.5")


def test_dois_exercicios_progridem_em_ritmos_diferentes_e_o_grupo_avanca_com_os_dois():
    """Lote 4/P6, ponta a ponta: o Supino sobe na 2a semana; o Crucifixo
    reseta e so sobe na 4a. Com 2 trilhas, a F3 exige as duas (maioria estrita)."""
    supino = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=1)
    cruci = fx.track(exercise_id=CRUCIFIXO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=2, load=20)
    plano = [  # (dia, (carga, reps) do Supino, (carga, reps) do Crucifixo)
        ("2026-09-01", (40, 12), (20, 12)),
        ("2026-09-08", (40, 12), (20, 11)),  # Supino: plato -> 42,5. Crucifixo: RESET
        ("2026-09-15", ("42.5", 12), (20, 12)),  # Supino efetiva (1/2: a fase fica)
        ("2026-09-22", ("42.5", 12), (20, 12)),  # Supino: plato -> 45. Crucifixo: plato -> 22,5
        ("2026-09-29", (45, 12), ("22.5", 12)),  # os dois efetivam: 2/2 -> F3
    ]
    groups, tracks = fx.state_maps(
        [supino, cruci], [fx.group(muscle_group_id=PEITO, phase=pe.F2, phase_entered_at=datetime(2026, 8, 1))]
    )
    inputs = fx.inputs(tracks=[supino, cruci])
    sessions, trail = [], []
    for index, (day, (sw, sr), (cw, cr)) in enumerate(plano, start=1):
        sessions.append(
            fx.session(
                activity_id=index,
                day=day,
                executions=[
                    _qualifying(SUPINO, weight=sw, reps=sr),
                    _qualifying(CRUCIFIXO, sequence=2, weight=cw, reps=cr),
                ],
            )
        )
        result = ev.evaluate_session(sessions[-1], sessions, group_states=groups, tracks=tracks, inputs=inputs)
        groups, tracks = result.group_states, result.tracks
        trail.append((tracks[supino.key].pending_load_kg, tracks[cruci.key].pending_load_kg, groups[PEITO].phase))
    assert trail == [
        (None, None, pe.F2),
        (Decimal("42.5"), None, pe.F2),
        (None, None, pe.F2),
        (Decimal("45"), Decimal("22.5"), pe.F2),
        (None, None, pe.F3),
    ]
    assert tracks[supino.key].working_load_kg == Decimal("45")
    assert tracks[cruci.key].working_load_kg == Decimal("22.5")


def test_reducao_do_grupo_d10_segue_efetivando_por_decurso_de_14_dias():
    """`DECREASE_LOAD` (D10) mantem a regra anterior: todas executaram ou 14 dias
    desde a entrada na fase."""
    executada = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=1)
    atrasada = fx.track(exercise_id=CRUCIFIXO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=2)
    group = fx.group(
        muscle_group_id=PEITO,
        phase=pe.F2,
        pending_action=pe.DECREASE_LOAD,
        phase_entered_at=datetime(2026, 9, 1, 18, 0),
    )
    antes = pe.group_pending_effective(group, [executada, atrasada], now=datetime(2026, 9, 10, 18, 0))
    depois = pe.group_pending_effective(group, [executada, atrasada], now=datetime(2026, 9, 16, 18, 0))
    assert antes is False and depois is True


def test_f4_aplica_tecnica_na_ultima_trilha_do_grupo():
    """Bloco D2: "F4 aplica tecnica na ultima trilha elegivel"."""
    primeira = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=1)
    ultima = fx.track(exercise_id=CRUCIFIXO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=2)
    sessions = [
        fx.session(activity_id=1, day="2026-09-01", executions=[_qualifying(SUPINO), _qualifying(CRUCIFIXO, sequence=2)]),
        fx.session(activity_id=2, day="2026-09-09", executions=[_qualifying(SUPINO), _qualifying(CRUCIFIXO, sequence=2)]),
    ]
    result = _run(sessions, [primeira, ultima], [fx.group(muscle_group_id=PEITO, phase=pe.F3)], max_safe_rpe=10)
    state = result.group_states[PEITO]
    assert state.pending_action == pe.APPLY_TECHNIQUE
    assert state.pending_payload["track_key"] == pe.track_key_payload(ultima.key)
    assert state.pending_payload["technique_code"] == "rest_pause"


def test_f4_com_par_proibido_em_todas_as_trilhas_pula_para_f5():
    """D8 no nivel do grupo: sem tecnica elegivel em nenhuma trilha, a F4 e
    pulada e o grupo segue para a F5."""
    tracks = [fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)]
    sessions = [
        fx.session(activity_id=1, day="2026-09-01", executions=[_qualifying(SUPINO, rpe=9)]),
        fx.session(activity_id=2, day="2026-09-09", executions=[_qualifying(SUPINO, rpe=9)]),
    ]
    result = _run(sessions, tracks, [fx.group(muscle_group_id=PEITO, phase=pe.F3)], max_safe_rpe=9)
    state = result.group_states[PEITO]
    assert state.phase_status == pe.PHASE_SKIPPED_NO_ELIGIBLE_TECHNIQUE
    assert state.pending_action == pe.ADD_SET
    assert pe.BLOCKED_NO_ELIGIBLE_TECHNIQUE in result.decisions[0].blocked_by


def test_f5_aplica_a_serie_na_trilha_de_maior_stimulus_weight_do_grupo():
    """Bloco D2: "F5 aplica a serie na trilha de maior stimulus_weight"."""
    forte = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=1, stimulus_weight=1.0)
    fraca = fx.track(exercise_id=CRUCIFIXO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=2, stimulus_weight=0.6)
    sessions = [
        fx.session(activity_id=1, day="2026-09-01", executions=[_qualifying(SUPINO), _qualifying(CRUCIFIXO, sequence=2)]),
        fx.session(activity_id=2, day="2026-09-09", executions=[_qualifying(SUPINO), _qualifying(CRUCIFIXO, sequence=2)]),
    ]
    result = _run(sessions, [forte, fraca], [fx.group(muscle_group_id=PEITO, phase=pe.F4)])
    assert result.group_states[PEITO].pending_payload["track_key"] == pe.track_key_payload(forte.key)


def test_f6_quando_todas_as_trilhas_do_grupo_tem_4_series():
    """Bloco D2: "F6 por todas as trilhas com 4 series"."""
    tracks = [
        fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=1, sets_count=4),
        fx.track(exercise_id=CRUCIFIXO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=2, sets_count=4),
    ]
    sessions = [
        fx.session(
            activity_id=1,
            day="2026-09-01",
            executions=[_qualifying(SUPINO, count=4), _qualifying(CRUCIFIXO, sequence=2, count=4)],
        ),
        fx.session(
            activity_id=2,
            day="2026-09-09",
            executions=[_qualifying(SUPINO, count=4), _qualifying(CRUCIFIXO, sequence=2, count=4)],
        ),
    ]
    result = _run(sessions, tracks, [fx.group(muscle_group_id=PEITO, phase=pe.F5)])
    assert result.group_states[PEITO].phase == pe.F6


def test_f6_por_teto_de_ativacao_do_grupo():
    """Bloco D2: "F6 ... por ACTIVATION_MAX"."""
    tracks = [fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)]
    sessions = [
        fx.session(activity_id=1, day="2026-09-01", executions=[_qualifying(SUPINO)]),
        fx.session(activity_id=2, day="2026-09-09", executions=[_qualifying(SUPINO)]),
    ]
    result = _run(
        sessions, tracks, [fx.group(muscle_group_id=PEITO, phase=pe.F4)], activation_max_reached={PEITO: True}
    )
    state = result.group_states[PEITO]
    assert (state.phase, state.phase_status) == (pe.F6, pe.PHASE_BLOCKED_ACTIVATION_MAX)


def test_f5_com_series_fixas_pelo_modelo_vira_recomendacao():
    """D9: Modo B nao muda series do modelo; o grupo segue para F6 com a
    recomendacao no `pending_payload`."""
    tracks = [fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)]
    sessions = [
        fx.session(activity_id=1, day="2026-09-01", executions=[_qualifying(SUPINO)]),
        fx.session(activity_id=2, day="2026-09-09", executions=[_qualifying(SUPINO)]),
    ]
    result = _run(sessions, tracks, [fx.group(muscle_group_id=PEITO, phase=pe.F4)], model_fixed_sets=True)
    state = result.group_states[PEITO]
    assert state.phase == pe.F6
    assert pe.BLOCKED_MODEL_FIXED_SETS in result.decisions[0].blocked_by
    assert "modelo" in (state.pending_payload or {}).get("recommendation", "").lower()


# ===========================================================================
# troca de exercicio e de equipamento
# ===========================================================================


def test_troca_de_exercicio_no_grupo_preserva_a_fase():
    """Bloco D2 / matriz: "troca de exercicio no grupo preserva a fase"; a
    trilha nova entra em calibracao (e a vantagem do D2)."""
    antiga = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=1)
    group = fx.group(muscle_group_id=PEITO, phase=pe.F4, phase_entered_at=datetime(2026, 8, 1, 18, 0))
    sessions = [fx.session(activity_id=1, day="2026-09-01", executions=[_qualifying(CRUCIFIXO, sequence=1)])]
    nova = fx.track(exercise_id=CRUCIFIXO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=1)
    result = _run(sessions, [antiga, nova], [group])
    assert result.group_states[PEITO].phase == pe.F4  # fase preservada
    assert result.group_states[PEITO].reps_range == (10, 12)


def test_exercicio_novo_nasce_em_calibracao_e_a_primeira_sessao_define_a_carga():
    """Matriz: "exercicio novo (calibracao)" (secao 5.10)."""
    sessions = [fx.session(activity_id=1, day="2026-09-01", executions=[_qualifying(SUPINO, weight=[40, 37.5, 40])])]
    inputs = fx.inputs(tracks=[fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)])
    result = ev.evaluate_session(sessions[-1], sessions, group_states={}, tracks={}, inputs=inputs)
    track = result.tracks[pe.track_key(SUPINO, BANCO, "rested")]
    assert track.load_status == pe.ACTIVE
    # Lote 3/P5: carga de TOPO que cumpriu o piso (antes, o minimo 37,5)
    assert track.working_load_kg == Decimal("40")
    # grupo sem estado nasce em F1 com faixa 6-8
    assert result.group_states[PEITO].phase == pe.F1
    assert result.group_states[PEITO].reps_range == (6, 8)


def test_troca_de_equipamento_cria_trilha_nova():
    """Matriz: "troca de equipamento (trilha nova)". A carga NUNCA e
    compartilhada entre trilhas."""
    banco = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, load=40)
    sessions = [
        fx.session(
            activity_id=1,
            day="2026-09-01",
            executions=[
                fx.execution(exercise_id=SUPINO, equipment_id=999, set_list=fx.sets(reps=12, rpe=10, weight=25))
            ],
        )
    ]
    inputs = fx.inputs(tracks=[banco, fx.track(exercise_id=SUPINO, equipment_id=999, muscle_group_id=PEITO)])
    group_states, tracks = fx.state_maps([banco], [fx.group(muscle_group_id=PEITO, phase=pe.F2)])
    result = ev.evaluate_session(sessions[-1], sessions, group_states=group_states, tracks=tracks, inputs=inputs)
    assert result.tracks[pe.track_key(SUPINO, 999, "rested")].working_load_kg == Decimal("25")
    assert result.tracks[pe.track_key(SUPINO, BANCO, "rested")].working_load_kg == Decimal("40")  # intacta


def test_treino_avulso_sem_plano_conta_para_a_trilha():
    """Matriz: "treino sem plano" (secao 5.10): conta se o par resolveu."""
    sessions = [
        fx.session(
            activity_id=1,
            day="2026-09-01",
            executions=[
                fx.execution(
                    exercise_id=SUPINO,
                    equipment_id=BANCO,
                    workout_plan_exercise_id=None,
                    set_list=fx.sets(reps=12, rpe=10, weight=40),
                )
            ],
        )
    ]
    inputs = fx.inputs(tracks=[fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)])
    result = ev.evaluate_session(sessions[-1], sessions, group_states={}, tracks={}, inputs=inputs)
    assert pe.track_key(SUPINO, BANCO, "rested") in result.tracks


def test_par_nao_resolvido_e_ignorado():
    """Sem `exercise_id` ou `equipment_id`, nao existe trilha (secao 5.10)."""
    execution = fx.execution(exercise_id=SUPINO, equipment_id=BANCO)
    orphan = type(execution)(**{**execution.__dict__, "equipment_id": None})
    sessions = [fx.session(activity_id=1, executions=[orphan])]
    result = ev.evaluate_session(sessions[-1], sessions, group_states={}, tracks={}, inputs=fx.inputs())
    assert result.tracks == {} and result.decisions == ()


# ===========================================================================
# execucao muda a trilha
# ===========================================================================


def test_drop_set_apos_working_nao_mexe_na_carga_da_trilha():
    """Regressao do bug G2 no nivel da trilha: a carga base continua a das
    series `working`, e o drop so marca a tecnica aplicada."""
    track = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, load=None, load_status="CALIBRATION")
    execution = fx.execution(
        exercise_id=SUPINO,
        equipment_id=BANCO,
        set_list=fx.sets(reps=12, rpe=10, weight=40, count=3)
        + fx.sets(set_type="dropset", reps=6, rpe=10, weight=20, count=1, start=4),
    )
    updated = ev.update_track_execution(track, execution, now=datetime(2026, 9, 1, 18, 0))
    assert updated.working_load_kg == Decimal("40")
    assert updated.technique_code == "drop_set"


def test_series_de_trabalho_sem_carga_marcam_a_trilha_como_peso_corporal():
    """Matriz: "peso corporal" -> `LOAD_NOT_APPLICABLE` (F3/F6 pulam)."""
    track = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, load=None, load_status="CALIBRATION")
    execution = fx.execution(exercise_id=SUPINO, equipment_id=BANCO, set_list=fx.sets(reps=12, rpe=10, weight=None))
    updated = ev.update_track_execution(track, execution, now=datetime(2026, 9, 1, 18, 0))
    assert updated.load_status == pe.LOAD_NOT_APPLICABLE
    assert updated.working_load_kg is None


def test_apenas_aquecimento_nao_tira_a_trilha_da_calibracao():
    track = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, load=None, load_status="CALIBRATION")
    execution = fx.execution(
        exercise_id=SUPINO, equipment_id=BANCO, set_list=fx.sets(set_type="warmup", reps=15, rpe=5, weight=20)
    )
    updated = ev.update_track_execution(track, execution, now=datetime(2026, 9, 1, 18, 0))
    assert updated.load_status == pe.CALIBRATION and updated.working_load_kg is None


# ===========================================================================
# recuperacao e regressao no avaliador
# ===========================================================================


@pytest.mark.parametrize("status", [REDUCED_DOSE, NO_DIRECT_WORK])
def test_sessao_com_readiness_reduzido_e_neutra_para_o_grupo(status):
    """Matriz: "READY / REDUCED_DOSE / NO_DIRECT_WORK". Vale para execucao SEM
    ficha congelada (treino livre/legado); com ficha, ver o teste abaixo."""
    track = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)
    contract = pe.ReadinessContract(muscle_group_id=PEITO, status=status, max_rpe=9, allow_failure=False)
    sessions = [
        fx.session(activity_id=1, day="2026-09-01", executions=[_qualifying(SUPINO)]),
        fx.session(activity_id=2, day="2026-09-09", executions=[_qualifying(SUPINO)]),
    ]
    result = _run(sessions, [track], [fx.group(muscle_group_id=PEITO, phase=pe.F2)], readiness={PEITO: contract})
    assert result.group_outcomes[PEITO] == pe.NEUTRAL
    assert result.group_states[PEITO].pending_action is None


def _qualifying_frozen(*, sets_count=2, decision=pe.HOLD, count=2):
    frozen = FrozenPrescription(
        sets_count=sets_count, effective_rpe_target=9, effective_rpe_ceiling=9, failure_allowed=False, decision=decision
    )
    return fx.execution(
        exercise_id=SUPINO,
        equipment_id=BANCO,
        set_list=fx.sets(reps=12, rpe=9, weight=40, count=count),
        prescribed=PrescribedSpec(sets=3, reps_min=10, reps_max=12, rpe_target=10, frozen=frozen),
    )


def test_lote2_p7_dose_reduzida_que_seguiu_a_ficha_conta_para_o_grupo():
    """Lote 2/P7 (decisao do TL, 2026-10-11): ficha congelada em dose reduzida
    (2 series, alvo/teto 9) seguida a risca e avaliada contra a ficha e CONTA,
    mesmo com o contrato REDUCED_DOSE na avaliacao. A trilha (3 series) nao
    pesa: a dose e a da ficha (P3)."""
    track = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)
    contract = pe.ReadinessContract(muscle_group_id=PEITO, status=REDUCED_DOSE, max_rpe=9, allow_failure=False)
    sessions = [
        fx.session(activity_id=1, day="2026-09-01", executions=[_qualifying_frozen()]),
        fx.session(activity_id=2, day="2026-09-09", executions=[_qualifying_frozen()]),
    ]
    result = _run(sessions, [track], [fx.group(muscle_group_id=PEITO, phase=pe.F2)], readiness={PEITO: contract})
    assert result.group_outcomes[PEITO] == pe.QUALIFY
    # Lote 4/P6: o aumento e da trilha
    assert result.tracks[track.key].pending_load_kg == Decimal("42.5")


def test_lote2_p7_item_skip_treinado_e_neutro_para_o_grupo():
    track = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)
    sessions = [
        fx.session(activity_id=1, day="2026-09-01", executions=[_qualifying_frozen(decision=pe.SKIP)]),
        fx.session(activity_id=2, day="2026-09-09", executions=[_qualifying_frozen(decision=pe.SKIP)]),
    ]
    result = _run(sessions, [track], [fx.group(muscle_group_id=PEITO, phase=pe.F2)])
    assert result.group_outcomes[PEITO] == pe.NEUTRAL
    assert result.track_outcomes[track.key].reasons == (pe.REASON_PRESCRIPTION_SKIP,)
    assert result.group_states[PEITO].pending_action is None


def test_regressao_d10_registra_decrease_load_na_trilha_sem_mudar_a_fase():
    """Matriz: "regressao (D10)" ponta a ponta. A acao mora no grupo, com o
    `track_key` no payload; a fase NAO muda."""
    track = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, load=40)
    abaixo = fx.execution(
        exercise_id=SUPINO, equipment_id=BANCO, set_list=fx.sets(reps=[8, 8, 8], rpe=10, weight=40)
    )
    sessions = [
        fx.session(activity_id=1, day="2026-09-01", executions=[abaixo]),
        fx.session(activity_id=2, day="2026-09-05", executions=[abaixo]),
    ]
    result = _run(sessions, [track], [fx.group(muscle_group_id=PEITO, phase=pe.F2)])
    state = result.group_states[PEITO]
    assert state.pending_action == pe.DECREASE_LOAD
    assert state.pending_payload == {"track_key": [SUPINO, BANCO, "rested"], "rule": "D10"}
    assert state.phase == pe.F2


def test_plato_tem_precedencia_sobre_regressao():
    """Nao ha como uma sequencia qualificante e a regressao valerem juntas."""
    track = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)
    sessions = [
        fx.session(activity_id=1, day="2026-09-01", executions=[_qualifying(SUPINO)]),
        fx.session(activity_id=2, day="2026-09-09", executions=[_qualifying(SUPINO)]),
    ]
    result = _run(sessions, [track], [fx.group(muscle_group_id=PEITO, phase=pe.F2)])
    assert result.group_states[PEITO].pending_action is None
    assert result.tracks[track.key].pending_load_kg == Decimal("42.5")


def test_f6_aumento_da_trilha_respeita_o_intervalo_de_14_dias_da_propria_trilha():
    """Lote 4/P6: na F6 o aumento tambem e por trilha, a cada
    `F6_LOAD_INTERVAL_DAYS` desde a ultima mudanca de carga DA TRILHA."""
    sessions = [
        fx.session(activity_id=1, day="2026-09-01", executions=[_qualifying(SUPINO)]),
        fx.session(activity_id=2, day="2026-09-09", executions=[_qualifying(SUPINO)]),
    ]
    group = fx.group(muscle_group_id=PEITO, phase=pe.F6, phase_entered_at=datetime(2026, 8, 1))
    recente = fx.track(
        exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, last_load_change_at=datetime(2026, 9, 1)
    )
    antiga = fx.track(
        exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, last_load_change_at=datetime(2026, 8, 1)
    )
    assert _run(sessions, [recente], [group]).tracks[recente.key].pending_load_kg is None
    resultado = _run(sessions, [antiga], [group])
    assert resultado.tracks[antiga.key].pending_load_kg == Decimal("42.5")
    assert resultado.group_states[PEITO].phase == pe.F6


# ===========================================================================
# replay puro
# ===========================================================================


def test_replay_da_historia_inteira_e_deterministico():
    """`evaluate_history` com a mesma entrada da o mesmo estado (base da
    idempotencia do script de replay)."""
    inputs = fx.inputs(tracks=[fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)])
    sessions = [
        fx.session(activity_id=index, day=day, executions=[_qualifying(SUPINO)])
        for index, day in enumerate(["2026-09-01", "2026-09-09", "2026-09-17"], start=1)
    ]
    primeiro = ev.evaluate_history(sessions, inputs=inputs)
    segundo = ev.evaluate_history(sessions, inputs=inputs)
    assert primeiro.group_states == segundo.group_states
    assert primeiro.tracks == segundo.tracks
    assert [d.to_dict() for d in primeiro.decisions] == [d.to_dict() for d in segundo.decisions]


def test_replay_sobe_a_escada_de_fases_ao_longo_da_historia():
    """F1 -> F2 -> F3: cada platô sobe UM degrau, e a acao vira efetiva na
    sessao seguinte."""
    inputs = fx.inputs(tracks=[fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)])
    days = ["2026-08-01", "2026-08-09", "2026-08-12", "2026-08-20", "2026-08-23", "2026-08-31", "2026-09-03"]
    sessions = [
        fx.session(activity_id=index, day=day, executions=[_qualifying(SUPINO, reps=12, rpe=10)])
        for index, day in enumerate(days, start=1)
    ]
    result = ev.evaluate_history(sessions, inputs=inputs)
    assert result.group_states[PEITO].phase in (pe.F2, pe.F3, pe.F4)
    assert result.group_states[PEITO].reps_range == (10, 12)
    assert result.group_states[PEITO].last_rep_range_change_at is not None


def test_regressao_d10_efetivada_nao_reinicia_a_fase_de_carga():
    """Lote 4/P6: a D10 e de UMA trilha; efetivada numa fase de carga ela nao
    reinicia `phase_entered_at`, que zeraria o plato e os aumentos das outras
    trilhas. Fora das fases de carga, comportamento anterior."""
    entrada = datetime(2026, 8, 1, 18, 0)
    agora = datetime(2026, 9, 10, 18, 0)
    f2 = fx.group(muscle_group_id=PEITO, phase=pe.F2, pending_action=pe.DECREASE_LOAD, phase_entered_at=entrada)
    depois = pe.apply_effective(f2, pe.F2, now=agora, reps=(10, 12))
    assert (depois.phase_entered_at, depois.last_load_change_at, depois.pending_action) == (entrada, agora, None)
    f4 = replace(f2, phase=pe.F4)
    assert pe.apply_effective(f4, pe.F4, now=agora, reps=(10, 12)).phase_entered_at == agora
