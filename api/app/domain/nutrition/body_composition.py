"""Composição corporal: % de gordura por circunferência (Marinha dos EUA),
massa livre de gordura (MLG), FFMI, razão cintura/altura e peso-alvo.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`, padrões `navy_gordura` e
`ffmi_normalizado`). Plano: `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §3.4 e §7.

- Marinha dos EUA (Hodgdon & Beckett 1984), medidas em POLEGADAS:
  H: %G = 86,010·log10(abdômen − pescoço) − 70,041·log10(altura) + 36,76
  M: %G = 163,205·log10(cintura + quadril − pescoço) − 97,684·log10(altura) − 78,387
  É o fallback quando não há bioimpedância; exige o site `neck`.
- FFMI = MLG / altura²; normalizado = FFMI + 6,1·(1,8 − altura) (Kouri 1995);
  referência natural 25, com ressalva.
- Cintura/altura < 0,5 (Ashwell & Hsieh 2005; limiar amplamente usado, fonte
  primária não verificada na sessão de 2026-10-14, ver plano §16).
- Peso-alvo = MLG atual / (1 − %G alvo). IMC e Devine só informativos.
- `BODY_FAT_METHOD_PRIORITY`: bioimpedância antes do fallback por circunferência
  (MÉTODO da leitura, não proveniência; até 2026-10-16 era `BodyFatMethod`).

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Optional

from .anthropometrics import CM_PER_METER, Sex, Subject, height_is_valid, height_m, positive_number

# --- Marinha dos EUA (Hodgdon & Beckett 1984) ---------------------------------
# (coeficiente do log da circunferência, coeficiente do log da altura, constante)
NAVY_MALE: Final[tuple[float, float, float]] = (86.010, 70.041, 36.76)
NAVY_FEMALE: Final[tuple[float, float, float]] = (163.205, 97.684, 78.387)
CM_PER_INCH: Final[float] = 2.54

# --- FFMI (Kouri 1995) --------------------------------------------------------
FFMI_NORMALIZATION_SLOPE: Final[float] = 6.1
FFMI_REFERENCE_HEIGHT_M: Final[float] = 1.8
FFMI_NATURAL_REFERENCE_MAX: Final[float] = 25.0

# --- Cintura/altura -----------------------------------------------------------
WHTR_HEALTHY_MAX: Final[float] = 0.5

# % de gordura plausível (fora disto a leitura é descartada como erro de medida).
BODY_FAT_RANGE_VALID: Final[tuple[float, float]] = (3.0, 60.0)

PERCENT: Final[float] = 100.0


class BodyFatMethod(str, Enum):
    BIOIMPEDANCE = "bioimpedance"
    NAVY = "navy"


# Ordem de preferência quando há mais de uma leitura.
BODY_FAT_METHOD_PRIORITY: Final[tuple[BodyFatMethod, ...]] = (BodyFatMethod.BIOIMPEDANCE, BodyFatMethod.NAVY)

LIMIT_BODY_FAT_OUT_OF_RANGE = "body_fat_out_of_range"
LIMIT_BODY_FAT_MISSING = "body_fat_missing"


def body_fat_is_valid(percent: Any) -> bool:
    valor = positive_number(percent)
    return valor is not None and BODY_FAT_RANGE_VALID[0] <= valor <= BODY_FAT_RANGE_VALID[1]


def _inches(cm: Any) -> Optional[float]:
    valor = positive_number(cm)
    return None if valor is None else valor / CM_PER_INCH


def navy_body_fat(
    sex: Optional[Sex], height_cm: Any, neck_cm: Any, waist_cm: Any, hip_cm: Any = None
) -> Optional[float]:
    """% de gordura pela Marinha dos EUA. `waist_cm` é o abdômen (homem) ou a
    cintura (mulher); `hip_cm` só para mulher. Sexo OTHER/None, medida faltando
    ou logaritmo impossível (abdômen <= pescoço) -> None."""
    if not height_is_valid(height_cm):
        return None
    altura = _inches(height_cm)
    pescoco = _inches(neck_cm)
    cintura = _inches(waist_cm)
    if altura is None or pescoco is None or cintura is None:
        return None
    if sex == Sex.MALE:
        circunferencia = cintura - pescoco
        coef_circ, coef_altura, constante = NAVY_MALE
        sinal = 1.0
    elif sex == Sex.FEMALE:
        quadril = _inches(hip_cm)
        if quadril is None:
            return None
        circunferencia = cintura + quadril - pescoco
        coef_circ, coef_altura, constante = NAVY_FEMALE
        sinal = -1.0
    else:
        return None
    if circunferencia <= 0:
        return None
    return coef_circ * math.log10(circunferencia) - coef_altura * math.log10(altura) + sinal * constante


def ffm_kg(weight_kg: Any, body_fat_percent: Any) -> Optional[float]:
    """Massa livre de gordura = peso × (1 − %G/100). %G fora da faixa -> None."""
    peso = positive_number(weight_kg)
    if peso is None or not body_fat_is_valid(body_fat_percent):
        return None
    return peso * (1.0 - float(body_fat_percent) / PERCENT)


def fat_mass_kg(weight_kg: Any, body_fat_percent: Any) -> Optional[float]:
    """Massa gorda = peso × %G/100. %G fora da faixa -> None."""
    peso = positive_number(weight_kg)
    if peso is None or not body_fat_is_valid(body_fat_percent):
        return None
    return peso * float(body_fat_percent) / PERCENT


def resolved_ffm_kg(subject: Subject) -> Optional[float]:
    """MLG do sujeito: a informada (bioimpedância) ou derivada de peso × %G."""
    informada = positive_number(subject.fat_free_mass_kg)
    if informada is not None:
        return informada
    return ffm_kg(subject.weight_kg, subject.body_fat_percent)


def resolved_fat_mass_kg(subject: Subject) -> Optional[float]:
    """Massa gorda do sujeito: por %G, ou peso − MLG informada."""
    por_percentual = fat_mass_kg(subject.weight_kg, subject.body_fat_percent)
    if por_percentual is not None:
        return por_percentual
    peso = positive_number(subject.weight_kg)
    mlg = positive_number(subject.fat_free_mass_kg)
    if peso is None or mlg is None or mlg >= peso:
        return None
    return peso - mlg


def ffmi(ffm_kg_value: Any, height_cm: Any) -> Optional[float]:
    """FFMI bruto = MLG / altura²."""
    mlg = positive_number(ffm_kg_value)
    altura = height_m(height_cm)
    if mlg is None or altura is None:
        return None
    return mlg / (altura * altura)


def ffmi_normalized(ffm_kg_value: Any, height_cm: Any) -> Optional[float]:
    """FFMI normalizado para 1,80 m (Kouri 1995): FFMI + 6,1 × (1,8 − altura)."""
    bruto = ffmi(ffm_kg_value, height_cm)
    altura = height_m(height_cm)
    if bruto is None or altura is None:
        return None
    return bruto + FFMI_NORMALIZATION_SLOPE * (FFMI_REFERENCE_HEIGHT_M - altura)


def waist_to_height(waist_cm: Any, height_cm: Any) -> Optional[float]:
    """Razão cintura/altura (mesma unidade)."""
    cintura = positive_number(waist_cm)
    if cintura is None or not height_is_valid(height_cm):
        return None
    return cintura / float(height_cm)


def waist_to_height_is_healthy(ratio: Any) -> Optional[bool]:
    valor = positive_number(ratio)
    return None if valor is None else valor < WHTR_HEALTHY_MAX


def target_weight_for_body_fat(ffm_kg_value: Any, target_body_fat_percent: Any) -> Optional[float]:
    """Peso em que a MLG atual corresponde ao %G alvo: MLG / (1 − alvo/100)."""
    mlg = positive_number(ffm_kg_value)
    if mlg is None or not body_fat_is_valid(target_body_fat_percent):
        return None
    return mlg / (1.0 - float(target_body_fat_percent) / PERCENT)


@dataclass(frozen=True)
class BodyFat:
    """% de gordura escolhido entre as leituras disponíveis (None = nenhuma válida)."""

    percent: Optional[float]
    method: Optional[BodyFatMethod]
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "percent": self.percent,
            "method": self.method.value if self.method else None,
            "limitations": list(self.limitations),
        }


def resolve_body_fat(bioimpedance_percent: Any = None, navy_percent: Any = None) -> BodyFat:
    """Primeira leitura válida na ordem de `BODY_FAT_METHOD_PRIORITY`."""
    leituras = {BodyFatMethod.BIOIMPEDANCE: bioimpedance_percent, BodyFatMethod.NAVY: navy_percent}
    limitacoes: list[str] = []
    for metodo in BODY_FAT_METHOD_PRIORITY:
        valor = leituras.get(metodo)
        if valor is None:
            continue
        if body_fat_is_valid(valor):
            return BodyFat(percent=float(valor), method=metodo, limitations=tuple(limitacoes))
        limitacoes.append(f"{LIMIT_BODY_FAT_OUT_OF_RANGE}:{metodo.value}")
    limitacoes.append(LIMIT_BODY_FAT_MISSING)
    return BodyFat(percent=None, method=None, limitations=tuple(limitacoes))


def height_cm_from_m(height_m_value: float) -> float:
    return height_m_value * CM_PER_METER
