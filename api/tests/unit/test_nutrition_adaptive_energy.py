"""Estimador adaptativo (sombra): janela, suavização, confiança, ajuste diário e fold. Spec §7–§13, §21."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta
from typing import Callable, Optional

import pytest

from app.domain.nutrition import adaptive_energy as ae
from app.domain.nutrition.anthropometrics import NON_EXERCISE_ACTIVITY_FACTORS, ActivityLevel
from app.domain.nutrition.dashboard import DayState
from app.domain.nutrition.energy_density import tissue_energy_density

D0 = date(2026, 1, 1)
BMR = 2020.0
FAT = 20.0
FATOR = NON_EXERCISE_ACTIVITY_FACTORS[ActivityLevel.SEDENTARY]


def _entradas(
    n: int,
    *,
    intake: float = 2400.0,
    peso: Callable[[int], Optional[float]] = lambda i: 80.0,
    logado: Callable[[int], bool] = lambda i: True,
    ativas: Callable[[int], Optional[float]] = lambda i: 600.0,
    confirmado: Callable[[int], bool] = lambda i: True,
    bmr: Optional[float] = BMR,
    gordura: Optional[float] = FAT,
    estado_nao_logado: DayState = DayState.NO_RECORD,
) -> list[ae.DayInput]:
    saida = []
    for i in range(n):
        ok = logado(i)
        p = peso(i)
        saida.append(
            ae.DayInput(
                day=D0 + timedelta(days=i),
                day_state=DayState.LOGGED if ok else estado_nao_logado,
                intake_kcal=intake if ok else None,
                bmr_kcal=bmr,
                weights_kg=(p,) if p is not None else (),
                fat_mass_kg=gordura,
                active_kcal=ativas(i),
                activity_confirmed=confirmado(i),
            )
        )
    return saida


def _final(entradas: list[ae.DayInput]) -> ae.AdaptiveEnergyState:
    return ae.replay(entradas)[-1]


def _com_n_dias_validos(n_validos: int, total: int = 40) -> ae.AdaptiveEnergyState:
    return _final(_entradas(total, logado=lambda i: i >= total - n_validos))


# --- status do dia (D3) -----------------------------------------------------


@pytest.mark.parametrize(
    "estado,ingestao,esperado",
    [
        (DayState.LOGGED, 2000.0, ae.DayStatus.COMPLETE),
        (DayState.LOGGED, ae.COMPLETE_MIN_INTAKE_BMR_SHARE * BMR, ae.DayStatus.COMPLETE),
        (DayState.LOGGED, ae.COMPLETE_MIN_INTAKE_BMR_SHARE * BMR - 1.0, ae.DayStatus.PARTIAL),
        (DayState.LOGGED, None, ae.DayStatus.PARTIAL),
        (DayState.INCOMPLETE, 2000.0, ae.DayStatus.PARTIAL),
        (DayState.NO_RECORD, None, ae.DayStatus.MISSING),
        (DayState.OPEN_TODAY, 500.0, ae.DayStatus.MISSING),
        (DayState.OUT_OF_PLAN, 2000.0, ae.DayStatus.MISSING),
    ],
)
def test_classify_day(estado, ingestao, esperado):
    assert ae.classify_day(estado, ingestao, BMR) is esperado


def test_logged_sem_tmb_nao_pode_ser_completo():
    assert ae.classify_day(DayState.LOGGED, 2000.0, None) is ae.DayStatus.PARTIAL


# --- exemplo da spec §9, com a densidade do dono -----------------------------


def test_exemplo_spec_gasto_observado_com_densidade_do_dono():
    kg_dia = -0.45 / 7
    estado = _final(_entradas(80, peso=lambda i: 90.0 + kg_dia * i))
    inclinacao = estado.weight.slope_kg_per_day
    assert inclinacao == pytest.approx(kg_dia, rel=0.02)
    densidade = tissue_energy_density(FAT)
    assert estado.observed_kcal == pytest.approx(2400.0 - inclinacao * densidade)
    # Equivalente da spec (2.895) trocando 7.700 pela densidade de Hall/Forbes (~6.800).
    assert estado.observed_kcal == pytest.approx(2400.0 + 0.45 / 7 * densidade, rel=0.02)
    assert estado.observed_kcal < 2895.0
    assert estado.adaptive_kcal == pytest.approx(estado.observed_kcal, rel=0.02)
    assert estado.suspension_reasons == ()


def test_ganho_de_peso_reduz_o_gasto_observado_abaixo_da_ingestao():
    estado = _final(_entradas(80, peso=lambda i: 70.0 + 0.03 * i))
    assert estado.weight.slope_kg_per_day > 0
    assert estado.observed_kcal < 2400.0


# --- transição 7 / 14 / 21 dias válidos --------------------------------------


def test_7_dias_validos_nao_atualiza_e_predomina_o_bootstrap():
    estado = _com_n_dias_validos(7)
    assert estado.valid_days == 7
    assert estado.adaptive_kcal is None and estado.observed_kcal is None
    assert estado.confidence == 0.0
    assert estado.base_kcal == pytest.approx(estado.bootstrap_mean_kcal)
    assert ae.FLAG_ADAPTIVE_UNAVAILABLE in estado.flags and ae.FLAG_UPDATE_SUSPENDED in estado.flags
    razao = {r.code: r for r in estado.suspension_reasons}
    assert razao[ae.REASON_COMPLETE_DAYS_BELOW_MIN].have == 7
    assert razao[ae.REASON_COMPLETE_DAYS_BELOW_MIN].need == ae.MIN_COMPLETE_DAYS_IN_WINDOW


def test_13_dias_validos_ainda_suspende_14_atualiza():
    assert _com_n_dias_validos(13).adaptive_kcal is None
    estado = _com_n_dias_validos(14)
    assert estado.suspension_reasons == ()
    assert estado.adaptive_kcal == pytest.approx(2400.0)
    assert estado.last_update_day == estado.day


def test_14_dias_validos_mistura_metade_bootstrap_metade_adaptativo():
    estado = _com_n_dias_validos(14)
    assert estado.confidence == pytest.approx(0.5)
    assert estado.bootstrap_mean_kcal == pytest.approx(BMR * FATOR + 600.0)
    esperado = 0.5 * estado.bootstrap_mean_kcal + 0.5 * estado.adaptive_kcal
    assert estado.base_kcal == pytest.approx(esperado)


def test_21_dias_validos_confianca_total_e_base_igual_ao_adaptativo():
    estado = _com_n_dias_validos(21)
    assert estado.valid_days == 21 and estado.window_days == 21
    assert estado.confidence == 1.0
    assert estado.base_kcal == pytest.approx(estado.adaptive_kcal)


def test_confianca_cresce_linearmente_entre_8_e_21_dias():
    # Janela válida só a partir de 14; abaixo disso a confiança fica em 0 (sem adaptativo).
    c = [_com_n_dias_validos(n).confidence for n in (14, 17, 21)]
    assert c == pytest.approx([0.5, 0.5 + 3 / 14, 1.0])


# --- dia parcial, peso ausente, janela inválida ------------------------------


def test_dia_parcial_nao_entra_na_media_de_ingestao():
    # 16 dias completos de 2400 e 5 parciais de 200: o observado usa só os completos.
    parcial = lambda i: i < 5  # noqa: E731
    entradas = [
        replace(e, day_state=DayState.INCOMPLETE, intake_kcal=200.0) if parcial(i) else e
        for i, e in enumerate(_entradas(21))
    ]
    estado = _final(entradas)
    assert estado.valid_days == 16
    assert ae.FLAG_PARTIAL_DAYS_EXCLUDED in estado.flags
    assert estado.observed_kcal == pytest.approx(2400.0)


def test_dia_logado_abaixo_da_fracao_da_tmb_nao_entra_na_media():
    entradas = _entradas(21)
    entradas[3] = replace(entradas[3], intake_kcal=100.0)
    estado = _final(entradas)
    assert estado.valid_days == 20
    assert estado.observed_kcal == pytest.approx(2400.0)


def test_peso_ausente_suspende_com_motivo_exato():
    estado = _final(_entradas(40, peso=lambda i: None))
    codigos = {r.code for r in estado.suspension_reasons}
    assert ae.REASON_WEIGHT_HISTORY_SHORT in codigos and ae.REASON_WEIGHINS_BELOW_MIN in codigos
    assert ae.REASON_COMPLETE_DAYS_BELOW_MIN not in codigos
    assert estado.adaptive_kcal is None


def test_pesagens_esparsas_demais_suspendem():
    estado = _final(_entradas(40, peso=lambda i: 80.0 if i % 14 == 0 else None))
    assert ae.REASON_WEIGHINS_BELOW_MIN in {r.code for r in estado.suspension_reasons}


def test_outlier_de_peso_aparece_nas_flags_e_nao_derruba_o_estado():
    pesos = lambda i: 95.0 if i == 39 else 80.0  # noqa: E731
    estado = _final(_entradas(40, peso=pesos))
    assert "weight_outlier" in estado.flags
    assert estado.observed_kcal == pytest.approx(2400.0)


def test_massa_gorda_ausente_suspende_sem_derrubar():
    estado = _final(_entradas(40, gordura=None))
    assert ae.REASON_FAT_MASS_MISSING in {r.code for r in estado.suspension_reasons}
    assert estado.adaptive_kcal is None


def test_inclinacao_implausivel_suspende():
    estado = _final(_entradas(60, peso=lambda i: 100.0 - 0.25 * i))
    razao = {r.code: r for r in estado.suspension_reasons}
    assert ae.REASON_SLOPE_IMPLAUSIBLE in razao
    assert razao[ae.REASON_SLOPE_IMPLAUSIBLE].need == ae.MAX_PLAUSIBLE_WEEKLY_CHANGE_KG
    # A tendência (EMA) ainda estava subindo de ritmo nos primeiros dias: o valor de então é preservado,
    # mas a atualização do último dia foi suspensa.
    assert estado.last_update_day is None or estado.last_update_day < estado.day
    assert ae.FLAG_UPDATE_SUSPENDED in estado.flags


def test_gasto_observado_implausivel_suspende():
    # Ingestão de 8.000 kcal com peso estável: observado muito acima de 3x a TMB.
    estado = _final(_entradas(40, intake=8000.0))
    assert ae.REASON_OBSERVED_IMPLAUSIBLE in {r.code for r in estado.suspension_reasons}
    assert estado.adaptive_kcal is None


def test_janela_invalida_preserva_o_ultimo_valor_e_reduz_a_confianca():
    historico = _entradas(40)
    ate_aqui = ae.replay(historico)[-1]
    assert ate_aqui.adaptive_kcal == pytest.approx(2400.0) and ate_aqui.confidence == 1.0
    # Dez dias sem registro: a janela perde dias completos e a atualização é suspensa.
    depois = [
        ae.DayInput(day=D0 + timedelta(days=40 + i), day_state=DayState.NO_RECORD, bmr_kcal=BMR, fat_mass_kg=FAT)
        for i in range(10)
    ]
    estados = ae.replay(depois, initial=ate_aqui)
    final = estados[-1]
    assert final.adaptive_kcal == ate_aqui.adaptive_kcal
    assert final.observed_kcal == ate_aqui.observed_kcal
    assert final.last_update_day is not None and final.last_update_day < final.day
    assert final.confidence <= ae.SUSPENDED_CONFIDENCE_CAP < ate_aqui.confidence
    assert final.base_kcal is not None and final.base_kcal > 0
    assert ae.FLAG_UPDATE_SUSPENDED in final.flags
    assert final.suspension_reasons and final.suspension_reasons[0].code == ae.REASON_COMPLETE_DAYS_BELOW_MIN
    # A confiança nunca sobe durante a suspensão.
    confs = [ate_aqui.confidence] + [e.confidence for e in estados]
    assert all(b <= a + 1e-12 for a, b in zip(confs, confs[1:]))


# --- suavização ±75 ----------------------------------------------------------


def test_passo_limitado_a_75_kcal_por_atualizacao():
    base = _final(_entradas(40))
    anterior = replace(base, adaptive_kcal=1500.0)
    proximo = ae.step(anterior, _entradas(41)[-1])
    assert proximo.adaptive_kcal == pytest.approx(1500.0 + ae.EXPENDITURE_MAX_STEP_KCAL)
    assert ae.FLAG_UPDATE_CLAMPED in proximo.flags
    abaixo = ae.step(replace(base, adaptive_kcal=3300.0), _entradas(41)[-1])
    assert abaixo.adaptive_kcal == pytest.approx(3300.0 - ae.EXPENDITURE_MAX_STEP_KCAL)


def test_passo_pequeno_usa_alpha_sem_limite():
    base = _final(_entradas(40))
    proximo = ae.step(replace(base, adaptive_kcal=2350.0), _entradas(41)[-1])
    assert proximo.adaptive_kcal == pytest.approx(ae.EXPENDITURE_ALPHA * 2400.0 + (1 - ae.EXPENDITURE_ALPHA) * 2350.0)
    assert ae.FLAG_UPDATE_CLAMPED not in proximo.flags


def test_primeira_atualizacao_adota_o_observado_sem_suavizar():
    estado = _com_n_dias_validos(14)
    assert estado.adaptive_kcal == estado.observed_kcal
    assert abs(estado.adaptive_kcal - estado.bootstrap_mean_kcal) > ae.EXPENDITURE_MAX_STEP_KCAL


# --- bootstrap e ajuste diário ----------------------------------------------


def test_bootstrap_do_dia_usa_fator_sedentario_do_dono_mais_atividade():
    estado = ae.step(None, _entradas(1, ativas=lambda i: 500.0)[0])
    assert ae.BOOTSTRAP_ACTIVITY_FACTOR == FATOR
    assert estado.bootstrap_today_kcal == pytest.approx(BMR * FATOR + 500.0)


def test_dia_sem_atividade_confirmada_usa_media_esperada_nunca_zero():
    entradas = _entradas(
        3,
        ativas=lambda i: 800.0 if i < 2 else None,
        confirmado=lambda i: i < 2,
    )
    estado = _final(entradas)
    assert estado.activity_today_kcal is None
    assert estado.bootstrap_today_kcal == pytest.approx(BMR * FATOR + 800.0)
    assert estado.bootstrap_today_kcal > BMR * FATOR


def test_sem_nenhuma_atividade_confirmada_so_o_fator_e_flag():
    estado = _final(_entradas(2, ativas=lambda i: None, confirmado=lambda i: False))
    assert estado.bootstrap_today_kcal == pytest.approx(BMR * FATOR)
    assert ae.FLAG_BOOTSTRAP_ACTIVITY_UNKNOWN in estado.flags


def test_descanso_confirmado_entra_como_zero_na_media():
    # [600, 0 (descanso confirmado), 600]: média 400; ajuste = (600 - 400) x 0,60.
    ativas = {0: 600.0, 1: None, 2: 600.0}
    estado = _final(_entradas(3, ativas=lambda i: ativas[i], confirmado=lambda i: True))
    assert estado.activity_average_kcal == pytest.approx(400.0)
    assert estado.activity_adjustment_kcal == pytest.approx(200.0 * ae.ACTIVITY_ADJUSTMENT_COEFFICIENT)


def test_dia_nao_confirmado_fica_fora_da_media():
    estado = _final(
        _entradas(3, ativas=lambda i: 600.0 if i != 1 else None, confirmado=lambda i: i != 1),
    )
    assert estado.activity_average_kcal == pytest.approx(600.0)
    assert estado.activity_adjustment_kcal == 0.0


def test_atividade_nao_sincronizada_hoje_nao_aplica_ajuste():
    entradas = _entradas(10, ativas=lambda i: 600.0 if i < 9 else None, confirmado=lambda i: i < 9)
    estado = _final(entradas)
    assert estado.activity_today_kcal is None
    assert estado.activity_adjustment_kcal == 0.0
    assert ae.FLAG_ACTIVITY_PENDING in estado.flags
    assert estado.estimated_today_kcal == pytest.approx(estado.base_kcal)


def test_kcal_ativa_invalida_com_dia_confirmado_conta_como_nao_confirmado():
    entradas = _entradas(2, ativas=lambda i: -50.0 if i == 1 else 600.0)
    estado = _final(entradas)
    assert estado.activity_today_kcal is None and ae.FLAG_ACTIVITY_PENDING in estado.flags


def test_ajuste_diario_limitado_a_mais_300_e_menos_250():
    def com_ultimo(valor: float) -> ae.AdaptiveEnergyState:
        return _final(_entradas(30, ativas=lambda i: 600.0 if i < 29 else valor))

    alto = com_ultimo(2000.0)
    assert alto.activity_adjustment_kcal == ae.ACTIVITY_ADJUSTMENT_MAX_KCAL
    assert ae.FLAG_ADJUSTMENT_CLAMPED in alto.flags
    baixo = _final(_entradas(30, ativas=lambda i: 1500.0 if i < 29 else 0.0))
    assert baixo.activity_adjustment_kcal == ae.ACTIVITY_ADJUSTMENT_MIN_KCAL
    # Dentro dos limites, sem clamp: (823 - 600) x 0,60 vira ~134 (spec §16), com média ajustada pela janela.
    medio = com_ultimo(823.0)
    media = (600.0 * (ae.WINDOW_DAYS - 1) + 823.0) / ae.WINDOW_DAYS
    assert medio.activity_adjustment_kcal == pytest.approx((823.0 - media) * ae.ACTIVITY_ADJUSTMENT_COEFFICIENT)
    assert ae.FLAG_ADJUSTMENT_CLAMPED not in medio.flags
    assert medio.estimated_today_kcal == pytest.approx(medio.base_kcal + medio.activity_adjustment_kcal)


def test_rotina_estavel_gera_ajuste_zero():
    estado = _final(_entradas(40))
    assert estado.activity_adjustment_kcal == 0.0
    assert estado.estimated_today_kcal == pytest.approx(estado.base_kcal)


# --- fold --------------------------------------------------------------------


def test_replay_igual_a_steps_encadeados():
    entradas = _entradas(
        45,
        peso=lambda i: 85.0 - 0.04 * i + (0.3 if i % 3 == 0 else 0.0),
        logado=lambda i: i % 9 != 4,
        ativas=lambda i: 400.0 + 50.0 * (i % 5),
        confirmado=lambda i: i % 11 != 10,
    )
    encadeado = []
    atual = None
    for e in entradas:
        atual = ae.step(atual, e)
        encadeado.append(atual)
    assert ae.replay(entradas) == encadeado
    assert ae.replay(reversed(entradas)) == encadeado  # replay ordena por dia


def test_replay_retomado_do_estado_persistido_igual_ao_completo():
    entradas = _entradas(50, peso=lambda i: 85.0 - 0.03 * i)
    completo = ae.replay(entradas)
    parcial = ae.replay(entradas[:30])
    assert ae.replay(entradas[30:], initial=parcial[-1]) == completo[30:]


def test_dias_pulados_viram_missing():
    entradas = _entradas(40)
    com_buraco = entradas[:30] + entradas[35:]
    explicito = entradas[:30] + [
        ae.DayInput(day=D0 + timedelta(days=i), day_state=DayState.NO_RECORD, bmr_kcal=BMR, fat_mass_kg=FAT) for i in range(30, 35)
    ] + entradas[35:]
    assert ae.replay(com_buraco)[-1].window == ae.replay(explicito)[-1].window


def test_step_exige_dia_posterior():
    estado = _final(_entradas(3))
    with pytest.raises(ValueError):
        ae.step(estado, _entradas(3)[2])


def test_tmb_e_massa_gorda_ausentes_no_dia_usam_o_ultimo_valor_conhecido():
    entradas = _entradas(30)
    entradas[-1] = replace(entradas[-1], bmr_kcal=None, fat_mass_kg=None)
    estado = _final(entradas)
    assert estado.bmr_kcal == BMR and estado.fat_mass_kg == FAT


def test_sem_tmb_nenhum_dia_e_completo_e_ha_flag():
    estado = _final(_entradas(40, bmr=None))
    assert estado.valid_days == 0 and estado.bootstrap_today_kcal is None
    assert ae.FLAG_BMR_MISSING in estado.flags
    assert estado.base_kcal is None and estado.estimated_today_kcal is None


# --- contrato do to_dict -----------------------------------------------------


def test_to_dict_contrato_e_serializavel():
    estado = _final(_entradas(40))
    d = estado.to_dict()
    json.dumps(d)
    assert set(d) == {
        "date",
        "bmr",
        "bootstrap_tdee",
        "bootstrap_mean_tdee",
        "observed_tdee",
        "adaptive_tdee",
        "base_tdee",
        "activity_calories_today",
        "average_activity_calories",
        "activity_adjustment",
        "estimated_tdee_today",
        "confidence",
        "valid_days",
        "window_days",
        "last_update_date",
        "trend_weight_kg",
        "weight_slope_kg_per_day",
        "suspension_reasons",
        "algorithm_version",
        "data_quality_flags",
    }
    assert d["algorithm_version"] == ae.ALGORITHM_VERSION == "adaptive-v1"
    assert d["date"] == (D0 + timedelta(days=39)).isoformat()
    assert isinstance(d["data_quality_flags"], list)


def test_to_dict_lista_o_que_falta():
    d = _com_n_dias_validos(7).to_dict()
    assert d["suspension_reasons"][0] == {"code": ae.REASON_COMPLETE_DAYS_BELOW_MIN, "have": 7, "need": ae.MIN_COMPLETE_DAYS_IN_WINDOW}


# --- forma pública, horizonte e estado da sombra ------------------------------


def test_to_public_dict_arredonda_e_classifica():
    estado = _final(_entradas(80, peso=lambda i: 90.0 - 0.45 / 7 * i))
    pub = estado.to_public_dict()
    json.dumps(pub)
    assert set(pub) == set(estado.to_dict()) | {"confidence_level", "weight_slope_kg_per_week"}
    for chave in ("bmr", "observed_tdee", "adaptive_tdee", "base_tdee", "estimated_tdee_today"):
        assert pub[chave] == round(pub[chave], ae.ROUNDING_DIGITS_PUBLIC)
    assert pub["confidence"] == 1.0 and pub["confidence_level"] == "high"
    assert pub["weight_slope_kg_per_week"] == pytest.approx(-0.45, abs=0.02)
    assert pub["weight_slope_kg_per_week"] == round(pub["weight_slope_kg_per_week"], 2)
    assert pub["trend_weight_kg"] == round(pub["trend_weight_kg"], 2)


def test_to_public_dict_sem_dados_tem_nulos_e_nivel_baixo():
    pub = ae.step(None, _entradas(1, bmr=None)[0]).to_public_dict()
    assert pub["confidence"] == 0.0 and pub["confidence_level"] == "low"
    assert pub["weight_slope_kg_per_day"] is None and pub["weight_slope_kg_per_week"] is None
    assert pub["adaptive_tdee"] is None


@pytest.mark.parametrize(
    "confianca,nivel",
    [(0.0, "low"), (0.39, "low"), (0.40, "medium"), (0.5, "medium"), (0.79, "medium"), (0.80, "high"), (1.0, "high")],
)
def test_confidence_level_limiares(confianca, nivel):
    assert ae.confidence_level(confianca).value == nivel


def test_replay_history_days_aquece_a_ema_e_fecha_a_janela():
    assert ae.REPLAY_HISTORY_DAYS >= ae.WINDOW_DAYS + 4 * 7
    estado = _final(_entradas(ae.REPLAY_HISTORY_DAYS, peso=lambda i: 90.0 - 0.45 / 7 * i))
    assert estado.weight.slope_kg_per_day == pytest.approx(-0.45 / 7, rel=0.01)
    assert estado.suspension_reasons == ()


def test_shadow_day_state_independe_de_plano():
    hoje = date(2026, 3, 10)
    ontem = hoje - timedelta(days=1)
    assert ae.shadow_day_state(hoje, hoje, True, True) is DayState.OPEN_TODAY
    assert ae.shadow_day_state(hoje + timedelta(days=1), hoje, False, False) is DayState.OPEN_TODAY
    assert ae.shadow_day_state(ontem, hoje, False, False) is DayState.NO_RECORD
    assert ae.shadow_day_state(ontem, hoje, True, False) is DayState.INCOMPLETE
    assert ae.shadow_day_state(ontem, hoje, True, True) is DayState.LOGGED
    assert ae.classify_day(ae.shadow_day_state(ontem, hoje, True, True), 2000.0, BMR) is ae.DayStatus.COMPLETE
    assert ae.classify_day(ae.shadow_day_state(hoje, hoje, True, True), 2000.0, BMR) is ae.DayStatus.MISSING


# --- TMB do dia (precedência no motor) ---------------------------------------


@pytest.mark.parametrize(
    "gravada,fallback,esperado",
    [
        (2000.0, 1900.0, 2000.0),
        (None, 1900.0, 1900.0),
        (0, 1900.0, 1900.0),
        (-5, 1900.0, 1900.0),
        ("x", 1900.0, 1900.0),
        (True, 1900.0, 1900.0),
        (float("nan"), 1900.0, 1900.0),
        (None, None, None),
        (0, 0, None),
        (2000.0, None, 2000.0),
    ],
)
def test_resolve_day_bmr(gravada, fallback, esperado):
    assert ae.resolve_day_bmr(gravada, fallback) == esperado


def test_latest_bmr_ultima_valida_na_ordem_dada():
    assert ae.latest_bmr([1900.0, None, 2000.0, 0, None]) == 2000.0
    assert ae.latest_bmr([2000.0, 1900.0]) == 1900.0
    assert ae.latest_bmr([None, 0, "x"]) is None
    assert ae.latest_bmr([]) is None
    assert ae.latest_bmr(iter([1800.0])) == 1800.0


def test_observado_implausivel_informa_o_limite_violado():
    piso, teto = ae.OBSERVED_BMR_SHARE_RANGE
    alto = _final(_entradas(40, intake=8000.0))
    razao = {r.code: r for r in alto.suspension_reasons}[ae.REASON_OBSERVED_IMPLAUSIBLE]
    assert razao.need == pytest.approx(teto * BMR) and razao.have > razao.need
    baixo = _final(_entradas(40, intake=1700.0))
    # 1.700 kcal com TMB de 2.020 fica abaixo do piso (0,9 x TMB = 1.818).
    razao = {r.code: r for r in baixo.suspension_reasons}[ae.REASON_OBSERVED_IMPLAUSIBLE]
    assert razao.need == pytest.approx(piso * BMR) and razao.have < razao.need


def test_mudanca_de_patamar_do_peso_nao_suspende_por_inclinacao_implausivel():
    pesos = lambda i: 80.0 if i < 50 else 90.0  # noqa: E731
    estado = _final(_entradas(64, peso=pesos))
    assert ae.REASON_SLOPE_IMPLAUSIBLE not in {r.code for r in estado.suspension_reasons}


def test_suspension_reasons_publicos_so_com_numero_nos_motivos_de_contagem():
    pub = _com_n_dias_validos(7).to_public_dict()["suspension_reasons"][0]
    assert pub == {"code": ae.REASON_COMPLETE_DAYS_BELOW_MIN, "have": 7, "need": ae.MIN_COMPLETE_DAYS_IN_WINDOW, "unit": "days"}
    sem_gordura = _final(_entradas(40, gordura=None)).to_public_dict()["suspension_reasons"]
    assert sem_gordura == [{"code": ae.REASON_FAT_MASS_MISSING, "have": None, "need": None, "unit": None}]
    implausivel = _final(_entradas(40, intake=8000.0)).to_public_dict()["suspension_reasons"]
    assert implausivel == [{"code": ae.REASON_OBSERVED_IMPLAUSIBLE, "have": None, "need": None, "unit": None}]
    esparso = _final(_entradas(40, peso=lambda i: 80.0 if i % 14 == 0 else None)).to_public_dict()["suspension_reasons"]
    semanal = next(r for r in esparso if r["code"] == ae.REASON_WEIGHINS_BELOW_MIN)
    assert semanal["unit"] == "per_week" and semanal["need"] == ae.MIN_WEIGHINS_PER_WEEK
    # O to_dict interno segue completo.
    assert _final(_entradas(40, intake=8000.0)).to_dict()["suspension_reasons"][0]["have"] > 0


def test_todo_motivo_de_contagem_tem_unidade_e_so_eles():
    assert set(ae.REASON_COUNT_UNITS) == {
        ae.REASON_COMPLETE_DAYS_BELOW_MIN,
        ae.REASON_WEIGHT_HISTORY_SHORT,
        ae.REASON_WEIGHINS_BELOW_MIN,
    }
