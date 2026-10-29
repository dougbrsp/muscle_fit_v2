"""Orquestrador da meta diária (dono `app/domain/nutrition/daily_target.py`, E2):
TMB -> kcal do treino -> TDEE -> alvo -> macros -> ciclo/fase. Nunca levanta por
dado faltante: sem altura, sexo ou peso o alvo é None com a limitação."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.domain.nutrition import ENGINE_VERSION, DailyTarget, DayContext, PlanParams, compute_daily_target
from app.domain.nutrition import bmr as bm
from app.domain.nutrition import carb_cycling as cc
from app.domain.nutrition import energy_expenditure as ee
from app.domain.nutrition import strategy as st
from app.domain.nutrition.anthropometrics import ActivityLevel, Sex, Subject
from app.domain.nutrition.phases import PhaseKind

SEGUNDA = date(2026, 10, 12)
S = st.Strategy

# Homem 80 kg, 180 cm, 30 anos, moderado, sem %G -> Mifflin 1.780 (plano §14).
SUJEITO = Subject(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=80, activity_level=ActivityLevel.MODERATE)
TMB = 1780.0
TDEE_DESCANSO = TMB * 1.4  # fator de não-exercício "moderado" (D-A)
# Mesmo sujeito com 15% de gordura (bioimpedância) -> MLG 68 kg -> Katch-McArdle.
COM_MLG = replace(SUJEITO, body_fat_percent=15.0)


def _plano(estrategia: S = S.MAINTENANCE, **extra) -> PlanParams:
    return PlanParams(strategy_params=st.StrategyParams.defaults_for(estrategia), **extra)


def _dia(**extra) -> DayContext:
    return DayContext(day=SEGUNDA, **extra)


def test_referencia_manutencao_em_dia_de_descanso():
    meta = compute_daily_target(SUJEITO, _plano(), _dia())
    assert isinstance(meta, DailyTarget) and meta.computable
    assert meta.engine_version == ENGINE_VERSION == "nutrition-v1"
    assert meta.bmr.kcal == pytest.approx(TMB) and meta.bmr.formula == bm.BmrFormula.MIFFLIN_ST_JEOR
    assert meta.tdee.kcal == pytest.approx(TDEE_DESCANSO)
    assert meta.kcal_target.kcal == pytest.approx(TDEE_DESCANSO)
    assert meta.macros.protein_g == pytest.approx(2.0 * 80)
    assert meta.macros.fat_g == pytest.approx(0.25 * TDEE_DESCANSO / 9)
    assert meta.macros.carb_g == pytest.approx((TDEE_DESCANSO - 160 * 4 - 0.25 * TDEE_DESCANSO) / 4)
    assert meta.day_kind == cc.DayKind.NORMAL and meta.phase_kind is None
    assert meta.training_kcal is ee.NO_TRAINING_KCAL
    assert meta.limitations == ()
    json.dumps(meta.to_dict())


@pytest.mark.parametrize(
    ("contexto", "kcal_treino", "natureza"),
    [
        (dict(is_training_day=True, training_kcal_reported=350.0), 350.0, ee.TrainingKcalNature.REPORTED),
        (dict(training_kcal_reported=350.0), 350.0, ee.TrainingKcalNature.REPORTED),
        (dict(is_training_day=True, training_duration_min=60), 294.0, ee.TrainingKcalNature.MET),
        (dict(is_training_day=True, training_duration_min=60, session_kind=ee.SessionKind.VIGOROUS), 504.0, ee.TrainingKcalNature.MET),
        (dict(is_training_day=True, training_kcal_reported=350.0, training_duration_min=60), 350.0, ee.TrainingKcalNature.REPORTED),
    ],
)
def test_treino_do_dia_entra_explicito_no_tdee(contexto, kcal_treino, natureza):
    meta = compute_daily_target(SUJEITO, _plano(), _dia(**contexto))
    assert meta.training_kcal.kcal == pytest.approx(kcal_treino)
    assert meta.training_kcal.kcal_nature == natureza
    assert meta.tdee.kcal == pytest.approx(TDEE_DESCANSO + kcal_treino)
    assert meta.limitations == ()


def test_dia_de_treino_sem_kcal_nem_duracao_avisa():
    meta = compute_daily_target(SUJEITO, _plano(), _dia(is_training_day=True))
    assert meta.tdee.kcal == pytest.approx(TDEE_DESCANSO)
    assert ee.LIMIT_NO_TRAINING_KCAL in meta.limitations


def test_treino_embutido_no_fator_nao_soma_nem_avisa():
    meta = compute_daily_target(
        SUJEITO, _plano(tdee_mode=ee.TdeeMode.INCLUDED_IN_FACTOR), _dia(is_training_day=True, training_kcal_reported=350.0)
    )
    assert meta.tdee.kcal == pytest.approx(TMB * 1.55)
    sem_dado = compute_daily_target(SUJEITO, _plano(tdee_mode=ee.TdeeMode.INCLUDED_IN_FACTOR), _dia(is_training_day=True))
    assert sem_dado.limitations == ()


@pytest.mark.parametrize("estrategia", list(S))
@pytest.mark.parametrize(
    ("sujeito", "limitacao"),
    [
        (replace(SUJEITO, height_cm=None), bm.LIMIT_MISSING_HEIGHT),
        (replace(SUJEITO, height_cm=90), bm.LIMIT_MISSING_HEIGHT),  # fora de HEIGHT_CM_RANGE
        (replace(SUJEITO, sex=None), bm.LIMIT_MISSING_SEX),
        (replace(SUJEITO, sex=Sex.OTHER), bm.LIMIT_SEX_OTHER_REQUIRES_FFM),
        (replace(SUJEITO, weight_kg=None), bm.LIMIT_MISSING_WEIGHT),
        (replace(SUJEITO, age_years=None), bm.LIMIT_MISSING_AGE),
    ],
)
def test_sem_altura_sexo_ou_peso_o_alvo_e_none_com_limitacao(estrategia, sujeito, limitacao):
    meta = compute_daily_target(sujeito, _plano(estrategia), _dia(is_training_day=True, training_duration_min=60))
    assert not meta.computable
    assert meta.bmr.kcal is None and meta.tdee.kcal is None and meta.kcal_target.kcal is None
    assert meta.macros.kcal is None and meta.macros.carb_g is None
    assert limitacao in meta.limitations
    json.dumps(meta.to_dict())


@pytest.mark.parametrize("estrategia", list(S))
def test_com_mlg_recente_katch_dispensa_altura_e_sexo_mas_nao_o_peso(estrategia):
    sem_altura_sexo = replace(COM_MLG, height_cm=None, sex=None)
    meta = compute_daily_target(sem_altura_sexo, _plano(estrategia), _dia())
    assert meta.bmr.formula == bm.BmrFormula.KATCH_MCARDLE
    assert meta.bmr.kcal == pytest.approx(370 + 21.6 * 68)
    assert meta.computable

    sem_peso = replace(COM_MLG, weight_kg=None, fat_free_mass_kg=68.0, body_fat_percent=None)
    sem_peso_meta = compute_daily_target(sem_peso, _plano(estrategia), _dia())
    assert sem_peso_meta.bmr.kcal is not None  # Katch só precisa da MLG
    assert not sem_peso_meta.computable  # mas alvo/proteína precisam do peso


def test_mlg_velha_volta_para_mifflin():
    meta = compute_daily_target(COM_MLG, _plano(ffm_age_days=bm.FFM_FRESHNESS_MAX_DAYS + 1), _dia())
    assert meta.bmr.formula == bm.BmrFormula.MIFFLIN_ST_JEOR and meta.bmr.kcal == pytest.approx(TMB)


def test_cutting_fica_abaixo_do_tdee_e_proteina_pela_mlg():
    meta = compute_daily_target(COM_MLG, _plano(S.CUTTING), _dia())
    assert meta.kcal_target.kcal < meta.tdee.kcal
    assert meta.macros.protein_g == pytest.approx(2.6 * 68)
    assert meta.limitations == ()


@pytest.mark.parametrize("fase", [PhaseKind.DIET_BREAK, PhaseKind.REFEED, PhaseKind.MAINTENANCE])
def test_pausa_e_refeed_levam_o_alvo_a_manutencao_sem_aviso_falso(fase):
    normal = compute_daily_target(COM_MLG, _plano(S.CUTTING), _dia(phase_kind=PhaseKind.CUTTING))
    pausa = compute_daily_target(COM_MLG, _plano(S.CUTTING), _dia(phase_kind=fase))
    assert pausa.kcal_target.kcal == pytest.approx(pausa.tdee.kcal)
    assert pausa.macros.protein_g == pytest.approx(normal.macros.protein_g)  # mesma proteína do plano
    assert pausa.macros.carb_g > normal.macros.carb_g  # o carbo absorve a diferença
    assert pausa.phase_kind == fase
    # Regressão (2026-10-16): a proteína do cutting (2,6 g/kg MLG) não é "fora da faixa" da manutenção.
    assert pausa.limitations == ()


def test_parametro_fora_da_faixa_do_plano_avisa_em_qualquer_fase():
    params = replace(st.StrategyParams.defaults_for(S.CUTTING), protein_g_per_kg=1.0)
    for fase in (None, PhaseKind.CUTTING, PhaseKind.DIET_BREAK):
        meta = compute_daily_target(COM_MLG, PlanParams(strategy_params=params), _dia(phase_kind=fase))
        assert f"{st.LIMIT_PARAM_OUT_OF_RANGE}:protein_g_per_kg" in meta.limitations


SEMANA = (True, False, True, False, True, False, False)


def test_ciclo_de_carbo_soma_sete_vezes_a_meta_sem_ciclo():
    plano = _plano(S.BULKING, carb_cycling_enabled=True)
    sem_ciclo = compute_daily_target(SUJEITO, _plano(S.BULKING), _dia())
    metas = [
        compute_daily_target(SUJEITO, plano, DayContext(day=SEGUNDA + timedelta(days=i), week_training_days=SEMANA))
        for i in range(7)
    ]
    assert [m.day_kind for m in metas] == list(cc.day_kinds_for_week(SEMANA))
    assert sum(m.macros.carb_g for m in metas) == pytest.approx(7 * sem_ciclo.macros.carb_g)
    assert sum(m.macros.kcal for m in metas) == pytest.approx(7 * sem_ciclo.macros.kcal)
    assert all(m.macros.protein_g == pytest.approx(sem_ciclo.macros.protein_g) for m in metas)
    assert all(m.macros.fat_g == pytest.approx(sem_ciclo.macros.fat_g) for m in metas)
    assert all(m.limitations == () for m in metas)


def test_ciclo_sem_os_flags_da_semana_avisa_e_fica_normal():
    meta = compute_daily_target(SUJEITO, _plano(S.BULKING, carb_cycling_enabled=True), _dia())
    assert meta.day_kind == cc.DayKind.NORMAL
    assert meta.limitations == ("week_training_days_required_for_carb_cycle",)


def test_proteina_por_refeicao_e_balanca():
    meta = compute_daily_target(SUJEITO, _plano(meals_per_day=4), _dia(), scale_bmr=2100)
    assert meta.protein_per_meal_g == pytest.approx(160 / 4)
    assert meta.scale_comparison.warn is True  # 2.100 vs 1.780 = +18% (> 15%)
    assert compute_daily_target(SUJEITO, _plano(), _dia(), scale_bmr=1800).scale_comparison.warn is False
    assert compute_daily_target(SUJEITO, _plano(), _dia()).scale_comparison is None


# ---------------------------------------------------------------------------
# L2b: congelamento, contexto do dia e dado faltante
# ---------------------------------------------------------------------------

from app.domain.nutrition import daily_target as dt  # noqa: E402

HOJE = date(2026, 10, 17)


def test_congela_dois_dias_depois_pelo_sync_do_intervals():
    assert dt.TARGET_FREEZE_LAG_DAYS == 2
    assert dt.is_target_frozen(HOJE, HOJE) is False
    assert dt.is_target_frozen(HOJE - timedelta(days=1), HOJE) is False  # ontem: a kcal do Intervals chega hoje cedo
    assert dt.is_target_frozen(HOJE - timedelta(days=2), HOJE) is True
    assert dt.is_target_frozen(HOJE + timedelta(days=5), HOJE) is False


def test_dia_futuro_nao_sabe_do_treino_e_avisa():
    contexto = dt.day_context_for(HOJE + timedelta(days=1), HOJE, [dt.SessionFacts(calories_kcal=400)])
    assert contexto.training_known is False and contexto.is_training_day is False
    meta = compute_daily_target(SUJEITO, _plano(), contexto)
    assert meta.computable
    assert meta.limitations == (dt.LIMIT_TRAINING_UNKNOWN_FUTURE_DAY,)
    assert meta.training_kcal is ee.NO_TRAINING_KCAL
    assert contexto.to_dict()["training_known"] is False


def test_dia_sem_sessao_e_descanso_sem_aviso():
    contexto = dt.day_context_for(HOJE, HOJE, [])
    assert contexto == DayContext(day=HOJE)
    assert compute_daily_target(SUJEITO, _plano(), contexto).limitations == ()


def test_kcal_informada_soma_as_sessoes_quando_todas_tem_kcal():
    sessoes = [dt.SessionFacts(calories_kcal=300, duration_seconds=3600), dt.SessionFacts(calories_kcal=150.5)]
    contexto = dt.day_context_for(HOJE - timedelta(days=1), HOJE, sessoes)
    assert contexto.is_training_day and contexto.training_kcal_reported == pytest.approx(450.5)
    meta = compute_daily_target(SUJEITO, _plano(), contexto)
    assert meta.training_kcal.kcal_nature == ee.TrainingKcalNature.REPORTED
    assert meta.tdee.kcal == pytest.approx(TDEE_DESCANSO + 450.5)


def test_sessao_sem_kcal_derruba_a_soma_para_met_pela_duracao():
    sessoes = [dt.SessionFacts(calories_kcal=300, duration_seconds=1800), dt.SessionFacts(duration_seconds=1800)]
    contexto = dt.day_context_for(HOJE, HOJE, sessoes)
    assert contexto.training_kcal_reported is None
    assert contexto.training_duration_min == pytest.approx(60.0)
    meta = compute_daily_target(SUJEITO, _plano(), contexto)
    assert meta.training_kcal.kcal_nature == ee.TrainingKcalNature.MET
    assert meta.training_kcal.kcal == pytest.approx(ee.training_kcal_met(80, 60))


def test_sessao_sem_kcal_nem_duracao_avisa_treino_indisponivel():
    contexto = dt.day_context_for(HOJE, HOJE, [dt.SessionFacts()])
    assert contexto.is_training_day and contexto.training_kcal_reported is None and contexto.training_duration_min is None
    assert ee.LIMIT_NO_TRAINING_KCAL in compute_daily_target(SUJEITO, _plano(), contexto).limitations


def test_dado_faltante_do_sujeito_lista_so_o_que_preencher():
    sem_altura_nem_sexo = replace(SUJEITO, height_cm=None, sex=None)
    meta = compute_daily_target(sem_altura_nem_sexo, _plano(), _dia())
    assert not meta.computable
    assert dt.missing_subject_data(meta) == (bm.LIMIT_MISSING_SEX, bm.LIMIT_MISSING_HEIGHT)
    assert dt.missing_subject_data(compute_daily_target(SUJEITO, _plano(), _dia())) == ()
    sem_nivel = replace(SUJEITO, activity_level=None)
    assert dt.missing_subject_data(compute_daily_target(sem_nivel, _plano(), _dia())) == (ee.LIMIT_MISSING_ACTIVITY_LEVEL,)
    # Cutting sem %G: falta a massa gorda (e a base da proteína, que é a MLG).
    faltas = dt.missing_subject_data(compute_daily_target(SUJEITO, _plano(S.CUTTING), _dia()))
    assert st.LIMIT_MISSING_FAT_MASS in faltas


# ---------------------------------------------------------------------------
# L2b (revisão): janelas de materialização no motor
# ---------------------------------------------------------------------------

SABADO = date(2026, 10, 17)
DOMINGO = date(2026, 10, 18)


def test_primeiro_dia_recalculavel_e_o_mesmo_criterio_do_congelamento():
    primeiro = dt.first_recomputable_day(SABADO)
    assert primeiro == SABADO - timedelta(days=1)
    assert dt.is_target_frozen(primeiro, SABADO) is False
    assert dt.is_target_frozen(primeiro - timedelta(days=1), SABADO) is True


def test_janela_da_semana():
    janela = dt.week_window(SABADO)
    assert janela.date_from == date(2026, 10, 12) and janela.date_to == DOMINGO == janela.create_until
    assert len(janela.days()) == 7


def test_janela_de_publicacao_cobre_toda_meta_ja_materializada():
    sem_metas = dt.publication_window(SABADO, None)
    assert (sem_metas.date_from, sem_metas.date_to, sem_metas.create_until) == (SABADO, DOMINGO, DOMINGO)
    longe = SABADO + timedelta(days=dt.TARGET_RECOMPUTE_MAX_DAYS + 10)  # além do teto: ainda atualiza
    janela = dt.publication_window(SABADO, longe)
    assert janela.date_to == longe and janela.create_until == DOMINGO
    assert dt.publication_window(SABADO, SABADO - timedelta(days=3)).date_to == DOMINGO


@pytest.mark.parametrize(
    ("de", "ate", "erro"),
    [
        (SABADO - timedelta(days=2), None, dt.ERR_DATE_FROM_FROZEN),
        (SABADO, SABADO - timedelta(days=1), dt.ERR_DATE_TO_BEFORE_DATE_FROM),
        (SABADO, SABADO + timedelta(days=dt.TARGET_RECOMPUTE_MAX_DAYS), dt.ERR_RANGE_TOO_LONG),
    ],
)
def test_janela_de_recalculo_recusa_com_o_codigo_do_motor(de, ate, erro):
    resultado = dt.recompute_window(de, ate, SABADO)
    assert resultado.window is None and resultado.error == erro


def test_janela_de_recalculo_valida():
    ontem = dt.recompute_window(SABADO - timedelta(days=1), None, SABADO).window
    assert ontem.date_from == SABADO - timedelta(days=1) and ontem.date_to == DOMINGO == ontem.create_until
    no_teto = dt.recompute_window(SABADO, SABADO + timedelta(days=dt.TARGET_RECOMPUTE_MAX_DAYS - 1), SABADO).window
    assert dt.days_in_range(no_teto.date_from, no_teto.date_to) == dt.TARGET_RECOMPUTE_MAX_DAYS
    assert dt.range_within_cap(no_teto.date_from, no_teto.date_to) is True
    assert dt.days_in_range(SABADO, SABADO - timedelta(days=1)) == 0


def test_dias_a_materializar_respeitam_congelamento_e_criacao():
    janela = dt.TargetWindow(date_from=SABADO - timedelta(days=3), date_to=SABADO + timedelta(days=5),
                             create_until=SABADO + timedelta(days=1))
    existentes = {SABADO - timedelta(days=3), SABADO + timedelta(days=4)}
    dias = dt.days_to_materialize(janela, SABADO, existentes)
    # anteontem e antes: congelados (mesmo existindo); amanhã+1 em diante só se já existe.
    assert dias == (SABADO - timedelta(days=1), SABADO, SABADO + timedelta(days=1), SABADO + timedelta(days=4))
    assert janela.to_dict()["create_until"] == (SABADO + timedelta(days=1)).isoformat()


# ---------------------------------------------------------------------------
# Diário (L4, risco R8): meta do dia lido pelo diário
# ---------------------------------------------------------------------------

A = dt.TargetAction


@pytest.mark.parametrize(
    ("dia", "tem_meta", "acao"),
    [
        (SABADO - timedelta(days=dt.TARGET_FREEZE_LAG_DAYS), True, A.READ),  # congelado com meta: só lê
        (SABADO - timedelta(days=30), False, A.BACKFILL),  # congelado SEM meta: grava uma vez
        (SABADO - timedelta(days=1), False, A.MATERIALIZE),  # ontem ainda recalcula (kcal do Intervals)
        (SABADO, True, A.MATERIALIZE),
        (DOMINGO, False, A.MATERIALIZE),  # fim da semana atual: cria
        (DOMINGO + timedelta(days=1), False, A.SKIP),  # além da semana, sem meta: não cria
        (DOMINGO + timedelta(days=3), True, A.MATERIALIZE),  # além da semana COM meta (publicação): atualiza
    ],
)
def test_diary_target_action(dia, tem_meta, acao):
    assert dt.diary_target_action(dia, SABADO, tem_meta) == acao
    assert dt.diary_target_notice(acao) == ((dt.LIMIT_TARGET_FUTURE_BEYOND_WEEK,) if acao == A.SKIP else ())


def test_diary_window_cria_so_ate_o_fim_da_semana():
    assert dt.diary_window([], SABADO) is None
    janela = dt.diary_window([SABADO + timedelta(days=4), SABADO - timedelta(days=1), SABADO], SABADO)
    assert (janela.date_from, janela.date_to, janela.create_until) == (
        SABADO - timedelta(days=1), SABADO + timedelta(days=4), DOMINGO)
    # Um dia além da semana sem meta, dentro da janela, NÃO é criado; o existente é atualizado.
    dias = dt.days_to_materialize(janela, SABADO, {SABADO + timedelta(days=4)})
    assert SABADO + timedelta(days=2) not in dias and SABADO + timedelta(days=4) in dias
    so_hoje = dt.diary_window([SABADO], SABADO)
    assert (so_hoje.date_from, so_hoje.date_to, so_hoje.create_until) == (SABADO, SABADO, SABADO)


def test_meta_retroativa_marcada_nas_limitacoes_e_nas_entradas():
    meta = compute_daily_target(SUJEITO, _plano(), _dia())
    marcadas = dt.backfill_limitations(meta)
    assert marcadas[-1] == dt.LIMIT_TARGET_BACKFILLED and marcadas[:-1] == meta.limitations
    assert dt.backfill_limitations(replace(meta, limitations=marcadas)) == marcadas  # sem repetir
    assert dt.was_backfilled(marcadas) is True and dt.was_backfilled(meta.limitations) is False
    assert dt.was_backfilled(None) is False
    assert dt.backfill_marker(SABADO) == {"kind": dt.BACKFILL_KIND, "computed_on": "2026-10-17"}
