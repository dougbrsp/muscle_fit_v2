"""Matriz da secao 13 do PLANO_20260912.md, bloco "Progressao (unit, tabela
parametrizada, PR-10)", sobre as funcoes PURAS de
`app/domain/progression_engine.py`. Nenhum banco.

Cada teste cita a linha da matriz que cobre.
"""

from __future__ import annotations

from dataclasses import replace

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.domain import progression_engine as pe
from app.domain.training_history import FrozenPrescription, PrescribedSpec
from app.domain.training_safety import READY, REDUCED_DOSE, NO_DIRECT_WORK
from tests.support import progression_fixtures as fx

RPE10_PAIR = 10  # par onde a falha e permitida
RPE9_PAIR = 9  # par onde a falha e PROIBIDA


# ===========================================================================
# 5.3 classificacao de series
# ===========================================================================


def test_aquecimento_e_serie_de_ajuste_sao_ignorados():
    """Matriz: "aquecimento/ajuste ignorados"."""
    mixed = (
        fx.sets(set_type="warmup", reps=15, rpe=5, weight=20, count=2)
        + fx.sets(set_type="feeler", reps=8, rpe=6, weight=30, count=1, start=3)
        + fx.sets(reps=12, rpe=10, weight=40, count=3, start=4)
    )
    classification = pe.classify_sets(mixed)
    assert len(classification.working) == 3
    assert len(classification.preparatory) == 3
    assert pe.session_working_load(classification.working) == Decimal("40")


@pytest.mark.parametrize("legacy_type", ["dropset", "drop_set", "DropSet", "drop set"])
def test_drop_set_depois_de_working_nao_vira_carga_base(legacy_type):
    """Matriz: "drop-set apos `working` (regressao do bug G2)". O `dropset`
    legado nunca foi normalizado no banco: a normalizacao e em runtime."""
    mixed = fx.sets(reps=12, rpe=10, weight=40, count=3) + fx.sets(
        set_type=legacy_type, reps=6, rpe=10, weight=20, count=1, start=4
    )
    classification = pe.classify_sets(mixed)
    assert len(classification.working) == 3
    assert [item.set_type for item in classification.technique] == ["drop_set"]
    # a carga de 20 kg do drop NAO entra no minimo das `working`
    assert pe.session_working_load(classification.working) == Decimal("40")


def test_set_type_desconhecido_gera_evidencia_e_nao_conta():
    """Matriz 5.3: "outros/desconhecido -> ignora + evidencia unknown_set_type"."""
    classification = pe.classify_sets(fx.sets(set_type="giant_set", count=2))
    assert classification.working == () and classification.has_unknown
    outcome = pe.evaluate_track_session(
        fx.execution(set_list=fx.sets(set_type="giant_set", count=2)),
        fx.track(),
        reps_min=10,
        reps_max=12,
        ceiling=10,
        failure_allowed=True,
    )
    assert pe.EVIDENCE_UNKNOWN_SET_TYPE in outcome.reasons
    assert outcome.outcome == pe.NEUTRAL  # sem series `working`, nada a julgar


def test_carga_da_sessao_e_o_minimo_das_working():
    assert pe.session_working_load(fx.sets(weight=[40, 37.5, 42.5], reps=12)) == Decimal("37.5")


# ===========================================================================
# 5.4 / 5.7 sessao qualificante
# ===========================================================================


def _outcome(
    set_list,
    *,
    track=None,
    ceiling=10,
    failure_allowed=True,
    readiness=READY,
    reps_max=12,
    reps_min=10,
    rpe_target=10,
    effort_scope="",
):
    prescribed = PrescribedSpec(sets=3, reps_min=10, reps_max=12, rpe_target=rpe_target)
    return pe.evaluate_track_session(
        fx.execution(set_list=set_list, prescribed=prescribed),
        track or fx.track(),
        effort_scope=effort_scope,
        reps_min=reps_min,
        reps_max=reps_max,
        ceiling=ceiling,
        failure_allowed=failure_allowed,
        readiness_status=readiness,
    )


@pytest.mark.parametrize(
    "caso, set_list, ceiling, failure_allowed, esperado",
    [
        # reps no teto e RPE no ceiling em TODAS as series -> qualifica
        ("teto de reps e RPE 10 em par permitido", fx.sets(reps=12, rpe=10), 10, True, pe.QUALIFY),
        ("reps acima do teto", fx.sets(reps=[13, 12, 14], rpe=10), 10, True, pe.QUALIFY),
        # Lote 3/P4: o teto de reps e cobrado so da MELHOR serie; o piso, de todas
        ("uma serie abaixo do teto de reps", fx.sets(reps=[12, 11, 12], rpe=10), 10, True, pe.QUALIFY),
        ("melhor serie abaixo do teto de reps", fx.sets(reps=[11, 11, 10], rpe=10), 10, True, pe.RESET),
        ("uma serie abaixo do piso de reps", fx.sets(reps=[12, 12, 9], rpe=10), 10, True, pe.RESET),
        # exigencia de esforco so na ULTIMA serie (D4 revisada em 2026-09-18)
        ("ultima serie abaixo do alvo de RPE", fx.sets(reps=12, rpe=[10, 10, 9]), 10, True, pe.RESET),
        ("series intermediarias abaixo do alvo", fx.sets(reps=12, rpe=[6, 7, 10]), 10, True, pe.QUALIFY),
        # par PROIBIDO: RPE 9 em todas as series qualifica INTEGRALMENTE (5.7)
        ("par proibido com RPE 9 em todas", fx.sets(reps=12, rpe=9), 9, False, pe.QUALIFY),
        # par proibido com RPE 10 registrado: clamp logico, qualifica
        ("par proibido com RPE 10 registrado", fx.sets(reps=12, rpe=10), 9, False, pe.QUALIFY),
        ("par proibido com RPE 8", fx.sets(reps=12, rpe=8), 9, False, pe.RESET),
        # ceiling menor que 9 (safety 8)
        ("ceiling 8 com RPE 8", fx.sets(reps=12, rpe=8), 8, False, pe.QUALIFY),
        ("ceiling 8 com RPE 7", fx.sets(reps=12, rpe=7), 8, False, pe.RESET),
        # RPE ausente na serie COBRADA -> NEUTRA; nas outras, irrelevante
        ("RPE ausente na ultima serie", fx.sets(reps=12, rpe=[10, 10, None]), 10, True, pe.NEUTRAL),
        ("RPE ausente numa serie intermediaria", fx.sets(reps=12, rpe=[10, None, 10]), 10, True, pe.QUALIFY),
    ],
)
def test_predicado_de_sessao_qualificante(caso, set_list, ceiling, failure_allowed, esperado):
    """Matriz: "reps no teto em todas as series (qualifica); uma serie abaixo
    do teto de reps (nao qualifica, sem punicao)"; "ultima serie no alvo de
    RPE (qualifica); ultima serie abaixo do alvo (nao qualifica)"; "par com
    RPE 10 permitido; par proibido com RPE 9 (qualifica)"; "RPE ausente
    (neutra)"."""
    assert _outcome(set_list, ceiling=ceiling, failure_allowed=failure_allowed).outcome == esperado


# ---------------------------------------------------------------------------
# D4 revisada em 2026-09-18: teto de seguranca nao e piso de exigencia
# ---------------------------------------------------------------------------


def test_equipamento_seguro_com_alvo_prescrito_menor_qualifica():
    """O caso que motivou a revisao: `max_safe_rpe=10` (equipamento SEGURO,
    pode ir a falha) com alvo prescrito 8. RPE 8 na ultima serie qualifica -
    antes exigia 10 em TODAS as series, invertendo o papel do teto."""
    outcome = _outcome(fx.sets(reps=12, rpe=8), ceiling=10, rpe_target=8)
    assert outcome.outcome == pe.QUALIFY
    assert outcome.effort_target_rpe == Decimal("8")


def test_series_intermediarias_leves_nao_impedem_a_qualificacao():
    """"E assim que se treina": as series iniciais ficam abaixo de proposito."""
    outcome = _outcome(fx.sets(reps=12, rpe=[6, 7, 8]), ceiling=10, rpe_target=8)
    assert outcome.outcome == pe.QUALIFY


def test_ultima_serie_abaixo_do_alvo_nao_qualifica_com_motivo():
    outcome = _outcome(fx.sets(reps=12, rpe=[8, 8, 7]), ceiling=10, rpe_target=8)
    assert outcome.outcome == pe.RESET
    assert outcome.reason == pe.REASON_RPE_BELOW_TARGET
    assert outcome.intensity_status == pe.INTENSITY_BELOW_TARGET


def test_teto_de_seguranca_corta_o_alvo_prescrito_para_baixo():
    """Alvo prescrito 10 num par com `max_safe_rpe=9`: exigencia cai para 9."""
    outcome = _outcome(fx.sets(reps=12, rpe=9), ceiling=9, failure_allowed=False, rpe_target=10)
    assert outcome.outcome == pe.QUALIFY
    assert outcome.effort_target_rpe == Decimal("9")
    # teto 6 com alvo 8: o teto continua cortando para baixo
    baixo = _outcome(fx.sets(reps=12, rpe=6), ceiling=6, failure_allowed=False, rpe_target=8)
    assert baixo.outcome == pe.QUALIFY and baixo.effort_target_rpe == Decimal("6")


def test_sessao_sem_alvo_prescrito_usa_o_default_clampado():
    """Treino avulso / historico legado sem `rpe_target`."""
    outcome = _outcome(fx.sets(reps=12, rpe=8), ceiling=10, rpe_target=None)
    assert outcome.outcome == pe.QUALIFY
    assert outcome.effort_target_rpe == pe.DEFAULT_EFFORT_TARGET_RPE == Decimal("8")
    # o default tambem passa pelo clamp do teto
    clampado = _outcome(fx.sets(reps=12, rpe=7), ceiling=7, failure_allowed=False, rpe_target=None)
    assert clampado.outcome == pe.QUALIFY and clampado.effort_target_rpe == Decimal("7")


def test_reps_ou_carga_abaixo_zeram_mesmo_com_a_ultima_serie_no_alvo():
    reps = _outcome(fx.sets(reps=[11, 11, 10], rpe=[6, 7, 8]), rpe_target=8)
    piso = _outcome(fx.sets(reps=[12, 12, 9], rpe=[6, 7, 8]), rpe_target=8)
    carga = _outcome(fx.sets(reps=12, rpe=[6, 7, 8], weight=37.5), rpe_target=8)
    assert (reps.outcome, reps.reason) == (pe.RESET, pe.REASON_REPS_BELOW_MAX)
    assert (piso.outcome, piso.reason) == (pe.RESET, pe.REASON_REPS_BELOW_MIN)
    assert (carga.outcome, carga.reason) == (pe.RESET, pe.REASON_LOAD_BELOW_TRACK)


def test_piso_vem_antes_do_teto_no_motivo():
    """Lote 3/P4: melhor serie abaixo do teto E uma serie abaixo do piso - o
    motivo e o piso (o sinal mais forte)."""
    outcome = _outcome(fx.sets(reps=[11, 10, 8], rpe=10))
    assert (outcome.outcome, outcome.reason) == (pe.RESET, pe.REASON_REPS_BELOW_MIN)


def test_melhor_serie_e_medida_na_carga_de_topo():
    """Lote 3/P4+P5: reps de uma serie mais LEVE nao provam a carga - a melhor
    serie e a da carga de topo da sessao."""
    leve = _outcome(fx.sets(reps=[11, 10, 15], rpe=10, weight=[40, 40, 35]))
    assert (leve.outcome, leve.reason) == (pe.RESET, pe.REASON_REPS_BELOW_MAX)
    topo = _outcome(fx.sets(reps=[12, 10, 15], rpe=10, weight=[40, 40, 35]))
    assert topo.outcome == pe.QUALIFY


def test_criterio_de_esforco_e_parametrizado():
    """Item 4: voltar a exigir o alvo em TODAS as series e uma linha."""
    set_list = fx.sets(reps=12, rpe=[6, 7, 8])
    assert _outcome(set_list, rpe_target=8).outcome == pe.QUALIFY
    todas = _outcome(set_list, rpe_target=8, effort_scope=pe.EFFORT_SCOPE_ALL_WORKING_SETS)
    assert (todas.outcome, todas.reason) == (pe.RESET, pe.REASON_RPE_BELOW_TARGET)


def test_rpe_10_antes_da_ultima_serie_nao_penaliza_nada():
    """Matriz: "RPE 10 antes da ultima (nenhuma penalidade de progressao nem
    de recuperacao)" (D4 revisada em 2026-09-14). Sem -5%, sem HOLD, sem
    evidencia negativa."""
    precoce = _outcome(fx.sets(reps=12, rpe=[10, 10, 10]))
    rampa = _outcome(fx.sets(reps=12, rpe=[9.5, 9.5, 10]))
    abaixo = _outcome(fx.sets(reps=12, rpe=[10, 10, 9.5]))
    assert precoce.outcome == pe.QUALIFY
    assert precoce.intensity_status == pe.INTENSITY_REACHED
    assert precoce.safety_events == ()  # par permite falha: nenhum evento
    assert "rpe" not in " ".join(precoce.reasons)
    # a rampa ate 10 na ultima serie qualifica (D4 revisada em 2026-09-18):
    # as series anteriores nao precisam atingir o alvo.
    assert rampa.outcome == pe.QUALIFY
    # so a ultima serie abaixo do alvo nao qualifica - e tambem nao e punida:
    # o motor nunca reduz carga por RPE 10 precoce.
    assert abaixo.outcome == pe.RESET and abaixo.safety_events == ()


def test_rpe_10_em_par_proibido_gera_safety_event_sem_elevar_alvo():
    """Matriz: "proibido com RPE 10 registrado (qualifica + safety_event, sem
    elevar alvo)" (secao 5.7)."""
    outcome = _outcome(fx.sets(reps=12, rpe=[10, 9, 10]), ceiling=9, failure_allowed=False)
    assert outcome.outcome == pe.QUALIFY
    assert outcome.intensity_status == pe.INTENSITY_ABOVE_SAFE_CEILING
    assert [event["type"] for event in outcome.safety_events] == [
        pe.SAFETY_EVENT_RPE_ABOVE_CEILING,
        pe.SAFETY_EVENT_RPE_ABOVE_CEILING,
    ]
    # o alvo futuro continua sendo o ceiling do par: nada aqui o eleva
    assert {event["ceiling"] for event in outcome.safety_events} == {9}
    assert {event["failure_denied_by"] for event in outcome.safety_events} == {"PAIR"}


def test_rpe_10_com_falha_negada_so_pela_recuperacao_e_informativo():
    """Lote 1/P1 (2026-10-11): par com falha permitida, teto 9 so pela
    recuperacao -> evento informativo e status normal (sem precedencia)."""
    prescribed = PrescribedSpec(sets=3, reps_min=10, reps_max=12, rpe_target=10)
    outcome = pe.evaluate_track_session(
        fx.execution(set_list=fx.sets(reps=12, rpe=[10, 9, 10]), prescribed=prescribed),
        fx.track(),
        reps_min=10,
        reps_max=12,
        ceiling=9,
        failure_allowed=False,
        failure_denied_by="RECOVERY",
    )
    assert outcome.outcome == pe.QUALIFY
    assert outcome.intensity_status == pe.INTENSITY_FAILURE_NOT_ALLOWED_REACHED_CEILING
    assert [event["type"] for event in outcome.safety_events] == [pe.SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET] * 2


def test_rpe_9_com_alvo_9_tem_status_de_teto_seguro_atingido():
    outcome = _outcome(fx.sets(reps=12, rpe=9), ceiling=9, failure_allowed=False)
    assert outcome.intensity_status == pe.INTENSITY_FAILURE_NOT_ALLOWED_REACHED_CEILING


def test_rpe_8_com_alvo_9_nao_qualifica_e_e_marcado_abaixo_do_alvo():
    outcome = _outcome(fx.sets(reps=12, rpe=8), ceiling=9, failure_allowed=False)
    assert outcome.outcome == pe.RESET and outcome.intensity_status == pe.INTENSITY_BELOW_TARGET


@pytest.mark.parametrize(
    "caso, kwargs, esperado",
    [
        ("dor muscular 3 nao bloqueia", {"muscle_pain": 3}, pe.QUALIFY),
        ("dor muscular 5 bloqueia", {"muscle_pain": 5}, pe.NEUTRAL),
        ("dor muscular 4 bloqueia (limite)", {"muscle_pain": 4}, pe.NEUTRAL),
        ("dor articular bloqueia", {"joint_pain": True}, pe.NEUTRAL),
        ("NULL de dor nao bloqueia", {"muscle_pain": None, "joint_pain": None}, pe.QUALIFY),
    ],
)
def test_dor_segue_o_d5(caso, kwargs, esperado):
    """Matriz: "dor muscular 3 (nao bloqueia) e 5 (bloqueia); dor articular;
    NULL de dor" (D5)."""
    assert _outcome(fx.sets(reps=12, rpe=10, **kwargs)).outcome == esperado


def test_sessao_com_menos_series_que_a_dose_e_neutra():
    """Sem ficha congelada (treino livre/legado), a dose e o `sets_count` da trilha."""
    assert _outcome(fx.sets(reps=12, rpe=10, count=2), track=fx.track(sets_count=3)).outcome == pe.NEUTRAL


def _frozen_execution(set_list, *, sets_count, decision=pe.HOLD, target=10, ceiling=10, failure=True):
    frozen = FrozenPrescription(
        sets_count=sets_count,
        effective_rpe_target=target,
        effective_rpe_ceiling=ceiling,
        failure_allowed=failure,
        decision=decision,
    )
    prescribed = PrescribedSpec(sets=3, reps_min=10, reps_max=12, rpe_target=10, frozen=frozen)
    return fx.execution(set_list=set_list, prescribed=prescribed)


def test_lote2_p3_dose_exigida_e_a_da_ficha_congelada():
    """Lote 2/P3: com ficha congelada, a dose e a da ficha daquele dia (2 series
    em dose reduzida cumprem, mesmo com a trilha em 4); faltar serie da ficha
    continua NEUTRO."""
    execution = _frozen_execution(fx.sets(reps=12, rpe=10, count=2), sets_count=2)
    outcome = pe.evaluate_track_session(
        execution, fx.track(sets_count=4), reps_min=10, reps_max=12, ceiling=10, failure_allowed=True
    )
    assert outcome.outcome == pe.QUALIFY
    falta = _frozen_execution(fx.sets(reps=12, rpe=10, count=2), sets_count=3)
    outcome = pe.evaluate_track_session(
        falta, fx.track(sets_count=2), reps_min=10, reps_max=12, ceiling=10, failure_allowed=True
    )
    assert outcome.reasons == (pe.REASON_SETS_BELOW_DOSE,)


def test_lote2_p7_alvo_de_esforco_e_o_alvo_efetivo_da_ficha():
    """Lote 2/P7: alvo efetivo congelado 9 (ex.: dose reduzida) - ultima @9 basta,
    mesmo com a linha da ficha pedindo 10."""
    execution = _frozen_execution(fx.sets(reps=12, rpe=[8, 8, 9]), sets_count=3, target=9, ceiling=9, failure=False)
    outcome = pe.evaluate_track_session(
        execution, fx.track(), reps_min=10, reps_max=12, ceiling=9, failure_allowed=False
    )
    assert outcome.outcome == pe.QUALIFY
    assert outcome.effort_target_rpe == Decimal("9")


def test_lote2_p7_item_skip_treinado_e_neutro():
    execution = _frozen_execution(fx.sets(reps=12, rpe=10), sets_count=3, decision=pe.SKIP)
    outcome = pe.evaluate_track_session(
        execution, fx.track(), reps_min=10, reps_max=12, ceiling=10, failure_allowed=True
    )
    assert outcome.outcome == pe.NEUTRAL
    assert outcome.reasons == (pe.REASON_PRESCRIPTION_SKIP,)


def test_carga_abaixo_da_trilha_zera_a_sequencia():
    outcome = _outcome(fx.sets(reps=12, rpe=10, weight=35), track=fx.track(load=40))
    assert (outcome.outcome, outcome.reason) == (pe.RESET, pe.REASON_LOAD_BELOW_TRACK)


def test_back_off_mais_leve_nao_zera_a_sequencia():
    """Lote 3/P5: a checagem de carga olha so a carga de TOPO da sessao (a maior
    que cumpriu o piso). Series mais leves depois - back-off - nao zeram
    (antes: qualquer serie abaixo da carga da trilha dava RESET
    `load_below_track`)."""
    outcome = _outcome(fx.sets(reps=12, rpe=10, weight=[40, 35, 40]), track=fx.track(load=40))
    assert outcome.outcome == pe.QUALIFY
    assert outcome.working_load_kg == Decimal("40")
    # a carga de topo que NAO cumpriu o piso nao conta: 40x9 + 35x12 -> topo 35
    abaixo = _outcome(fx.sets(reps=[9, 12, 12], rpe=10, weight=[40, 35, 35]), track=fx.track(load=40))
    assert (abaixo.outcome, abaixo.reason) == (pe.RESET, pe.REASON_LOAD_BELOW_TRACK)
    assert abaixo.working_load_kg == Decimal("35")


def test_trilha_sem_carga_ignora_o_criterio_de_carga():
    """Matriz: "peso corporal"."""
    outcome = _outcome(fx.sets(reps=12, rpe=10, weight=None), track=fx.track(load=None, load_status="LOAD_NOT_APPLICABLE"))
    assert outcome.outcome == pe.QUALIFY


def test_trilha_em_calibracao_e_neutra():
    """Matriz: "exercicio novo (calibracao)" e bloco D2 "trilha em calibracao
    e neutra"."""
    assert _outcome(fx.sets(reps=12, rpe=10), track=fx.track(load_status="CALIBRATION")).outcome == pe.NEUTRAL


@pytest.mark.parametrize("status", [REDUCED_DOSE, NO_DIRECT_WORK])
def test_sessao_com_readiness_diferente_de_ready_e_neutra(status):
    """O predicado continua neutralizando com status != READY. Desde o Lote
    2/P7 o avaliador so passa status != READY para execucoes SEM ficha
    congelada (com ficha, o contrato ja esta nela)."""
    assert _outcome(fx.sets(reps=12, rpe=10), readiness=status).outcome == pe.NEUTRAL


def test_tecnica_gravada_como_set_type_nao_impede_qualificacao():
    """Matriz: "tecnica gravada como `set_type` (nao vira carga base)"."""
    set_list = fx.sets(reps=12, rpe=10, weight=40, count=3) + fx.sets(
        set_type="rest_pause", reps=4, rpe=10, weight=40, count=1, start=4
    )
    assert _outcome(set_list).outcome == pe.QUALIFY


# ===========================================================================
# 5.4 nivel grupo (D2)
# ===========================================================================


def _o(outcome, key=(1, 1)):
    return pe.TrackSessionOutcome(key=key, outcome=outcome)


@pytest.mark.parametrize(
    "caso, outcomes, esperado",
    [
        ("duas trilhas qualificam", [_o(pe.QUALIFY, (1, 1)), _o(pe.QUALIFY, (2, 1))], pe.QUALIFY),
        ("uma trilha nao qualifica zera o grupo", [_o(pe.QUALIFY, (1, 1)), _o(pe.RESET, (2, 1))], pe.RESET),
        ("todas neutras -> grupo neutro", [_o(pe.NEUTRAL, (1, 1)), _o(pe.NEUTRAL, (2, 1))], pe.NEUTRAL),
        ("neutra + qualificante -> qualifica", [_o(pe.NEUTRAL, (1, 1)), _o(pe.QUALIFY, (2, 1))], pe.QUALIFY),
        ("nenhuma trilha executada", [], pe.NEUTRAL),
    ],
)
def test_predicado_do_grupo(caso, outcomes, esperado):
    """Lote 4/P6: o veredito de GRUPO so decide as acoes de fase sem carga; nas
    fases de carga o plato e por trilha (ver `test_plato_por_trilha_*`)."""
    assert pe.evaluate_group_session(outcomes) == esperado


# ===========================================================================
# 5.5 platô
# ===========================================================================


@pytest.mark.parametrize(
    "caso, historico, confirmado",
    [
        ("2 sessoes com 8 dias", [("2026-09-01", pe.QUALIFY), ("2026-09-09", pe.QUALIFY)], True),
        ("2 sessoes com exatamente 7 dias", [("2026-09-01", pe.QUALIFY), ("2026-09-08", pe.QUALIFY)], True),
        ("janela < 7 dias", [("2026-09-01", pe.QUALIFY), ("2026-09-05", pe.QUALIFY)], False),
        ("duas sessoes no MESMO dia", [("2026-09-01", pe.QUALIFY), ("2026-09-01", pe.QUALIFY)], False),
        ("uma so sessao qualificante", [("2026-09-01", pe.QUALIFY)], False),
        (
            "neutras no meio sao ignoradas",
            [("2026-09-01", pe.QUALIFY), ("2026-09-04", pe.NEUTRAL), ("2026-09-10", pe.QUALIFY)],
            True,
        ),
        (
            "uma sessao que zera interrompe a sequencia",
            [("2026-09-01", pe.QUALIFY), ("2026-09-04", pe.RESET), ("2026-09-10", pe.QUALIFY)],
            False,
        ),
        (
            "3 sessoes em 3 dias nao confirmam",
            [("2026-09-01", pe.QUALIFY), ("2026-09-02", pe.QUALIFY), ("2026-09-03", pe.QUALIFY)],
            False,
        ),
    ],
)
def test_plateau_confirmado(caso, historico, confirmado):
    """Matriz: "janela <7 d e >=7 d; duas sessoes no mesmo dia"."""
    plateau = pe.plateau_confirmed([(date.fromisoformat(day), outcome) for day, outcome in historico])
    assert plateau.confirmed is confirmado


# ===========================================================================
# 5.6 tabela de fases
# ===========================================================================


def _step(phase, *, tracks=None, eligible=None, **kwargs):
    tracks = tracks or [fx.track()]
    return pe.next_step(
        fx.group(phase=phase),
        tracks,
        now=datetime(2026, 9, 17, 18, 0),
        eligible_by_track=eligible if eligible is not None else {item.key: ("rest_pause",) for item in tracks},
        **kwargs,
    )


@pytest.mark.parametrize(
    "fase, acao, fase_depois",
    [
        (pe.F1, pe.SET_REP_RANGE_10_12, pe.F2),
        (pe.F2, pe.INCREASE_LOAD, pe.F3),
        (pe.F3, pe.APPLY_TECHNIQUE, pe.F4),
        (pe.F4, pe.ADD_SET, pe.F5),
        (pe.F5, pe.ADD_SET, pe.F5),
    ],
)
def test_escada_de_fases(fase, acao, fase_depois):
    """Matriz: "F1->F2, F2->F3, F3->F4, F4->F5, F5 com +1 serie ate 4".
    Lote 4/P6: o degrau F2 (carga) segue na tabela, mas com trilha de carga o
    avaliador nao o usa (`load_phase_by_track`): o aumento e por trilha e a
    F2 -> F3 sai pela maioria das trilhas."""
    step = _step(fase)
    assert (step.pending_action, step.phase_after) == (acao, fase_depois)


@pytest.mark.parametrize(
    "fase, trilhas, por_trilha",
    [
        (pe.F1, [fx.track()], False),
        (pe.F2, [fx.track()], True),
        (pe.F3, [fx.track()], False),
        (pe.F6, [fx.track()], True),
        (pe.F2, [fx.track(load=None, load_status=pe.LOAD_NOT_APPLICABLE)], False),
        (pe.F2, [fx.track(load=None, load_status=pe.LOAD_NOT_APPLICABLE), fx.track(exercise_id=2)], True),
    ],
)
def test_plato_por_trilha_nas_fases_de_carga(fase, trilhas, por_trilha):
    """Lote 4/P6: F2 e F6 com trilha de carga aplicavel = plato por trilha."""
    assert pe.TRACK_LOAD_PHASES == (pe.F2, pe.F6)
    assert pe.load_phase_by_track(fx.group(phase=fase), trilhas) is por_trilha


@pytest.mark.parametrize(
    "fase, ultima_mudanca, devido",
    [
        (pe.F2, None, True),
        (pe.F3, None, False),
        (pe.F6, datetime(2026, 9, 10, 18, 0), False),  # 7 dias < 14
        (pe.F6, datetime(2026, 9, 3, 18, 0), True),  # 14 dias
    ],
)
def test_plato_por_trilha_arma_o_aumento_da_trilha(fase, ultima_mudanca, devido):
    now = datetime(2026, 9, 17, 18, 0)
    track = fx.track(load=40, last_load_change_at=ultima_mudanca)
    armada = pe.arm_track_plateau_load(track, fx.group(phase=fase), pe.Plateau(confirmed=True), now=now)
    assert pe.track_plateau_load_due(track, fx.group(phase=fase), pe.Plateau(confirmed=True), now=now) is devido
    if devido:
        assert (armada.pending_load_kg, pe.pending_load_source(armada)) == (Decimal("42.5"), pe.PENDING_LOAD_SOURCE_PLATEAU)
    else:
        assert armada == track
    # sem plato confirmado, nada
    assert pe.arm_track_plateau_load(track, fx.group(phase=pe.F2), pe.Plateau(), now=now) == track


def test_f6_so_de_peso_corporal_nao_arma_aumento_de_grupo():
    """Lote 4/P6: na F6 sem carga aplicavel nao ha o que subir (antes armava um
    `INCREASE_LOAD` de grupo que a prescricao bloqueava)."""
    step = _step(pe.F6, tracks=[fx.track(load=None, load_status=pe.LOAD_NOT_APPLICABLE)])
    assert (step.pending_action, step.phase_after) == (None, pe.F6)
    assert pe.BLOCKED_LOAD_NOT_APPLICABLE in step.blocked_by


def test_f1_para_f2_muda_a_faixa_para_10_12():
    step = _step(pe.F1)
    assert (step.reps_min, step.reps_max) == pe.PROGRESSED_REPS


def test_f5_com_todas_as_trilhas_em_4_series_vai_para_f6():
    """Matriz: "4 series ja existentes"; bloco D2 "F6 por todas as trilhas com
    4 series"."""
    step = _step(pe.F5, tracks=[fx.track(sets_count=4), fx.track(exercise_id=2, sets_count=4)])
    assert (step.pending_action, step.phase_after) == (None, pe.F6)
    assert pe.BLOCKED_MAX_SETS in step.blocked_by


def test_f4_com_intersecao_vazia_pula_para_f5():
    """Matriz: "intersecao vazia na F4" (D8): F4 vira
    SKIPPED_NO_ELIGIBLE_TECHNIQUE e o grupo segue para a F5."""
    step = _step(pe.F3, eligible={})
    assert step.phase_status == pe.PHASE_SKIPPED_NO_ELIGIBLE_TECHNIQUE
    assert pe.BLOCKED_NO_ELIGIBLE_TECHNIQUE in step.blocked_by
    assert (step.pending_action, step.phase_after) == (pe.ADD_SET, pe.F5)


def test_f5_com_series_fixas_pelo_modelo_vira_recomendacao_e_vai_para_f6():
    """Matriz Modo B / D9: "Modo B: series fixas pelo modelo ->
    blocked_by=MODEL_FIXED_SETS, gera recomendacao de edicao e segue para F6"."""
    step = _step(pe.F4, model_fixed_sets=True)
    assert (step.pending_action, step.phase_after) == (None, pe.F6)
    assert pe.BLOCKED_MODEL_FIXED_SETS in step.blocked_by
    assert step.recommendation and "modelo" in step.recommendation.lower()


def test_f6_por_teto_de_ativacao_semanal():
    """Bloco D2: "F6 ... por ACTIVATION_MAX"."""
    step = _step(pe.F4, activation_max_reached=True)
    assert (step.phase_after, step.phase_status) == (pe.F6, pe.PHASE_BLOCKED_ACTIVATION_MAX)
    assert pe.BLOCKED_ACTIVATION_MAX in step.blocked_by


def test_trilha_sem_carga_pula_a_f3():
    """Matriz: "peso corporal"; tabela 5.6: `load_not_applicable` (trilha sem
    carga pula para F4)."""
    step = _step(pe.F2, tracks=[fx.track(load=None, load_status=pe.LOAD_NOT_APPLICABLE)])
    assert step.phase_status == pe.PHASE_SKIPPED_LOAD_NOT_APPLICABLE
    assert pe.BLOCKED_LOAD_NOT_APPLICABLE in step.blocked_by
    assert (step.pending_action, step.phase_after) == (pe.APPLY_TECHNIQUE, pe.F4)


@pytest.mark.parametrize(
    "dias, acao, bloqueio",
    [(13, None, pe.BLOCKED_NEXT_ELIGIBLE_AT), (14, pe.INCREASE_LOAD, None), (20, pe.INCREASE_LOAD, None)],
)
def test_f6_respeita_a_janela_de_14_dias(dias, acao, bloqueio):
    """Matriz: "F6 com janela <14 d e >=14 d"."""
    change = datetime(2026, 9, 1, 18, 0)
    step = pe.next_step(
        fx.group(phase=pe.F6, last_load_change_at=change),
        [fx.track(sets_count=4)],
        now=change.replace(day=1) + __import__("datetime").timedelta(days=dias),
        eligible_by_track={},
    )
    assert step.pending_action == acao
    assert (bloqueio in step.blocked_by) if bloqueio else step.blocked_by == ()


def test_f4_aplica_tecnica_na_ultima_trilha_elegivel():
    """Bloco D2: "F4 aplica tecnica na ultima trilha elegivel" (ordem da sessao)."""
    primeira = fx.track(exercise_id=1, sequence=1)
    ultima = fx.track(exercise_id=2, sequence=2)
    step = _step(pe.F3, tracks=[primeira, ultima], eligible={primeira.key: ("rest_pause",), ultima.key: ("drop_set",)})
    assert step.target_track_key == ultima.key and step.technique_code == "drop_set"


def test_f4_pula_a_ultima_trilha_sem_tecnica_elegivel():
    primeira = fx.track(exercise_id=1, sequence=1)
    ultima = fx.track(exercise_id=2, sequence=2)
    step = _step(pe.F3, tracks=[primeira, ultima], eligible={primeira.key: ("rest_pause",), ultima.key: ()})
    assert step.target_track_key == primeira.key


def test_f5_aplica_a_serie_na_trilha_de_maior_stimulus_weight():
    """Bloco D2: "F5 aplica a serie na trilha de maior stimulus_weight"."""
    fraca = fx.track(exercise_id=1, sequence=1, stimulus_weight=0.6)
    forte = fx.track(exercise_id=2, sequence=2, stimulus_weight=1.0)
    step = _step(pe.F4, tracks=[fraca, forte])
    assert step.target_track_key == forte.key


def test_empate_de_stimulus_weight_decide_pela_ordem_na_sessao():
    primeira = fx.track(exercise_id=1, sequence=1, stimulus_weight=1.0)
    segunda = fx.track(exercise_id=2, sequence=2, stimulus_weight=1.0)
    step = _step(pe.F4, tracks=[segunda, primeira])
    assert step.target_track_key == primeira.key


def test_f5_ignora_trilha_que_ja_tem_4_series():
    cheia = fx.track(exercise_id=1, sequence=1, stimulus_weight=1.0, sets_count=4)
    livre = fx.track(exercise_id=2, sequence=2, stimulus_weight=0.5, sets_count=3)
    assert _step(pe.F5, tracks=[cheia, livre]).target_track_key == livre.key


# ===========================================================================
# 5.6 incremento e regressao
# ===========================================================================


@pytest.mark.parametrize(
    "caso, composto, step, delta, excede",
    [
        ("composto sem step declarado", True, None, "5.0", False),
        ("isolador sem step declarado", False, None, "2.5", False),
        ("isolador com step 5", False, "5", "5", False),
        ("composto com step 5", True, "5", "5", False),
        ("isolador com step 10 (anilha grossa)", False, "10", "10", True),
        # halter de 1 kg por mao = step 2 TOTAL (D12); a regra da 5.6 eleva o
        # base 2,5 ao proximo multiplo do step, que da 4 kg totais
        ("halteres de 1 kg por mao, step 2 total (D12)", False, "2", "4", False),
        ("composto com step 2 total", True, "2", "6", True),
        ("step invalido cai no default", False, "0", "2.5", False),
    ],
)
def test_incremento_de_carga(caso, composto, step, delta, excede):
    """Matriz: "incremento com step 5 em isolador"; secao 5.6 (G) e D12 -
    carga e incremento sempre TOTAIS."""
    increment = pe.load_increment(is_compound=composto, load_increment_kg=step)
    assert increment.delta_kg == Decimal(delta)
    assert (pe.EVIDENCE_INCREMENT_EXCEEDS_RULE in increment.evidence) is excede


def test_incremento_nunca_sai_de_um_multiplo_do_step():
    increment = pe.load_increment(is_compound=True, load_increment_kg="1.25")
    assert increment.delta_kg % increment.step_kg == 0


def test_next_load_em_trilha_sem_carga_devolve_none():
    assert pe.next_load(fx.track(load=None))[0] is None


@pytest.mark.parametrize(
    "caso, outcomes, dispara",
    [
        (
            "2 sessoes consecutivas abaixo de reps_min",
            [pe.TrackSessionOutcome((1, 1), pe.RESET, below_reps_min=True)] * 2,
            True,
        ),
        ("1 sessao so", [pe.TrackSessionOutcome((1, 1), pe.RESET, below_reps_min=True)], False),
        (
            "abaixo do teto mas nao abaixo de reps_min",
            [pe.TrackSessionOutcome((1, 1), pe.RESET, below_reps_min=False)] * 2,
            False,
        ),
        (
            "sessao com dor (neutra) nao conta e nao interrompe",
            [
                pe.TrackSessionOutcome((1, 1), pe.RESET, below_reps_min=True),
                pe.TrackSessionOutcome((1, 1), pe.NEUTRAL, below_reps_min=True),
                pe.TrackSessionOutcome((1, 1), pe.RESET, below_reps_min=True),
            ],
            True,
        ),
        (
            "qualificante no meio interrompe",
            [
                pe.TrackSessionOutcome((1, 1), pe.RESET, below_reps_min=True),
                pe.TrackSessionOutcome((1, 1), pe.QUALIFY),
            ],
            False,
        ),
    ],
)
def test_regressao_d10(caso, outcomes, dispara):
    """Matriz: "regressao (D10)". Substitui o -5% por RPE 10 precoce, removido."""
    assert pe.regression_triggered(outcomes) is dispara


def test_below_reps_min_exige_todas_as_series_abaixo_do_minimo():
    abaixo = _outcome(fx.sets(reps=[8, 8, 8], rpe=10), reps_min=10)
    parcial = _outcome(fx.sets(reps=[8, 11, 8], rpe=10), reps_min=10)
    assert abaixo.below_reps_min and not parcial.below_reps_min


# ===========================================================================
# 5.9 tecnicas
# ===========================================================================


def test_tecnica_candidata_compativel_em_par_que_permite_falha():
    """Matriz: "tecnica candidata compativel"."""
    assert pe.choose_technique(fx.safety(max_safe_rpe=RPE10_PAIR), fx.CATALOG) == "rest_pause"


def test_ordem_deterministica_rest_pause_vence_drop_set():
    codes = pe.eligible_techniques(fx.safety(max_safe_rpe=RPE10_PAIR), fx.CATALOG)
    assert codes[:2] == ("rest_pause", "drop_set")


def test_tecnica_incompativel_na_matriz_e_excluida():
    """Matriz: "incompativel" (D3: sem marcacao = incompativel)."""
    marks = {"rest_pause": 0, "drop_set": 1}
    assert pe.choose_technique(fx.safety(max_safe_rpe=RPE10_PAIR, techniques=marks), fx.CATALOG) == "drop_set"


def test_sem_marcacao_nenhuma_nao_ha_tecnica_elegivel():
    assert pe.eligible_techniques(fx.safety(max_safe_rpe=RPE10_PAIR, techniques={}), fx.CATALOG) == ()


def test_requires_failure_em_par_proibido_e_invalida_mesmo_marcada_compativel():
    """Matriz: "compativel mas requires_failure com falha proibida"
    (validacao defensiva da 5.9, terminal em `training_safety`)."""
    assert pe.eligible_techniques(fx.safety(max_safe_rpe=RPE9_PAIR), fx.CATALOG) == ()


def test_tecnica_existente_na_trilha_bloqueia_nova_escolha():
    """Matriz: "tecnica existente" (uma tecnica por exercicio)."""
    safety = fx.safety(max_safe_rpe=RPE10_PAIR)
    assert pe.eligible_techniques(safety, fx.CATALOG, track=fx.track(technique_code="drop_set")) == ()


def test_apenas_auto_applicable_entra_na_progressao():
    assert set(pe.eligible_techniques(fx.safety(max_safe_rpe=RPE10_PAIR), fx.CATALOG)) == {"rest_pause", "drop_set"}


def test_experiencia_minima_filtra_tecnica_avancada():
    """D11: `is_advanced_user` -> nivel; usada SO na elegibilidade."""
    catalog = {**fx.CATALOG, "drop_set": fx.TechniqueCatalogEntry("drop_set", "requires_failure", True, "advanced", "drop_set")}
    safety = fx.safety(max_safe_rpe=RPE10_PAIR)
    assert "drop_set" not in pe.eligible_techniques(safety, catalog, is_advanced_user=False)
    assert "drop_set" in pe.eligible_techniques(safety, catalog, is_advanced_user=True)


def test_is_advanced_user_nao_afrouxa_par_proibido():
    """Matriz: "is_advanced_user=true em par proibido (restricao mantida)"."""
    proibido = fx.safety(max_safe_rpe=RPE9_PAIR)
    assert proibido.failure_allowed is False
    assert pe.eligible_techniques(proibido, fx.CATALOG, is_advanced_user=True) == ()


@pytest.mark.parametrize("status", [REDUCED_DOSE, NO_DIRECT_WORK])
def test_readiness_diferente_de_ready_nao_elege_tecnica(status):
    contract = pe.ReadinessContract(status=status, max_rpe=9, allow_failure=False)
    assert pe.eligible_techniques(fx.safety(max_safe_rpe=RPE10_PAIR), fx.CATALOG, readiness=contract) == ()


def test_dor_bloqueante_no_contrato_nao_elege_tecnica():
    contract = pe.ReadinessContract(blocking_signals=({"code": "JOINT_PAIN", "value": True},))
    assert contract.has_blocking_pain
    assert pe.eligible_techniques(fx.safety(max_safe_rpe=RPE10_PAIR), fx.CATALOG, readiness=contract) == ()


def test_modelo_com_tecnica_fixa_vence_a_progressao():
    """Secao 5.9: Modelo (Modo B) > Progressao > nenhuma."""
    code, evidence = pe.resolve_technique_authority(
        model_technique="drop_set", progression_technique="rest_pause", safety=fx.safety(max_safe_rpe=RPE10_PAIR)
    )
    assert code == "drop_set" and pe.EVIDENCE_TECHNIQUE_OVERRIDDEN_BY_MODEL in evidence


def test_tecnica_fixa_invalida_pela_seguranca_e_removida():
    code, evidence = pe.resolve_technique_authority(
        model_technique="drop_set", progression_technique=None, safety=fx.safety(max_safe_rpe=RPE9_PAIR)
    )
    assert code is None and any("MODEL_TECHNIQUE_REMOVED_BY_SAFETY" in item for item in evidence)


# ===========================================================================
# 5.8 recuperacao e 5.11 prescricao
# ===========================================================================


def test_no_direct_work_devolve_skip():
    """Matriz: "NO_DIRECT_WORK (SKIP)"."""
    contract = pe.ReadinessContract(status=NO_DIRECT_WORK, max_rpe=8, allow_failure=False, volume_multiplier=0.0)
    decision = pe.prescribe(fx.track(), fx.group(), fx.safety(), contract)
    assert decision.decision == pe.SKIP
    assert decision.blocked_by == (pe.BLOCKED_NO_DIRECT_WORK,)
    assert decision.suggested_sets is None and decision.can_progress is False


def test_reduced_dose_difere_a_acao_e_corta_series():
    """Matriz: "REDUCED_DOSE (acao diferida, dose 0,75)" (secao 5.8)."""
    contract = pe.ReadinessContract(status=REDUCED_DOSE, max_rpe=9, allow_failure=False, volume_multiplier=0.75)
    decision = pe.prescribe(
        replace(
            fx.track(sets_count=4, technique_code="rest_pause"),
            pending_load_kg=Decimal("42.5"),
            pending_load_source=pe.PENDING_LOAD_SOURCE_PLATEAU,
        ),
        fx.group(pending_action=pe.SET_REP_RANGE_10_12),
        fx.safety(max_safe_rpe=10),
        contract,
    )
    assert decision.decision == pe.HOLD  # acao pendente (grupo ou trilha) NAO e prescrita
    assert decision.suggested_load_kg == 40.0
    assert decision.suggested_sets == 3  # ceil(4 x 0,75)
    assert decision.advanced_technique is None  # sem tecnica em dose reduzida
    assert decision.effective_rpe_ceiling <= 9
    assert decision.evidence[pe.EVIDENCE_TRANSITION_DEFERRED] is True


@pytest.mark.parametrize("sets_count, esperado", [(1, 1), (2, 2), (3, 3), (4, 3)])
def test_dose_reduzida_tem_minimo_de_uma_serie(sets_count, esperado):
    contract = pe.ReadinessContract(status=REDUCED_DOSE, max_rpe=9, allow_failure=False, volume_multiplier=0.75)
    decision = pe.prescribe(fx.track(sets_count=sets_count), fx.group(), fx.safety(), contract)
    assert decision.suggested_sets == esperado


def test_ready_prescreve_o_aumento_de_carga_pendente_da_trilha():
    """Lote 4/P6: o aumento pelo plato e DA TRILHA (`pending_load_kg`, armado
    com `next_load`: +5 kg no composto, carga TOTAL)."""
    track = fx.track(load=40, is_compound=True)
    armada = pe.arm_track_plateau_load(
        track, fx.group(phase=pe.F2), pe.Plateau(confirmed=True), now=datetime(2026, 9, 17, 18, 0)
    )
    decision = pe.prescribe(armada, fx.group(phase=pe.F2), fx.safety(max_safe_rpe=10))
    assert decision.decision == pe.INCREASE_LOAD and decision.can_progress
    assert decision.suggested_load_kg == 45.0
    assert decision.evidence["load_increment_kg"] == 5.0


def test_acao_legada_de_aumento_do_grupo_ainda_e_prescrita():
    """`INCREASE_LOAD` de grupo gravado antes do Lote 4 (o avaliador o retira na
    proxima sessao): a ficha ainda o prescreve, sem segundo aumento."""
    decision = pe.prescribe(
        fx.track(load=40, is_compound=True),
        fx.group(phase=pe.F2, pending_action=pe.INCREASE_LOAD),
        fx.safety(max_safe_rpe=10),
    )
    assert decision.decision == pe.INCREASE_LOAD and decision.suggested_load_kg == 45.0


def test_faixa_de_reps_pendente_vale_para_a_trilha():
    """Bloco D2: "mudanca de faixa aplicada a todas as trilhas"."""
    decision = pe.prescribe(
        fx.track(), fx.group(phase=pe.F1, reps=(6, 8), pending_action=pe.SET_REP_RANGE_10_12), fx.safety()
    )
    assert (decision.suggested_reps_min, decision.suggested_reps_max) == (10, 12)


def test_aumento_de_carga_em_trilha_sem_carga_e_bloqueado():
    decision = pe.prescribe(
        fx.track(load=None, load_status=pe.LOAD_NOT_APPLICABLE),
        fx.group(pending_action=pe.INCREASE_LOAD),
        fx.safety(),
    )
    assert decision.decision == pe.HOLD and pe.BLOCKED_LOAD_NOT_APPLICABLE in decision.blocked_by


def test_add_set_bloqueado_por_series_fixas_do_modelo():
    decision = pe.prescribe(
        fx.track(sets_count=3), fx.group(phase=pe.F4, pending_action=pe.ADD_SET), fx.safety(), model_fixed_sets=True
    )
    assert decision.decision == pe.HOLD and pe.BLOCKED_MODEL_FIXED_SETS in decision.blocked_by
    assert decision.suggested_sets == 3


def test_acao_pendente_de_outra_trilha_nao_afeta_esta():
    """A acao de tecnica/serie e de UMA trilha do grupo por platô (5.6)."""
    decision = pe.prescribe(
        fx.track(exercise_id=1),
        fx.group(phase=pe.F4, pending_action=pe.ADD_SET, pending_payload={"track_key": [2, 1]}),
        fx.safety(),
    )
    assert decision.decision == pe.HOLD and decision.suggested_sets == 3


def test_tecnica_pendente_e_prescrita_na_trilha_alvo():
    decision = pe.prescribe(
        fx.track(exercise_id=1),
        fx.group(
            phase=pe.F3,
            pending_action=pe.APPLY_TECHNIQUE,
            pending_payload={"track_key": [1, 1], "technique_code": "rest_pause"},
        ),
        fx.safety(max_safe_rpe=10),
        catalog=fx.CATALOG,
    )
    assert decision.decision == pe.APPLY_TECHNIQUE and decision.advanced_technique == "rest_pause"


def test_regressao_pendente_reduz_a_carga_da_trilha_alvo():
    decision = pe.prescribe(
        fx.track(load=40, is_compound=True),
        fx.group(pending_action=pe.DECREASE_LOAD, pending_payload={"track_key": [1, 1], "rule": "D10"}),
        fx.safety(),
    )
    assert decision.decision == pe.DECREASE_LOAD and decision.suggested_load_kg == 35.0


def test_ceiling_em_par_proibido_nunca_chega_a_10_e_o_alvo_fica_no_default():
    """Secao 5.7: com falha proibida a ficha nunca mostra 10 no TETO.

    D4 revisada em 2026-09-18 (PR #121): sem RPE prescrito, o alvo e o
    `DEFAULT_TARGET_RPE` (8) cortado pelo teto - o teto de seguranca nunca
    vira piso de exigencia (era esse o alvo 9 de antes)."""
    decision = pe.prescribe(fx.track(), fx.group(), fx.safety(max_safe_rpe=RPE9_PAIR))
    assert decision.effective_rpe_target == 8 and decision.effective_rpe_ceiling == 9
    assert decision.failure_allowed is False
    assert decision.safety_constraints == {"max_safe_rpe": 9, "source": "equipment_default"}


def test_teto_abaixo_do_default_corta_o_alvo_sem_prescricao():
    decision = pe.prescribe(fx.track(), fx.group(), fx.safety(max_safe_rpe=6))
    assert decision.effective_rpe_target == 6 and decision.effective_rpe_ceiling == 6


def test_plano_legado_com_rpe_target_10_em_par_proibido_sofre_clamp():
    """Matriz: "plano legado rpe_target=10 em par proibido (overlay/clamp)"."""
    decision = pe.prescribe(fx.track(), fx.group(), fx.safety(max_safe_rpe=RPE9_PAIR), legacy_rpe_target=10)
    assert decision.effective_rpe_target == 9
    assert decision.evidence["safety_clamps"][0]["from"] == 10
    assert decision.evidence["safety_clamps"][0]["to"] == 9


def test_override_do_par_aparece_em_safety_constraints():
    """Precedencia override > default (a origem vai no contrato de saida)."""
    decision = pe.prescribe(fx.track(), fx.group(), fx.safety(max_safe_rpe=10, override=9))
    assert decision.safety_constraints == {"max_safe_rpe": 9, "source": "exercise_equipment_override"}


def test_contrato_de_saida_tem_exatamente_os_campos_da_secao_5_11():
    esperado = {
        "group_state_id",
        "muscle_group_id",
        "track_id",
        "engine_version",
        "phase_before",
        "phase_after",
        "decision",
        "suggested_sets",
        "suggested_reps_min",
        "suggested_reps_max",
        "suggested_load_kg",
        "advanced_technique",
        "advanced_technique_parameters",
        "failure_allowed",
        "effective_rpe_target",
        "effective_rpe_ceiling",
        "safety_constraints",
        "can_progress",
        "blocked_by",
        "evidence",
        "next_eligible_progression_at",
        "reason",
    }
    payload = pe.prescribe(fx.track(), fx.group(), fx.safety()).to_dict()
    assert set(payload) == esperado
    assert payload["engine_version"] == "progression-v2.0.0"


def test_contrato_de_recuperacao_vem_da_projecao_do_pr09():
    """Secao 5.8: a progressao consome SO o contrato, nunca sinal bruto."""
    item = {
        "muscle_group_id": 3,
        "projected_readiness_status": REDUCED_DOSE,
        "projected_recovery_pct": 68,
        "max_rpe": 9,
        "allow_failure": False,
        "volume_multiplier": 0.75,
        "blocking_signals": [{"code": "MUSCLE_PAIN", "value": 5}],
        "signals_policy": "pain_persists_until_superseded",
        "projected_for": "2026-09-15T07:00:00-03:00",
    }
    contract = pe.ReadinessContract.from_projection(item)
    assert (contract.status, contract.max_rpe, contract.allow_failure) == (REDUCED_DOSE, 9, False)
    assert contract.has_blocking_pain and contract.readiness.max_rpe == 9


@pytest.mark.parametrize(
    "signals, bloqueia",
    [
        ([{"code": "MUSCLE_PAIN", "value": 3}], False),
        ([{"code": "MUSCLE_PAIN", "value": 4}], True),
        ([{"code": "JOINT_PAIN", "value": True}], True),
        ([{"code": "MUSCLE_PAIN", "value": None}], False),
        ([{"code": "PERFORMANCE_RATIO", "value": 0.8}], False),
        ([], False),
    ],
)
def test_dor_bloqueante_do_contrato(signals, bloqueia):
    assert pe.ReadinessContract(blocking_signals=tuple(signals)).has_blocking_pain is bloqueia


def test_experiencia_derivada_da_flag():
    assert pe.experience_level(True) == "advanced" and pe.experience_level(False) == "intermediate"


# ---------------------------------------------------------------------------
# Tecnica so EXECUTADA na trilha nao vira prescricao (bug da ficha 125)
# ---------------------------------------------------------------------------

F4_APPLIED_AT = datetime(2026, 9, 1, 18, 0)
REDUCED = pe.ReadinessContract(status=REDUCED_DOSE, max_rpe=9, allow_failure=False, volume_multiplier=0.75)
APPLY_RP = {
    "pending_action": pe.APPLY_TECHNIQUE,
    "pending_payload": {"track_key": [1, 1], "technique_code": "rest_pause"},
}


@pytest.mark.parametrize(
    "track_code, group_kwargs, model_technique, readiness, esperado",
    [
        # rest-pause executado com outro Modelo + Modelo sem tecnica + F1/F2/F3
        ("rest_pause", {"phase": pe.F1}, None, pe.READY_CONTRACT, None),
        ("rest_pause", {"phase": pe.F2}, None, pe.READY_CONTRACT, None),
        ("drop_set", {"phase": pe.F3}, None, pe.READY_CONTRACT, None),
        # F5 alcancada PULANDO a F4 (sem technique_applied_at): nao vale
        ("rest_pause", {"phase": pe.F5}, None, pe.READY_CONTRACT, None),
        # tecnica do Modelo manda, com ou sem tecnica executada na trilha
        (None, {"phase": pe.F2}, "drop_set", pe.READY_CONTRACT, "drop_set"),
        ("rest_pause", {"phase": pe.F2}, "drop_set", pe.READY_CONTRACT, "drop_set"),
        # APPLY_TECHNIQUE pendente da F4 aplicado a esta trilha
        (None, {"phase": pe.F3, **APPLY_RP}, None, pe.READY_CONTRACT, "rest_pause"),
        # F4 efetivada: a tecnica escolhida continua valendo em F4, F5 e F6
        ("rest_pause", {"phase": pe.F4, "technique_applied_at": F4_APPLIED_AT}, None, pe.READY_CONTRACT, "rest_pause"),
        ("rest_pause", {"phase": pe.F5, "technique_applied_at": F4_APPLIED_AT}, None, pe.READY_CONTRACT, "rest_pause"),
        ("drop_set", {"phase": pe.F6, "technique_applied_at": F4_APPLIED_AT}, None, pe.READY_CONTRACT, "drop_set"),
        # dose reduzida: sem tecnica da progressao, mesmo com F4 legitima
        ("rest_pause", {"phase": pe.F4, "technique_applied_at": F4_APPLIED_AT}, None, REDUCED, None),
        (None, {"phase": pe.F3, **APPLY_RP}, None, REDUCED, None),
    ],
)
def test_tecnica_da_trilha_so_e_prescrita_por_decisao_da_progressao(
    track_code, group_kwargs, model_technique, readiness, esperado
):
    decision = pe.prescribe(
        fx.track(technique_code=track_code),
        fx.group(**group_kwargs),
        fx.safety(max_safe_rpe=10),
        readiness,
        catalog=fx.CATALOG,
        model_technique=model_technique,
    )
    assert decision.advanced_technique == esperado


def test_tecnica_executada_continua_gravada_na_trilha():
    """A correcao e so de LEITURA: o dado gravado nao e apagado, so deixa de
    ser prescrito fora da F4."""
    track = fx.track(technique_code="rest_pause")
    assert pe.track_technique_in_force(track, fx.group(phase=pe.F2)) is None
    assert pe.track_technique_in_force(track, None) is None
    assert track.technique_code == "rest_pause"
