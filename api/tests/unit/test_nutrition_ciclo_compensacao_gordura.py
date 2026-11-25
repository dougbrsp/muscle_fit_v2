"""Ciclo com compensação carboidrato <-> gordura (#242): dono `carb_cycling.py`, integrado por `daily_target.py` e
`plan.py`. Padrão (`vary_kcal`) = comportamento de sempre."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.domain.nutrition import DayContext, PlanParams, compute_daily_target
from app.domain.nutrition import carb_cycling as cc
from app.domain.nutrition import labels as lb
from app.domain.nutrition import macros as mc
from app.domain.nutrition import plan as pl
from app.domain.nutrition import strategy as st
from app.domain.nutrition.anthropometrics import ActivityLevel, Sex, Subject
from app.domain.nutrition.energy_expenditure import TdeeMode
from app.domain.nutrition.manual_targets import ManualTrainingMode
from app.domain.nutrition.phases import PhaseKind

H, N, L = cc.DayKind.HIGH, cc.DayKind.NORMAL, cc.DayKind.LOW
FAT = cc.CarbCyclePolicy.FAT_COMPENSATION
SEGUNDA = date(2026, 10, 12)
SUJEITO = Subject(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=80, activity_level=ActivityLevel.MODERATE)
SEMANA = (H, L, H, L, H, L, L)
MANUAL = (160.0, 250.0, 70.0)


def _alvo(proteina=160.0, carbo=250.0, gordura=70.0) -> mc.MacroTargets:
    kcal = mc.kcal_from_macros(proteina, carbo, gordura)
    return mc.MacroTargets(
        kcal=kcal, protein_g=proteina, carb_g=carbo, fat_g=gordura, fiber_g=mc.fiber_target_g(kcal),
        protein_basis=st.ProteinBasis.BODY_WEIGHT, protein_basis_kg=80.0, protein_g_per_kg=2.0,
    )


def _plano(estrategia=st.Strategy.MAINTENANCE, **extra) -> PlanParams:
    return PlanParams(strategy_params=st.StrategyParams.defaults_for(estrategia), **extra)


def _dia(i: int, **extra) -> DayContext:
    return DayContext(day=SEGUNDA + timedelta(days=i), **extra)


# --- carb_cycling ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("delta", "gordura"), [(-45.0, 90.0), (45.0, 50.0)])
def test_exemplo_aritmetico_da_issue(delta, gordura):
    # -45 g de carbo = -180 kcal = +20 g de gordura; +45 g = -20 g.
    alvo = _alvo()
    dia, lim = cc._day_with_fat_compensation(alvo, delta)
    assert lim == ()
    assert dia.carb_g == pytest.approx(250.0 + delta) and dia.fat_g == pytest.approx(gordura)
    assert dia.protein_g == alvo.protein_g and dia.kcal == pytest.approx(alvo.kcal)
    assert dia.kcal == pytest.approx(mc.kcal_from_macros(dia.protein_g, dia.carb_g, dia.fat_g))


def test_dias_do_ciclo_baixo_alto_e_normal():
    alvo = _alvo()
    por_tipo = {k: cc.apply_cycle_over_base(alvo, alvo, k, SEMANA, 80.0, policy=FAT) for k in (H, N, L)}
    assert all(lim == () for _, lim in por_tipo.values())
    (alto, _), (baixo, _) = por_tipo[H], por_tipo[L]
    assert alto.carb_g > alvo.carb_g > baixo.carb_g and alto.fat_g < alvo.fat_g < baixo.fat_g
    for dia, _ in por_tipo.values():
        assert dia.protein_g == alvo.protein_g and dia.kcal == pytest.approx(alvo.kcal)
        assert dia.kcal == pytest.approx(mc.kcal_from_macros(dia.protein_g, dia.carb_g, dia.fat_g))
    # Mesmo delta de carbo do modo antigo (multiplicadores intocados).
    antigo, _ = cc.apply_cycle_over_base(alvo, alvo, L, SEMANA, 80.0)
    assert baixo.carb_g == pytest.approx(antigo.carb_g)


@pytest.mark.parametrize(
    "alvo", [_alvo(), _alvo(carbo=180.0, gordura=95.0)], ids=["percentual_ou_g_kg", "manual_trio"]
)
@pytest.mark.parametrize("kind", [H, N, L])
def test_padrao_e_politica_ausente_sao_identicos(alvo, kind):
    base = _alvo(carbo=250.0)  # base sem treino diferente do alvo do dia (add_training)
    for kfs in (False, True):
        sem = cc.apply_cycle_over_base(alvo, base, kind, SEMANA, 80.0, kfs)
        com = cc.apply_cycle_over_base(alvo, base, kind, SEMANA, 80.0, kfs, policy=cc.CarbCyclePolicy.VARY_KCAL)
        assert sem == com
    assert cc.DEFAULT_CARB_CYCLE_POLICY == cc.CarbCyclePolicy.VARY_KCAL


def test_gordura_negativa_nao_cicla_e_avisa():
    alvo = _alvo(gordura=10.0)  # dia alto pede -20 g de gordura: não há
    dia, lim = cc.apply_cycle_over_base(alvo, alvo, H, SEMANA, 80.0, policy=FAT)
    assert dia == alvo and cc.LIMIT_FAT_COMPENSATION_INFEASIBLE in lim


def test_alvo_que_nao_fecha_por_atwater_nao_cicla():
    fechado = _alvo()
    aberto = replace(fechado, kcal=fechado.kcal - 200.0)
    dia, lim = cc.apply_cycle_over_base(aberto, aberto, L, SEMANA, 80.0, policy=FAT)
    assert dia == aberto and lim == (cc.LIMIT_FAT_COMPENSATION_TARGET_NOT_CLOSED,)
    limitado = replace(fechado, limitations=(mc.LIMIT_KCAL_BELOW_PROTEIN_AND_FAT,))
    assert cc.apply_cycle_over_base(limitado, limitado, L, SEMANA, 80.0, policy=FAT)[1] == (
        cc.LIMIT_FAT_COMPENSATION_TARGET_NOT_CLOSED,
    )


def test_alvo_nao_calculavel_mantem_o_limite_de_sempre():
    nulo = replace(_alvo(), carb_g=None)
    assert cc.apply_cycle_over_base(nulo, nulo, L, SEMANA, 80.0, policy=FAT)[1] == (cc.LIMIT_TARGET_NOT_COMPUTABLE,)
    assert cc._day_with_fat_compensation(nulo, -10.0)[1] == (cc.LIMIT_FAT_COMPENSATION_TARGET_NOT_CLOSED,)


# --- daily_target ---------------------------------------------------------------------------------------------


def _semana(plano, sujeito=SUJEITO, **extra):
    return [compute_daily_target(sujeito, plano, _dia(i, **extra)) for i in range(7)]


@pytest.mark.parametrize("modo", [None, ManualTrainingMode.FIXED, ManualTrainingMode.ADD_TRAINING])
def test_dia_com_e_sem_treino_tem_a_kcal_do_alvo_pre_ciclo(modo):
    # Sem gramas manuais a compensação mantém a kcal do alvo pré-ciclo; com gramas manuais (L54) vale vary_kcal:
    # gordura e proteína fixas, só o carbo e a kcal oscilam, e a soma semanal do delta de carbo é zero.
    manual = {} if modo is None else {"manual_macros": MANUAL, "manual_training_mode": modo}
    comum = {"tdee_mode": TdeeMode.EXPLICIT_TRAINING, **manual}
    sem_ciclo = _plano(st.Strategy.BULKING, **comum)
    com = _plano(st.Strategy.BULKING, **comum, cycle_kinds=SEMANA, carb_cycle_policy=FAT)
    for i in range(7):
        for ctx in (_dia(i), _dia(i, is_training_day=True, training_kcal_reported=500.0)):
            pre = compute_daily_target(SUJEITO, sem_ciclo, ctx)
            dia = compute_daily_target(SUJEITO, com, ctx)
            assert dia.macros.protein_g == pytest.approx(pre.macros.protein_g)
            assert dia.day_kind == SEMANA[i]
            if modo is None:
                # A gordura do dia fica na faixa da estratégia: o carbo é limitado e o aviso é o único acréscimo.
                assert tuple(x for x in dia.limitations if x != cc.LIMIT_FAT_COMPENSATION_CARB_LIMITED) == pre.limitations
                assert dia.macros.kcal == pytest.approx(pre.macros.kcal)
                assert dia.carb_cycle_policy == FAT
                assert dia.to_dict()["carb_cycle_policy"] == "fat_compensation"
            else:
                assert dia.macros.fat_g == pytest.approx(pre.macros.fat_g)
                assert dia.carb_cycle_policy == cc.CarbCyclePolicy.VARY_KCAL
                assert cc.LIMIT_FAT_COMPENSATION_IGNORED_MANUAL in dia.limitations
                assert dia.macros.kcal == pytest.approx(pre.macros.kcal + 4 * (dia.macros.carb_g - pre.macros.carb_g))
    if modo is None:
        assert sum(m.macros.kcal for m in _semana(com)) == pytest.approx(sum(m.macros.kcal for m in _semana(sem_ciclo)))


def test_dia_baixo_tem_mais_gordura_e_alto_menos_no_motor():
    plano = _plano(cycle_kinds=SEMANA, carb_cycle_policy=FAT)
    metas, base = _semana(plano), compute_daily_target(SUJEITO, _plano(), _dia(0)).macros
    assert metas[1].macros.fat_g > base.fat_g > metas[0].macros.fat_g
    assert metas[1].macros.carb_g < base.carb_g < metas[0].macros.carb_g


@pytest.mark.parametrize("modo", [ManualTrainingMode.FIXED, ManualTrainingMode.ADD_TRAINING])
def test_gramas_manuais_com_compensacao_usam_vary_kcal_e_avisam(modo):
    """L54 (#313): com gramas manuais a gordura fica nos gramas fixos em todos os dias; só o carbo oscila."""
    manual = (160.0, 250.0, 60.0)
    plano = _plano(st.Strategy.CUTTING, manual_macros=manual, manual_training_mode=modo, cycle_kinds=SEMANA, carb_cycle_policy=FAT)
    metas = _semana(plano)
    assert {m.macros.fat_g for m in metas} == {60.0}
    assert metas[0].macros.carb_g > 250.0 > metas[1].macros.carb_g
    for m in metas:
        assert m.carb_cycle_policy == cc.CarbCyclePolicy.VARY_KCAL
        assert cc.LIMIT_FAT_COMPENSATION_IGNORED_MANUAL in m.limitations
    assert lb.limitation_label(cc.LIMIT_FAT_COMPENSATION_IGNORED_MANUAL).startswith("Com gramas manuais")


def test_padrao_nao_muda_saida_nem_dict():
    base = _plano(manual_macros=MANUAL, cycle_kinds=SEMANA)
    explicito = replace(base, carb_cycle_policy=cc.CarbCyclePolicy.VARY_KCAL)
    assert _semana(base) == _semana(explicito)
    assert "carb_cycle_policy" not in base.to_dict() and "carb_cycle_policy" not in _semana(base)[0].to_dict()
    assert replace(base, carb_cycle_policy=FAT).to_dict()["carb_cycle_policy"] == "fat_compensation"


def test_inviavel_no_dia_devolve_alvo_sem_ciclo_e_limitacao():
    # Sem faixa, gordura final negativa continua inviável (caso real do dono); com faixa, a base fora dela anula o ciclo.
    alvo = _alvo(gordura=10.0)
    assert cc._day_with_fat_compensation(alvo, 45.0) == (alvo, (cc.LIMIT_FAT_COMPENSATION_INFEASIBLE,))


def test_gordura_base_fora_da_faixa_da_estrategia_pula_o_ciclo():
    # LIMIT_FAT_COMPENSATION_CYCLE_SKIPPED: fração de gordura fora da faixa é só aviso na montagem, então é alcançável.
    base = _plano()
    sem_ciclo = replace(base, strategy_params=replace(base.strategy_params, fat_kcal_share=0.05))
    dias = _semana(replace(sem_ciclo, cycle_kinds=SEMANA, carb_cycle_policy=FAT))
    pre = compute_daily_target(SUJEITO, sem_ciclo, _dia(0))
    assert any(cc.LIMIT_FAT_COMPENSATION_CYCLE_SKIPPED in d.limitations for d in dias)
    assert all(d.macros.fat_g == pytest.approx(pre.macros.fat_g) for d in dias)
    # Direto no dono: gordura base abaixo da faixa e delta que a baixaria mais.
    alvo = _alvo(gordura=10.0)
    faixa = st.fat_share_range_for(st.Strategy.MAINTENANCE)
    assert cc._day_with_fat_compensation(alvo, 45.0, faixa) == (alvo, (cc.LIMIT_FAT_COMPENSATION_CYCLE_SKIPPED,))


# --- plan -----------------------------------------------------------------------------------------------------

CICLO = {i: k.value for i, k in enumerate(SEMANA)}


def _cfg(**extra):
    r = pl.build_plan_settings(strategy="maintenance", cycle_days=CICLO, **extra)
    assert r.ok, r.errors
    return r.settings


def test_build_plan_settings_valida_a_politica():
    assert pl.build_plan_settings(strategy="maintenance", cycle_days=CICLO, cycle_policy="x").errors == (
        "invalid_code:carb_cycle_policy",
    )
    sem_ciclo = pl.build_plan_settings(strategy="maintenance", cycle_policy="fat_compensation")  # lembrada sem ciclo
    assert sem_ciclo.ok and sem_ciclo.settings.carb_cycle_policy == FAT
    assert pl.build_plan_settings(strategy="maintenance").settings.carb_cycle_policy == cc.DEFAULT_CARB_CYCLE_POLICY
    assert _cfg(cycle_policy="fat_compensation").plan_params().carb_cycle_policy == FAT


def test_to_dict_e_hash_so_com_a_chave_fora_do_padrao():
    padrao, novo = _cfg(), _cfg(cycle_policy="fat_compensation")
    for d in (padrao.to_dict(), padrao.fingerprint_dict()):
        assert "carb_cycle_policy" not in d
    for d in (novo.to_dict(), novo.fingerprint_dict()):
        assert d["carb_cycle_policy"] == "fat_compensation"


def test_politica_sem_ciclo_nao_muda_impressao_digital_nem_metas():
    padrao = pl.build_plan_settings(strategy="maintenance").settings
    lembrada = pl.build_plan_settings(strategy="maintenance", cycle_policy="fat_compensation").settings
    assert lembrada.carb_cycle_policy == FAT and padrao.fingerprint_dict() == lembrada.fingerprint_dict()
    assert "carb_cycle_policy" not in lembrada.fingerprint_dict()
    assert _semana(padrao.plan_params()) == _semana(lembrada.plan_params())
    # Com ciclo a política continua diferenciando (impressão digital e metas).
    assert _cfg().fingerprint_dict() != _cfg(cycle_policy="fat_compensation").fingerprint_dict()


def test_check_cycle_floors_modo_novo_viavel_sem_erro_e_padrao_inalterado():
    manual = {"protein_g": 160, "carb_g": 250, "fat_g": 70}
    padrao = _cfg(manual_targets=manual)
    novo = _cfg(manual_targets=manual, cycle_policy="fat_compensation")
    assert pl.check_cycle_floors(novo, SUJEITO)[0] == ()
    assert pl.check_cycle_floors(padrao, SUJEITO) == pl.check_cycle_floors(replace(novo, carb_cycle_policy=cc.DEFAULT_CARB_CYCLE_POLICY), SUJEITO)


def test_check_cycle_floors_gramas_manuais_com_compensacao_avisam_e_nao_bloqueiam():
    # L54 (#313): gramas manuais + compensação = vary_kcal; o aviso próprio entra no salvar e a gordura fixa não gera erro de ciclo.
    for gordura in (40, 70):
        cfg = _cfg(manual_targets={"protein_g": 160, "carb_g": 250, "fat_g": gordura}, cycle_policy="fat_compensation")
        erros, avisos = pl.check_cycle_floors(cfg, SUJEITO)
        assert erros == () and cc.LIMIT_FAT_COMPENSATION_IGNORED_MANUAL in avisos
    r = pl.build_plan_settings(
        strategy="maintenance", cycle_days=CICLO, cycle_policy="fat_compensation",
        manual_targets={"protein_g": 160, "carb_g": 250, "fat_g": 15},
    )
    assert not r.ok and r.errors == ("manual_blocked_below:fat_g",)


def test_check_cycle_floors_proteina_acima_da_faixa_com_compensacao_so_avisa():
    # Fora do salvar (a montagem barra proteína fora da faixa), o valor chega por settings montados à mão.
    cfg = replace(_cfg(cycle_policy="fat_compensation"), protein_g_per_kg=3.5)
    erros, avisos = pl.check_cycle_floors(cfg, SUJEITO)
    assert erros == () and "manual_above_range:protein_g" in avisos


# --- labels ---------------------------------------------------------------------------------------------------


def test_rotulos_novos():
    assert set(lb.CARB_CYCLE_POLICY_LABELS) == set(cc.CarbCyclePolicy) == set(lb.CARB_CYCLE_POLICY_HINTS)
    for codigo in (cc.LIMIT_FAT_COMPENSATION_INFEASIBLE, cc.LIMIT_FAT_COMPENSATION_TARGET_NOT_CLOSED):
        assert lb.limitation_label(codigo)
        assert lb.limitation_label(codigo) != codigo


# --- #242: a compensação não muda kcal nem proteína do dia em nenhum modo (pré-ciclo = referência) -----------------


def _igual_ao_pre_ciclo(pre, dia, idx):
    assert dia.macros.kcal == pytest.approx(pre.macros.kcal)
    assert dia.macros.protein_g == pytest.approx(pre.macros.protein_g)
    assert dia.carb_cycle_policy == FAT and dia.day_kind == SEMANA[idx % 7]
    # a compensação foi de fato exercida (não é kcal igual por limitação)
    assert cc.LIMIT_FAT_COMPENSATION_INFEASIBLE not in dia.limitations
    assert cc.LIMIT_FAT_COMPENSATION_TARGET_NOT_CLOSED not in dia.limitations
    m, p = dia.macros, pre.macros
    if dia.day_kind == H:
        assert m.carb_g > p.carb_g and m.fat_g < p.fat_g
    elif dia.day_kind == L:
        assert m.carb_g < p.carb_g and m.fat_g > p.fat_g
    else:
        assert m.carb_g == pytest.approx(p.carb_g) and m.fat_g == pytest.approx(p.fat_g)


@pytest.mark.parametrize("sincronizado", [True, False], ids=["sincronizado", "sem_sincronia"])
@pytest.mark.parametrize("sessoes", [(), "forca"], ids=["sem_treino", "forca"])
def test_gasto_automatico_do_intervals_com_compensacao_igual_ao_pre_ciclo(sincronizado, sessoes):
    from app.domain.nutrition.daily_target import day_context_for
    from tests.unit import test_nutrition_atividades_extras as ex

    sessoes = ex.FORCA if sessoes == "forca" else ()
    extras = ex._extras(("Walk", 130), ("VirtualRun", 265))
    sem_ciclo = ex._plano()
    com = ex._plano(cycle_kinds=SEMANA, carb_cycle_policy=FAT)
    for i in range(7):
        dia = ex.HOJE + timedelta(days=i)
        ctx = day_context_for(dia, dia, sessoes, extras, intervals_in_sync=sincronizado)
        pre = compute_daily_target(ex.SUJEITO, sem_ciclo, ctx)
        meta = compute_daily_target(ex.SUJEITO, com, ctx)
        assert meta.extra_activity.kcal == pytest.approx(pre.extra_activity.kcal)  # atividade não conta 2x
        assert meta.tdee.kcal == pytest.approx(pre.tdee.kcal)
        _igual_ao_pre_ciclo(pre, meta, dia.weekday())
    if sincronizado and sessoes:
        assert pre.extra_activity.kcal == pytest.approx(395)


def test_modo_adaptativo_com_compensacao_igual_ao_pre_ciclo():
    from app.domain.nutrition import adaptive_energy as ae
    from tests.unit import test_nutrition_modo_adaptativo as ad

    estados = ad._estados(40)
    hoje = ad.D0 + timedelta(days=39)
    ciclo = {"cycle_days": {i: k.value for i, k in enumerate(SEMANA)}}
    sem_ciclo = ad._plano()
    com = ad._plano(**ciclo, cycle_policy="fat_compensation")
    for i in (*range(30, 37), 39, 41):  # passados, hoje (aberto) e futuro
        dia = ad.D0 + timedelta(days=i)
        est = ae.estimate_for_day(estados, dia, hoje)
        pre = ad._meta(sem_ciclo, dia, est)
        meta = ad._meta(com, dia, est)
        _igual_ao_pre_ciclo(pre, meta, dia.weekday())


@pytest.mark.parametrize("fase", [None, *PhaseKind])
def test_fases_e_refeed_com_compensacao_igual_ao_pre_ciclo_da_mesma_fase(fase):
    from tests.unit.test_nutrition_daily_target import COM_MLG

    sem_ciclo = _plano(st.Strategy.CUTTING)
    com = _plano(st.Strategy.CUTTING, cycle_kinds=SEMANA, carb_cycle_policy=FAT)
    for i in range(7):
        pre = compute_daily_target(COM_MLG, sem_ciclo, _dia(i, phase_kind=fase))
        meta = compute_daily_target(COM_MLG, com, _dia(i, phase_kind=fase))
        assert meta.phase_kind == pre.phase_kind
        _igual_ao_pre_ciclo(pre, meta, i)


def test_semana_parcial_peso_variavel_futuro_desconhecido_e_dia_isolado_com_compensacao():
    from tests.unit.test_nutrition_plano_autogerido_motor import KINDS, _contexto

    sem_ciclo = _plano()
    com = _plano(cycle_kinds=KINDS, carb_cycle_policy=FAT)

    def kcal(plano, sujeito, i, conhecido=True):
        return compute_daily_target(sujeito, plano, _contexto(i, conhecido)).macros.kcal

    # congeladas (4 primeiros dias) + abertas com peso novo = alvos pré-ciclo de cada data
    novo = replace(SUJEITO, weight_kg=80.4)
    partes = [kcal(com, SUJEITO, i) for i in range(4)] + [kcal(com, novo, i) for i in (4, 5, 6)]
    pre = [kcal(sem_ciclo, SUJEITO, i) for i in range(4)] + [kcal(sem_ciclo, novo, i) for i in (4, 5, 6)]
    assert partes == pytest.approx(pre) and sum(partes) == pytest.approx(sum(pre))
    # treino futuro desconhecido funciona
    for i in range(7):
        assert kcal(com, SUJEITO, i, False) == pytest.approx(kcal(sem_ciclo, SUJEITO, i, False))
    # cada dia depende só de si: mudar o peso SÓ do dia 5 muda o dia 5 e deixa as metas dos outros 6 dias idênticas
    antes = [compute_daily_target(SUJEITO, com, _contexto(j)).macros for j in range(7)]
    depois = [compute_daily_target(novo if j == 5 else SUJEITO, com, _contexto(j)).macros for j in range(7)]
    assert depois[5] != antes[5]
    assert [m for j, m in enumerate(depois) if j != 5] == [m for j, m in enumerate(antes) if j != 5]
    for i in range(7):
        _igual_ao_pre_ciclo(
            compute_daily_target(SUJEITO, sem_ciclo, _contexto(i)),
            compute_daily_target(SUJEITO, com, _contexto(i)), i,
        )
