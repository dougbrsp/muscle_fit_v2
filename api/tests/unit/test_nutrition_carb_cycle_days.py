"""Ciclo de carbo por dias escolhidos (N1, D5; dono `domain/nutrition/carb_cycling.py`)."""

from __future__ import annotations

import pytest

from app.domain.nutrition import carb_cycling as cc
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


@pytest.mark.parametrize("extremo", [H, L])
def test_os_sete_dias_altos_ou_os_sete_baixos_nao_tem_como_ciclar(extremo):
    r = cc.validate_cycle_days({i: extremo.value for i in range(7)})
    assert r.errors == (cc.ERR_CYCLE_ALL_DAYS_SAME_EXTREME,) and not r.valid


@pytest.mark.parametrize("so", [H, L])
def test_so_altos_ou_so_baixos_com_normais_e_valido_e_preserva_a_media(so):
    r = cc.validate_cycle_days({0: so.value, 3: so.value})
    assert r.valid and r.cycles


def test_altos_e_normais_escalam_o_dia_normal_para_preservar_a_media():
    # 2 altos (1,25) + 5 normais (1,0): soma nominal 7,5; escala 7/7,5; normal = 0,9333, alto = 1,1667.
    mult = cc.week_multipliers((H, N, N, H, N, N, N), 300.0, PESO)
    assert mult.by_kind[N] == pytest.approx(0.93333, abs=1e-5)
    assert mult.by_kind[H] == pytest.approx(1.16667, abs=1e-5)
    assert 2 * mult.by_kind[H] + 5 * mult.by_kind[N] == pytest.approx(7.0)


def test_baixos_e_normais_escalam_o_dia_normal_para_cima():
    # 2 baixos (0,75) + 5 normais: soma 6,5; escala 7/6,5; normal = 1,0769, baixo = 0,8077.
    mult = cc.week_multipliers((L, N, N, L, N, N, N), 300.0, PESO)
    assert mult.by_kind[N] == pytest.approx(1.07692, abs=1e-5)
    assert mult.by_kind[L] == pytest.approx(0.80769, abs=1e-5)


def test_distribuicao_expoe_o_multiplicador_real_de_cada_dia():
    semana = cc.distribute_week(_meta(300.0), (H, N, N, H, N, N, N), PESO)
    assert semana.day_multipliers == pytest.approx((1.16667, 0.93333, 0.93333, 1.16667, 0.93333, 0.93333, 0.93333), abs=1e-5)
    assert semana.to_dict()["day_multipliers"] == list(semana.day_multipliers)
    assert [d.carb_g for d in semana.days] == pytest.approx([300.0 * m for m in semana.day_multipliers])


def test_apply_cycle_no_ramo_novo_bate_com_a_distribuicao():
    alvo = _meta(300.0)
    kinds = (H, N, N, H, N, N, N)
    semana = cc.distribute_week(alvo, kinds, PESO)
    for indice, kind in enumerate(kinds):
        dia, limitacoes = cc.apply_cycle(alvo, kind, kinds, PESO)
        assert dia.carb_g == pytest.approx(semana.days[indice].carb_g) and limitacoes == ()


def test_piso_de_carbo_no_ramo_altos_e_normais():
    # Carbo médio de 85 g (1,06 g/kg) com 1 alto + 6 normais: o dia normal escalado (0,9459) cairia
    # abaixo de 1 g/kg; ele fica no piso (1/1,0625 = 0,941) e o alto absorve o resto.
    carbo = 85.0
    mult = cc.week_multipliers((H, N, N, N, N, N, N), carbo, PESO)
    assert mult.by_kind[N] * carbo >= PESO * cc.MIN_LOW_CARB_G_PER_KG - 1e-9
    assert mult.by_kind[H] + 6 * mult.by_kind[N] == pytest.approx(7.0)
    assert mult.by_kind[H] > mult.by_kind[N]


def test_piso_de_carbo_sem_folga_no_ramo_altos_e_normais_fica_plano_com_limitacao():
    # Média abaixo de 1 g/kg: nenhum dia pode descer, então a semana fica plana.
    mult = cc.week_multipliers((H, N, N, N, N, N, N), 70.0, PESO)
    assert mult.limitations == (cc.LIMIT_CARB_FLOOR_PREVENTS_CYCLE,)
    assert set(mult.by_kind.values()) == {1.0}


def test_piso_de_carbo_no_ramo_baixos_e_normais_o_normal_absorve():
    # 6 baixos + 1 normal com média de 85 g: o baixo escalado (0,6 g.) cairia sob o piso; o normal leva o resto.
    carbo = 85.0
    mult = cc.week_multipliers((L, L, L, L, L, L, N), carbo, PESO)
    assert mult.by_kind[L] * carbo >= PESO * cc.MIN_LOW_CARB_G_PER_KG - 1e-9
    assert 6 * mult.by_kind[L] + mult.by_kind[N] == pytest.approx(7.0)
    assert mult.by_kind[N] > 1.0


def test_toda_combinacao_aceita_preserva_a_media_semanal():
    alvo = _meta(300.0)
    aceitas = 0
    for altos in range(0, 8):
        for baixos in range(0, 8 - altos):
            mapa = {i: "high" for i in range(altos)} | {altos + j: "low" for j in range(baixos)}
            r = cc.validate_cycle_days(mapa)
            if not r.valid:
                assert altos == 7 or baixos == 7
                continue
            aceitas += 1
            semana = cc.distribute_week(alvo, r.kinds, PESO)
            assert cc.weekly_carb_total(semana) == pytest.approx(7 * 300.0)
            assert sum(d.kcal for d in semana.days) == pytest.approx(7 * alvo.kcal)
    assert aceitas == 34  # 36 combinações (altos, baixos) com altos + baixos <= 7, menos as duas de 7 iguais


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
    assert lb.limitation_label(f"{cc.ERR_INVALID_DAY_KIND}:3") == "Tipo de dia inválido (Quinta-feira)."
    assert "Domingo" in lb.limitation_label(f"{cc.ERR_DUPLICATE_WEEKDAY}:6")
    assert lb.limitation_label(f"{cc.ERR_DUPLICATE_WEEKDAY}:3").startswith("Dia da semana repetido")


@pytest.mark.parametrize(
    "chave", ["²", "٣", "1.0", "", " ", "10", "-1", "9" * 5000, "0x1", "1e0", 1.0, 3.5, None, (1,)]
)
def test_chave_de_dia_estranha_vira_erro_sem_excecao(chave):
    r = cc.validate_cycle_days({chave: "high", 0: "low"})
    assert not r.valid and any(e.startswith(cc.ERR_INVALID_WEEKDAY) for e in r.errors)
    assert not cc.propose_day_kinds([chave]).valid


def test_chave_de_dia_com_espacos_e_digito_unico_e_aceita():
    r = cc.validate_cycle_days({" 1 ": "high", "3": "low"})
    assert r.valid and r.kinds == (N, H, N, L, N, N, N)


def test_soma_de_kcal_so_se_preserva_quando_a_meta_fecha_por_atwater():
    # Meta de entrada que NÃO fecha por Atwater: kcal declarada 1000, mas proteína + gordura = 1270 kcal e carbo 0
    # (`kcal_below_protein_and_fat`). A saída recalcula por Atwater (1270): carbo, proteína e gordura somam igual,
    # a kcal não.
    fechada = _meta(0.0)
    declarada = mc.MacroTargets(
        kcal=1000.0, protein_g=160.0, carb_g=0.0, fat_g=70.0, fiber_g=14.0,
        protein_basis=st.ProteinBasis.BODY_WEIGHT, protein_basis_kg=PESO, protein_g_per_kg=2.0,
        limitations=(mc.LIMIT_KCAL_BELOW_PROTEIN_AND_FAT,),
    )
    assert fechada.kcal == 1270.0
    dias = (declarada,) * 7
    semana = cc.distribute_week_over_mean(dias, (H, L, H, L, H, L, L), PESO)
    assert cc.weekly_carb_total(semana) == 0.0
    assert sum(d.kcal for d in semana.days) == pytest.approx(7 * 1270.0)
    assert sum(d.kcal for d in semana.days) != pytest.approx(sum(d.kcal for d in dias))
