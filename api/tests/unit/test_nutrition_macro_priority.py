"""Prioridade de macros (N1; dono `domain/nutrition/macros.py`, enum em `strategy.py`).

BALANCED e "sem prioridade" são EXATAMENTE `macro_split`; os demais presets mantêm a
proteína e movem só a fração de gordura dentro da faixa da estratégia."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.domain.nutrition import macros as mc
from app.domain.nutrition import strategy as st

S = st.Strategy
P = st.MacroPriority
PESO, MLG = 80.0, 64.0
CASOS = [(S.CUTTING, 2000.0), (S.BULKING, 3200.0), (S.MAINTENANCE, 2600.0)]


def _split(estrategia, kcal, prioridade=None, peso=PESO, mlg=MLG):
    params = st.StrategyParams.defaults_for(estrategia)
    return mc.macro_split_for_priority(kcal, params, prioridade, peso, mlg)


def _share_gordura(alvo: mc.MacroTargets) -> float:
    return alvo.fat_g * mc.KCAL_PER_G_FAT / alvo.kcal


def test_default_retrocompativel_valores_de_referencia():
    # Cutting 2000 kcal, MLG 64: proteína 2,6 × 64; gordura 25% das kcal; carbo = resto.
    alvo = _split(S.CUTTING, 2000.0)
    assert alvo.protein_g == pytest.approx(166.4)
    assert alvo.fat_g == pytest.approx(0.25 * 2000 / 9)
    assert alvo.carb_g == pytest.approx((2000 - 166.4 * 4 - 500) / 4)


@pytest.mark.parametrize(("estrategia", "kcal"), CASOS)
@pytest.mark.parametrize("prioridade", [None, P.BALANCED, "balanced", "BALANCED"])
def test_sem_prioridade_ou_equilibrado_e_o_macro_split_de_sempre(estrategia, kcal, prioridade):
    params = st.StrategyParams.defaults_for(estrategia)
    assert _split(estrategia, kcal, prioridade) == mc.macro_split(kcal, params, PESO, MLG)


@pytest.mark.parametrize(("estrategia", "kcal"), CASOS)
@pytest.mark.parametrize("prioridade", list(P))
def test_todo_preset_fecha_a_energia_e_mantem_a_proteina(estrategia, kcal, prioridade):
    base = _split(estrategia, kcal)
    alvo = _split(estrategia, kcal, prioridade)
    assert alvo.protein_g == base.protein_g
    assert mc.kcal_from_macros(alvo.protein_g, alvo.carb_g, alvo.fat_g) == pytest.approx(kcal)
    piso, teto = st.fat_share_range_for(estrategia)
    assert piso - 1e-9 <= _share_gordura(alvo) <= teto + 1e-9


@pytest.mark.parametrize(("estrategia", "kcal"), CASOS)
def test_carbo_alto_tem_mais_carbo_e_menos_gordura_que_o_equilibrado(estrategia, kcal):
    base = _split(estrategia, kcal)
    alto = _split(estrategia, kcal, P.HIGH_CARB)
    assert alto.carb_g > base.carb_g and alto.fat_g < base.fat_g
    assert _share_gordura(alto) == pytest.approx(st.fat_share_range_for(estrategia)[0])


@pytest.mark.parametrize(("estrategia", "kcal"), CASOS)
def test_gordura_alta_tem_mais_gordura_e_menos_carbo_que_o_equilibrado(estrategia, kcal):
    base = _split(estrategia, kcal)
    alta = _split(estrategia, kcal, P.HIGH_FAT)
    assert alta.fat_g > base.fat_g and alta.carb_g < base.carb_g
    assert _share_gordura(alta) <= st.fat_share_range_for(estrategia)[1] + 1e-9


def test_gordura_alta_no_cutting_vai_ao_teto_da_faixa():
    alvo = _split(S.CUTTING, 2400.0, P.HIGH_FAT)
    assert _share_gordura(alvo) == pytest.approx(st.CUT_FAT_KCAL_SHARE_RANGE[1])


def test_gordura_alta_no_bulking_respeita_o_carbo_minimo():
    # 2300 kcal, 80 kg: proteína 640 kcal; carbo mínimo 3 g/kg = 960 kcal; sobram 700 kcal
    # para gordura (30,4%), abaixo do teto de 35%: o carbo fica no piso.
    alvo = _split(S.BULKING, 2300.0, P.HIGH_FAT)
    assert alvo.carb_g == pytest.approx(st.BULK_CARB_MIN_G_PER_KG * PESO)
    assert st.BULK_FAT_KCAL_SHARE_DEFAULT < _share_gordura(alvo) < st.BULK_FAT_KCAL_SHARE_RANGE[1]
    assert mc.LIMIT_CARB_BELOW_MIN not in alvo.limitations


def test_gordura_alta_sem_folga_de_carbo_cai_para_o_padrao_da_estrategia():
    # 1900 kcal no bulking: nem o padrão de gordura deixa o carbo no piso; vale o padrão (e o aviso de carbo).
    alvo = _split(S.BULKING, 1900.0, P.HIGH_FAT)
    assert _share_gordura(alvo) == pytest.approx(st.BULK_FAT_KCAL_SHARE_DEFAULT)
    assert mc.LIMIT_CARB_BELOW_MIN in alvo.limitations


def test_proteina_fixa_respeita_a_fracao_de_gordura_configurada_do_plano():
    # Maintenance 2000, 80 kg: proteína 2,0 × 80 = 160 g; gordura = 25% (a do plano) das kcal, não "partes iguais".
    alvo = _split(S.MAINTENANCE, 2000.0, P.FIXED_PROTEIN)
    assert alvo.fat_g * mc.KCAL_PER_G_FAT == pytest.approx(0.25 * 2000.0)
    assert alvo == _split(S.MAINTENANCE, 2000.0)


def test_proteina_fixa_com_plano_fora_do_default_usa_a_fracao_do_plano():
    params = replace(st.StrategyParams.defaults_for(S.MAINTENANCE), fat_kcal_share=0.30)
    alvo = mc.macro_split_for_priority(2600.0, params, P.FIXED_PROTEIN, PESO, MLG)
    assert _share_gordura(alvo) == pytest.approx(0.30)


def test_proteina_fixa_no_cutting_usa_a_fracao_configurada_e_nao_30_por_cento():
    params = replace(st.StrategyParams.defaults_for(S.CUTTING), fat_kcal_share=0.20)
    alvo = mc.macro_split_for_priority(2000.0, params, P.FIXED_PROTEIN, PESO, MLG)
    assert _share_gordura(alvo) == pytest.approx(0.20)


def test_proteina_fixa_com_kcal_que_nao_cobre_a_proteina_usa_o_padrao():
    alvo = _split(S.CUTTING, 600.0, P.FIXED_PROTEIN)
    assert alvo.carb_g == 0.0 and mc.LIMIT_KCAL_BELOW_PROTEIN_AND_FAT in alvo.limitations


def test_sem_dados_o_preset_devolve_o_nao_calculavel_do_macro_split():
    params = st.StrategyParams.defaults_for(S.CUTTING)
    alvo = mc.macro_split_for_priority(2000.0, params, P.HIGH_CARB, PESO, None)  # cutting sem MLG
    assert not alvo.computable and mc.LIMIT_MISSING_PROTEIN_BASIS in alvo.limitations


def test_prioridade_invalida_levanta_erro_com_o_codigo():
    params = st.StrategyParams.defaults_for(S.CUTTING)
    with pytest.raises(ValueError, match=mc.ERR_INVALID_MACRO_PRIORITY):
        mc.macro_split_for_priority(2000.0, params, "keto", PESO, MLG)


def test_normalize_macro_priority():
    assert mc.normalize_macro_priority(None) == st.DEFAULT_MACRO_PRIORITY == P.BALANCED
    assert mc.normalize_macro_priority(" High_Carb ") == P.HIGH_CARB
    assert mc.normalize_macro_priority("x") is None


def test_gordura_alta_nunca_da_menos_gordura_que_o_equilibrado_com_params_fora_do_default():
    # Bulking com gordura de 30% no plano, 80 kg, 2000 kcal: o piso de carbo (960 kcal) deixa só 20% para a gordura;
    # o preset não pode recuar para o padrão da estratégia (25%) nem ficar abaixo dos 30% do plano.
    params = replace(st.StrategyParams.defaults_for(S.BULKING), fat_kcal_share=0.30)
    base = mc.macro_split(2000.0, params, PESO, MLG)
    alta = mc.macro_split_for_priority(2000.0, params, P.HIGH_FAT, PESO, MLG)
    assert alta.fat_g >= base.fat_g
    assert _share_gordura(alta) == pytest.approx(0.30)


def test_gordura_alta_com_plano_fora_do_default_sobe_ate_o_teto_quando_cabe():
    params = replace(st.StrategyParams.defaults_for(S.MAINTENANCE), fat_kcal_share=0.30)
    alta = mc.macro_split_for_priority(2600.0, params, P.HIGH_FAT, PESO, MLG)
    assert _share_gordura(alta) == pytest.approx(st.MAINT_FAT_KCAL_SHARE_RANGE[1])


def test_proteina_fixa_nao_troca_a_fracao_para_proteger_o_piso_de_carbo_do_bulking():
    # 80 kg, 2200 kcal, gordura 30%: carbo abaixo de 3 g/kg. A fração configurada vale e o motor AVISA
    # (`carb_below_min_g_per_kg`), igual a BALANCED, em vez de mudar a fração silenciosamente.
    params = replace(st.StrategyParams.defaults_for(S.BULKING), fat_kcal_share=0.30)
    alvo = mc.macro_split_for_priority(2200.0, params, P.FIXED_PROTEIN, PESO, MLG)
    assert _share_gordura(alvo) == pytest.approx(0.30)
    assert alvo.carb_g / PESO < st.BULK_CARB_MIN_G_PER_KG
    assert mc.LIMIT_CARB_BELOW_MIN in alvo.limitations


def test_proteina_fixa_nao_leva_a_gordura_abaixo_do_piso_da_faixa():
    # Sem folga alguma de carbo: a gordura fica no piso da faixa (15% no cutting), nunca abaixo.
    params = st.StrategyParams.defaults_for(S.CUTTING)
    alvo = mc.macro_split_for_priority(1000.0, params, P.FIXED_PROTEIN, PESO, MLG)
    assert _share_gordura(alvo) >= st.CUT_FAT_KCAL_SHARE_RANGE[0] - 1e-9
