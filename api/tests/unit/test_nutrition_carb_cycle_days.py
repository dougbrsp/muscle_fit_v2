"""Ciclo de carbo por dias escolhidos (N1, D5; dono `domain/nutrition/carb_cycling.py`)."""

from __future__ import annotations

import pytest

from app.domain.nutrition import carb_cycling as cc
from app.domain.nutrition import daily_target as dt
from app.domain.nutrition import labels as lb
from app.domain.nutrition import macros as mc
from app.domain.nutrition import strategy as st

H, N, L = cc.DayKind.HIGH, cc.DayKind.NORMAL, cc.DayKind.LOW
PESO = 80.0


def _meta(carb_g: float, gordura_g: float = 70.0, proteina_g: float = 160.0) -> mc.MacroTargets:
    kcal = mc.kcal_from_macros(proteina_g, carb_g, gordura_g)
    return mc.MacroTargets(
        kcal=kcal,
        protein_g=proteina_g,
        carb_g=carb_g,
        fat_g=gordura_g,
        fiber_g=mc.fiber_target_g(kcal),
        protein_basis=st.ProteinBasis.BODY_WEIGHT,
        protein_basis_kg=PESO,
        protein_g_per_kg=2.0,
    )


def test_piso_de_carbo_do_dia_baixo_e_o_do_dono_das_macros():
    assert cc.MIN_LOW_CARB_G_PER_KG == mc.MIN_CARB_G_PER_KG == 1.0


# --- validação -------------------------------------------------------------------------------


def test_mapa_valido_com_chaves_inteiras_ou_texto_e_valores_enum_ou_texto():
    r = cc.validate_cycle_days({0: "high", "2": H, 4: "HIGH", 1: "low", "3": L, 5: N})
    assert r.valid and r.cycles
    assert r.kinds == (H, L, H, L, H, N, N)  # dia ausente = normal
    assert r.day_map[0] == "high" and r.day_map[6] == "normal"


def test_semana_toda_normal_e_valida_e_sem_ciclo():
    for entrada in ({}, {i: "normal" for i in range(7)}):
        r = cc.validate_cycle_days(entrada)
        assert r.valid and not r.cycles and r.kinds == (N,) * 7


@pytest.mark.parametrize(
    ("entrada", "erro"),
    [
        ({7: "high", 0: "low"}, f"{cc.ERR_INVALID_WEEKDAY}:7"),
        ({-1: "high"}, f"{cc.ERR_INVALID_WEEKDAY}:-1"),
        ({"seg": "high"}, f"{cc.ERR_INVALID_WEEKDAY}:seg"),
        ({True: "high"}, f"{cc.ERR_INVALID_WEEKDAY}:True"),
        ({0: "alto"}, f"{cc.ERR_INVALID_DAY_KIND}:0"),
        ({0: None}, f"{cc.ERR_INVALID_DAY_KIND}:0"),
        ({0: "high", "0": "low"}, f"{cc.ERR_DUPLICATE_WEEKDAY}:0"),
        (["high"], cc.ERR_INVALID_DAY_MAP),
        (None, cc.ERR_INVALID_DAY_MAP),
    ],
)
def test_entradas_invalidas_geram_erro_e_semana_plana(entrada, erro):
    r = cc.validate_cycle_days(entrada)
    assert not r.valid and erro in r.errors
    assert r.kinds == (N,) * 7 and not r.cycles


@pytest.mark.parametrize("so", [H, L])
def test_so_altos_ou_so_baixos_nao_preservam_a_media(so):
    # Com dias normais fixos em 1,0 não há multiplicador que some 7: a média não se preserva.
    r = cc.validate_cycle_days({0: so.value, 3: so.value})
    assert r.errors == (cc.ERR_CYCLE_NEEDS_HIGH_AND_LOW,)


def test_toda_combinacao_aceita_preserva_a_media_semanal():
    alvo = _meta(300.0)
    for altos in range(0, 7):
        for baixos in range(0, 7 - altos):
            mapa = {i: "high" for i in range(altos)} | {altos + j: "low" for j in range(baixos)}
            r = cc.validate_cycle_days(mapa)
            if not r.valid:
                assert (altos == 0) != (baixos == 0)
                continue
            semana = cc.distribute_week(alvo, r.kinds, PESO)
            assert cc.weekly_carb_total(semana) == pytest.approx(7 * 300.0)
            assert sum(d.kcal for d in semana.days) == pytest.approx(7 * alvo.kcal)


# --- proposta a partir do Modelo ------------------------------------------------------------------


def test_proposta_do_modelo_treino_alto_descanso_baixo():
    r = cc.propose_day_kinds([0, 2, 4])  # seg, qua, sex
    assert r.valid and r.kinds == (H, L, H, L, H, L, L)
    assert cc.validate_cycle_days(r.day_map).kinds == r.kinds  # a proposta passa na validação


def test_proposta_aceita_texto_e_repetidos():
    assert cc.propose_day_kinds(["1", 1, 3]).kinds == (L, H, L, H, L, L, L)


@pytest.mark.parametrize("treino", [[], [0, 1, 2, 3, 4, 5, 6]])
def test_proposta_sem_treino_ou_treino_todo_dia_fica_normal(treino):
    r = cc.propose_day_kinds(treino)
    assert r.valid and r.kinds == (N,) * 7 and not r.cycles


@pytest.mark.parametrize("treino", [[7], [0, "x"], "012", 5, None])
def test_proposta_invalida(treino):
    r = cc.propose_day_kinds(treino)
    assert not r.valid and r.kinds == (N,) * 7


# --- ciclo sobre a média semanal -------------------------------------------------------------------


def test_ciclo_sobre_a_media_preserva_a_soma_com_treino_explicito():
    # Gasto explícito: o carbo já é maior nos dias de treino (seg, qua, sex +60 g).
    base = 260.0
    dias = tuple(_meta(base + (60.0 if i in (0, 2, 4) else 0.0)) for i in range(7))
    kinds = cc.propose_day_kinds([0, 2, 4]).kinds
    soma_entrada = sum(d.carb_g for d in dias)
    semana = cc.distribute_week_over_mean(dias, kinds, PESO)
    assert cc.weekly_carb_total(semana) == pytest.approx(soma_entrada)
    assert sum(d.kcal for d in semana.days) == pytest.approx(sum(d.kcal for d in dias))
    assert sum(d.protein_g for d in semana.days) == pytest.approx(sum(d.protein_g for d in dias))
    assert sum(d.fat_g for d in semana.days) == pytest.approx(sum(d.fat_g for d in dias))

    # O jeito antigo (multiplicador sobre o carbo de cada dia) NÃO preserva: o achado de +50 g/semana.
    mult = cc.week_multipliers(kinds, base, PESO)
    antigo = sum(d.carb_g * mult.by_kind[k] for d, k in zip(dias, kinds))
    assert antigo - soma_entrada > 40.0


def test_ciclo_sobre_a_media_usa_media_vezes_multiplicador():
    dias = tuple(_meta(c) for c in (330.0, 250.0, 330.0, 250.0, 330.0, 250.0, 250.0))
    kinds = (H, L, H, L, H, L, L)
    media = sum(d.carb_g for d in dias) / 7
    mult = cc.week_multipliers(kinds, media, PESO)
    semana = cc.distribute_week_over_mean(dias, kinds, PESO)
    for dia, kind in zip(semana.days, kinds):
        assert dia.carb_g == pytest.approx(media * mult.by_kind[kind])
    assert semana.days[0].carb_g > semana.days[1].carb_g


def test_semana_sem_ciclo_devolve_as_metas_como_vieram():
    dias = tuple(_meta(250.0 + i) for i in range(7))
    semana = cc.distribute_week_over_mean(dias, (N,) * 7, PESO)
    assert semana.days == dias


def test_piso_de_carbo_que_impede_o_ciclo_devolve_as_metas_com_limitacao():
    dias = tuple(_meta(75.0) for _ in range(7))  # menos de 1 g/kg na média: o piso do dia baixo anula o ciclo
    kinds = (H, L, L, L, L, L, L)
    semana = cc.distribute_week_over_mean(dias, kinds, PESO)
    assert cc.LIMIT_CARB_FLOOR_PREVENTS_CYCLE in semana.limitations
    assert semana.days == dias


def test_ciclo_sobre_a_media_valida_a_semana_e_a_meta():
    dias = tuple(_meta(250.0) for _ in range(7))
    with pytest.raises(ValueError, match=cc.LIMIT_WEEK_NOT_SEVEN_DAYS):
        cc.distribute_week_over_mean(dias[:6], (N,) * 6)
    nula = mc.MacroTargets(None, None, None, None, None, None, None, None)
    semana = cc.distribute_week_over_mean((nula, *dias[1:]), (H, L, H, L, H, L, L), PESO)
    assert semana.limitations == (cc.LIMIT_TARGET_NOT_COMPUTABLE,)


def test_codigos_de_erro_tem_rotulo_e_o_ciclo_do_plano_segue_indisponivel_ate_o_n2():
    for erro in cc.validate_cycle_days({7: "high"}).errors + cc.validate_cycle_days({0: "high"}).errors:
        assert lb.limitation_label(erro) != erro
    assert lb.limitation_label(f"{cc.ERR_INVALID_DAY_KIND}:3").startswith("Tipo de dia inválido")
    assert lb.limitation_label(f"{cc.ERR_DUPLICATE_WEEKDAY}:3").startswith("Dia da semana repetido")
    assert dt.LIMIT_WEEK_FLAGS_REQUIRED_FOR_CYCLE  # o orquestrador atual não foi tocado
