"""M42/M59 - o mesmo numero sai com o mesmo centesimo nos dois backends.

O PyMySQL entrega `DECIMAL` como `Decimal`, o shim da RDS Data API como `str`,
e as horas sao `float` calculado em Python. Com `round(x, 2)` nativo, um valor
exatamente no meio centesimo (`72.645`) virava `72.64` (o binario do float fica
um fio abaixo) - e `Decimal` com arredondamento bancario tambem dava `72.64`
enquanto a conta exata pedia `72.65`. `round_half_up` e o ponto unico: os tres
tipos convergem para `ROUND_HALF_UP`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.intelligence.dashboard.contracts import DataNature
from app.intelligence.dashboard.coercion import round_half_up
from app.intelligence.dashboard.muscle_summary_service import median_interval_hours, metric

# Valores vistos nos flakes do CI (#253: 72,65 x 72,64; #302: 73,85 x 73,84)
# e o classico 2.675, que o float guarda abaixo do meio.
FRONTEIRAS = [("72.645", 72.65), ("73.845", 73.85), ("25.845", 25.85), ("2.675", 2.68), ("1.005", 1.01)]


@pytest.mark.parametrize(("texto", "esperado"), FRONTEIRAS)
def test_meio_centesimo_igual_para_float_decimal_e_str(texto, esperado):
    como_float = round_half_up(float(texto), 2)
    como_decimal = round_half_up(Decimal(texto), 2)
    como_str = round_half_up(texto, 2)

    assert como_float == como_decimal == como_str == esperado
    assert isinstance(como_float, float) and isinstance(como_decimal, float)


@pytest.mark.parametrize(("texto", "esperado"), FRONTEIRAS)
def test_metric_da_o_mesmo_valor_venha_decimal_ou_float(texto, esperado):
    def valor(v):
        return metric(v, unit="h", nature=DataNature.DERIVED, version_key="muscle_frequency")["value"]

    assert valor(float(texto)) == valor(Decimal(texto)) == esperado


def test_metric_preserva_inteiro_e_none():
    def valor(v):
        return metric(v, unit="x", nature=DataNature.OBSERVED, version_key="muscle_frequency")["value"]

    assert valor(None) is None
    assert valor(3) == 3 and isinstance(valor(3), int)


def test_round_half_up_ausencia_e_nao_finito():
    assert round_half_up(None) is None
    assert round_half_up("nao-e-numero") is None
    assert round_half_up(float("inf")) == float("inf")


def test_horas_na_fronteira_sobem_meio_centesimo():
    """72h + 0,645h (2322 s) entre dois estimulos: 72.645 h exatos."""
    inicio = datetime(2026, 9, 28, 19, 0)
    fim = inicio + timedelta(hours=72, seconds=2322)

    assert median_interval_hours([inicio, fim]) == 72.65
