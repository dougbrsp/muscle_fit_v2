"""Espera da suite de integracao pela virada do dia do usuario (#280)."""

from datetime import datetime, timedelta

from tests.support.relogio_usuario import (
    FOLGA_APOS_VIRADA,
    MARGEM_VIRADA_DO_DIA,
    segundos_de_espera_ate_a_virada,
)


def test_perto_da_meia_noite_espera_ate_ela_mais_folga():
    esperado = (timedelta(minutes=10) + FOLGA_APOS_VIRADA).total_seconds()
    assert segundos_de_espera_ate_a_virada(datetime(2026, 11, 23, 23, 50), MARGEM_VIRADA_DO_DIA) == esperado


def test_longe_da_meia_noite_nao_espera():
    assert segundos_de_espera_ate_a_virada(datetime(2026, 11, 23, 23, 39), MARGEM_VIRADA_DO_DIA) == 0


def test_logo_apos_a_virada_nao_espera():
    assert segundos_de_espera_ate_a_virada(datetime(2026, 11, 24, 0, 0, 5), MARGEM_VIRADA_DO_DIA) == 0


def test_exatamente_na_margem_nao_espera_mas_um_segundo_depois_espera():
    assert segundos_de_espera_ate_a_virada(datetime(2026, 11, 23, 23, 40), MARGEM_VIRADA_DO_DIA) > 0
    assert segundos_de_espera_ate_a_virada(datetime(2026, 11, 23, 23, 39, 59), MARGEM_VIRADA_DO_DIA) == 0
