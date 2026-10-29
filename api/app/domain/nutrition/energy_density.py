"""Densidade energética da variação de peso (Hall 2008 + curva de Forbes).

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`, padrão `densidade_energetica`).
Plano: `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §3.2 e §7.

Nada de "7.700 kcal por kg" fixo: a energia por kg de peso perdido/ganho depende
de quanto da variação é gordura. Hall (2008): tecido adiposo ≈ 9.400 kcal/kg,
tecido magro ≈ 1.800 kcal/kg; fração de gordura na variação pela curva de Forbes
(1987): p_gordura = MG / (MG + 10,4), com MG = massa gorda atual em kg.

Também é o dono de `DAYS_PER_WEEK` dentro do pacote de nutrição: a conversão
"ritmo semanal -> kcal/dia" nasce aqui e os módulos de semana/ciclo/fases
importam a constante daqui (nunca escrevem o 7 na conta).

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

from typing import Any, Final, Optional

from .anthropometrics import non_negative_number, positive_number

# Hall 2008 (kcal por kg de tecido).
FAT_TISSUE_KCAL_PER_KG: Final[float] = 9400.0
LEAN_TISSUE_KCAL_PER_KG: Final[float] = 1800.0
# Forbes 1987: constante da curva MLG × gordura (kg).
FORBES_CONSTANT_KG: Final[float] = 10.4

DAYS_PER_WEEK: Final[int] = 7


def fat_share_of_weight_change(fat_mass_kg: Any) -> Optional[float]:
    """Fração da variação de peso que é gordura: MG / (MG + 10,4). MG >= 0."""
    massa_gorda = non_negative_number(fat_mass_kg)
    if massa_gorda is None:
        return None
    return massa_gorda / (massa_gorda + FORBES_CONSTANT_KG)


def tissue_energy_density(fat_mass_kg: Any) -> Optional[float]:
    """kcal por kg de variação de peso para a massa gorda atual."""
    fracao_gordura = fat_share_of_weight_change(fat_mass_kg)
    if fracao_gordura is None:
        return None
    return fracao_gordura * FAT_TISSUE_KCAL_PER_KG + (1.0 - fracao_gordura) * LEAN_TISSUE_KCAL_PER_KG


def daily_kcal_for_weekly_rate(weekly_kg_change: Any, fat_mass_kg: Any) -> Optional[float]:
    """kcal/dia de déficit (positivo) ou superávit (negativo) para variar
    `weekly_kg_change` kg por semana (positivo = perder). None sem massa gorda."""
    if weekly_kg_change is None or isinstance(weekly_kg_change, bool):
        return None
    try:
        variacao = float(weekly_kg_change)
    except (TypeError, ValueError):
        return None
    densidade = tissue_energy_density(fat_mass_kg)
    if densidade is None:
        return None
    return variacao * densidade / DAYS_PER_WEEK


def weekly_kg_for_daily_kcal(daily_kcal: Any, fat_mass_kg: Any) -> Optional[float]:
    """Inverso de `daily_kcal_for_weekly_rate`: kg/semana para um déficit diário."""
    if daily_kcal is None or isinstance(daily_kcal, bool):
        return None
    try:
        kcal = float(daily_kcal)
    except (TypeError, ValueError):
        return None
    densidade = tissue_energy_density(fat_mass_kg)
    if densidade is None or positive_number(densidade) is None:
        return None
    return kcal * DAYS_PER_WEEK / densidade
