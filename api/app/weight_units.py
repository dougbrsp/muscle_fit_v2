"""Unidade de carga: quilos (canonico) x libras (entrada/exibicao opcional).

REGRA UNICA DO SISTEMA: o banco guarda SEMPRE quilos. `exercise_sets.weight_kg`
e `DECIMAL(8,2)` em KG e continua sendo carga TOTAL (decisao D12) - a
preferencia por libras (`users.preferred_weight_unit`, migracao 040) e so de
ENTRADA e EXIBICAO. Nenhum motor (progressao, volume, relatorios, IA) conhece
libras: todos leem `weight_kg` como sempre leram.

A conversao acontece na BORDA, e essa borda e a tela (`frontend/src/lib/
weight-units.ts`, espelho deste modulo). Este modulo existe para que:

- o fator e as casas decimais tenham UMA definicao autoritativa tambem no
  servidor (o teste `tests/unit/test_weight_units.py` compara os dois lados);
- qualquer caminho de ESCRITA do servidor (importacao, IA de avaliacao de
  serie) que um dia precise aceitar libras tenha para onde ir, em vez de
  reimplementar a conta.

Fator: 1 lb = 0.45359237 kg (exato, por definicao internacional da libra
avoirdupois desde 1959 - nao e aproximacao).

ARREDONDAMENTO (a decisao que evita "22.6796 lb" na tela):

- kg: 2 casas. E exatamente a precisao da coluna `DECIMAL(8,2)`; gravar mais
  casas seria mentira, porque o banco truncaria.
- lb: 1 casa. NAO e estetica: e o que torna o ida-e-volta ESTAVEL. O erro
  maximo de quantizar kg em 2 casas e 0.005 kg = 0.011 lb, MENOR que a meia
  casa (0.05 lb) que decidiria o arredondamento em 1 casa. Ou seja, um valor
  digitado em lb (1 casa), salvo em kg (2 casas) e reexibido em lb volta
  IDENTICO - e pode ser salvo/reexibido infinitas vezes sem derivar. Com 2
  casas em lb a garantia cairia (0.011 > 0.005) e "1.00 lb" viraria
  "0.99 lb" na primeira reedicao.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

KG = "kg"
LB = "lb"
WEIGHT_UNITS = (KG, LB)
DEFAULT_WEIGHT_UNIT = KG

# 1 lb = 0.45359237 kg (exato). Decimal, nunca float: a conta precisa dar o
# mesmo resultado no servidor e no banco, sem residuo binario.
LB_IN_KG = Decimal("0.45359237")

# Casas decimais de EXIBICAO/gravacao por unidade (ver docstring).
DISPLAY_DECIMALS = {KG: 2, LB: 1}

# Precisao da coluna `exercise_sets.weight_kg` (DECIMAL(8,2)).
KG_DECIMALS = DISPLAY_DECIMALS[KG]


def normalize_weight_unit(raw: Any) -> str:
    """Unidade valida a partir de qualquer entrada. Desconhecido/vazio -> kg
    (default da coluna 040: quem nunca escolheu nada usa quilos)."""
    value = str(raw).strip().lower() if raw is not None else ""
    return value if value in WEIGHT_UNITS else DEFAULT_WEIGHT_UNIT


def is_weight_unit(raw: Any) -> bool:
    return isinstance(raw, str) and raw.strip().lower() in WEIGHT_UNITS


def _round(value: Decimal, decimals: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)


def to_kg(value: Any, unit: Any) -> Decimal:
    """Valor digitado NA UNIDADE `unit` -> quilos, ja quantizado em 2 casas
    (o que a coluna aceita). Esta e a unica funcao que deve produzir o numero
    que vai para `exercise_sets.weight_kg`."""
    amount = Decimal(str(value))
    if normalize_weight_unit(unit) == LB:
        amount = amount * LB_IN_KG
    return _round(amount, KG_DECIMALS)


def from_kg(weight_kg: Any, unit: Any) -> Decimal:
    """Quilos do banco -> numero para MOSTRAR na unidade `unit`, ja arredondado
    para as casas de exibicao daquela unidade (kg: 2, lb: 1)."""
    target = normalize_weight_unit(unit)
    amount = Decimal(str(weight_kg))
    if target == LB:
        amount = amount / LB_IN_KG
    return _round(amount, DISPLAY_DECIMALS[target])


def format_weight(weight_kg: Any, unit: Any) -> str:
    """Texto curto para a tela: "22.5 kg" / "50 lb" (sem zeros a direita)."""
    target = normalize_weight_unit(unit)
    amount = from_kg(weight_kg, target).normalize()
    # `normalize()` de um inteiro vira notacao cientifica (5E+1); `+ 0` volta
    # para a forma posicional.
    text = format(amount if amount.as_tuple().exponent <= 0 else amount.quantize(Decimal(1)), "f")
    return f"{text} {target}"
