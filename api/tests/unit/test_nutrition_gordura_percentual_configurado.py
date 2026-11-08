"""Gordura = fração configurada das kcal FINAIS do dia, também no ciclo de carbo e em `fixed_protein`.

Bug (plano Cutting, proteína 2,6 g/kg de peso, gordura 20%, `fixed_protein`, ciclo por dias): sábado
(alto) saía com 28,5% de gordura. Causas: (1) `FIXED_PROTEIN` ignorava `fat_kcal_share`
(partes iguais do que sobra, preso na faixa 15-30%); (2) o ciclo mantinha a gordura em gramas e
somava só carbo. Regra corrigida (dono `macros.py` + `carb_cycling.apply_cycle_over_base`):
proteína fixa pela base; gordura = kcal FINAL do dia × fração ÷ 9; carbo = resto; a média
semanal de kcal se preserva (o delta do ciclo soma zero)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.domain.nutrition import DayContext, PlanParams, compute_daily_target
from app.domain.nutrition import carb_cycling as cc
from app.domain.nutrition import energy_expenditure as ee
from app.domain.nutrition import macros as mc
from app.domain.nutrition import strategy as st
from app.domain.nutrition.anthropometrics import ActivityLevel, Sex, Subject

S = st.Strategy
P = st.MacroPriority
D = cc.DayKind
SEGUNDA = date(2026, 11, 2)  # segunda-feira
# seg normal, ter alto, qua baixo, qui normal, sex normal, sáb alto, dom baixo
KINDS = (D.NORMAL, D.HIGH, D.LOW, D.NORMAL, D.NORMAL, D.HIGH, D.LOW)
MLG_ALVO = 76.4
PESO = 95.0
SUJEITO = Subject(
    sex=Sex.MALE,
    age_years=35,
    height_cm=182,
    weight_kg=PESO,
    activity_level=ActivityLevel.MODERATE,
    body_fat_percent=round((1 - MLG_ALVO / PESO) * 100, 6),
)


def _params(share: float = 0.20, g_por_kg: float = 2.6):
    return replace(st.StrategyParams.defaults_for(S.CUTTING), fat_kcal_share=share, protein_g_per_kg=g_por_kg)


def _plano(share: float = 0.20, prioridade=P.FIXED_PROTEIN, kinds=KINDS, **extra) -> PlanParams:
    return PlanParams(
        strategy_params=_params(share),
        tdee_mode=ee.TdeeMode.EXPLICIT_TRAINING,
        macro_priority=prioridade,
        cycle_kinds=kinds,
        **extra,
    )


def _semana(plano: PlanParams, treino_kcal=(300.0, 450.0, 0.0, 350.0, 300.0, 500.0, 0.0), sujeito=SUJEITO):
    out = []
    for i in range(7):
        dia = SEGUNDA + timedelta(days=i)
        ctx = DayContext(day=dia, is_training_day=treino_kcal[i] > 0, training_kcal_reported=treino_kcal[i] or None)
        out.append(compute_daily_target(sujeito, plano, ctx))
    return out


def _share_g(m: mc.MacroTargets) -> float:
    return m.fat_g * mc.KCAL_PER_G_FAT / m.kcal


# --- reprodução: causa 1 (preset ignorava a fração) -------------------------------------------


def test_proteina_fixa_respeita_a_fracao_de_gordura_configurada():
    alvo = mc.macro_split_for_priority(2940.0, _params(0.20), P.FIXED_PROTEIN, PESO, MLG_ALVO)
    assert _share_g(alvo) == pytest.approx(0.20)  # antes: 0,30 (metade do que sobra, preso no teto)
    assert alvo.protein_g == pytest.approx(2.6 * PESO)


@pytest.mark.parametrize("share", [0.20, 0.25, 0.30])
@pytest.mark.parametrize("kcal", [2000.0, 2501.0, 3094.0])
def test_proteina_fixa_e_equilibrado_dao_o_mesmo_split(share, kcal):
    params = _params(share)
    assert mc.macro_split_for_priority(kcal, params, P.FIXED_PROTEIN, PESO, MLG_ALVO) == mc.macro_split(
        kcal, params, PESO, MLG_ALVO
    )


# --- reprodução: causa 2 (ciclo mantinha gordura em gramas) ------------------------------------


def test_caso_do_sabado_alto_gordura_20_por_cento():
    semana = _semana(_plano())
    sabado = semana[5]
    assert sabado.day_kind == D.HIGH and sabado.day.weekday() == 5
    m = sabado.macros
    assert _share_g(m) == pytest.approx(0.20)  # antes: 28,5%
    assert m.protein_g == pytest.approx(2.6 * PESO)
    assert m.protein_g * 4 + m.carb_g * 4 + m.fat_g * 9 == pytest.approx(m.kcal)
    # Caso de referência do relato: 3094 kcal -> ~247 P (2,6 × 95 kg de peso) / ~372 C / ~69 G (v4; na v3, pela MLG, 199 P / 420 C).
    ref = mc.macro_split(3094.0, _params(0.20), PESO, MLG_ALVO)
    assert (round(ref.protein_g), round(ref.carb_g), round(ref.fat_g)) == (247, 372, 69)


@pytest.mark.parametrize("share", [0.20, 0.25, 0.30])
@pytest.mark.parametrize("prioridade", [P.FIXED_PROTEIN, P.BALANCED])
def test_todos_os_dias_com_a_fracao_configurada_e_atwater_fechado(share, prioridade):
    for meta in _semana(_plano(share, prioridade)):
        m = meta.macros
        assert _share_g(m) == pytest.approx(share, abs=1e-9)
        assert m.protein_g == pytest.approx(2.6 * PESO)  # proteína fixa pela base
        assert mc.kcal_from_macros(m.protein_g, m.carb_g, m.fat_g) == pytest.approx(m.kcal)


def test_dia_alto_sobe_o_carbo_e_a_kcal_e_dia_baixo_reduz_o_carbo_sem_ir_para_gordura():
    semana = _semana(_plano(), treino_kcal=(0.0,) * 7)  # sem treino: normal = base
    normal, alto, baixo = semana[0].macros, semana[1].macros, semana[2].macros
    assert alto.kcal > normal.kcal > baixo.kcal
    assert alto.carb_g > normal.carb_g > baixo.carb_g
    assert alto.fat_g > normal.fat_g > baixo.fat_g  # a gordura acompanha a kcal, no mesmo %
    assert _share_g(baixo) == pytest.approx(0.20)


def test_media_semanal_de_kcal_e_de_carbo_preservada():
    sem_ciclo = _semana(_plano(kinds=None))
    com_ciclo = _semana(_plano())
    assert sum(d.macros.kcal for d in com_ciclo) == pytest.approx(sum(d.macros.kcal for d in sem_ciclo))
    # O carbo semanal também: o ciclo só move kcal entre dias, no mesmo % de gordura.
    assert sum(d.macros.carb_g for d in com_ciclo) == pytest.approx(sum(d.macros.carb_g for d in sem_ciclo))


@pytest.mark.parametrize("peso_mlg", [(80.0, 15.0), (95.0, 19.58), (120.0, 30.0)])
@pytest.mark.parametrize("share", [0.20, 0.25, 0.30])
def test_invariantes_com_mlgs_diferentes(peso_mlg, share):
    peso, gordura_corporal = peso_mlg
    sujeito = replace(SUJEITO, weight_kg=peso, body_fat_percent=gordura_corporal)
    semana = _semana(_plano(share), treino_kcal=(0.0,) * 7, sujeito=sujeito)
    for meta in semana:
        m = meta.macros
        assert abs(m.protein_g * 4 + m.carb_g * 4 + m.fat_g * 9 - m.kcal) < 1e-6
        assert abs(_share_g(m) - share) < 1e-9
    plana = _semana(_plano(share, kinds=None), treino_kcal=(0.0,) * 7, sujeito=sujeito)
    assert sum(d.macros.kcal for d in semana) == pytest.approx(sum(d.macros.kcal for d in plana))


def test_dia_do_ciclo_vem_do_fuso_do_usuario_sabado_23h30_local_e_domingo_utc():
    # 23h30 de sábado em São Paulo = 02h30 de domingo em UTC: o dia do usuário continua sábado (alto).
    instante = datetime(2026, 11, 8, 2, 30, tzinfo=timezone.utc)
    local = instante.astimezone(ZoneInfo("America/Sao_Paulo")).date()
    assert instante.date().weekday() == 6 and local.weekday() == 5
    meta = compute_daily_target(SUJEITO, _plano(), DayContext(day=local))
    assert meta.day_kind == D.HIGH
    domingo = compute_daily_target(SUJEITO, _plano(), DayContext(day=local + timedelta(days=1)))
    assert domingo.day_kind == D.LOW


def test_gramas_manuais_continuam_com_a_gordura_em_gramas():
    plano = _plano(manual_macros=(200.0, 300.0, 70.0))
    semana = _semana(plano, treino_kcal=(0.0,) * 7)
    assert all(d.macros.fat_g == pytest.approx(70.0) and d.macros.protein_g == pytest.approx(200.0) for d in semana)


@pytest.mark.parametrize("prioridade", [P.HIGH_CARB, P.HIGH_FAT])
def test_presets_de_carbo_e_gordura_mantem_a_fracao_efetiva_em_todos_os_dias(prioridade):
    semana = _semana(_plano(prioridade=prioridade), treino_kcal=(0.0,) * 7)
    efetiva = _share_g(semana[0].macros)
    assert all(_share_g(d.macros) == pytest.approx(efetiva) for d in semana)


def test_carbo_nunca_negativo_no_dia_baixo():
    pesado = replace(SUJEITO, weight_kg=200.0, body_fat_percent=60.0)
    for meta in _semana(_plano(0.30), treino_kcal=(0.0,) * 7, sujeito=pesado):
        assert meta.macros.carb_g >= 0.0


def test_dia_baixo_que_corta_o_carbo_em_zero_avisa():
    base = mc.MacroTargets(kcal=1000.0, protein_g=200.0, carb_g=50.0, fat_g=0.0, fiber_g=None, protein_basis=None,
                           protein_basis_kg=None, protein_g_per_kg=None)
    alvo = replace(base, kcal=1000.0, fat_g=0.0)
    out, lim = cc.apply_cycle_over_base(alvo, replace(base, carb_g=400.0), D.LOW, KINDS, None, keep_fat_share=True)
    assert out.carb_g == 0.0 and cc.LIMIT_CARB_FLOOR_PREVENTS_CYCLE in lim


def test_fracao_efetiva_vem_pronta_do_motor():
    alvo = mc.macro_split_for_priority(3094.0, _params(0.20), P.FIXED_PROTEIN, PESO, MLG_ALVO)
    assert alvo.fat_kcal_share_effective == pytest.approx(0.20)
    assert alvo.to_dict()["fat_kcal_share_effective"] == pytest.approx(0.20)
    nao = mc.macro_split_for_priority(3094.0, _params(0.20), P.FIXED_PROTEIN, None, MLG_ALVO)
    assert nao.fat_kcal_share_effective is None
