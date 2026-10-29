"""A trilha adota a carga que a pessoa de fato executa (secoes 5.3, 5.4, 5.6 e
5.10 do PLANO_20260912.md; D10 para a reducao).

Bug de producao (2026-09-19, "Abdominal crunch na maquina"); os numeros abaixo
sao um cenario sintetico baseado nesse caso real: a ficha prescreveu **40 kg**,
a carga da PRIMEIRA sessao (3-8 de agosto), enquanto o historico mostrava 50,
52,5 e 55 kg. Duas causas,
as duas em `update_track_execution`:

1. `working_load_kg` so era escrito na CALIBRACAO. Nem a efetivacao do
   `INCREASE_LOAD` escrevia a carga nova na trilha, entao a trilha ficava presa
   na carga da primeira sessao para sempre - bem abaixo do executado.
2. `sets_count` copiava o numero de series executadas SEM o teto de 4 da secao
   5.6. Uma sessao com 6 series `working` (2026-08-24) deixou a trilha com
   `sets_count = 6`: dali em diante toda sessao com 2, 3 ou 5 series virou
   NEUTRA por `sets_below_prescribed_dose` e a trilha nunca mais pode
   qualificar, reiniciar a sequencia OU regredir.

O historico sintetico (`working`, cargas em kg) esta em `HISTORICO`, ja no
equipamento canonico (secao 5.1): dois aparelhos de academia distintos viram
cada um dos dois `equipment.id` abaixo.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

import app.domain.progression_engine as pe
import app.domain.progression_evaluator as ev
from tests.support import progression_fixtures as fx

CRUNCH = 27  # exercise_catalog.id
MTS = 5  # equipment.id (MTS Abdominal Crunch)
MAQUINA = 15  # equipment.id (Maquina Abdominal Crunch)
ABDOMEN = 1  # muscle_group_id PRIMARY do exercicio 27

NOW = datetime(2026, 9, 19, 12, 25)

# (dia, equipamento canonico, [(reps, carga, rpe), ...]) - so series `working`
HISTORICO = [
    ("2026-08-03", MTS, [(15, 40, 8), (13, 40, 8), (12, 40, 10)]),
    ("2026-08-08", MAQUINA, [(12, 40, 8), (14, 40, 8), (13, 40, 8)]),
    ("2026-08-12", MAQUINA, [(15, "42.5", 8), (15, 45, 8), (12, 45, "9.5")]),
    ("2026-08-16", MTS, [(15, "42.5", "7.5"), (13, 45, 8), (15, 45, 9)]),
    ("2026-08-20", MTS, [(13, "47.5", 8), (14, "47.5", 9), (10, 50, 10)]),
    ("2026-08-24", MTS, [(10, 50, 8), (10, 50, "8.5"), (13, 50, 10)]),
    # a sessao das SEIS series de trabalho, na maquina, no mesmo dia
    (
        "2026-08-24",
        MAQUINA,
        [(12, 40, 8), (12, "42.6", "8.5"), (15, "42.6", 9), (16, "42.6", 10), (12, "42.6", 10), (12, "42.6", "9.5")],
    ),
    ("2026-08-28", MTS, [(12, 50, 8), (15, 50, 9), (16, 50, 10)]),
    ("2026-09-01", MAQUINA, [(12, "52.5", None), (13, "52.5", 8), (13, "52.5", "8.5"), (11, "52.5", 9), (9, "52.5", 10)]),
    ("2026-09-05", MTS, [(15, 50, 8), (17, 50, "8.5"), (12, "52.5", 10)]),
    ("2026-09-11", MTS, [(15, "52.5", 10), (12, 55, 10)]),
    ("2026-09-19", MAQUINA, [(13, 55, 10), (11, 55, 10)]),
]

TEMPLATE_TRACKS = [
    fx.track(exercise_id=CRUNCH, equipment_id=MTS, muscle_group_id=ABDOMEN, sequence=1),
    fx.track(exercise_id=CRUNCH, equipment_id=MAQUINA, muscle_group_id=ABDOMEN, sequence=2),
]


def _sessions():
    """A historia sintetica como `SessionRecord`s, uma sessao por dia+equipamento
    (as duas de 2026-08-24 sao a MESMA sessao no banco: viram um `SessionRecord`
    com duas execucoes)."""
    by_day: dict[str, list] = {}
    for day, equipment_id, series in HISTORICO:
        set_list = fx.sets(
            reps=[item[0] for item in series],
            weight=[item[1] for item in series],
            rpe=[item[2] for item in series],
        )
        if day == "2026-09-19":  # a sessao comeca com um aquecimento de 7,5 kg
            set_list = fx.sets(set_type="warmup", reps=12, weight="7.5", rpe=None, count=1) + fx.sets(
                reps=[item[0] for item in series],
                weight=[item[1] for item in series],
                rpe=[item[2] for item in series],
                start=2,
            )
        by_day.setdefault(day, []).append(
            fx.execution(
                exercise_id=CRUNCH,
                equipment_id=equipment_id,
                sequence=1 if equipment_id == MTS else 2,
                set_list=set_list,
            )
        )
    return [
        fx.session(activity_id=index, day=day, executions=executions)
        for index, (day, executions) in enumerate(sorted(by_day.items()), start=1)
    ]


# ===========================================================================
# o cenario sintetico baseado no caso real, ponta a ponta
# ===========================================================================


def test_caso_real_exercicio_27_nao_prescreve_mais_a_carga_inicial():
    """Reproducao do bug: com o historico, as duas trilhas terminavam em
    40 kg (a carga de 3-8 de agosto). Agora terminam na carga executada."""
    result = ev.evaluate_history(_sessions(), inputs=fx.inputs(tracks=TEMPLATE_TRACKS))
    mts = result.tracks[pe.track_key(CRUNCH, MTS, "rested")]
    maquina = result.tracks[pe.track_key(CRUNCH, MAQUINA, "rested")]

    # Lote 3/P5: carga de TOPO que cumpriu o piso (12x55 em 2026-09-11; antes o
    # minimo, 52,5)
    assert mts.working_load_kg == Decimal("55")
    assert maquina.working_load_kg == Decimal("55")  # 13x55 e 11x55 de 2026-09-19
    assert mts.load_status == pe.ACTIVE and maquina.load_status == pe.ACTIVE


def test_caso_real_a_ficha_nunca_prescreve_menos_do_que_a_ultima_execucao():
    """O principio: `prescribe` nao pode devolver menos do que a pessoa acabou
    de levantar. Antes devolvia 40 kg contra 55 kg executados."""
    result = ev.evaluate_history(_sessions(), inputs=fx.inputs(tracks=TEMPLATE_TRACKS))
    track = result.tracks[pe.track_key(CRUNCH, MAQUINA, "rested")]
    group = result.group_states[ABDOMEN]
    decision = pe.prescribe(track, group, fx.safety(exercise_id=CRUNCH, equipment_id=MAQUINA))
    assert decision.suggested_load_kg >= 55.0


def test_caso_real_series_extras_nao_mudam_o_contador_da_trilha():
    """A sessao de 2026-08-24 na maquina teve 6 series `working` e deixava
    `sets_count = 6`; depois do teto, 4. Lote 2/P3
    (2026-10-11): series extras nao mudam mais `sets_count` - a trilha fica
    nas series do Modelo (3) e so `ADD_SET` sobe."""
    result = ev.evaluate_history(_sessions(), inputs=fx.inputs(tracks=TEMPLATE_TRACKS))
    template = {track.key: track.sets_count for track in TEMPLATE_TRACKS}
    for key, track in result.tracks.items():
        assert track.sets_count == template[key]
    assert result.tracks[pe.track_key(CRUNCH, MAQUINA, "rested")].sets_count == 3


def test_caso_real_registra_a_evidencia_da_adocao_na_decisao():
    """Secao 14: a carga mudou sem decisao do motor, entao a decisao tem de
    dizer de onde ela veio."""
    result = ev.evaluate_history(_sessions(), inputs=fx.inputs(tracks=TEMPLATE_TRACKS))
    adopted = [
        decision.evidence[pe.EVIDENCE_LOAD_ADOPTED_FROM_EXECUTION]
        for decision in result.decisions
        if pe.EVIDENCE_LOAD_ADOPTED_FROM_EXECUTION in decision.evidence
    ]
    assert adopted, "nenhuma decisao registrou a adocao da carga executada"
    ultima = adopted[-1][f"{CRUNCH}:{MAQUINA}"]
    assert ultima == {"from_kg": 52.5, "to_kg": 55.0, "source": pe.LOAD_ADOPTION_SOURCE}


def test_caso_real_o_replay_continua_idempotente():
    """A correcao nao pode quebrar "rodar duas vezes = mesmo estado", que e o
    contrato do `rebuild_progression_tracks.py`."""
    inputs = fx.inputs(tracks=TEMPLATE_TRACKS)
    primeiro = ev.evaluate_history(_sessions(), inputs=inputs)
    segundo = ev.evaluate_history(_sessions(), inputs=inputs)
    assert primeiro.tracks == segundo.tracks
    assert primeiro.group_states == segundo.group_states
    assert [d.to_dict() for d in primeiro.decisions] == [d.to_dict() for d in segundo.decisions]


# ===========================================================================
# adocao: quando sim, quando nao
# ===========================================================================


def test_carga_executada_maior_que_a_da_trilha_e_adotada():
    track = fx.track(exercise_id=CRUNCH, equipment_id=MAQUINA, muscle_group_id=ABDOMEN, load=35)
    execution = fx.execution(
        exercise_id=CRUNCH, equipment_id=MAQUINA, set_list=fx.sets(reps=12, rpe=9, weight=50, count=3)
    )
    updated = ev.update_track_execution(track, execution, now=NOW, reps_min=10)
    assert updated.working_load_kg == Decimal("50")
    assert updated.last_load_change_at == NOW


def test_a_carga_adotada_e_a_de_topo_que_cumpriu_o_piso_e_se_repetiu():
    """Lote 3/P5 (2026-10-11): a carga da sessao e a MAIOR carga de trabalho
    cuja serie cumpriu `reps_min` (antes: o minimo das `working`, secao 5.3).
    Lote 7/D1: e ela precisa se repetir em ceil(n/2) das series - 50 kg e uma
    serie so de 3; 47,5 kg ou mais sao 2 de 3 e cumpriram o piso."""
    track = fx.track(exercise_id=CRUNCH, equipment_id=MAQUINA, muscle_group_id=ABDOMEN, load=35)
    execution = fx.execution(
        exercise_id=CRUNCH,
        equipment_id=MAQUINA,
        set_list=fx.sets(reps=[13, 11, 12], rpe=[9, 9, 10], weight=[45, 50, "47.5"]),
    )
    updated = ev.update_track_execution(track, execution, now=NOW, reps_min=10)
    assert updated.working_load_kg == Decimal("47.5")


def test_carga_de_topo_abaixo_do_piso_nao_e_a_carga_da_sessao():
    """Lote 3/P5: a serie mais pesada que NAO cumpriu o piso (50x8) nao define a
    carga; vale a maior que cumpriu (45x12)."""
    track = fx.track(exercise_id=CRUNCH, equipment_id=MAQUINA, muscle_group_id=ABDOMEN, load=35)
    execution = fx.execution(
        exercise_id=CRUNCH,
        equipment_id=MAQUINA,
        set_list=fx.sets(reps=[12, 12, 8], rpe=[9, 9, 10], weight=[45, 45, 50]),
    )
    updated = ev.update_track_execution(track, execution, now=NOW, reps_min=10)
    assert updated.working_load_kg == Decimal("45")
    assert pe.session_working_load(execution.sets, 10) == Decimal("45")
    # sem piso conhecido (ou ninguem no piso): o minimo, como antes
    assert pe.session_working_load(execution.sets) == Decimal("45")
    assert pe.session_working_load(fx.sets(reps=[8, 7], weight=[50, 45]), 10) == Decimal("45")


def _load(reps, weight, reps_min=10):
    return pe.session_working_load(fx.sets(reps=reps, rpe=[10] * len(reps), weight=weight), reps_min)


def test_lote7_d1_carga_da_sessao_exige_a_carga_de_topo_repetida():
    """Lote 7/D1 (2026-10-11, decisao do usuario): carga da sessao = maior L
    com >= ceil(n * TOP_LOAD_MIN_SHARE_OF_WORKING_SETS) series em L ou acima e
    ao menos uma delas no piso."""
    # 2 de 4 series em 55 e 13x55 cumpriu o piso
    assert _load([16, 13, 9, 6], [50, 55, 55, 50]) == Decimal("55")
    # so 1 de 3 em 45: a serie pesada isolada nao vira a carga
    assert _load([12, 12, 12], [35, 35, 45]) == Decimal("35")
    # as de 45 nao cumpriram o piso; >= 40 sao 4 de 4 e cumprem
    assert _load([10, 10, 8, 8], [40, 40, 45, 45]) == Decimal("40")
    # uma serie so: ela mesma (ceil(1/2) = 1)
    assert _load([12], [50]) == Decimal("50")
    # ninguem no piso: fallback do minimo (M77 guarda a adocao)
    assert _load([8, 7, 6], [50, 55, 55]) == Decimal("50")
    assert pe.top_load_min_sets(4) == 2 and pe.top_load_min_sets(3) == 2 and pe.top_load_min_sets(1) == 1


def test_lote7_d1_serie_pesada_isolada_nao_e_adotada():
    track = fx.track(exercise_id=CRUNCH, equipment_id=MAQUINA, muscle_group_id=ABDOMEN, load=35)
    execution = fx.execution(
        exercise_id=CRUNCH,
        equipment_id=MAQUINA,
        set_list=fx.sets(reps=[12, 12, 12], rpe=[8, 9, 10], weight=[35, 35, 45]),
    )
    updated = ev.update_track_execution(track, execution, now=NOW, reps_min=10)
    assert updated.working_load_kg == Decimal("35")
    assert pe.adopted_working_load(track, execution.sets, reps_min=10) is None


def test_carga_menor_isolada_nao_derruba_a_trilha():
    """Reducao nunca vem da execucao: e da D10, para nao derrubar a carga por
    um dia ruim. Cenario sintetico baseado no caso real de 2026-08-24 na maquina
    (uma serie mais leve com a trilha em 37,5)."""
    track = fx.track(
        exercise_id=CRUNCH, equipment_id=MAQUINA, muscle_group_id=ABDOMEN, load="37.5", sets_count=3
    )
    execution = fx.execution(
        exercise_id=CRUNCH,
        equipment_id=MAQUINA,
        set_list=fx.sets(reps=[12, 12, 15], rpe=[8, 9, 10], weight=[35, "37.5", "37.5"]),
    )
    updated = ev.update_track_execution(track, execution, now=NOW, reps_min=10)
    assert updated.working_load_kg == Decimal("37.5")
    assert updated.last_load_change_at is None
    # a sessao inteira mais leve tambem nao derruba
    leve = fx.execution(exercise_id=CRUNCH, equipment_id=MAQUINA, set_list=fx.sets(reps=12, rpe=9, weight=35))
    assert ev.update_track_execution(track, leve, now=NOW, reps_min=10).working_load_kg == Decimal("37.5")


def test_carga_maior_com_todas_as_series_abaixo_de_reps_min_nao_e_adotada():
    """Teste de carga (5/4/4 reps na faixa 10-12, longe do piso) nao e carga de
    trabalho: a trilha nao adota. Desde o M77 a carga abaixo do piso PERTO
    dele e adotada (ver `test_progression_m77_adocao_abaixo_do_piso.py`)."""
    track = fx.track(exercise_id=CRUNCH, equipment_id=MAQUINA, muscle_group_id=ABDOMEN, load=35)
    execution = fx.execution(
        exercise_id=CRUNCH, equipment_id=MAQUINA, set_list=fx.sets(reps=[5, 4, 4], rpe=[10, 10, 10], weight=60)
    )
    updated = ev.update_track_execution(track, execution, now=NOW, reps_min=10)
    assert updated.working_load_kg == Decimal("35")


def test_drop_set_mais_pesado_que_a_trilha_nao_vira_carga_base():
    """Regressao do bug G2 no caminho da adocao: series de tecnica NUNCA sao
    carga base, nem quando sao as mais pesadas da sessao."""
    track = fx.track(exercise_id=CRUNCH, equipment_id=MAQUINA, muscle_group_id=ABDOMEN, load=35)
    execution = fx.execution(
        exercise_id=CRUNCH,
        equipment_id=MAQUINA,
        set_list=fx.sets(reps=12, rpe=9, weight=35, count=3)
        + fx.sets(set_type="drop_set", reps=8, rpe=10, weight=60, count=1, start=4)
        + fx.sets(set_type="dropset", reps=6, rpe=10, weight=55, count=1, start=5),
    )
    updated = ev.update_track_execution(track, execution, now=NOW, reps_min=10)
    assert updated.working_load_kg == Decimal("35")
    assert updated.technique_code == "drop_set"


def test_peso_corporal_continua_sem_carga():
    """Trilha `LOAD_NOT_APPLICABLE` nao adota nada (secao 5.10)."""
    track = fx.track(
        exercise_id=CRUNCH,
        equipment_id=MAQUINA,
        muscle_group_id=ABDOMEN,
        load=None,
        load_status=pe.LOAD_NOT_APPLICABLE,
    )
    execution = fx.execution(
        exercise_id=CRUNCH, equipment_id=MAQUINA, set_list=fx.sets(reps=12, rpe=9, weight=20, count=3)
    )
    updated = ev.update_track_execution(track, execution, now=NOW, reps_min=10)
    assert updated.load_status == pe.LOAD_NOT_APPLICABLE and updated.working_load_kg is None


def test_trilha_em_calibracao_usa_o_caminho_da_calibracao():
    """A 1a sessao define L = carga de topo que cumpriu o piso (Lote 3/P5;
    antes o minimo das `working`); a adocao nao se aplica."""
    track = fx.track(
        exercise_id=CRUNCH, equipment_id=MAQUINA, muscle_group_id=ABDOMEN, load=None, load_status=pe.CALIBRATION
    )
    execution = fx.execution(
        exercise_id=CRUNCH, equipment_id=MAQUINA, set_list=fx.sets(reps=12, rpe=9, weight=[45, 50, 50])
    )
    updated = ev.update_track_execution(track, execution, now=NOW, reps_min=10)
    assert updated.load_status == pe.ACTIVE and updated.working_load_kg == Decimal("50")


# ===========================================================================
# teto de series
# ===========================================================================


@pytest.mark.parametrize(("executadas", "esperado"), [(2, 3), (3, 3), (4, 3), (5, 3), (6, 3)])
def test_sets_count_nao_acompanha_a_execucao(executadas, esperado):
    """Lote 2/P3: a execucao nunca muda `sets_count` (antes subia ate 4)."""
    track = fx.track(exercise_id=CRUNCH, equipment_id=MAQUINA, muscle_group_id=ABDOMEN, load=35, sets_count=3)
    execution = fx.execution(
        exercise_id=CRUNCH,
        equipment_id=MAQUINA,
        set_list=fx.sets(reps=12, rpe=9, weight=35, count=executadas),
    )
    updated = ev.update_track_execution(track, execution, now=NOW, reps_min=10)
    assert updated.sets_count == esperado


def test_sessao_com_muitas_series_nao_congela_a_trilha_em_neutra():
    """O efeito colateral do `sets_count = 6`: a trilha nao qualificava mais.
    Lote 2/P3: as sessoes de 6 series nem sobem mais o contador, e a de 4
    series continua avaliavel."""
    inputs = fx.inputs(tracks=TEMPLATE_TRACKS)
    seis = fx.execution(
        exercise_id=CRUNCH, equipment_id=MAQUINA, set_list=fx.sets(reps=[12] * 6, rpe=[8, 8, 9, 9, 10, 10], weight=40)
    )
    quatro = fx.execution(
        exercise_id=CRUNCH, equipment_id=MAQUINA, set_list=fx.sets(reps=[12] * 4, rpe=[8, 9, 9, 10], weight=40)
    )
    sessions = [
        fx.session(activity_id=1, day="2026-08-01", executions=[seis]),
        fx.session(activity_id=2, day="2026-08-08", executions=[seis]),
        fx.session(activity_id=3, day="2026-08-15", executions=[quatro]),
    ]
    parcial = ev.evaluate_history(sessions[:2], inputs=inputs)
    assert parcial.tracks[pe.track_key(CRUNCH, MAQUINA, "rested")].sets_count == 3
    ultima = ev.evaluate_session(
        sessions[2],
        sessions,
        group_states=parcial.group_states,
        tracks=parcial.tracks,
        inputs=inputs,
    )
    assert ultima.group_outcomes[ABDOMEN] != pe.NEUTRAL
    assert ultima.track_outcomes[pe.track_key(CRUNCH, MAQUINA, "rested")].reason != pe.REASON_SETS_BELOW_DOSE


# ===========================================================================
# a D10 continua valendo
# ===========================================================================


def test_duas_sessoes_abaixo_de_reps_min_continuam_gerando_decrease_load():
    """D10 intacta: a reducao continua sendo decisao do motor, com duas
    sessoes consecutivas abaixo de `reps_min` e sem dor."""
    inputs = fx.inputs(tracks=TEMPLATE_TRACKS)
    ruim = fx.execution(
        exercise_id=CRUNCH, equipment_id=MAQUINA, set_list=fx.sets(reps=[5, 4, 4], rpe=[9, 9, 10], weight=40)
    )
    sessions = [
        fx.session(
            activity_id=1,
            day="2026-08-01",
            executions=[
                fx.execution(
                    exercise_id=CRUNCH, equipment_id=MAQUINA, set_list=fx.sets(reps=12, rpe=9, weight=40, count=3)
                )
            ],
        ),
        fx.session(activity_id=2, day="2026-08-08", executions=[ruim]),
        fx.session(activity_id=3, day="2026-08-15", executions=[ruim]),
    ]
    result = ev.evaluate_history(sessions, inputs=inputs)
    group = result.group_states[ABDOMEN]
    assert group.pending_action == pe.DECREASE_LOAD
    assert group.pending_payload["rule"] == "D10"
    # e a carga da trilha nao foi adotada para cima nem para baixo pela execucao
    assert result.tracks[pe.track_key(CRUNCH, MAQUINA, "rested")].working_load_kg == Decimal("40")


def test_decrease_load_prescreve_um_incremento_a_menos():
    """A reducao da D10 desce UM incremento a partir da carga real da trilha -
    depois da correcao, a carga real e a executada."""
    track = fx.track(exercise_id=CRUNCH, equipment_id=MAQUINA, muscle_group_id=ABDOMEN, load=50)
    group = fx.group(
        muscle_group_id=ABDOMEN,
        pending_action=pe.DECREASE_LOAD,
        pending_payload={"track_key": [CRUNCH, MAQUINA], "rule": "D10"},
    )
    decision = pe.prescribe(track, group, fx.safety(exercise_id=CRUNCH, equipment_id=MAQUINA))
    assert decision.decision == pe.DECREASE_LOAD
    assert decision.suggested_load_kg == 47.5
