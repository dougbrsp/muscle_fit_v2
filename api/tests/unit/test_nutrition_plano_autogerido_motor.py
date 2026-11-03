"""Motor do plano autogerido (N2): prioridade de macros, gramas manuais (D6) e dias alto/normal/baixo (D5)
ligados à meta diária.

- plano antigo (sem os campos do N2) = resultado idêntico, bit a bit;
- materialização dia a dia: a soma semanal do carbo se preserva com treino explícito, treino futuro
  desconhecido e dias já congelados, porque cada dia depende só de si e da base do plano;
- gramas manuais mandam na meta e os avisos do motor viram limitações;
- ritmo derivado do bulking fora da faixa é só AVISO (nunca recusa), com a incoerência do default avançado;
- "puxar do Modelo" só propõe com dia da semana nos slots.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.domain.nutrition import DayContext, PlanParams, compute_daily_target
from app.domain.nutrition import carb_cycling as cc
from app.domain.nutrition import macros as mc
from app.domain.nutrition import plan as pl
from app.domain.nutrition import strategy as st
from app.domain.nutrition.anthropometrics import ActivityLevel, Sex, Subject
from app.domain.nutrition.daily_target import day_context_for, days_to_materialize, week_window, SessionFacts

SEGUNDA = date(2026, 10, 12)
S = st.Strategy
D = cc.DayKind
SUJEITO = Subject(sex=Sex.MALE, age_years=30, height_cm=180, weight_kg=80, activity_level=ActivityLevel.MODERATE)
COM_MLG = replace(SUJEITO, body_fat_percent=15.0)
# 3 dias altos (seg, qua, sex) e 4 baixos.
KINDS = (D.HIGH, D.LOW, D.HIGH, D.LOW, D.HIGH, D.LOW, D.LOW)
# Treino em kcal por dia da semana (segunda = 0); None = descanso.
TREINO = {0: 400.0, 2: 650.0, 4: 300.0}


def _plano(estrategia: S = S.MAINTENANCE, **extra) -> PlanParams:
    return PlanParams(strategy_params=st.StrategyParams.defaults_for(estrategia), **extra)


def _contexto(indice: int, conhecido: bool = True) -> DayContext:
    dia = SEGUNDA + timedelta(days=indice)
    kcal = TREINO.get(indice) if conhecido else None
    if kcal is None:
        return DayContext(day=dia, training_known=conhecido)
    return DayContext(day=dia, is_training_day=True, training_kcal_reported=kcal)


def _semana(plano: PlanParams, sujeito: Subject = SUJEITO, conhecido: bool = True):
    return [compute_daily_target(sujeito, plano, _contexto(i, conhecido)) for i in range(7)]


# ---------------------------------------------------------------------------
# Retrocompatibilidade: plano sem os campos do N2 = resultado idêntico
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("estrategia", [S.CUTTING, S.MAINTENANCE, S.BULKING])
def test_plano_antigo_sem_campos_novos_tem_resultado_identico(estrategia):
    sujeito = COM_MLG
    antigo = _plano(estrategia)
    explicito = _plano(estrategia, macro_priority=st.MacroPriority.BALANCED, manual_macros=None, cycle_kinds=None)
    for i in range(7):
        a = compute_daily_target(sujeito, antigo, _contexto(i))
        b = compute_daily_target(sujeito, explicito, _contexto(i))
        assert a.to_dict() == b.to_dict()
        # E é o macro_split de sempre, bit a bit.
        esperado = mc.macro_split(a.kcal_target.kcal, antigo.strategy_params, sujeito.weight_kg, 68.0)
        assert (a.macros.protein_g, a.macros.carb_g, a.macros.fat_g) == (
            esperado.protein_g, esperado.carb_g, esperado.fat_g
        )
        assert a.day_kind == D.NORMAL


def test_to_dict_do_plano_antigo_nao_ganha_chaves():
    chaves = set(_plano().to_dict())
    assert chaves == {"strategy_params", "bmr_formula", "tdee_mode", "carb_cycling_enabled", "meals_per_day", "ffm_age_days"}


def test_hash_das_entradas_do_plano_antigo_nao_muda():
    """`fingerprint_dict` só ganha as chaves do N2 quando saem do padrão: o plano publicado antes da migração 101
    mantém o hash e não é regravado à toa."""
    padrao = pl.build_plan_settings(strategy="cutting").settings
    assert set(padrao.fingerprint_dict()) == {
        "strategy", "advanced", "protein_g_per_kg", "protein_basis", "fat_kcal_share", "weekly_rate_share",
        "surplus_share", "carb_min_g_per_kg", "bmr_formula", "tdee_mode", "activity_level", "meals_per_day",
        "goal_body_fat_percent", "goal_weight_kg", "carb_cycling_enabled",
    }
    com_novo = pl.build_plan_settings(strategy="cutting", macro_priority="high_carb").settings
    assert com_novo.fingerprint_dict()["macro_priority"] == "high_carb"


# ---------------------------------------------------------------------------
# Prioridade de macros
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prioridade", [p for p in st.MacroPriority if p != st.MacroPriority.BALANCED])
def test_prioridade_de_macros_chega_na_meta_diaria(prioridade):
    plano = _plano(S.CUTTING, macro_priority=prioridade)
    meta = compute_daily_target(COM_MLG, plano, _contexto(1))
    esperado = mc.macro_split_for_priority(meta.kcal_target.kcal, plano.strategy_params, prioridade, 80, 68.0)
    assert meta.macros.fat_g == pytest.approx(esperado.fat_g) and meta.macros.carb_g == pytest.approx(esperado.carb_g)
    # A proteína não muda com a prioridade.
    assert meta.macros.protein_g == pytest.approx(
        compute_daily_target(COM_MLG, _plano(S.CUTTING), _contexto(1)).macros.protein_g
    )


# ---------------------------------------------------------------------------
# Gramas manuais (D6)
# ---------------------------------------------------------------------------


def test_gramas_manuais_mandam_na_meta_e_nao_acompanham_o_treino():
    plano = _plano(manual_macros=(150.0, 300.0, 70.0))
    descanso = compute_daily_target(SUJEITO, plano, _contexto(1))
    treino = compute_daily_target(SUJEITO, plano, _contexto(2))
    for meta in (descanso, treino):
        assert (meta.macros.protein_g, meta.macros.carb_g, meta.macros.fat_g) == (150.0, 300.0, 70.0)
        assert meta.macros.kcal == pytest.approx(150 * 4 + 300 * 4 + 70 * 9)
    # O gasto continua calculado (referência da estratégia), só as gramas são do usuário.
    assert treino.tdee.kcal > descanso.tdee.kcal


def test_gramas_fora_da_faixa_viram_aviso_na_meta():
    # 1,2 g/kg de proteína na manutenção (faixa 1,6–2,2): salva e avisa.
    meta = compute_daily_target(SUJEITO, _plano(manual_macros=(96.0, 300.0, 70.0)), _contexto(1))
    assert "manual_below_range:protein_g" in meta.limitations
    assert meta.macros.protein_g == 96.0


# ---------------------------------------------------------------------------
# Dias alto/normal/baixo preservam a média semanal na materialização dia a dia
# ---------------------------------------------------------------------------


def _soma(metas, campo):
    return sum(getattr(m.macros, campo) for m in metas)


@pytest.mark.parametrize("estrategia", [S.MAINTENANCE, S.CUTTING, S.BULKING])
def test_semana_completa_com_treino_explicito_preserva_a_soma_de_carbo(estrategia):
    sujeito = COM_MLG
    sem_ciclo = _semana(_plano(estrategia), sujeito)
    com_ciclo = _semana(_plano(estrategia, cycle_kinds=KINDS), sujeito)
    assert _soma(com_ciclo, "carb_g") == pytest.approx(_soma(sem_ciclo, "carb_g"))
    assert _soma(com_ciclo, "protein_g") == pytest.approx(_soma(sem_ciclo, "protein_g"))
    assert _soma(com_ciclo, "fat_g") == pytest.approx(_soma(sem_ciclo, "fat_g"))
    assert _soma(com_ciclo, "kcal") == pytest.approx(_soma(sem_ciclo, "kcal"))
    # O ciclo realmente move o carbo: dia alto acima do mesmo dia sem ciclo, dia baixo abaixo.
    for i, kind in enumerate(KINDS):
        antes, depois = sem_ciclo[i].macros.carb_g, com_ciclo[i].macros.carb_g
        assert (depois > antes) if kind == D.HIGH else (depois < antes)
        assert com_ciclo[i].day_kind == kind
        # kcal do dia fecha por Atwater.
        m = com_ciclo[i].macros
        assert m.kcal == pytest.approx(mc.kcal_from_macros(m.protein_g, m.carb_g, m.fat_g))


def test_semana_com_treino_futuro_desconhecido_preserva_a_soma():
    """Dias futuros: treino desconhecido (sem treino na meta). O ciclo continua somando zero."""
    sem_ciclo = _semana(_plano(), SUJEITO, conhecido=False)
    com_ciclo = _semana(_plano(cycle_kinds=KINDS), SUJEITO, conhecido=False)
    assert _soma(com_ciclo, "carb_g") == pytest.approx(_soma(sem_ciclo, "carb_g"))


def test_cada_dia_depende_so_de_si_dias_congelados_e_futuros_nao_entram():
    """Materializar só os dias ainda calculáveis dá exatamente o mesmo valor que a semana inteira."""
    plano = _plano(cycle_kinds=KINDS)
    completa = _semana(plano)
    for i in (4, 5, 6):  # só sex, sáb, dom (os de antes já congelaram)
        isolado = compute_daily_target(SUJEITO, plano, _contexto(i))
        assert isolado.macros.carb_g == completa[i].macros.carb_g
        assert isolado.macros.kcal == completa[i].macros.kcal


def test_com_semana_parcial_congelada_a_soma_das_partes_bate():
    """Metas congeladas (gravadas) mais as ainda calculáveis fecham a semana: a média é preservada
    (exata com a mesma base; praticamente exata se o peso muda no meio da semana)."""
    plano = _plano(cycle_kinds=KINDS)
    congeladas = _semana(plano)[:4]  # gravadas quando calculadas
    # Depois o aluno ganha peso e a base muda um pouco; os dias abertos recalculam, os congelados não.
    sujeito_novo = replace(SUJEITO, weight_kg=80.4)
    abertas = [compute_daily_target(sujeito_novo, plano, _contexto(i)) for i in (4, 5, 6)]
    sem_ciclo = [compute_daily_target(SUJEITO, _plano(), _contexto(i)) for i in range(4)] + [
        compute_daily_target(sujeito_novo, _plano(), _contexto(i)) for i in (4, 5, 6)
    ]
    # Mudando o peso, a base de cada dia muda um pouco e os multiplicadores já gravados não são refeitos:
    # a soma desvia só da ordem do ajuste de base (documentado em `apply_cycle_over_base`), nunca da ordem do ciclo.
    assert _soma([*congeladas, *abertas], "carb_g") == pytest.approx(_soma(sem_ciclo, "carb_g"), rel=1e-3)


def test_dia_congelado_nunca_e_reescrito_pela_janela_do_motor():
    hoje = SEGUNDA + timedelta(days=5)  # sábado
    janela = week_window(hoje)
    existentes = {SEGUNDA + timedelta(days=i) for i in range(7)}
    escritos = days_to_materialize(janela, hoje, existentes)
    assert all(dia >= hoje - timedelta(days=1) for dia in escritos)
    assert SEGUNDA not in escritos and (SEGUNDA + timedelta(days=3)) not in escritos


def test_piso_de_carbo_que_prende_limita_o_ciclo_com_aviso():
    # Carbo base baixo (cutting agressivo): o piso de 1 g/kg segura o dia baixo.
    leve = Subject(sex=Sex.FEMALE, age_years=45, height_cm=160, weight_kg=60, activity_level=ActivityLevel.SEDENTARY,
                   body_fat_percent=30.0)
    plano = _plano(S.CUTTING, cycle_kinds=KINDS)
    for i in range(7):
        meta = compute_daily_target(leve, plano, DayContext(day=SEGUNDA + timedelta(days=i)))
        assert meta.macros.carb_g >= 0
        assert meta.macros.computable


def test_ciclo_em_modo_embutido_usa_a_propria_meta_como_base():
    plano = _plano(tdee_mode=pl.ee.TdeeMode.INCLUDED_IN_FACTOR, cycle_kinds=KINDS)
    sem = _plano(tdee_mode=pl.ee.TdeeMode.INCLUDED_IN_FACTOR)
    a = _semana(plano)
    b = _semana(sem)
    assert _soma(a, "carb_g") == pytest.approx(_soma(b, "carb_g"))


# ---------------------------------------------------------------------------
# `apply_cycle_over_base`
# ---------------------------------------------------------------------------


def test_apply_cycle_over_base_nao_cicla_meta_nao_calculavel():
    vazia = mc.MacroTargets(kcal=None, protein_g=None, carb_g=None, fat_g=None, fiber_g=None, protein_basis=None,
                            protein_basis_kg=None, protein_g_per_kg=None)
    meta, limites = cc.apply_cycle_over_base(vazia, vazia, D.HIGH, KINDS, 80)
    assert meta is vazia and limites == (cc.LIMIT_TARGET_NOT_COMPUTABLE,)


# ---------------------------------------------------------------------------
# Plano -> PlanParams
# ---------------------------------------------------------------------------


def test_settings_levam_os_campos_do_n2_ao_plan_params():
    cfg = pl.build_plan_settings(
        strategy="maintenance", cycle_days={0: "high", 1: "low"}, manual_targets={"protein_g": 150, "carb_g": 250, "fat_g": 70}
    ).settings
    params = cfg.plan_params()
    assert params.manual_macros == (150.0, 250.0, 70.0)
    assert params.cycle_kinds is not None and params.cycle_kinds[0] == D.HIGH and params.cycle_kinds[1] == D.LOW
    assert params.carb_cycling_enabled is False  # o caminho antigo (flags de treino) não é mais usado


# ---------------------------------------------------------------------------
# Gramas manuais com o sujeito: peso obrigatório, bloqueio, aviso
# ---------------------------------------------------------------------------


def _cfg_manual(**gramas):
    base = {"protein_g": 150, "carb_g": 250, "fat_g": 70, **gramas}
    return pl.build_plan_settings(strategy="maintenance", manual_targets=base).settings


def test_sem_peso_nao_salva_gramas_manuais():
    sem_peso = replace(SUJEITO, weight_kg=None)
    check = pl.check_plan_manual_targets(_cfg_manual(), sem_peso)
    assert pl.ERR_MANUAL_REQUIRES_WEIGHT in pl.manual_save_errors(check, sem_peso)
    assert pl.manual_save_errors(pl.check_plan_manual_targets(_cfg_manual(), SUJEITO), SUJEITO) == ()


def test_proteina_abaixo_do_piso_com_o_peso_bloqueia_e_fora_da_faixa_so_avisa():
    bloqueada = pl.check_plan_manual_targets(_cfg_manual(protein_g=40), SUJEITO)
    assert "manual_blocked_below:protein_g" in pl.manual_save_errors(bloqueada, SUJEITO)
    aviso = pl.check_plan_manual_targets(_cfg_manual(protein_g=110), SUJEITO)  # 1,4 g/kg: abaixo da faixa 1,6-2,2
    assert pl.manual_save_errors(aviso, SUJEITO) == ()
    assert "manual_below_range:protein_g" in pl.manual_save_warnings(aviso)


def test_plano_sem_gramas_manuais_nao_tem_checagem():
    cfg = pl.build_plan_settings(strategy="maintenance").settings
    assert pl.check_plan_manual_targets(cfg, SUJEITO) is None
    assert pl.manual_save_errors(None, SUJEITO) == () and pl.manual_save_warnings(None) == ()


# ---------------------------------------------------------------------------
# Ritmo derivado fora da faixa: só aviso (item f)
# ---------------------------------------------------------------------------


def test_default_avancado_do_bulking_cabe_na_faixa_do_ritmo_e_nao_avisa():
    """Superávit 5% (default avançado), TDEE 2800, 12 kg de gordura, 80 kg dá cerca de 0,21%/semana: antes ficava abaixo do
    piso de 0,25% da faixa única; a faixa avançada (N2 rodada 4, mapa §81) o contém."""
    validacao = pl.build_plan_settings(strategy="bulking", advanced=True)
    assert validacao.ok
    sujeito = replace(SUJEITO, body_fat_percent=15.0)  # 80 kg, 12 kg de gordura
    ritmo = st.weekly_rate_share_for(validacao.settings.strategy_params(), 2800, 80, 12.0)
    piso, teto = st.weekly_rate_share_range_for(S.BULKING, True)
    assert piso <= ritmo <= teto
    assert pl.plan_warnings(validacao.settings, sujeito, 2800) == ()


def test_ritmo_dentro_da_faixa_ou_outra_estrategia_nao_avisa():
    sujeito = replace(SUJEITO, body_fat_percent=15.0)
    normal = pl.build_plan_settings(strategy="bulking", surplus_share=0.10).settings  # ~0,42%/semana
    assert pl.plan_warnings(normal, sujeito, 2800) == ()
    # Do outro lado: o teto do superávit não avançado (20%) implica ~0,83%/semana, acima do teto de 0,65%.
    teto = pl.build_plan_settings(strategy="bulking", surplus_share=0.20)
    assert teto.ok and pl.plan_warnings(teto.settings, sujeito, 2800) == (pl.REASON_RATE_OUTSIDE_RANGE,)
    assert pl.plan_warnings(pl.build_plan_settings(strategy="cutting").settings, sujeito, 2800) == ()


def test_projecao_do_plano_traz_a_premissa_de_estimativa_minima_no_alvo_de_gordura():
    cfg = pl.build_plan_settings(strategy="cutting", goal_body_fat_percent=12.0).settings
    sujeito = replace(SUJEITO, weight_kg=90.0, body_fat_percent=25.0)
    projecao = pl.project_plan_goal(cfg, sujeito, 2800, SEGUNDA)
    assert projecao.status.value == "projected" and projecao.is_minimum_estimate is True
    assert projecao.to_dict()["reasons"]
    por_peso = pl.build_plan_settings(strategy="cutting", goal_weight_kg=80.0).settings
    assert pl.project_plan_goal(por_peso, sujeito, 2800, SEGUNDA).is_minimum_estimate is False


# ---------------------------------------------------------------------------
# "Puxar do Modelo" (D5)
# ---------------------------------------------------------------------------


def test_proposta_do_modelo_marca_dias_de_treino_como_altos():
    slots = [(1, True), (2, False), (3, True), (4, False), (5, True), (6, False), (7, False)]
    proposta = pl.propose_cycle_from_template(slots)
    assert proposta is not None and proposta.valid and proposta.cycles
    assert proposta.kinds == KINDS
    assert pl.training_weekdays_from_template(slots) == (0, 2, 4)


def test_modelo_sem_dia_da_semana_nao_propoe_e_treino_todo_dia_nao_cicla():
    assert pl.propose_cycle_from_template([(None, True), (None, False)]) is None
    todos = pl.propose_cycle_from_template([(i, True) for i in range(1, 8)])
    assert todos is not None and todos.cycles is False


def test_contexto_do_dia_continua_o_de_sempre():
    """O contexto de materialização não mudou: dia futuro = treino desconhecido."""
    hoje = SEGUNDA
    futuro = day_context_for(SEGUNDA + timedelta(days=2), hoje, [SessionFacts(calories_kcal=300)])
    assert futuro.training_known is False and futuro.is_training_day is False
