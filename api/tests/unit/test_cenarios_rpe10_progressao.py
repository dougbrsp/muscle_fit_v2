"""Cenarios da investigacao RPE 10 / progressao travada (2026-10-11), Lote 0.

Fonte: investigacao RPE 10 de 2026-10-11 e seu harness
(PR #515; arquivo privado). Os cenarios estao em
`tests/support/cenarios_progressao.py` (DADOS + runner que so chama os motores
donos; regra "Motores: fonte unica da verdade", `docs/motores/FONTES_DA_VERDADE.md`).

Cada teste FIXA o comportamento ATUAL de producao. As asserções marcadas com
`# COMPORTAMENTO ATUAL (muda no Lote N/Pk)` sao as que o lote correspondente do
plano deve INVERTER no proprio diff, documentando a mudanca de regra:

- Lote 1 (P1/P2): RPE 10 com teto cortado so pela recuperacao vira informativo
  (aplicado: cenario G, marcado `# Lote 1/P1:`).
- Lote 2 (P7/P3): avaliacao contra a ficha congelada; dose exigida = series da ficha
  (aplicado: cenarios E, G e R invertidos, marcados `# Lote 2/Pk:`).
- Lote 3 (P4/P5): qualifica com a melhor serie em reps_max (todas >= reps_min);
  excesso de reps (melhor serie >= reps_max + 3 no RPE alvo) sobe a carga da
  trilha na ficha seguinte; carga da sessao = topo que cumpriu o piso; aumento
  so efetiva quando a carga sobe (aplicado: U, A, B, H, H2 e R invertidos,
  marcados `# Lote 3/Pk:`).
- Lote 4 (P6): plato por trilha - a trilha sobe a carga com 2 QUALIFY DELA em
  >= 7 dias (aumento pendente da trilha, origem `track_plateau`), sem acao do
  grupo; a fase vai a F3 pela maioria das trilhas (aplicado: E, G e H, marcados
  `# Lote 4/P6:`).
- Lote 7 (D1/D2): carga da sessao exige que a carga de topo se repita em
  ceil(n/2) das series de trabalho; a fase do grupo conta por EXERCICIO
  (aplicado: U e R, marcados `# Lote 7/D1:`).
- Lote 5 (P8): mesmas reps cruas na mesma carga com RPE maior = Estavel no
  dashboard (comparability `same_reps_higher_rpe`); reps cruas que mudam seguem o RIR.

Sem banco; roda em poucos segundos.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from functools import lru_cache

from app.domain import progression_engine as pe
from app.domain import progression_evaluator as ev
from app.domain import training_safety as ts
from app.domain.training_history import ExerciseExecution, FrozenPrescription, PrescribedSpec, SetRecord
from tests.support import cenarios_progressao as cen
from tests.support import progression_fixtures as fx

CALIBRATION = "track_in_calibration"
BELOW_REPS_MAX = "reps_below_reps_max"
BELOW_REPS_MIN = "reps_below_reps_min"
LOAD_BELOW_TRACK = "load_below_track"
BELOW_DOSE = "sets_below_prescribed_dose"
QUALIFY, RESET, NEUTRAL = "QUALIFY", "RESET", "NEUTRAL"


@lru_cache(maxsize=None)
def run(code: str) -> cen.ScenarioResult:
    return cen.run_scenario(cen.SCENARIOS[code])


def outcomes(result: cen.ScenarioResult) -> list[tuple[str, tuple[str, ...]]]:
    return [(s.outcome, s.reasons) for s in result.sessions]


def decisions(result: cen.ScenarioResult) -> list[str]:
    return [f.decision for f in result.fichas]


def test_todos_os_cenarios_da_investigacao_estao_portados():
    assert {"U", "A", "A2", "B", "E", "G", "H", "H2", "R"} <= set(cen.SCENARIOS)


# --- U: cenario literal do usuario ------------------------------------------


def test_u_aumento_de_carga_ja_na_quarta_ficha_e_trilha_no_topo_45kg():
    result = run("U")
    # Lote 3/P4: 12/11/10@10 qualifica (melhor serie 12 = reps_max, todas >= 10).
    # Antes: RESET reps_below_reps_max pela ultima serie.
    assert outcomes(result) == [
        (NEUTRAL, (CALIBRATION,)),
        (QUALIFY, ()),
        (QUALIFY, ()),
        (QUALIFY, ()),
    ]
    # Lote 3/P4: o plato confirma na 3a sessao e 15/13/12@10 ainda dispara o
    # excesso (15 = reps_max + 3): a 4a ficha ja sobe para 40 kg (antes: so a 5a).
    assert decisions(result) == ["HOLD", "HOLD", "HOLD", pe.INCREASE_LOAD, "HOLD"]
    assert result.sessions[3].ficha.load == Decimal("40")
    # Lote 7/D1: na 4a sessao (17x35, 12x35, 12x45) a serie de 45 kg e ISOLADA
    # (1 de 3 series; a carga de topo precisa se repetir em ceil(3/2) = 2): a
    # carga da sessao e 35 kg. O aumento para 40 nao efetiva e o grupo segue em
    # F2. Antes (Lote 3/P5): a carga da sessao era 45, efetivava e ia a F3.
    assert result.sessions[3].effective is False
    assert result.sessions[3].phase_after == pe.F2
    assert result.sessions[3].track_load == Decimal("35")
    # Lote 4/P6: o aumento da 4a ficha e o do PLATO DA TRILHA (S2+S3, 7 dias;
    # o excesso da S3 ficou com a origem do plato), sem acao do grupo; a F3 sai
    # pela maioria dos exercicios (Lote 7/D2) - aqui nenhum efetivou.
    assert result.sessions[2].pending_action is None
    assert result.sessions[2].group_decision == pe.INCREASE_LOAD
    assert result.sessions[2].track_pending_source == pe.PENDING_LOAD_SOURCE_PLATEAU
    # Lote 7/D1: o aumento segue pendente depois da 4a sessao (35 < 40) e a
    # ficha seguinte (7 dias depois, 14 desde o armamento) ja o encontra
    # expirado: a trilha fica nos 35 kg. Antes: 45 kg.
    assert result.sessions[3].track_pending_load == Decimal("40")
    assert result.next_ficha.load == Decimal("35")
    # Falha permitida (par teto 10, READY): nenhuma serie vira evento de seguranca.
    assert all(e is None for s in result.sessions for e in s.safety_events)


# --- A: "ideal para o motor" --------------------------------------------------


def test_a_12_11_10_qualifica_e_a_carga_sobe_na_quarta_ficha():
    result = run("A")
    # Lote 3/P4: 12/11/10@10 na carga da ficha qualifica (melhor serie 12, todas
    # >= 10). Antes: RESET reps_below_reps_max e HOLD indefinidamente.
    assert outcomes(result) == [
        (NEUTRAL, (CALIBRATION,)),
        (QUALIFY, ()),
        (QUALIFY, ()),
        (QUALIFY, ()),
    ]
    assert decisions(result) == ["HOLD", "HOLD", "HOLD", pe.INCREASE_LOAD, "HOLD"]
    assert result.sessions[3].ficha.load == Decimal("40")
    # Lote 3/P5: 12/11/10 nos 40 kg da ficha efetiva o aumento (carga executada >=
    # alvo) e o grupo vai a F3
    assert result.sessions[3].effective is True
    assert result.sessions[3].phase_after == pe.F3
    assert result.sessions[-1].track_load == Decimal("40")
    # Lote 5/P8 (comparability): a S3 continua REGRESSING porque as reps CRUAS
    # cairam (12/12/12 -> 12/11/10): o RIR segue valendo quando as reps mudam.
    # A S4 (mesmas reps com +5 kg) e PROGRESSING.
    assert [s.comparability_status for s in result.sessions] == [
        "INSUFFICIENT_DATA",
        "STABLE",
        "REGRESSING",
        "PROGRESSING",
    ]


def test_a2_tres_ideais_confirmam_o_plato_e_a_carga_sobe_para_40():
    result = run("A2")
    assert outcomes(result) == [
        (NEUTRAL, (CALIBRATION,)),
        (QUALIFY, ()),
        (QUALIFY, ()),
        (QUALIFY, ()),  # Lote 3/P4: 12/11/10 nos 40 kg qualifica (antes RESET)
        (QUALIFY, ()),
    ]
    # o plato da F3 (S4+S5, 7 dias) arma a tecnica da F4
    assert decisions(result) == ["HOLD", "HOLD", "HOLD", pe.INCREASE_LOAD, "HOLD", pe.APPLY_TECHNIQUE]
    assert result.sessions[3].ficha.load == Decimal("40")
    assert result.sessions[3].effective is True
    assert result.sessions[3].phase_after == pe.F3
    assert result.sessions[-1].track_load == Decimal("40")


# --- B: RPE 10 em tudo, reps caem ------------------------------------------------


def test_b_reset_em_todas_pelo_piso_e_hold():
    result = run("B")
    # Lote 3/P4: melhor serie 12 = reps_max, mas a 3a (8) ficou abaixo do PISO
    # (10): continua RESET, agora por reps_below_reps_min (antes reps_below_reps_max).
    assert outcomes(result) == [(NEUTRAL, (CALIBRATION,))] + [(RESET, (BELOW_REPS_MIN,))] * 3
    assert set(decisions(result)) == {"HOLD"}


# --- E: series a menos que a trilha ---------------------------------------------


def test_e_series_extras_nao_inflam_a_trilha_e_a_dose_e_a_da_ficha():
    result = run("E")
    # Lote 2/P3: a 4a serie extra da S1 NAO sobe mais o sets_count da trilha (so
    # ADD_SET muda) e a dose exigida e a da ficha congelada (3 series). Antes a
    # trilha ia a 4 e S2/S3 ficavam NEUTRAL sets_below_prescribed_dose para sempre.
    assert all(s.track_sets == 3 for s in result.sessions)
    assert outcomes(result) == [
        (NEUTRAL, (CALIBRATION,)),
        (QUALIFY, ()),
        (QUALIFY, ()),
    ]
    assert BELOW_DOSE not in {r for s in result.sessions for r in s.reasons}
    assert all(f.sets_count == 3 for f in result.fichas)  # a ficha segue o Modelo
    # Lote 4/P6: duas qualificantes DA TRILHA com 7 dias confirmam o plato dela:
    # o aumento e da trilha (antes: `INCREASE_LOAD` pendente no grupo)
    assert result.sessions[-1].pending_action is None
    assert result.sessions[-1].group_decision == pe.INCREASE_LOAD
    assert result.sessions[-1].track_pending_source == pe.PENDING_LOAD_SOURCE_PLATEAU
    assert result.sessions[-1].track_pending_load == Decimal("40")
    assert result.next_ficha.decision == pe.INCREASE_LOAD
    assert result.next_ficha.load == Decimal("40")
    # Lote 5/P8 (comparability): S1 4x12@9 -> S2 3x12@10 tem as mesmas reps cruas
    # emparelhadas com RPE maior. Antes a S2 era REGRESSING (reps + RIR 15 -> 12
    # por serie); agora e STABLE (`same_reps_higher_rpe`), e a S3 sem movimento no
    # run vira suspeita de plato.
    assert [s.comparability_status for s in result.sessions] == [
        "INSUFFICIENT_DATA",
        "STABLE",
        "PLATEAU_SUSPECTED",
    ]


# --- G: dose reduzida ao congelar, avaliacao READY --------------------------------


def test_g_avaliacao_usa_a_ficha_e_tela_e_avaliacao_gravam_o_mesmo_informativo():
    result = run("G")
    s2 = result.sessions[1]
    assert s2.ficha.effective_rpe_target == 9
    assert s2.ficha.effective_rpe_ceiling == 9
    assert s2.ficha.failure_allowed is False
    assert s2.ficha.snapshot["failure_denied_by"] == ts.FAILURE_DENIED_BY_RECOVERY
    # Lote 1/P1: o teto 9 foi cortado SO pela recuperacao (o par permite falha),
    # entao a serie @10 grava o evento INFORMATIVO RPE_ABOVE_RECOVERY_TARGET e o
    # status da sessao segue o normal (alvo atingido, falha nao liberada no dia),
    # sem o laranja ABOVE_SAFE_CEILING.
    assert s2.safety_events == (None, None, pe.SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET)
    assert s2.session_intensity_status == pe.INTENSITY_FAILURE_NOT_ALLOWED_REACHED_CEILING
    # Lote 2/P7: a avaliacao usa o teto (9) e a falha (negada) da FICHA congelada,
    # nao READY: re-deriva o MESMO evento na mesma serie que a tela gravou - e,
    # com a origem congelada (Lote 1/P1), o mesmo INFORMATIVO. A sessao em dose
    # reduzida que seguiu a ficha conta (QUALIFY, alvo efetivo 9).
    assert [(e["type"], e["set_number"], e["ceiling"], e["failure_denied_by"]) for e in s2.eval_safety_events] == [
        (pe.SAFETY_EVENT_RPE_ABOVE_RECOVERY_TARGET, 3, 9, ts.FAILURE_DENIED_BY_RECOVERY)
    ]
    assert s2.outcome == QUALIFY
    # Lote 4/P6: o plato e da trilha (antes: `INCREASE_LOAD` pendente no grupo)
    assert result.sessions[2].pending_action is None
    assert result.sessions[2].track_pending_source == pe.PENDING_LOAD_SOURCE_PLATEAU
    assert result.sessions[3].ficha.decision == pe.INCREASE_LOAD


# --- H: muito acima da faixa, mesma carga ------------------------------------------


def test_h_aumento_nao_efetiva_sem_a_carga_subir():
    result = run("H")
    # Lote 3/P4: 16/14/12@10 na S2 ja e excesso (16 >= reps_max + 3): a 3a ficha
    # sobe para 40 kg sem esperar o plato (antes: so a 4a, pelo plato).
    assert decisions(result)[:3] == ["HOLD", "HOLD", pe.INCREASE_LOAD]
    s4 = result.sessions[3]
    assert s4.ficha.decision == pe.INCREASE_LOAD
    assert s4.ficha.load == Decimal("40")
    # Lote 3/P5: a pessoa treina 35 kg na ficha de 40 kg: o aumento NAO e dado como
    # efetivado (antes: efetivado, grupo em F3 com a trilha parada em 35 kg e o
    # plato seguinte virando APPLY_TECHNIQUE). Depois de 14 dias sem subir ele
    # expira sem promover a fase e o plato o arma de novo.
    assert not any(s.effective for s in result.sessions)
    assert {s.phase_after for s in result.sessions} == {pe.F2}
    assert all(s.track_load == Decimal("35") for s in result.sessions)
    # Lote 4/P6: o aumento e DA TRILHA (mecanismo unico com o excesso). O
    # excesso da S2 arma 40 kg; o plato da trilha na S3 assume a origem (mesma
    # carga alvo, mesmo instante); na S4 (14 dias) expira e o excesso + o plato
    # o rearmam com instante novo. Nenhuma acao do grupo.
    assert all(s.pending_action is None for s in result.sessions)
    assert [s.track_pending_source for s in result.sessions[2:]] == [pe.PENDING_LOAD_SOURCE_PLATEAU] * 3
    assert all(s.track_pending_load == Decimal("40") for s in result.sessions[1:])
    # a ficha seguinte cai 14 dias depois do rearme da S4: o aumento da trilha
    # expirou e a ficha volta a HOLD ate a proxima sessao rearma-lo (antes, a
    # acao do grupo seguia gravada ate a avaliacao seguinte)
    assert result.next_ficha.decision == pe.HOLD
    assert result.next_ficha.load == Decimal("35")


# --- H2: muito acima da faixa, ultima serie abaixo de 12 ---------------------------


def test_h2_qualifica_e_o_excesso_acelera_a_carga():
    result = run("H2")
    # Lote 3/P4: 17/17/11 tem a melhor serie em reps_max + 5 e todas >= reps_min:
    # qualifica (antes RESET reps_below_reps_max pela ultima) e, como 17 >= 12 + 3
    # no RPE alvo, a ficha seguinte ja sobe um passo (40 kg) sem esperar o plato.
    assert outcomes(result) == [(NEUTRAL, (CALIBRATION,))] + [(QUALIFY, ())] * 3
    assert decisions(result) == ["HOLD", "HOLD", pe.INCREASE_LOAD, pe.INCREASE_LOAD, pe.INCREASE_LOAD]
    assert result.sessions[2].ficha.load == Decimal("40")
    assert "Excesso de repeticoes" in result.sessions[2].ficha.reason
    # a carga nunca foi executada: a trilha segue em 35 kg e o grupo em F2
    assert all(s.track_load == Decimal("35") for s in result.sessions)
    assert {s.phase_after for s in result.sessions} == {pe.F2}


# --- R: cenario sintetico baseado em caso real (Abdominal crunch na maquina) ------


def test_r_excesso_sobe_a_carga_e_trilha_no_topo_que_cumpriu_o_piso():
    result = run("R")
    final = result.sessions[-1]
    # Lote 3/P4: 12/15/16x50@10 em 28/08 e excesso (16 >= 12 + 3): a ficha de
    # 01/09 sai INCREASE_LOAD para 52,5 kg (antes HOLD em toda a historia).
    assert decisions(result).count(pe.INCREASE_LOAD) == 1
    # Lote 4/P6: o unico aumento e o do excesso; o plato da trilha nao confirma
    # (24/08 e 28/08 qualificam com 4 dias; 05/09 vem depois de um RESET), entao
    # a fase nao avanca - a maioria conta so aumento por plato.
    assert [s.track_pending_source for s in result.sessions if s.track_pending_source] == [
        pe.PENDING_LOAD_SOURCE_EXCESS
    ]
    assert result.sessions[3].ficha.decision == pe.INCREASE_LOAD
    assert result.sessions[3].ficha.load == Decimal("52.5")
    # 01/09: 9@52,5 abaixo do piso -> RESET pelo piso (antes reps_below_reps_max)
    assert result.sessions[3].reasons == (BELOW_REPS_MIN,)
    # Lote 7/D1: carga da sessao = maior carga que cumpriu o piso E se repetiu
    # em ceil(n/2) das series. 05/09 (15x50, 17x50, 12x52,5) conta 50 kg (a
    # serie de 52,5 e isolada) e fica abaixo dos 52,5 da trilha (adotados em
    # 01/09 pelo M77): RESET pela carga (Lote 3/P5: contava 47,5 e qualificava).
    # 01/10 (16x55, 13x60, 9x60, 6x55) continua levando a trilha a 60 kg (2 de 4
    # series em 60 e 13x60 cumpriu o piso). As sessoes seguintes em 55-57,5 kg
    # ficam abaixo da carga da trilha.
    assert result.sessions[4].outcome == RESET
    assert result.sessions[4].reasons == (LOAD_BELOW_TRACK,)
    assert result.sessions[8].track_load == Decimal("60")
    assert [s.reasons for s in result.sessions[9:]] == [(LOAD_BELOW_TRACK,), (LOAD_BELOW_TRACK,)]
    assert final.track_load == Decimal("60")
    assert result.next_ficha.load == Decimal("60")
    assert {s.phase_after for s in result.sessions} == {pe.F2}
    # Lote 2/P3: a sessao de 5 series de 01/09 nao infla mais o sets_count (fica
    # nas 3 do Modelo). Dose abaixo so onde a pessoa fez MENOS que a ficha
    # (2 series em fichas de 3: 11/09 e 19/09).
    assert all(s.track_sets == 3 for s in result.sessions)
    below_dose = [s.day for s in result.sessions if BELOW_DOSE in s.reasons]
    assert below_dose == ["2026-09-11", "2026-09-19"]
    # Lote 2/P7: 11/10 (ficha REDUCED_DOSE de 2 series, alvo 9) e avaliada contra
    # a ficha: 2 series cumprem a dose e a sessao conta (Lote 3/P5: RESET pela
    # carga - 57,5 kg abaixo dos 60 kg da trilha - antes de chegar ao RPE).
    assert final.outcome == RESET
    # Ficha de 11/10 congelada em REDUCED_DOSE com Modelo de 2 series: 60 kg
    # (Lote 3/P5; antes 55 kg), 10-12, RPE 10 do Modelo rebaixado para 9.
    ficha = final.ficha
    assert result.sessions[-2].ficha.rpe_target == "10"
    assert ficha.load == Decimal("60")
    assert ficha.sets_count == 2
    assert ficha.reps_target == "10-12"
    assert ficha.rpe_target == "9"
    assert ficha.effective_rpe_target == 9
    assert ficha.failure_allowed is False
    assert cen.SCENARIOS["R"].sessions[-1].freeze_readiness == ts.REDUCED_DOSE


# --- Lote 2/P7: item que a ficha mandou pular e foi treinado -----------------------


def _execucao_com_ficha(frozen: FrozenPrescription, rpes=(8, 9, 10)) -> ExerciseExecution:
    sets = tuple(
        SetRecord(set_number=i, set_type="working", reps=12, weight_kg=Decimal("35"), rpe=Decimal(r))
        for i, r in enumerate(rpes, start=1)
    )
    return ExerciseExecution(
        exercise_row_id=1,
        sequence=1,
        exercise_id=cen.EX,
        equipment_id=cen.EQ,
        prescribed=PrescribedSpec(sets=3, reps_min=10, reps_max=12, rpe_target=Decimal("10"), frozen=frozen),
        sets=sets,
    )


TRILHA_35 = pe.TrackState(
    exercise_id=cen.EX,
    equipment_id=cen.EQ,
    muscle_group_id=cen.GID,
    load_status=pe.ACTIVE,
    working_load_kg=Decimal("35"),
    sets_count=3,
)
GRUPO_F2 = pe.GroupState(muscle_group_id=cen.GID, phase=pe.F2, reps_min=10, reps_max=12)


def _inputs(**kw) -> ev.SessionInputs:
    return ev.SessionInputs(safety={(cen.EX, cen.EQ): fx.safety(max_safe_rpe=10)}, **kw)


def test_p7_item_skip_treinado_fica_neutro_com_motivo_proprio():
    skip = FrozenPrescription(
        sets_count=3, effective_rpe_target=9, effective_rpe_ceiling=9, failure_allowed=False, decision=pe.SKIP
    )
    outcome = ev._outcome_for(_execucao_com_ficha(skip), TRILHA_35, GRUPO_F2, _inputs())
    assert outcome.outcome == NEUTRAL
    assert outcome.reasons == (pe.REASON_PRESCRIPTION_SKIP,)
    # sem a marca SKIP, a mesma sessao conta contra a ficha (qualifica)
    hold = replace(skip, decision=pe.HOLD)
    assert ev._outcome_for(_execucao_com_ficha(hold), TRILHA_35, GRUPO_F2, _inputs()).outcome == QUALIFY


def test_p7_dose_reduzida_ignora_o_contrato_da_avaliacao_e_conta():
    # ficha congelada em dose reduzida (2 series, alvo/teto 9): mesmo com contrato
    # REDUCED_DOSE na avaliacao, a sessao que seguiu a ficha conta.
    reduzida = FrozenPrescription(
        sets_count=2, effective_rpe_target=9, effective_rpe_ceiling=9, failure_allowed=False, decision=pe.HOLD
    )
    contrato = {cen.GID: cen._contract(ts.REDUCED_DOSE)}
    outcome = ev._outcome_for(
        _execucao_com_ficha(reduzida, rpes=(8, 9)), TRILHA_35, GRUPO_F2, _inputs(readiness=contrato)
    )
    assert outcome.outcome == QUALIFY
    assert outcome.effort_target_rpe == Decimal("9")


def test_p7_sem_ficha_congelada_segue_o_caminho_antigo():
    # treino livre / ficha legada: contrato da avaliacao continua valendo
    contrato = {cen.GID: cen._contract(ts.REDUCED_DOSE)}
    outcome = ev._outcome_for(_execucao_com_ficha(None), TRILHA_35, GRUPO_F2, _inputs(readiness=contrato))
    assert outcome.outcome == NEUTRAL
    assert outcome.reasons == ("readiness_reduced_dose",)
    assert ev._outcome_for(_execucao_com_ficha(None), TRILHA_35, GRUPO_F2, _inputs()).outcome == QUALIFY


def test_p3_dose_exigida_e_a_da_ficha_com_fallback_na_trilha():
    ficha4 = FrozenPrescription(sets_count=4, effective_rpe_target=9, effective_rpe_ceiling=10, failure_allowed=True)
    execution = _execucao_com_ficha(ficha4)
    assert pe.required_dose_sets(execution, TRILHA_35) == 4
    assert ev._outcome_for(execution, TRILHA_35, GRUPO_F2, _inputs()).reasons == (BELOW_DOSE,)
    # ficha de 9 series (legado) respeita o teto de 4 da trilha
    assert pe.required_dose_sets(_execucao_com_ficha(replace(ficha4, sets_count=9)), TRILHA_35) == 4
    # sem ficha congelada: sets_count da trilha
    assert pe.required_dose_sets(_execucao_com_ficha(None), replace(TRILHA_35, sets_count=2)) == 2


# --- Lote T3 (medidas de serie): prancha por tempo e maximo de reps --------


def test_prancha_por_tempo_fica_neutra_sem_calibrar_nem_adotar():
    result = run("PRANCHA")
    reason = pe.REASON_MEASURE_NOT_REP_BASED
    assert outcomes(result) == [(NEUTRAL, (reason,)), (NEUTRAL, (reason,))]
    # A trilha continua em calibracao, sem carga: nada da prancha virou carga.
    assert [s.track_load for s in result.sessions] == [None, None]
    assert {s.track_load_status for s in result.sessions} == {pe.CALIBRATION}
    # Dashboard: a 2a exposicao nao e comparavel pela medida.
    assert [s.comparability_status for s in result.sessions] == ["INSUFFICIENT_DATA", "NOT_COMPARABLE"]


def test_maximo_de_reps_com_carga_maior_nao_vira_carga_da_trilha():
    result = run("RM")
    assert outcomes(result)[-1] == (NEUTRAL, (pe.REASON_MEASURE_NOT_REP_BASED,))
    # 45 kg do "maximo em 30 s" nao foi adotado: a trilha segue nos 35 kg.
    assert [s.track_load for s in result.sessions] == [Decimal(35), Decimal(35), Decimal(35)]
    assert result.sessions[-1].comparability_status == "NOT_COMPARABLE"
    assert result.next_ficha.load == Decimal(35)


def test_motivo_da_medida_e_o_do_dono():
    from app.domain import set_measures as sm

    assert pe.REASON_MEASURE_NOT_REP_BASED == sm.REASON_MEASURE_NOT_REP_BASED
