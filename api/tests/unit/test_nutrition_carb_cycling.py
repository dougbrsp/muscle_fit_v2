"""Ciclo de carboidrato (dono `app/domain/nutrition/carb_cycling.py`, decisão
D-I): distribuição do carbo da semana com soma = 7 × alvo, proteína e gordura
constantes, piso de 1 g/kg no dia baixo e kcal do dia acompanhando o carbo."""

from __future__ import annotations

import json

import pytest

from app.domain.nutrition import carb_cycling as cc
from app.domain.nutrition import macros as mc
from app.domain.nutrition import strategy as st

H, N, L = cc.DayKind.HIGH, cc.DayKind.NORMAL, cc.DayKind.LOW
PESO = 80.0
T, F = True, False


def _alvo(kcal: float = 2600.0) -> mc.MacroTargets:
    return mc.macro_split(kcal, st.StrategyParams.defaults_for(st.Strategy.BULKING), PESO)


def _alvo_com_carbo(carb_g: float) -> mc.MacroTargets:
    proteina, gordura = 160.0, 70.0
    kcal = mc.kcal_from_macros(proteina, carb_g, gordura)
    return mc.MacroTargets(
        kcal=kcal,
        protein_g=proteina,
        carb_g=carb_g,
        fat_g=gordura,
        fiber_g=mc.fiber_target_g(kcal),
        protein_basis=st.ProteinBasis.BODY_WEIGHT,
        protein_basis_kg=PESO,
        protein_g_per_kg=2.0,
    )


def test_constantes_da_fonte():
    assert cc.CARB_CYCLE_SWING_SHARE == 0.25
    assert cc.HIGH_CARB_MULTIPLIER == 1.25 and cc.LOW_CARB_MULTIPLIER == 0.75
    assert cc.NORMAL_CARB_MULTIPLIER == 1.0 and cc.MIN_LOW_CARB_G_PER_KG == 1.0


@pytest.mark.parametrize(
    ("treino", "esperado"),
    [
        ((T, F, T, F, T, F, F), (H, L, H, L, H, L, L)),
        ((F, T, F, F, F, F, F), (L, H, L, L, L, L, L)),
        ((T, T, T, T, T, T, F), (H, H, H, H, H, H, L)),
        ((T,) * 7, (N,) * 7),
        ((F,) * 7, (N,) * 7),
        ((1, 0, 1, 0, 1, 0, 0), (H, L, H, L, H, L, L)),  # flags "truthy" também valem
    ],
)
def test_day_kinds_for_week(treino, esperado):
    assert cc.day_kinds_for_week(treino) == esperado


@pytest.mark.parametrize("dias", [0, 6, 8])
def test_semana_precisa_de_sete_dias(dias):
    with pytest.raises(ValueError, match=cc.LIMIT_WEEK_NOT_SEVEN_DAYS):
        cc.day_kinds_for_week((T,) * dias)
    with pytest.raises(ValueError, match=cc.LIMIT_WEEK_NOT_SEVEN_DAYS):
        cc.week_multipliers((H,) * dias, 300.0, PESO)


SEMANAS = [
    (T, F, F, F, F, F, F),
    (T, F, T, F, F, F, F),
    (T, F, T, F, T, F, F),
    (T, T, F, T, T, F, F),
    (T, T, T, F, T, T, F),
    (T, T, T, T, T, T, F),
    (F, F, F, F, F, F, F),
    (T, T, T, T, T, T, T),
]


@pytest.mark.parametrize("treino", SEMANAS)
@pytest.mark.parametrize("kcal", [1800.0, 2600.0, 3400.0])
def test_invariante_soma_semanal_igual_a_sete_vezes_o_alvo(treino, kcal):
    alvo = _alvo(kcal)
    semana = cc.distribute_week(alvo, cc.day_kinds_for_week(treino), PESO)
    assert len(semana.days) == 7
    assert sum(d.carb_g for d in semana.days) == pytest.approx(7 * alvo.carb_g)
    assert cc.weekly_carb_total(semana) == pytest.approx(7 * alvo.carb_g)
    assert sum(d.kcal for d in semana.days) == pytest.approx(7 * alvo.kcal)
    assert sum(semana.multipliers.by_kind[k] for k in semana.kinds) == pytest.approx(7)


@pytest.mark.parametrize("treino", SEMANAS)
def test_proteina_e_gordura_constantes_e_kcal_acompanha_o_carbo(treino):
    alvo = _alvo()
    semana = cc.distribute_week(alvo, cc.day_kinds_for_week(treino), PESO)
    for dia in semana.days:
        assert dia.protein_g == alvo.protein_g
        assert dia.fat_g == alvo.fat_g
        assert dia.kcal == pytest.approx(mc.kcal_from_macros(dia.protein_g, dia.carb_g, dia.fat_g))
        assert dia.fiber_g == pytest.approx(mc.fiber_target_g(dia.kcal))


@pytest.mark.parametrize("treino", SEMANAS[:6])
def test_alto_e_baixo_mantem_a_razao_nominal_sem_piso(treino):
    semana = cc.distribute_week(_alvo(), cc.day_kinds_for_week(treino), PESO)
    por_tipo = semana.multipliers.by_kind
    assert por_tipo[H] > 1.0 > por_tipo[L]
    assert por_tipo[H] / por_tipo[L] == pytest.approx(cc.HIGH_CARB_MULTIPLIER / cc.LOW_CARB_MULTIPLIER)
    assert semana.limitations == ()


def test_semana_com_o_mesmo_numero_de_altos_e_baixos_usa_os_nominais():
    tipos = (H, L, H, L, H, L, N)
    mult = cc.week_multipliers(tipos, 300.0, PESO)
    assert mult.by_kind[H] == pytest.approx(cc.HIGH_CARB_MULTIPLIER)
    assert mult.by_kind[L] == pytest.approx(cc.LOW_CARB_MULTIPLIER)
    assert mult.by_kind[N] == pytest.approx(cc.NORMAL_CARB_MULTIPLIER)


@pytest.mark.parametrize("tipos", [(H,) * 7, (L,) * 7, (N,) * 7, (H, N, N, N, N, N, N), (L, N, N, N, N, N, N)])
def test_sem_alto_ou_sem_baixo_fica_plano(tipos):
    mult = cc.week_multipliers(tipos, 300.0, PESO)
    assert all(valor == cc.NORMAL_CARB_MULTIPLIER for valor in mult.by_kind.values())


def test_piso_de_1_g_por_kg_prende_o_dia_baixo_e_o_alto_absorve():
    # 3 altos, 4 baixos: o baixo nominal reescalado (0,778) daria 77,8 g < 80 g (1 g/kg).
    alvo = _alvo_com_carbo(100.0)
    semana = cc.distribute_week(alvo, cc.day_kinds_for_week((T, F, T, F, T, F, F)), PESO)
    baixos = [d.carb_g for d, k in zip(semana.days, semana.kinds) if k == L]
    altos = [d.carb_g for d, k in zip(semana.days, semana.kinds) if k == H]
    assert baixos == [pytest.approx(cc.MIN_LOW_CARB_G_PER_KG * PESO)] * 4
    assert altos == [pytest.approx((7 * 100.0 - 4 * 80.0) / 3)] * 3
    assert sum(d.carb_g for d in semana.days) == pytest.approx(7 * 100.0)
    assert semana.limitations == ()


@pytest.mark.parametrize("carbo", [80.0, 60.0])
def test_media_no_piso_ou_abaixo_nao_cicla(carbo):
    alvo = _alvo_com_carbo(carbo)
    semana = cc.distribute_week(alvo, cc.day_kinds_for_week((T, F, T, F, T, F, F)), PESO)
    assert semana.limitations == (cc.LIMIT_CARB_FLOOR_PREVENTS_CYCLE,)
    assert all(d.carb_g == pytest.approx(carbo) for d in semana.days)
    assert sum(d.kcal for d in semana.days) == pytest.approx(7 * alvo.kcal)


def test_sem_peso_nao_aplica_piso():
    mult = cc.week_multipliers((H, L, H, L, H, L, L), 10.0, None)
    assert mult.limitations == () and mult.by_kind[L] < 1.0


def test_alvo_nao_calculavel_repete_o_alvo_com_limitacao():
    vazio = mc.macro_split(None, st.StrategyParams.defaults_for(st.Strategy.MAINTENANCE), PESO)
    semana = cc.distribute_week(vazio, cc.day_kinds_for_week((T, F, T, F, T, F, F)), PESO)
    assert semana.days == (vazio,) * 7
    assert semana.limitations == (cc.LIMIT_TARGET_NOT_COMPUTABLE,)
    assert cc.weekly_carb_total(semana) is None
    dia, limitacoes = cc.apply_cycle(vazio, H, semana.kinds, PESO)
    assert dia is vazio and limitacoes == (cc.LIMIT_TARGET_NOT_COMPUTABLE,)


@pytest.mark.parametrize("treino", SEMANAS[:6])
def test_apply_cycle_e_o_mesmo_dia_de_distribute_week(treino):
    alvo = _alvo()
    tipos = cc.day_kinds_for_week(treino)
    semana = cc.distribute_week(alvo, tipos, PESO)
    for dia, tipo in zip(semana.days, tipos):
        sozinho, limitacoes = cc.apply_cycle(alvo, tipo, tipos, PESO)
        assert sozinho == dia and limitacoes == semana.limitations


def test_to_dict_serializavel():
    semana = cc.distribute_week(_alvo(), cc.day_kinds_for_week((T, F, T, F, T, F, F)), PESO)
    dados = json.loads(json.dumps(semana.to_dict()))
    assert dados["kinds"] == ["high", "low", "high", "low", "high", "low", "low"]
    assert set(dados["multipliers"]["by_kind"]) == {"high", "normal", "low"}
