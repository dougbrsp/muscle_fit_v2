"""Issue #188 (Lote 1): progressao de MAQUINA separada por academia, com
calibracao ate a 3a sessao comparavel SO depois de uma troca de aparelho ou
academia (decisao do usuario de 2026-11-16). Sem troca e peso livre: como antes.

Donos: `domain/equipment_context.py` (codigos de maquina e limiar) e
`intelligence/dashboard/comparability.py` (chave, sequencia, veredito).
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.domain import equipment_context as ctx
from app.domain.prior_fatigue import PriorFatigue, PriorFatigueBand
from app.intelligence.dashboard import comparability as cmp
from app.intelligence.dashboard import exposure_service as svc
from app.intelligence.dashboard.contracts import ComparabilityKeyOut, ExposureOut
from app.intelligence.dashboard.metrics_registry import ExerciseProgressionStatus as S

EX = 1001
LEG_PRESS = 11
OUTRA_MAQUINA = 12
HALTER = 21
CALIBRANDO = cmp.REASON_EQUIPMENT_CALIBRATING
OUTRA_ACADEMIA = cmp.REASON_OTHER_GYM_SKIPPED


def sessao(row_id, dia, reps, carga=60.0, *, gym=1, eq=LEG_PRESS, tipo="MACHINE", rpe=8.0, conv="total"):
    """Linhas (uma por serie) no formato das queries de exposicao."""
    return [
        {
            "canonical_exercise_id": EX,
            "exercise_name": "Leg press",
            "exercise_row_id": row_id,
            "activity_id": row_id,
            "local_date": date(2026, 9, dia),
            "started_at": datetime(2026, 9, dia, 19, 0, 0),
            "position_in_session": 1,
            "training_goal": "hypertrophy",
            "equipment_id": eq,
            "equipment_name": None,
            "gym_id": gym,
            "equipment_type_code": tipo,
            "load_convention": conv,
            "prescribed_reps_target": "8-12",
            "set_number": numero,
            "reps": r,
            "weight_kg": carga,
            "rpe": rpe,
            "rest_seconds": None,
            "notes": None,
        }
        for numero, r in enumerate(reps, start=1)
    ]


def montar(*sessoes):
    rows = [linha for s in sessoes for linha in s]
    return svc.assemble_exposures(svc.build_exposures(rows), EX)


def sem_contexto(*sessoes):
    """Mesmas linhas como eram lidas ANTES da #188 (sem academia nem tipo)."""
    rows = [
        {k: v for k, v in linha.items() if k not in ("gym_id", "equipment_type_code")}
        for s in sessoes
        for linha in s
    ]
    return svc.assemble_exposures(svc.build_exposures(rows), EX)


def veredito(exposures):
    v = svc.progression_verdict(exposures)
    return S(v["status"]), v["status_reasons"], v["comparable_exposures"]


def seta(exposures, index=-1):
    return svc.consecutive_comparisons(exposures)[index]


def par(exposures):
    """`latest_comparison` reduzido a (comparavel, motivos)."""
    p = svc.latest_comparison(exposures)
    return p["comparable"], p["reasons"]


def sem_chave(verdict):
    """Veredito sem a chave (que agora carrega `gym_id`)."""
    return {k: v for k, v in verdict.items() if k != "comparability_key"}


ACADEMIA_1 = (sessao(1, 1, [10, 10]), sessao(2, 3, [11, 11]), sessao(3, 5, [12, 12]))


# --- (5) dono: maquina x peso livre -----------------------------------------


@pytest.mark.parametrize("code", ["MACHINE", "PLATE_LOADED", "CABLE_STATION", "machine"])
def test_codigos_de_maquina(code):
    assert ctx.is_machine_type(code)
    assert ctx.context_gym_id(code, 4) == 4


@pytest.mark.parametrize("code", ["BARBELL", "DUMBBELL", "SMITH", "BENCH", "NONE", None, "DESCONHECIDO"])
def test_peso_livre_sem_tipo_ou_desconhecido_nao_separa_academia(code):
    assert not ctx.is_machine_type(code)
    assert ctx.context_gym_id(code, 4) is None


def test_maquina_sem_academia_nao_separa():
    assert ctx.context_gym_id("MACHINE", None) is None


def test_limiar_de_calibracao():
    assert [ctx.is_calibrating(n) for n in range(5)] == [True, True, True, False, False]


# --- (a) sem troca: maquina sempre no mesmo aparelho e academia = antes ------


def test_primeira_exposicao_de_maquina_sem_troca_nao_e_calibracao():
    exposures = montar(sessao(1, 1, [10]))
    assert veredito(exposures) == (S.INSUFFICIENT_DATA, [cmp.REASON_NOT_ENOUGH_EXPOSURES], 1)
    assert svc.consecutive_comparisons(exposures) == [None]
    uma = svc.latest_comparison(exposures)
    assert (uma["comparable"], uma["previous_index"], uma["metrics"]) == (False, None, [])
    assert uma["reasons"] == [cmp.REASON_NOT_ENOUGH_EXPOSURES]
    assert (uma["current_index"], uma["current_activity_id"], uma["previous_activity_id"]) == (0, 1, None)
    assert svc.latest_comparison([]) is None
    peso_livre = svc.to_summaries(montar(sessao(1, 1, [10], eq=HALTER, tipo="BARBELL")))
    assert cmp.classify_exercise_progression(peso_livre) == (S.INSUFFICIENT_DATA, [cmp.REASON_NOT_ENOUGH_EXPOSURES])


@pytest.mark.parametrize(
    ("reps", "esperado"),
    [([11, 11], S.PROGRESSING), ([9, 9], S.REGRESSING), ([10, 10], S.STABLE)],
)
def test_segunda_sessao_sem_troca_classifica_como_antes(reps, esperado):
    sessoes = (sessao(1, 1, [10, 10]), sessao(2, 3, reps))
    agora, antes = montar(*sessoes), sem_contexto(*sessoes)
    status, reasons, comparaveis = veredito(agora)
    assert (status, comparaveis) == (esperado, 2) and CALIBRANDO not in reasons
    assert sem_chave(svc.progression_verdict(agora)) == sem_chave(svc.progression_verdict(antes))
    assert svc.consecutive_comparisons(agora) == svc.consecutive_comparisons(antes)
    assert svc.latest_comparison(agora) == svc.latest_comparison(antes)
    assert par(agora) == (True, [])
    assert cmp.in_equipment_calibration(svc.to_summaries(agora)) is False


# --- (1)/(b)/(c) depois de trocar de academia ou maquina: calibracao ---------


def test_queda_na_mesma_maquina_em_outra_academia_nao_vira_regressao():
    antes = sem_contexto(*ACADEMIA_1, sessao(4, 7, [6, 6], gym=2))
    assert veredito(antes)[0] is S.REGRESSING  # o comportamento que a issue corrige

    exposures = montar(*ACADEMIA_1, sessao(4, 7, [6, 6], gym=2))
    assert veredito(exposures) == (S.INSUFFICIENT_DATA, [CALIBRANDO, OUTRA_ACADEMIA], 0)
    assert seta(exposures) == {"status": S.NOT_COMPARABLE.value, "reasons": [CALIBRANDO, OUTRA_ACADEMIA]}
    assert par(exposures) == (False, [CALIBRANDO, OUTRA_ACADEMIA])
    # Seta por sessao: historico ATE ela. A 2a sessao (antes da troca) classifica.
    assert seta(exposures, 1)["status"] == S.PROGRESSING.value


def test_alta_na_segunda_sessao_em_calibracao_nao_vira_progresso():
    exposures = montar(*ACADEMIA_1, sessao(4, 7, [6, 6], gym=2), sessao(5, 9, [9, 9], gym=2))
    assert veredito(exposures) == (S.INSUFFICIENT_DATA, [CALIBRANDO, OUTRA_ACADEMIA], 0)
    assert seta(exposures) == {"status": S.NOT_COMPARABLE.value, "reasons": [CALIBRANDO, OUTRA_ACADEMIA]}
    assert par(exposures) == (False, [CALIBRANDO, OUTRA_ACADEMIA])
    assert svc.latest_comparison(exposures)["paired_reps"] is None


def test_maquina_nova_troca_fisica_e_calibracao():
    nova = (sessao(4, 7, [5, 5], eq=OUTRA_MAQUINA), sessao(5, 9, [9, 9], eq=OUTRA_MAQUINA))
    so_a_primeira = montar(*ACADEMIA_1, nova[0])
    assert veredito(so_a_primeira)[:2] == (S.NOT_COMPARABLE, [cmp.REASON_KEY_CHANGED])
    assert seta(so_a_primeira)["reasons"] == [cmp.REASON_KEY_CHANGED]
    # Sem par pela troca fisica: o motivo e o mesmo do painel.
    assert par(so_a_primeira) == (False, [cmp.REASON_KEY_CHANGED])

    exposures = montar(*ACADEMIA_1, *nova)
    assert veredito(exposures) == (S.INSUFFICIENT_DATA, [CALIBRANDO], 0)
    assert seta(exposures) == {"status": S.NOT_COMPARABLE.value, "reasons": [CALIBRANDO]}
    assert par(exposures) == (False, [CALIBRANDO])

    terceira = montar(*ACADEMIA_1, *nova, sessao(6, 11, [10, 10], eq=OUTRA_MAQUINA))
    assert veredito(terceira)[0::2] == (S.PROGRESSING, 3)
    assert par(terceira) == (True, [])
    assert [c["status"] for c in svc.consecutive_comparisons(terceira)[3:]] == [
        S.NOT_COMPARABLE.value,
        S.NOT_COMPARABLE.value,
        S.PROGRESSING.value,
    ]


# --- (d)/(e) o que NAO e troca ----------------------------------------------


def test_exposicoes_sem_aparelho_identificado_nao_sao_troca():
    sessoes = (
        sessao(1, 1, [10, 10], eq=None, gym=None, tipo=None),
        sessao(2, 3, [10, 10], eq=None, gym=None, tipo=None),
        sessao(3, 5, [10, 10]),
        sessao(4, 7, [11, 11]),
    )
    exposures = montar(*sessoes)
    assert cmp.in_equipment_calibration(svc.to_summaries(exposures)) is False
    assert veredito(montar(*sessoes[:3]))[:2] == (S.NOT_COMPARABLE, [cmp.REASON_KEY_CHANGED])
    assert veredito(exposures)[0::2] == (S.PROGRESSING, 2)
    assert seta(exposures)["status"] == S.PROGRESSING.value
    assert par(exposures) == (True, [])


def test_so_outra_convencao_no_mesmo_aparelho_e_academia_nao_e_troca():
    exposures = montar(
        sessao(1, 1, [10, 10], conv="per_side"),
        sessao(2, 3, [10, 10]),
        sessao(3, 5, [11, 11]),
    )
    assert cmp.in_equipment_calibration(svc.to_summaries(exposures)) is False
    assert veredito(exposures)[0::2] == (S.PROGRESSING, 2)
    assert seta(exposures)["status"] == S.PROGRESSING.value
    assert par(exposures) == (True, [])


# --- (2) 1 e 2 calibram; a 3a classifica so com o contexto -------------------


def test_terceira_sessao_comparavel_classifica_so_com_a_academia():
    exposures = montar(
        *ACADEMIA_1,
        sessao(4, 7, [6, 6], gym=2),
        sessao(5, 9, [9, 9], gym=2),
        sessao(6, 11, [10, 10], gym=2),
    )
    status, reasons, comparaveis = veredito(exposures)
    assert status is S.PROGRESSING
    assert reasons[-1] == OUTRA_ACADEMIA
    assert comparaveis == 3
    assert seta(exposures)["status"] == S.PROGRESSING.value
    ultimo = svc.latest_comparison(exposures)
    assert ultimo["comparable"] and ultimo["paired_reps"]["previous_reps"] == [9, 9]
    assert ultimo["reasons"] == []


def _resumo(reps, *, gym=2, fadiga="rested", objetivo="hypertrophy", conv="total"):
    key = cmp.build_key(EX, LEG_PRESS, conv, fatigue_context=fadiga, training_goal=objetivo, gym_id=gym)
    return cmp.ExposureSummary(key=key, total_reps=reps, best_load_kg=60.0)


def test_so_outro_objetivo_ou_fadiga_no_mesmo_aparelho_e_academia_nao_e_troca():
    historico = [_resumo(30, fadiga="pre_fatigued"), _resumo(30, objetivo="strength"), _resumo(10), _resumo(11)]
    assert not cmp.in_equipment_calibration(historico)
    status, reasons = cmp.classify_exercise_progression(historico)
    assert status is S.PROGRESSING and CALIBRANDO not in reasons


def test_mesmo_aparelho_em_outra_academia_com_outra_convencao_nao_e_troca():
    # A troca de academia exige o MESMO aparelho e a MESMA convencao.
    assert cmp.in_equipment_calibration([_resumo(10, gym=1, conv="per_side"), _resumo(10), _resumo(11)]) is False
    assert cmp.in_equipment_calibration([_resumo(10, gym=1), _resumo(10), _resumo(11)]) is True


def test_segmento_fisico_conta_qualquer_fadiga_ou_objetivo_para_o_minimo():
    # Rodada 1 (#188): o minimo da calibracao e medido no SEGMENTO fisico
    # (aparelho + academia), com qualquer contexto de fadiga ou objetivo.
    historico = [
        _resumo(10),
        _resumo(30, fadiga="pre_fatigued"),
        _resumo(30, objetivo="strength"),
        _resumo(30, gym=1),
        _resumo(11),
    ]
    janela = cmp._trailing_comparable_window(historico)
    assert (janela.indices, janela.skipped, janela.skipped_other_gym) == ([0, 4], 1, 1)
    assert not cmp.in_equipment_calibration(historico)
    status, reasons = cmp.classify_exercise_progression(historico)
    assert status is S.PROGRESSING and CALIBRANDO not in reasons

    curto = [_resumo(30, gym=1), _resumo(30, fadiga="pre_fatigued"), _resumo(11)]
    status, reasons = cmp.classify_exercise_progression(curto)
    assert status is S.INSUFFICIENT_DATA and reasons[0] == CALIBRANDO


def _outro_aparelho(reps=10):
    return cmp.ExposureSummary(key=cmp.build_key(EX, OUTRA_MAQUINA, "total", gym_id=2), total_reps=reps, best_load_kg=60.0)


def _resultado(historico):
    o = cmp.progression_outcome(historico)
    return o.status, o.reasons, o.comparable_count, o.in_calibration


def test_troca_antiga_nao_reabre_calibracao_e_nao_depende_da_leitura():
    # B x1 -> A (contexto X) x7 -> A (contexto Y) x2: o segmento de A tem 9.
    historico = [
        _outro_aparelho(),
        *[_resumo(10, fadiga="pre_fatigued") for _ in range(7)],
        _resumo(10),
        _resumo(11),
    ]
    completo = _resultado(historico)
    assert completo[3] is False and completo[0] is S.PROGRESSING
    # Lote le 8 por exercicio; drill-down/briefing leem ate 30: mesmo veredito.
    assert _resultado(historico[-8:]) == completo
    assert _resultado(historico[-30:]) == completo


def test_troca_seguida_de_tres_sessoes_e_mudanca_de_fadiga_nao_calibra():
    historico = [_outro_aparelho(), _resumo(10), _resumo(11), _resumo(12), _resumo(12, fadiga="pre_fatigued")]
    status, reasons, comparaveis, calibrando = _resultado(historico)
    assert calibrando is False and CALIBRANDO not in reasons
    assert status is S.INSUFFICIENT_DATA and comparaveis == 1
    assert reasons[:2] == [cmp.REASON_NOT_ENOUGH_EXPOSURES, cmp.REASON_FIRST_IN_CONTEXT]


def test_exposicao_sem_aparelho_antes_do_segmento_nao_calibra():
    sem_aparelho = cmp.ExposureSummary(key=cmp.build_key(EX, None, "total"), total_reps=10, best_load_kg=60.0)
    historico = [sem_aparelho, _resumo(10), _resumo(11)]
    assert not cmp.in_equipment_calibration(historico)
    # O comeco do historico lido tambem nao e troca.
    assert not cmp.in_equipment_calibration([_resumo(10), _resumo(11)])
    assert cmp.in_equipment_calibration([_outro_aparelho(), _resumo(10), _resumo(11)])


def test_janela_conta_puladas_por_contexto_e_por_academia():
    historico = [_resumo(10), _resumo(10, gym=1), _resumo(10, fadiga="pre_fatigued"), _resumo(11)]
    run = cmp.comparable_run(historico)
    # A janela conta as puladas a parte; o segmento da academia 2 ja tem 3
    # sessoes (qualquer fadiga): nao calibra, o par e o do contexto.
    janela = cmp._trailing_comparable_window(historico)
    assert janela.indices == [0, 3] and janela.skipped == 1 and janela.skipped_other_gym == 1
    assert not run.in_calibration and run.indices == [0, 3]
    assert cmp.latest_comparable_pair(historico).previous_index == 0
    # Outro historico (sem a 1a sessao): com menos de 3 no segmento, a volta a
    # academia 2 calibra. Nao e corte de leitura: toda leitura cobre a janela
    # fixa do dono (`VERDICT_LOOKBACK_EXPOSURES`).
    curto = historico[1:]
    assert cmp.comparable_run(curto).in_calibration and cmp.comparable_run(curto).indices == []
    assert cmp.latest_comparable_pair(curto).previous_index is None


def test_calibracao_nao_depende_do_tamanho_da_leitura():
    # G2 x1 (antiga) -> G1 x6 -> G2 x2, mesma maquina: a janela fixa do dono
    # (ultimas 8) ve 2 sessoes em G2 depois de outra academia -> calibra, no
    # lote (8), no drill-down (30), no historico inteiro e no briefing.
    sessoes = (
        sessao(1, 1, [10, 10], gym=2),
        *[sessao(2 + i, 3 + 2 * i, [10 + i, 10 + i], gym=1) for i in range(6)],
        sessao(8, 15, [9, 9], gym=2),
        sessao(9, 17, [10, 10], gym=2),
    )
    historico = montar(*sessoes)
    assert len(historico) == 9
    completo = svc.progression_verdict(historico)
    assert CALIBRANDO in completo["status_reasons"]
    for lido in (historico[-cmp.VERDICT_LOOKBACK_EXPOSURES :], historico[-30:]):
        assert veredito(lido) == veredito(historico)
        assert seta(lido) == seta(historico)
        briefing, _, no_aparelho = svc.outcome_for_equipment(lido, LEG_PRESS)
        assert no_aparelho and briefing.in_calibration and briefing.status.value == completo["status"]


def test_leitura_do_lote_cobre_a_janela_do_veredito():
    janela = cmp.VERDICT_LOOKBACK_EXPOSURES
    assert svc.MAX_BATCH_EXPOSURES_PER_EXERCISE >= janela
    assert svc.MAX_EXPOSURES >= janela
    # Os limiares de plato cabem na janela.
    assert int(cmp.THRESHOLDS["plateau_min_exposures"]) <= janela
    assert not hasattr(ctx, "CALIBRATION_LOOKBACK_EXPOSURES") and not hasattr(ctx, "calibration_lookback")


def test_trend_metric_do_dono():
    def run(**valores):
        return [dict(valores) for _ in range(cmp.TREND_MIN_POINTS)]

    assert cmp.trend_metric(run(e1rm=80.0, best_load_kg=60.0, total_reps=20), "total") == "e1rm"
    assert cmp.trend_metric(run(e1rm=None, best_load_kg=60.0, total_reps=20), "total") == "best_load_kg"
    assert cmp.trend_metric(run(e1rm=None, best_load_kg=None, total_reps=20), "total") == "total_reps"
    # Peso corporal compara so reps: nunca e1RM.
    assert cmp.trend_metric(run(e1rm=80.0, best_load_kg=0.0, total_reps=20), "bodyweight") == "total_reps"
    assert cmp.trend_metric(run(e1rm=80.0, best_load_kg=5.0, total_reps=None), "bodyweight") == "best_load_kg"
    assert cmp.trend_metric([{"e1rm": 80.0, "best_load_kg": 60.0, "total_reps": 20}], "total") is None
    assert cmp.trend_metric([], None) is None


def test_troca_de_equipamento_continua_interrompendo():
    outro = cmp.ExposureSummary(key=cmp.build_key(EX, OUTRA_MAQUINA, "total", gym_id=2), total_reps=10, best_load_kg=60.0)
    historico = [_resumo(10), outro, _resumo(10), _resumo(11)]
    assert cmp._trailing_comparable_window(historico).indices == [2, 3]


# --- (3) voltar a um contexto calibrado reaproveita a sequencia --------------


def test_voltar_a_academia_calibrada_reaproveita_a_sequencia():
    exposures = montar(
        *ACADEMIA_1,
        sessao(4, 7, [6, 6], gym=2),
        sessao(5, 9, [7, 7], gym=2),
        sessao(6, 11, [13, 13], gym=1),
    )
    status, reasons, comparaveis = veredito(exposures)
    assert status is S.PROGRESSING
    assert OUTRA_ACADEMIA in reasons and CALIBRANDO not in reasons
    assert comparaveis == 4
    assert seta(exposures)["status"] == S.PROGRESSING.value
    assert svc.latest_comparison(exposures)["paired_reps"]["previous_reps"] == [12, 12]


def test_voltar_a_contexto_antigo_com_menos_de_tres_calibra():
    exposures = montar(
        sessao(1, 1, [10, 10]),
        sessao(2, 3, [6, 6], gym=2),
        sessao(3, 5, [7, 7], gym=2),
        sessao(4, 7, [13, 13], gym=1),
    )
    assert veredito(exposures) == (S.INSUFFICIENT_DATA, [CALIBRANDO, OUTRA_ACADEMIA], 0)
    assert seta(exposures) == {"status": S.NOT_COMPARABLE.value, "reasons": [CALIBRANDO, OUTRA_ACADEMIA]}
    assert par(exposures) == (False, [CALIBRANDO, OUTRA_ACADEMIA])
    assert [c and c["status"] for c in svc.consecutive_comparisons(exposures)] == [
        None,
        S.NOT_COMPARABLE.value,
        S.NOT_COMPARABLE.value,
        S.NOT_COMPARABLE.value,
    ]


# --- (4) peso livre: identico ao de antes ------------------------------------


@pytest.mark.parametrize("tipo", ["DUMBBELL", "BARBELL", "SMITH", None])
def test_peso_livre_em_academias_diferentes_identico_ao_de_antes(tipo):
    sessoes = (
        sessao(1, 1, [10, 10], eq=HALTER, tipo=tipo, gym=1),
        sessao(2, 3, [12, 12], eq=HALTER, tipo=tipo, gym=2),
        sessao(3, 5, [6, 6], eq=HALTER, tipo=tipo, gym=1),
    )
    agora, antes = montar(*sessoes), sem_contexto(*sessoes)
    assert all(item["_key"].gym_id is None for item in agora)
    assert [item["comparability_key"] for item in agora] == [item["comparability_key"] for item in antes]
    assert svc.progression_verdict(agora) == svc.progression_verdict(antes)
    assert svc.consecutive_comparisons(agora) == svc.consecutive_comparisons(antes)
    assert svc.latest_comparison(agora) == svc.latest_comparison(antes)
    assert veredito(agora)[0] is S.REGRESSING


# --- (6) recorde e e1RM continuam globais ------------------------------------


def test_recorde_e_linha_de_e1rm_ignoram_a_academia():
    sessoes = (*ACADEMIA_1, sessao(4, 7, [6, 6], 80.0, gym=2))
    agora, antes = montar(*sessoes), sem_contexto(*sessoes)
    chave = agora[-1]["_key"]
    assert svc.records_all_goals(agora, agora, chave) == svc.records_all_goals(antes, antes, antes[-1]["_key"])
    assert svc.records_all_goals(agora, agora, chave)["best_e1rm"]["activity_id"] in {3, 4}
    serie = svc.e1rm_series(agora, chave)
    assert serie == svc.e1rm_series(antes, antes[-1]["_key"])
    assert all(ponto["same_physical_key"] for ponto in serie)


# --- (7) lote = unitario ----------------------------------------------------


@pytest.mark.parametrize(
    ("sessoes", "status", "run"),
    [
        ((*ACADEMIA_1, sessao(4, 7, [6, 6], gym=2), sessao(5, 9, [9, 9], gym=2)), S.INSUFFICIENT_DATA, []),
        ((sessao(1, 1, [10, 10]),), S.INSUFFICIENT_DATA, [1]),
        ((sessao(1, 1, [10, 10]), sessao(2, 3, [11, 11])), S.PROGRESSING, [1, 2]),
        (
            (*ACADEMIA_1, sessao(4, 7, [5, 5], eq=OUTRA_MAQUINA), sessao(5, 9, [9, 9], eq=OUTRA_MAQUINA)),
            S.INSUFFICIENT_DATA,
            [],
        ),
    ],
)
def test_caminho_em_lote_da_o_mesmo_veredito(sessoes, status, run):
    rows = [linha for s in sessoes for linha in s]
    itens, _ = svc.classify_batch(rows, per_exercise_limit=8)
    unitario = svc.progression_verdict(montar(*sessoes))
    assert len(itens) == 1
    assert (itens[0]["status"], itens[0]["status_reasons"], itens[0]["comparable_exposures"]) == (
        unitario["status"],
        unitario["status_reasons"],
        unitario["comparable_exposures"],
    )
    assert itens[0]["status"] == status.value
    assert [e["activity_id"] for e in itens[0]["_comparable_run"]] == run


def test_relatorio_e_ia_nao_recebem_anterior_comparavel_em_calibracao():
    from app.intelligence.progression_service import exercise_progression_context

    def contexto(*sessoes):
        itens, _ = svc.classify_batch([linha for s in sessoes for linha in s], per_exercise_limit=8)
        return exercise_progression_context(itens)[0]

    calibrando = contexto(*ACADEMIA_1, sessao(4, 7, [6, 6], gym=2), sessao(5, 9, [9, 9], gym=2))
    assert calibrando["status"] == S.INSUFFICIENT_DATA.value
    assert calibrando["previous_comparable"] is None and calibrando["paired_top_reps"] is None
    assert calibrando["latest"] is not None

    sem_troca = contexto(sessao(1, 1, [10, 10]), sessao(2, 3, [11, 11]))
    assert sem_troca["previous_comparable"] is not None and sem_troca["paired_top_reps"] is not None


def test_latest_comparison_valida_contra_o_contrato_dentro_e_fora_da_calibracao():
    from app.intelligence.dashboard.contracts import LatestComparisonOut

    for exposures in (
        montar(*ACADEMIA_1, sessao(4, 7, [6, 6], gym=2), sessao(5, 9, [9, 9], gym=2)),
        montar(sessao(1, 1, [10, 10]), sessao(2, 3, [11, 11])),
    ):
        assert LatestComparisonOut.model_validate(svc.latest_comparison(exposures))


def test_sql_das_duas_leituras_traz_academia_e_tipo():
    for sql in (svc.EXPOSURES_SQL_TEMPLATE, svc.BATCH_EXPOSURES_SQL_TEMPLATE):
        assert "ge.gym_id" in sql and "AS gym_id" in sql
        assert "AS equipment_type_code" in sql
        assert "LEFT JOIN equipment_types et ON et.id = eq.equipment_type_id" in sql
        assert "LEFT JOIN equipment eq ON eq.id = ge.equipment_id" in sql


# --- (8) contrato -----------------------------------------------------------


def test_resposta_valida_contra_o_contrato_com_gym_id_e_gym_changed():
    exposures = montar(*ACADEMIA_1, sessao(4, 7, [6, 6], gym=2))
    atual = exposures[-1]["_key"]
    publicas = [
        ExposureOut.model_validate({**svc._public_exposure(item), **cmp.comparison_with_current(item["_key"], atual)})
        for item in exposures
    ]
    assert [p.mismatch_with_current for p in publicas] == ["gym_changed"] * 3 + [None]
    assert publicas[-1].comparability_key.gym_id == 2
    assert ComparabilityKeyOut.model_validate(atual.to_dict()).gym_id == 2
    assert cmp.build_key(EX, HALTER, "total").to_dict()["gym_id"] is None


# --- (9) um ponto so: lote x drill-down x briefing ---------------------------

NOVA = (sessao(4, 7, [5, 5], eq=OUTRA_MAQUINA), sessao(5, 9, [9, 9], eq=OUTRA_MAQUINA))


@pytest.mark.parametrize(
    ("sessoes", "esperado"),
    [
        pytest.param((*ACADEMIA_1, *NOVA), (S.INSUFFICIENT_DATA, [CALIBRANDO], 0), id="aparelho-A3-B2"),
        pytest.param(
            (*ACADEMIA_1, sessao(4, 7, [6, 6], gym=2), sessao(5, 9, [9, 9], gym=2)),
            (S.INSUFFICIENT_DATA, [CALIBRANDO, OUTRA_ACADEMIA], 0),
            id="so-academia",
        ),
        pytest.param(
            (sessao(1, 1, [10, 10]), sessao(2, 3, [11, 11])), (S.PROGRESSING, None, 2), id="sem-troca-2"
        ),
        pytest.param(
            (*ACADEMIA_1, sessao(4, 7, [6, 6], gym=2), sessao(5, 9, [7, 7], gym=2), sessao(6, 11, [13, 13], gym=1)),
            (S.PROGRESSING, None, 4),
            id="volta-ao-contexto-3+",
        ),
        pytest.param(
            (
                sessao(1, 1, [10, 10], eq=HALTER, tipo="DUMBBELL", gym=1),
                sessao(2, 3, [12, 12], eq=HALTER, tipo="DUMBBELL", gym=2),
                sessao(3, 5, [6, 6], eq=HALTER, tipo="DUMBBELL", gym=1),
            ),
            (S.REGRESSING, None, 3),
            id="peso-livre",
        ),
    ],
)
def test_lote_drill_down_e_briefing_dao_o_mesmo_veredito(sessoes, esperado):
    from app.domain.exercise_briefing import summarize_performance

    exposures = montar(*sessoes)
    itens, _ = svc.classify_batch([linha for s in sessoes for linha in s], per_exercise_limit=8)
    lote = (S(itens[0]["status"]), itens[0]["status_reasons"], itens[0]["comparable_exposures"])
    tela = veredito(exposures)
    drill = cmp.classify_exercise_progression(svc.to_summaries(exposures))
    b = summarize_performance(exposures, equipment_id=sessoes[-1][0]["equipment_id"])
    briefing = (S(b["status"]), b["reasons"], b["comparable_exposures"])

    assert lote == tela == briefing
    assert drill == tela[:2]
    status, reasons, contagem = esperado
    assert (tela[0], tela[2]) == (status, contagem)
    if reasons is not None:
        assert tela[1] == reasons
    assert len(itens[0]["_comparable_run"]) == contagem


def test_desfecho_unico_esvazia_a_sequencia_so_em_calibracao():
    calibrando = cmp.progression_outcome(svc.to_summaries(montar(*ACADEMIA_1, *NOVA)))
    assert calibrando.in_calibration and calibrando.run_indices == [] and calibrando.comparable_count == 0
    marcas = cmp.run_flags(calibrando, len(montar(*ACADEMIA_1, *NOVA)))
    assert [i for i, m in enumerate(marcas) if m["in_context_run"]] == [3, 4]

    sem_troca = cmp.progression_outcome(svc.to_summaries(montar(*ACADEMIA_1)))
    assert not sem_troca.in_calibration and sem_troca.run_indices == [0, 1, 2]
    assert (sem_troca.status, sem_troca.reasons) == cmp.classify_exercise_progression(
        svc.to_summaries(montar(*ACADEMIA_1))
    )


BARRA = 22


def _briefing_painel_lote(sessoes, equipment_id):
    """(status, motivos, contagem) do briefing com o item em `equipment_id`, do
    painel e do lote sobre o historico ate a ultima exposicao nesse aparelho."""
    from app.domain.exercise_briefing import summarize_performance

    exposures = montar(*sessoes)
    b = summarize_performance(exposures, equipment_id=equipment_id)
    fim = max(i for i, e in enumerate(exposures) if e["equipment_id"] == equipment_id) + 1
    ate = sessoes[:fim]
    itens, _ = svc.classify_batch([linha for s in ate for linha in s], per_exercise_limit=16)
    lote = (S(itens[0]["status"]), itens[0]["status_reasons"], itens[0]["comparable_exposures"])
    return (S(b["status"]), b["reasons"], b["comparable_exposures"]), veredito(montar(*ate)), lote


@pytest.mark.parametrize(
    ("sessoes", "equipment_id", "esperado"),
    [
        pytest.param(
            (
                *ACADEMIA_1,
                *(sessao(4 + i, 7 + i, [8, 8], eq=OUTRA_MAQUINA) for i in range(3)),
                sessao(7, 20, [11, 11]),
                sessao(8, 22, [12, 12]),
            ),
            LEG_PRESS,
            (S.INSUFFICIENT_DATA, [CALIBRANDO], 0),
            id="maquina-A3-B3-A2-item-A",
        ),
        pytest.param(
            (
                *(sessao(1 + i, 1 + i, [10, 10], eq=HALTER, tipo="DUMBBELL") for i in range(3)),
                sessao(4, 7, [8, 8], eq=BARRA, tipo="BARBELL"),
                sessao(5, 9, [10, 10], eq=HALTER, tipo="DUMBBELL"),
                sessao(6, 11, [12, 12], eq=HALTER, tipo="DUMBBELL"),
            ),
            HALTER,
            (S.PROGRESSING, None, 2),
            id="peso-livre-A3-B1-A2-item-A",
        ),
        pytest.param((*ACADEMIA_1, *NOVA), OUTRA_MAQUINA, (S.INSUFFICIENT_DATA, [CALIBRANDO], 0), id="A3-B2-item-B"),
        pytest.param((*ACADEMIA_1, *NOVA), LEG_PRESS, None, id="A3-B2-item-A-corta-no-A"),
    ],
)
def test_briefing_no_aparelho_e_o_veredito_do_painel_sobre_o_historico_completo(sessoes, equipment_id, esperado):
    briefing, painel, lote = _briefing_painel_lote(sessoes, equipment_id)
    assert briefing == painel == lote
    if esperado is not None:
        status, reasons, contagem = esperado
        assert (briefing[0], briefing[2]) == (status, contagem)
        if reasons is not None:
            assert briefing[1] == reasons


def test_briefing_a_b_a_sai_como_no_painel_troca_fisica():
    from app.domain.exercise_briefing import summarize_performance

    sessoes = (sessao(1, 1, [10, 10]), sessao(2, 3, [10, 10], eq=OUTRA_MAQUINA), sessao(3, 5, [12, 12]))
    b = summarize_performance(montar(*sessoes), equipment_id=LEG_PRESS)
    assert (S(b["status"]), b["reasons"], b["comparable_exposures"]) == veredito(montar(*sessoes))
    assert S(b["status"]) is S.NOT_COMPARABLE and cmp.REASON_KEY_CHANGED in b["reasons"]


# --- (10) par x veredito: uma decisao so (A2) ---------------------------------


def _nao_reps(resumo):
    from dataclasses import replace

    return replace(resumo, has_non_rep_sets=True)


@pytest.mark.parametrize(
    "historico",
    [
        pytest.param([_resumo(10), _nao_reps(_resumo(11))], id="ultima-nao-reps"),
        pytest.param([_resumo(10), _nao_reps(_resumo(11)), _resumo(12)], id="penultima-nao-reps"),
        pytest.param(
            [
                _resumo(10),
                cmp.ExposureSummary(key=cmp.build_key(EX, OUTRA_MAQUINA, "total", gym_id=2), total_reps=10, best_load_kg=60.0),
            ],
            id="troca-fisica",
        ),
        pytest.param([_resumo(10, gym=1), _resumo(10), _resumo(11)], id="calibracao"),
        pytest.param([_resumo(10), _resumo(11, fadiga="pre_fatigued")], id="primeira-no-contexto"),
        pytest.param([_resumo(10)], id="uma-exposicao"),
        pytest.param([_resumo(10), _resumo(12)], id="com-par"),
    ],
)
def test_par_concorda_com_o_veredito(historico):
    outcome = cmp.progression_outcome(historico)
    pair = cmp.latest_comparable_pair(historico)
    assert pair == outcome.pair and pair.current_index == len(historico) - 1
    sem_par = outcome.status in (S.NOT_COMPARABLE, S.INSUFFICIENT_DATA)
    assert pair.comparable is not sem_par
    if sem_par:
        assert pair.previous_index is None and pair.reasons == outcome.reasons
    else:
        assert pair.reasons == [] and pair.previous_index == outcome.run_indices[-2]


def test_serie_nao_baseada_em_reps_nao_vira_par_nem_seta_estavel():
    exposures = montar(*ACADEMIA_1)
    exposures[-1]["has_non_rep_sets"] = True
    assert veredito(exposures)[:2] == (S.NOT_COMPARABLE, [cmp.REASON_MEASURE_NOT_REP_BASED])
    p = svc.latest_comparison(exposures)
    assert (p["comparable"], p["previous_index"], p["reasons"]) == (False, None, [cmp.REASON_MEASURE_NOT_REP_BASED])
    assert seta(exposures) == {"status": S.NOT_COMPARABLE.value, "reasons": [cmp.REASON_MEASURE_NOT_REP_BASED]}


# --- Rodada 1B (#188): marcacoes, desfecho por metrica e decisoes do contexto ---


def _contexto_e_veredito(marcas):
    contexto = [i for i, m in enumerate(marcas) if m["in_context_run"]]
    veredito = [i for i, m in enumerate(marcas) if m["in_verdict_run"]]
    return contexto, veredito


def test_run_flags_em_calibracao_plota_o_contexto_e_nao_destaca_nada():
    historico = [_resumo(10, gym=1), _resumo(10, fadiga="pre_fatigued"), _resumo(11)]
    desfecho = cmp.progression_outcome(historico)
    assert desfecho.in_calibration
    contexto, veredito = _contexto_e_veredito(cmp.run_flags(desfecho, len(historico)))
    # A exposicao da outra academia (0) e a de outro contexto de fadiga (1) ficam fora das duas.
    assert contexto == [2] and veredito == []


def test_run_flags_fora_de_calibracao_coincidem_com_run_indices():
    historico = [_resumo(10), _resumo(11), _resumo(12)]
    desfecho = cmp.progression_outcome(historico)
    contexto, veredito = _contexto_e_veredito(cmp.run_flags(desfecho, len(historico)))
    assert contexto == veredito == desfecho.run_indices == [0, 1, 2]


def test_verdict_run_flags_usa_so_a_janela_do_veredito():
    # 10 exposicoes iguais (sobe 1 rep cada): so as ultimas 8 sao marcadas.
    exposicoes = montar(*[sessao(i, i, [10 + i]) for i in range(1, 11)])
    marcas = svc.verdict_run_flags(exposicoes)
    janela = cmp.VERDICT_LOOKBACK_EXPOSURES
    fora = len(exposicoes) - janela
    assert marcas[:fora] == [{"in_context_run": False, "in_verdict_run": False}] * fora
    assert all(m == {"in_context_run": True, "in_verdict_run": True} for m in marcas[fora:])
    assert [m["in_verdict_run"] for m in svc.verdict_run_flags(montar(*ACADEMIA_1))] == [True, True, True]


# --- Janela unica do veredito (decisao do usuario de 2026-11-16) -------------

FADIGA_F2 = PriorFatigue(1, "Peitoral", 4.0, 4.0 / 9, PriorFatigueBand.PRE_FATIGUED)


def _leituras(sessoes, fadiga_por_row=None):
    """Exposicoes do historico inteiro, das ultimas 30 e das ultimas 8 (a
    janela do dono), e as linhas de cada leitura para o lote real."""
    rows = [linha for s in sessoes for linha in s]
    historico = svc.assemble_exposures(svc.build_exposures(rows), EX, fadiga_por_row)
    leituras = []
    for lido in (historico, historico[-30:], historico[-cmp.VERDICT_LOOKBACK_EXPOSURES :]):
        ids = {e["activity_id"] for e in lido}
        leituras.append((lido, [r for r in rows if r["activity_id"] in ids]))
    return leituras


def _todas_as_telas(sessoes, fadiga_por_row=None):
    resultados = []
    for lido, linhas in _leituras(sessoes, fadiga_por_row):
        (lote,), _ = svc.classify_batch(linhas, per_exercise_limit=svc.MAX_EXPOSURES, fatigue_by_row=fadiga_por_row)
        briefing, _, _ = svc.outcome_for_equipment(lido, LEG_PRESS)
        resultados.append(
            {
                "veredito": veredito(lido),
                "lote": (S(lote["status"]), lote["status_reasons"], lote["comparable_exposures"]),
                "seta": seta(lido),
                "briefing": (briefing.status, briefing.reasons, briefing.comparable_count),
            }
        )
    assert resultados[0] == resultados[1] == resultados[2]
    assert resultados[0]["veredito"] == resultados[0]["lote"] == resultados[0]["briefing"]
    return resultados[0]


def test_janela_plato_maquina_igual_em_todas_as_leituras():
    # G1: 100x10, 100x11, 100x11; G2 x5; G1 100x11. A 1a (100x10) sai da
    # janela de 8: sobram 3 em G1 sem movimento -> plato em todas as telas.
    sessoes = (
        sessao(1, 1, [10], carga=100.0, gym=1),
        sessao(2, 2, [11], carga=100.0, gym=1),
        sessao(3, 3, [11], carga=100.0, gym=1),
        *[sessao(4 + i, 4 + i, [10], carga=80.0, gym=2) for i in range(5)],
        sessao(9, 10, [11], carga=100.0, gym=1),
    )
    r = _todas_as_telas(sessoes)
    status, reasons, comparaveis = r["veredito"]
    assert status is S.PLATEAU_SUSPECTED and comparaveis == 3
    assert OUTRA_ACADEMIA in reasons and CALIBRANDO not in reasons


def test_janela_anterior_fora_da_janela_igual_em_todas_as_leituras():
    # G1 F1 x1; G1 F2 x2; G2 x5; G1 F1 (ultima). A unica anterior em G1 F1
    # esta fora da janela: primeira do contexto em todas as telas.
    sessoes = (
        sessao(1, 1, [10], gym=1),
        sessao(2, 2, [10], gym=1),
        sessao(3, 3, [10], gym=1),
        *[sessao(4 + i, 4 + i, [10], gym=2) for i in range(5)],
        sessao(9, 10, [12], gym=1),
    )
    fadiga = {2: FADIGA_F2, 3: FADIGA_F2}
    r = _todas_as_telas(sessoes, fadiga)
    status, reasons, comparaveis = r["veredito"]
    assert status is S.INSUFFICIENT_DATA and comparaveis == 1
    assert cmp.REASON_FIRST_IN_CONTEXT in reasons and CALIBRANDO not in reasons
    assert r["seta"]["status"] == S.NOT_COMPARABLE.value


def test_indices_do_par_e_marcacoes_apontam_para_a_lista_original():
    historico = [_resumo(10 + i) for i in range(12)]
    desfecho = cmp.progression_outcome(historico)
    assert desfecho.run_indices == list(range(4, 12))
    assert (desfecho.pair.previous_index, desfecho.pair.current_index) == (10, 11)
    contexto, marcado = _contexto_e_veredito(cmp.run_flags(desfecho, len(historico)))
    assert contexto == marcado == list(range(4, 12))
    dicts = montar(*[sessao(i, i, [10 + i]) for i in range(1, 13)])
    par = svc.latest_comparison(dicts)
    assert (par["previous_index"], par["current_index"]) == (10, 11)
    assert par["previous_activity_id"] == 11


def test_peso_livre_plato_decidido_sobre_as_ultimas_8():
    iguais = montar(*[sessao(i, i, [10], eq=HALTER, tipo="DUMBBELL", gym=None) for i in range(1, 13)])
    status, reasons, comparaveis = veredito(iguais)
    assert status is S.PLATEAU_SUSPECTED and comparaveis == cmp.VERDICT_LOOKBACK_EXPOSURES
    # Um progresso antes da janela nao desfaz o plato das 8 ultimas.
    antes = montar(
        sessao(1, 1, [8], eq=HALTER, tipo="DUMBBELL", gym=None),
        *[sessao(i, i, [10], eq=HALTER, tipo="DUMBBELL", gym=None) for i in range(2, 13)],
    )
    assert veredito(antes)[0] is S.PLATEAU_SUSPECTED


@pytest.mark.parametrize(
    ("delta", "better_when", "esperado"),
    [
        (2.5, "up", "improved"),
        (-2.5, "up", "worsened"),
        (-0.5, "down", "improved"),
        (0.5, "down", "worsened"),
        (0, "up", "flat"),
        (None, "up", "neutral"),
        (3.0, "neutral", "neutral"),
    ],
)
def test_metric_outcome(delta, better_when, esperado):
    assert cmp.metric_outcome(delta, better_when) == esperado


def test_latest_comparison_emite_outcome_por_metrica():
    par = svc.latest_comparison(montar(*ACADEMIA_1))
    por_codigo = {m["code"]: m for m in par["metrics"]}
    assert por_codigo["paired_reps"]["outcome"] == "improved"
    assert all(m["outcome"] == cmp.metric_outcome(m["delta"], m["better_when"]) for m in par["metrics"])


@pytest.mark.parametrize(
    ("decisao", "chave", "esperado"),
    [
        ({"equipment_id": 7}, None, True),
        ({"equipment_id": 7}, cmp.build_key(EX, None, "total"), True),
        ({"equipment_id": 7}, cmp.build_key(EX, 8, "total", equipment_agnostic=True), True),
        ({"equipment_id": None}, cmp.build_key(EX, 8, "total"), True),
        ({"equipment_id": 8}, cmp.build_key(EX, 8, "total", gym_id=3), True),
        ({"equipment_id": 7}, cmp.build_key(EX, 8, "total"), False),
    ],
)
def test_decisao_no_contexto_atual_segue_decisions_for_key(decisao, chave, esperado):
    assert svc.decision_in_context(decisao, chave) is esperado


def test_marcacoes_da_curva_sao_por_objetivo():
    forca = [dict(linha, training_goal="strength") for linha in sessao(3, 4, [5, 5], 90.0)]
    misturadas = montar(sessao(1, 1, [10, 10]), forca, sessao(2, 3, [11, 11]))
    marcas = svc.verdict_run_flags(misturadas)
    so_hipertrofia = svc.verdict_run_flags(montar(sessao(1, 1, [10, 10]), sessao(2, 3, [11, 11])))
    por_objetivo = [m for item, m in zip(misturadas, marcas) if item["_key"].training_goal == "hypertrophy"]
    assert por_objetivo == so_hipertrofia
    forca_marca = [m for item, m in zip(misturadas, marcas) if item["_key"].training_goal == "strength"]
    # Antes: a forca, pulada pela sequencia da hipertrofia, ficava sem marcacao.
    assert forca_marca == [{"in_context_run": True, "in_verdict_run": True}]
