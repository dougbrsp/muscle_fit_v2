"""Desconto do repouso da sessão no gasto do modo explícito/automático (ADR 0008, `nutrition-v3`).

O fator de rotina já cobre a TMB das 24 h; a kcal do treino (informada ou MET, ambas brutas) entra LÍQUIDA:
`kcal − TMB/1440 × minutos`, piso 0, pela MESMA regra do motor adaptativo (`activity_energy.net_of_resting_kcal`).
Dono: `energy_expenditure.py` (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7)."""

from __future__ import annotations

import pytest

from app.domain.nutrition import activity_energy as ae
from app.domain.nutrition import energy_expenditure as ee
from app.domain.nutrition import strategy as st
from app.domain.nutrition.anthropometrics import ActivityLevel, Sex, Subject
from app.domain.nutrition.daily_target import DayContext, PlanParams, SessionFacts, compute_daily_target, day_context_for
from app.domain.nutrition.labels import LIMITATION_LABELS, TDEE_BREAKDOWN_PART_LABELS
from datetime import date

TMB = 2020.24
REPOUSO_MIN = TMB / 1440.0
HOJE = date(2026, 11, 7)
SUJEITO = Subject(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=80, activity_level=ActivityLevel.MODERATE)


def _sessao(kcal, minutos):
    return [ee.SessionKcal(calories_kcal=kcal, duration_min=minutos)]


def test_a_regra_do_repouso_e_a_mesma_funcao_do_adaptativo():
    repouso = ae.resting_kcal(TMB, 107.2)
    assert ae.net_of_resting_kcal(874, ae.CalorieMode.UNKNOWN, repouso) == pytest.approx(874 - REPOUSO_MIN * 107.2)
    assert ae.net_of_resting_kcal(874, ae.CalorieMode.GROSS, repouso) == pytest.approx(874 - REPOUSO_MIN * 107.2)
    assert ae.net_of_resting_kcal(874, ae.CalorieMode.ACTIVE, repouso) == 874
    assert ae.net_of_resting_kcal(874, "total", repouso) == pytest.approx(874 - REPOUSO_MIN * 107.2)  # apelido de gross
    assert ae.net_of_resting_kcal(100, ae.CalorieMode.GROSS, 500.0) == 0.0  # piso 0
    assert ae.net_of_resting_kcal(874, ae.CalorieMode.GROSS, None) is None  # sem repouso quem chama decide
    assert ae.net_of_resting_kcal(874, ae.CalorieMode.ACTIVE, None) == 874
    assert ae.net_of_resting_kcal(None, ae.CalorieMode.GROSS, 10.0) is None
    # o adaptativo usa a mesma função: o `active_kcal` normalizado bate com o líquido
    bruta = ae.RawActivity(id="1", start=None, duration_seconds=107.2 * 60, kind="strength", calories_kcal=874)  # type: ignore[arg-type]
    assert ae.normalize_activity(bruta, TMB).active_kcal == pytest.approx(874 - REPOUSO_MIN * 107.2)


def test_informada_desconta_o_repouso_da_janela_e_expoe_bruta_e_desconto():
    t = ee.resolve_sessions_kcal(_sessao(874, 107.2), 80, None, TMB)
    assert t.kcal == pytest.approx(874 - REPOUSO_MIN * 107.2)
    assert t.kcal_nature == ee.TrainingKcalNature.REPORTED and t.limitations == ()
    assert t.gross_kcal == 874 and t.resting_discount_kcal == pytest.approx(REPOUSO_MIN * 107.2)
    assert t.to_dict()["gross_kcal"] == 874


def test_met_desconta_porque_e_bruta():
    t = ee.resolve_sessions_kcal([ee.SessionKcal(duration_min=60)], 80, None, TMB)
    assert t.kcal_nature == ee.TrainingKcalNature.MET
    assert t.kcal == pytest.approx(294.0 - REPOUSO_MIN * 60)
    # mesmo com o relógio declarado `active`, a MET segue bruta
    ativo = ee.resolve_sessions_kcal([ee.SessionKcal(duration_min=60)], 80, None, TMB, ae.CalorieMode.ACTIVE)
    assert ativo.kcal == pytest.approx(294.0 - REPOUSO_MIN * 60)
    unica = ee.resolve_training_kcal(weight_kg=80, duration_min=60, bmr_kcal=TMB)
    assert unica.kcal == pytest.approx(294.0 - REPOUSO_MIN * 60)


def test_active_entra_como_veio():
    t = ee.resolve_sessions_kcal(_sessao(500, 60), 80, None, TMB, ae.CalorieMode.ACTIVE)
    assert t.kcal == 500 and t.resting_discount_kcal == 0 and t.gross_kcal == 500
    assert t.to_dict()["gross_kcal"] == 500 and t.to_dict()["resting_discount_kcal"] == 0  # contrato: sempre presentes
    sem_duracao = ee.resolve_sessions_kcal(_sessao(500, None), 80, None, TMB, ae.CalorieMode.ACTIVE)
    assert sem_duracao.kcal == 500 and sem_duracao.limitations == ()  # active não precisa de duração


def test_piso_zero_quando_o_repouso_passa_da_kcal():
    t = ee.resolve_sessions_kcal(_sessao(50, 120), 80, None, TMB)
    assert t.kcal == 0.0 and t.kcal_nature == ee.TrainingKcalNature.REPORTED and t.gross_kcal == 50


def test_sem_duracao_mantem_a_kcal_e_limita_sem_descartar_o_treino():
    t = ee.resolve_sessions_kcal(_sessao(874, None), 80, None, TMB)
    assert t.kcal == 874 and t.limitations == (ee.LIMIT_REST_NOT_DISCOUNTED,)
    assert ee.resolve_training_kcal(reported_kcal=874, bmr_kcal=TMB).limitations == (ee.LIMIT_REST_NOT_DISCOUNTED,)
    # sem TMB o gasto já é desconhecido (limitação própria): não se duplica o aviso e nada é descontado
    sem_tmb = ee.resolve_sessions_kcal(_sessao(874, 60), 80, None, None)
    assert sem_tmb.kcal == 874 and sem_tmb.limitations == ()
    assert ee.LIMIT_REST_NOT_DISCOUNTED in LIMITATION_LABELS and LIMITATION_LABELS[ee.LIMIT_REST_NOT_DISCOUNTED]


def test_dia_com_sessao_sem_duracao_desconta_so_a_que_tem_duracao():
    sessoes = [ee.SessionKcal(calories_kcal=300, duration_min=30), ee.SessionKcal(calories_kcal=200)]
    t = ee.resolve_sessions_kcal(sessoes, 80, None, TMB)
    assert t.kcal == pytest.approx(500 - REPOUSO_MIN * 30)
    assert t.kcal_nature == ee.TrainingKcalNature.REPORTED and t.limitations == (ee.LIMIT_REST_NOT_DISCOUNTED,)


def test_dia_misto_desconta_informada_e_met_por_sessao():
    sessoes = [ee.SessionKcal(calories_kcal=300, duration_min=30), ee.SessionKcal(duration_min=30)]
    t = ee.resolve_sessions_kcal(sessoes, 80, None, TMB)
    assert t.kcal == pytest.approx(300 + 147.0 - 2 * REPOUSO_MIN * 30)
    assert t.kcal_nature == ee.TrainingKcalNature.MET and t.limitations == (ee.LIMIT_TRAINING_PARTIALLY_ESTIMATED,)


def test_sem_bmr_nada_muda_o_comportamento_de_antes():
    assert ee.resolve_sessions_kcal(_sessao(874, 107.2), 80, None).kcal == 874
    assert ee.resolve_training_kcal(reported_kcal=874, duration_min=107.2).kcal == 874


def test_tdee_do_caso_real_07_11_do_plano_10():
    t = ee.resolve_sessions_kcal(_sessao(874, 6433 / 60), 80, None, TMB)
    resultado = ee.tdee(TMB, ActivityLevel.LIGHT, ee.TdeeMode.EXPLICIT_TRAINING, t.kcal)
    assert resultado.kcal == pytest.approx(TMB * 1.3 + 874 - TMB / 1440 * 6433 / 60)
    assert round(resultado.kcal) == 3350  # antes: 3.500


def test_breakdown_mostra_a_parcela_liquida_e_soma_o_total():
    t = ee.resolve_sessions_kcal(_sessao(874, 6433 / 60), 80, None, TMB)
    extra = ee.extra_activity_kcal((ee.ExtraActivity(kind="cardio", calories_kcal=300, duration_seconds=1800),), TMB)
    resultado = ee.tdee(TMB, ActivityLevel.LIGHT, ee.TdeeMode.EXPLICIT_TRAINING, t.kcal + extra.kcal)
    quebra = ee.tdee_breakdown(resultado, extra.kcal, ee.TdeeBasis.AUTO_SYNCED)
    assert quebra is not None
    partes = {p.code: p.kcal for p in quebra.parts}
    assert partes["training"] == round(t.kcal) and partes["extras"] == round(extra.kcal)
    assert sum(partes.values()) == quebra.total_kcal == round(resultado.kcal)
    assert "sem o repouso" in TDEE_BREAKDOWN_PART_LABELS[ee.BREAKDOWN_STRENGTH]
    assert "sem o repouso" in TDEE_BREAKDOWN_PART_LABELS[ee.BREAKDOWN_EXTRAS]


def _plano(modo, **extra):
    return PlanParams(strategy_params=st.StrategyParams.defaults_for(st.Strategy.MAINTENANCE), tdee_mode=modo, **extra)


def test_embutido_continua_ignorando_o_treino_e_sem_aviso():
    contexto = day_context_for(HOJE, HOJE, [SessionFacts(calories_kcal=874, duration_seconds=6433)])
    meta = compute_daily_target(SUJEITO, _plano(ee.TdeeMode.INCLUDED_IN_FACTOR), contexto)
    assert meta.tdee.kcal == pytest.approx(meta.bmr.kcal * 1.55)
    assert ee.LIMIT_REST_NOT_DISCOUNTED not in meta.limitations
    sem_duracao = day_context_for(HOJE, HOJE, [SessionFacts(calories_kcal=874)])
    assert ee.LIMIT_REST_NOT_DISCOUNTED not in compute_daily_target(SUJEITO, _plano(ee.TdeeMode.INCLUDED_IN_FACTOR), sem_duracao).limitations


def test_automatico_desconta_treino_e_extras_com_duracao_e_avisa_o_extra_sem_duracao():
    extras = (
        ee.ExtraActivity(kind="cardio", calories_kcal=300, duration_seconds=1800),
        ee.ExtraActivity(kind="other", calories_kcal=100),
    )
    contexto = day_context_for(
        HOJE, HOJE, [SessionFacts(calories_kcal=500, duration_seconds=3600)], extras, intervals_in_sync=True
    )
    meta = compute_daily_target(SUJEITO, _plano(ee.TdeeMode.AUTO_INTERVALS), contexto)
    tmb = meta.bmr.kcal
    assert meta.training_kcal.kcal == pytest.approx(500 - tmb / 1440 * 60)
    assert meta.extra_activity.kcal == pytest.approx(400 - tmb / 1440 * 30)
    assert meta.tdee.training_kcal == pytest.approx(meta.training_kcal.kcal + meta.extra_activity.kcal)
    assert ee.LIMIT_REST_NOT_DISCOUNTED in meta.limitations
    fallback = day_context_for(HOJE, HOJE, [SessionFacts(calories_kcal=500, duration_seconds=3600)], extras, intervals_in_sync=False)
    assert meta.tdee.kcal != compute_daily_target(SUJEITO, _plano(ee.TdeeMode.AUTO_INTERVALS), fallback).tdee.kcal


def test_adaptativo_nao_muda_e_a_versao_do_motor_e_v3():
    from app.domain.nutrition import ENGINE_VERSION

    assert ENGINE_VERSION == "nutrition-v4"
    contexto = DayContext(day=HOJE, is_training_day=True, training_kcal_reported=500.0, training_duration_min=60.0)
    meta = compute_daily_target(SUJEITO, _plano(ee.TdeeMode.ADAPTIVE_BALANCE), contexto)
    assert meta.tdee.adaptive is not None and meta.tdee.training_kcal is None  # o treino não entra pela via explícita


# Dias reais do usuário 1 (activities, 2026-11-02..07): antes × depois pelo motor puro, fator 1,3.
_DIAS_REAIS = [
    ("2026-11-02", 7943, 763, 2020.24, 3389, 3204),
    ("2026-11-03", 6357, 707, 2015.92, 3328, 3179),
    ("2026-11-05", 4859, 428, 2020.24, 3054, 2941),
    ("2026-11-06", 5304, 508, 2014.0, 3126, 3003),
    ("2026-11-07", 6433, 874, 2020.24, 3500, 3350),
]


@pytest.mark.parametrize(("dia", "segundos", "kcal", "tmb", "antes", "depois"), _DIAS_REAIS)
def test_tabela_de_impacto_do_adr_0008(dia, segundos, kcal, tmb, antes, depois):
    nivel = ActivityLevel.LIGHT  # fator de não-exercício 1,3
    t = ee.resolve_sessions_kcal(_sessao(kcal, segundos / 60), 80, None, tmb)
    assert round(ee.tdee(tmb, nivel, ee.TdeeMode.EXPLICIT_TRAINING, kcal).kcal) == antes
    assert round(ee.tdee(tmb, nivel, ee.TdeeMode.EXPLICIT_TRAINING, t.kcal).kcal) == depois
