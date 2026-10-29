"""TDEE observado (dono `app/domain/nutrition/adaptive_tdee.py`, decisão D-J):
só com ≥ 14 dias e ≥ 80% de dias registrados, tendência do peso por mínimos
quadrados (pesagem diária ou esparsa dá o mesmo número) e `reconcile` que só
SUGERE, nunca recalcula o plano sozinho."""

from __future__ import annotations

import dataclasses
import json

import pytest

from app.domain.nutrition import adaptive_tdee as at
from app.domain.nutrition import energy_density as ed
from app.domain.data_nature import DataNature, DataQuality

MASSA_GORDA = 12.0
DENSIDADE = ed.tissue_energy_density(MASSA_GORDA)  # ≈ 5.871 kcal/kg
INGESTAO = 2500.0


def _pesos(dias: int, inicial: float = 80.0, por_dia: float = 0.0, em: tuple[int, ...] | None = None) -> list:
    """Peso linear; `em` = só esses dias pesados (o resto None)."""
    return [inicial + por_dia * i if em is None or i in em else None for i in range(dias)]


def test_constantes():
    assert at.MIN_WINDOW_DAYS == 14 and at.PREFERRED_WINDOW_DAYS == 28
    assert at.MIN_LOGGED_DAYS_SHARE == 0.8
    assert at.MIN_WEIGHT_READINGS == 2 and at.MIN_WEIGHT_SPAN_DAYS == 7
    assert at.RECONCILE_WEIGHT_BY_QUALITY == {
        DataQuality.HIGH: 1.0, DataQuality.MEDIUM: 0.5, DataQuality.LOW: 0.0, DataQuality.INSUFFICIENT: 0.0,
    }
    assert set(at.RECONCILE_WEIGHT_BY_QUALITY) == set(DataQuality)


@pytest.mark.parametrize(("dias", "qualidade"), [(14, DataQuality.MEDIUM), (27, DataQuality.MEDIUM), (28, DataQuality.HIGH), (35, DataQuality.HIGH)])
def test_peso_estavel_da_a_media_da_ingestao(dias, qualidade):
    resultado = at.observed_tdee([INGESTAO] * dias, _pesos(dias), MASSA_GORDA)
    assert resultado.kcal == pytest.approx(INGESTAO)
    assert resultado.quality == qualidade and resultado.nature == DataNature.ESTIMATED
    assert resultado.limitations == () and resultado.computable


@pytest.mark.parametrize(
    ("por_dia", "em"),
    [
        (-0.05, None),  # perdendo, pesagem diária
        (-0.05, (0, 7, 13)),  # perdendo, pesagem semanal
        (-0.05, (6, 13)),  # perdendo, só a segunda semana (o caso que o rascunho errava)
        (0.03, None),  # ganhando, pesagem diária
        (0.03, (0, 4, 9, 13)),
    ],
)
def test_variacao_linear_do_peso_vira_energia_armazenada(por_dia, em):
    resultado = at.observed_tdee([INGESTAO] * 14, _pesos(14, por_dia=por_dia, em=em), MASSA_GORDA)
    assert resultado.weight_trend.slope_kg_per_day == pytest.approx(por_dia)
    assert resultado.kcal == pytest.approx(INGESTAO - por_dia * DENSIDADE)
    if por_dia < 0:
        assert resultado.kcal > INGESTAO  # perdendo peso: gasta mais do que come
    else:
        assert resultado.kcal < INGESTAO


def test_ruido_diario_em_torno_da_reta_nao_muda_a_inclinacao():
    oscilacao = [0.4, -0.4] * 7  # água, simétrica
    pesos = [80.0 - 0.05 * i + oscilacao[i] for i in range(14)]
    resultado = at.observed_tdee([INGESTAO] * 14, pesos, MASSA_GORDA)
    assert resultado.weight_trend.slope_kg_per_day == pytest.approx(-0.05, abs=0.02)


def test_dias_sem_ingestao_contam_so_os_registrados_na_media():
    ingestao = [2400.0, 2600.0] * 7
    ingestao[3] = None
    ingestao[10] = None  # 12/14 = 86% registrados
    resultado = at.observed_tdee(ingestao, _pesos(14), MASSA_GORDA)
    assert resultado.logged_days == 12
    assert resultado.kcal == pytest.approx(sum(v for v in ingestao if v is not None) / 12)


@pytest.mark.parametrize(
    ("ingestao", "pesos", "massa_gorda", "limitacao"),
    [
        ([INGESTAO] * 13, _pesos(13), MASSA_GORDA, at.LIMIT_WINDOW_TOO_SHORT),
        ([INGESTAO] * 11 + [None] * 3, _pesos(14), MASSA_GORDA, at.LIMIT_TOO_FEW_LOGGED_DAYS),  # 78,6%
        ([INGESTAO] * 15 + [None] * 5, _pesos(20), MASSA_GORDA, at.LIMIT_TOO_FEW_LOGGED_DAYS),  # 75%
        ([INGESTAO] * 14, _pesos(14, em=(5,)), MASSA_GORDA, at.LIMIT_TOO_FEW_WEIGHTS),
        ([INGESTAO] * 14, _pesos(14, em=(2, 6)), MASSA_GORDA, at.LIMIT_WEIGHT_SPAN_TOO_SHORT),
        ([INGESTAO] * 14, _pesos(14), None, at.LIMIT_MISSING_FAT_MASS),
        ([], [], MASSA_GORDA, at.LIMIT_TOO_FEW_LOGGED_DAYS),
    ],
)
def test_janela_insuficiente_devolve_none_com_limitacao(ingestao, pesos, massa_gorda, limitacao):
    resultado = at.observed_tdee(ingestao, pesos, massa_gorda)
    assert resultado.kcal is None and not resultado.computable
    assert limitacao in resultado.limitations
    assert resultado.quality == DataQuality.INSUFFICIENT and resultado.nature == DataNature.ESTIMATED
    json.dumps(resultado.to_dict())


def test_oitenta_por_cento_exatos_bastam():
    resultado = at.observed_tdee([INGESTAO] * 16 + [None] * 4, _pesos(20), MASSA_GORDA)
    assert resultado.computable and resultado.logged_days == 16


@pytest.mark.parametrize("em", [(0, 7), (3, 10), (0, 13)])
def test_pesagens_a_uma_semana_ou_mais_bastam(em):
    assert at.observed_tdee([INGESTAO] * 14, _pesos(14, por_dia=-0.05, em=em), MASSA_GORDA).computable


def test_series_de_tamanhos_diferentes_sao_erro_do_chamador():
    with pytest.raises(ValueError, match=at.LIMIT_SERIES_LENGTH_MISMATCH):
        at.observed_tdee([INGESTAO] * 14, _pesos(13), MASSA_GORDA)


def test_weight_trend_ignora_pesagem_invalida():
    tendencia = at.weight_trend([80.0, None, 0, -1, "x", 79.9, None, 79.8, 79.7, 79.6])
    assert tendencia.readings == 5 and tendencia.span_days == 9
    assert tendencia.slope_kg_per_day is not None
    json.dumps(tendencia.to_dict())


def _observado(dias: int, por_dia: float) -> at.AdaptiveTdee:
    return at.observed_tdee([INGESTAO] * dias, _pesos(dias, por_dia=por_dia), MASSA_GORDA)


@pytest.mark.parametrize(("dias", "peso_esperado"), [(28, 1.0), (14, 0.5)])
def test_reconcile_desloca_a_formula_pelo_peso_da_qualidade(dias, peso_esperado):
    observado = _observado(dias, -0.05)
    formula = 2700.0
    sugestao = at.reconcile(formula, observado)
    assert sugestao.weight == peso_esperado
    assert sugestao.suggested_kcal == pytest.approx(formula + peso_esperado * (observado.kcal - formula))
    assert sugestao.delta_kcal == pytest.approx(sugestao.suggested_kcal - formula)
    assert sugestao.should_suggest is True


def test_reconcile_sem_observado_ou_sem_formula_nao_sugere():
    insuficiente = _observado(13, 0.0)
    sem_observado = at.reconcile(2700.0, insuficiente)
    assert sem_observado.suggested_kcal is None and sem_observado.should_suggest is False
    assert sem_observado.weight == 0.0 and at.LIMIT_WINDOW_TOO_SHORT in sem_observado.limitations

    sem_formula = at.reconcile(None, _observado(28, 0.0))
    assert sem_formula.suggested_kcal is None and sem_formula.should_suggest is False
    assert at.LIMIT_FORMULA_TDEE_MISSING in sem_formula.limitations


def test_reconcile_igual_a_formula_nao_sugere_mudanca():
    observado = _observado(28, 0.0)  # kcal = 2500
    sugestao = at.reconcile(observado.kcal, observado)
    assert sugestao.delta_kcal == 0.0 and sugestao.should_suggest is False


def test_adaptativo_nunca_recalcula_sozinho():
    """D-J: o motor devolve a SUGESTÃO; quem aplica é o admin/PT. Os resultados
    são imutáveis, `reconcile` é puro e não recebe o plano para alterar."""
    observado = _observado(28, -0.05)
    primeira = at.reconcile(2700.0, observado)
    assert at.reconcile(2700.0, observado) == primeira
    assert observado == _observado(28, -0.05)
    with pytest.raises(dataclasses.FrozenInstanceError):
        observado.kcal = 0.0  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        primeira.suggested_kcal = 0.0  # type: ignore[misc]
    assert list(dataclasses.asdict(primeira)) == [
        "formula_kcal", "observed_kcal", "suggested_kcal", "delta_kcal", "weight", "should_suggest", "limitations",
    ]
    json.dumps(primeira.to_dict())
