"""Taxa metabólica basal (TMB): fórmulas e escolha automática.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`, padrão `tmb_formula`).
Plano: `docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §3.1 e §7.

Fórmulas (plano §16):
- Mifflin-St Jeor (1990): H = 10·kg + 6,25·cm − 5·idade + 5; M = … − 161.
  Padrão sem % de gordura (Frankenfield 2005: a mais confiável dentro de ±10%).
- Katch-McArdle (McArdle, Katch & Katch): 370 + 21,6·MLG. Padrão com massa
  livre de gordura (bioimpedância) recente.
- Cunningham (1980): 500 + 22·MLG. Variante para muito treinados; só comparação
  ou escolha explícita do admin/PT.
- Harris-Benedict revisada (Roza & Shizgal 1984): H = 88,362 + 13,397·kg +
  4,799·cm − 5,677·idade; M = 447,593 + 9,247·kg + 3,098·cm − 4,330·idade.
  Só comparação.

Seleção `auto` (plano §3.1): MLG disponível e recente (`FFM_FRESHNESS_MAX_DAYS`)
-> Katch-McArdle; senão Mifflin-St Jeor. `Sex.OTHER` sem MLG -> TMB não
calculável (Mifflin exige sexo biológico); com MLG -> Katch-McArdle, que não
depende do sexo. `body_composition.bmr_kcal` (balança) só é comparado
(`compare_with_scale`), nunca é dono.

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Optional

from .anthropometrics import Sex, Subject, height_is_valid, positive_number
from .body_composition import resolved_ffm_kg

# --- Mifflin-St Jeor (1990) --------------------------------------------------
MIFFLIN_WEIGHT_COEF: Final[float] = 10.0
MIFFLIN_HEIGHT_COEF: Final[float] = 6.25
MIFFLIN_AGE_COEF: Final[float] = 5.0
MIFFLIN_MALE_CONSTANT: Final[float] = 5.0
MIFFLIN_FEMALE_CONSTANT: Final[float] = -161.0

# --- Katch-McArdle -----------------------------------------------------------
KATCH_MCARDLE_INTERCEPT: Final[float] = 370.0
KATCH_MCARDLE_FFM_COEF: Final[float] = 21.6

# --- Cunningham (1980) -------------------------------------------------------
CUNNINGHAM_INTERCEPT: Final[float] = 500.0
CUNNINGHAM_FFM_COEF: Final[float] = 22.0

# --- Harris-Benedict revisada (Roza & Shizgal 1984), só comparação ----------
HARRIS_BENEDICT_MALE: Final[tuple[float, float, float, float]] = (88.362, 13.397, 4.799, 5.677)
HARRIS_BENEDICT_FEMALE: Final[tuple[float, float, float, float]] = (447.593, 9.247, 3.098, 4.330)

# MLG mais velha que isto não entra na seleção automática (plano §3.1: "≤ 60 d").
FFM_FRESHNESS_MAX_DAYS: Final[int] = 60

# Diferença relativa entre a TMB da balança e a da fórmula que vira aviso.
SCALE_BMR_DEVIATION_WARN_SHARE: Final[float] = 0.15


class BmrFormula(str, Enum):
    AUTO = "auto"
    MIFFLIN_ST_JEOR = "mifflin_st_jeor"
    KATCH_MCARDLE = "katch_mcardle"
    CUNNINGHAM = "cunningham"
    HARRIS_BENEDICT = "harris_benedict"


# Fórmula quando o plano não fixa uma: a seleção automática (plano §3.1).
DEFAULT_BMR_FORMULA: Final[BmrFormula] = BmrFormula.AUTO


# Códigos de limitação (estáveis; o consumidor rotula).
LIMIT_MISSING_WEIGHT = "missing_weight"
LIMIT_MISSING_HEIGHT = "missing_or_invalid_height"
LIMIT_MISSING_AGE = "missing_age"
LIMIT_MISSING_SEX = "missing_sex"
LIMIT_SEX_OTHER_REQUIRES_FFM = "sex_other_requires_ffm"
LIMIT_MISSING_FFM = "missing_ffm"
LIMIT_FFM_STALE = "ffm_stale"


def _sex_constant(sex: Optional[Sex]) -> Optional[float]:
    if sex == Sex.MALE:
        return MIFFLIN_MALE_CONSTANT
    if sex == Sex.FEMALE:
        return MIFFLIN_FEMALE_CONSTANT
    return None


def mifflin_st_jeor(sex: Optional[Sex], weight_kg: Any, height_cm: Any, age_years: Any) -> Optional[float]:
    """TMB de Mifflin-St Jeor. Sexo OTHER/None, peso, altura ou idade inválidos -> None."""
    constante = _sex_constant(sex)
    peso = positive_number(weight_kg)
    idade = positive_number(age_years)
    if constante is None or peso is None or idade is None or not height_is_valid(height_cm):
        return None
    return MIFFLIN_WEIGHT_COEF * peso + MIFFLIN_HEIGHT_COEF * float(height_cm) - MIFFLIN_AGE_COEF * idade + constante


def katch_mcardle(ffm_kg: Any) -> Optional[float]:
    """TMB de Katch-McArdle a partir da massa livre de gordura (kg)."""
    mlg = positive_number(ffm_kg)
    if mlg is None:
        return None
    return KATCH_MCARDLE_INTERCEPT + KATCH_MCARDLE_FFM_COEF * mlg


def cunningham(ffm_kg: Any) -> Optional[float]:
    """TMB de Cunningham (1980) a partir da massa livre de gordura (kg)."""
    mlg = positive_number(ffm_kg)
    if mlg is None:
        return None
    return CUNNINGHAM_INTERCEPT + CUNNINGHAM_FFM_COEF * mlg


def harris_benedict(sex: Optional[Sex], weight_kg: Any, height_cm: Any, age_years: Any) -> Optional[float]:
    """TMB de Harris-Benedict revisada (só comparação)."""
    if sex == Sex.MALE:
        coefs = HARRIS_BENEDICT_MALE
    elif sex == Sex.FEMALE:
        coefs = HARRIS_BENEDICT_FEMALE
    else:
        return None
    peso = positive_number(weight_kg)
    idade = positive_number(age_years)
    if peso is None or idade is None or not height_is_valid(height_cm):
        return None
    intercepto, c_peso, c_altura, c_idade = coefs
    return intercepto + c_peso * peso + c_altura * float(height_cm) - c_idade * idade


def ffm_is_fresh(ffm_age_days: Optional[int]) -> bool:
    """MLG sem data (None) conta como recente; com data, só até o teto."""
    return ffm_age_days is None or int(ffm_age_days) <= FFM_FRESHNESS_MAX_DAYS


def select_formula(
    subject: Subject, requested: BmrFormula = BmrFormula.AUTO, ffm_age_days: Optional[int] = None
) -> BmrFormula:
    """Fórmula efetiva: a pedida, ou (AUTO) Katch-McArdle com MLG recente e
    Mifflin-St Jeor no resto."""
    if requested != BmrFormula.AUTO:
        return requested
    if resolved_ffm_kg(subject) is not None and ffm_is_fresh(ffm_age_days):
        return BmrFormula.KATCH_MCARDLE
    return BmrFormula.MIFFLIN_ST_JEOR


@dataclass(frozen=True)
class BmrResult:
    """TMB calculada (None = não calculável) com a fórmula efetiva e o que faltou."""

    kcal: Optional[float]
    formula: BmrFormula
    requested_formula: BmrFormula
    ffm_kg: Optional[float]
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kcal": self.kcal,
            "formula": self.formula.value,
            "requested_formula": self.requested_formula.value,
            "ffm_kg": self.ffm_kg,
            "limitations": list(self.limitations),
        }


def _limitations_sex_based(subject: Subject) -> tuple[str, ...]:
    faltas: list[str] = []
    if subject.sex is None:
        faltas.append(LIMIT_MISSING_SEX)
    elif subject.sex == Sex.OTHER:
        faltas.append(LIMIT_SEX_OTHER_REQUIRES_FFM)
    if positive_number(subject.weight_kg) is None:
        faltas.append(LIMIT_MISSING_WEIGHT)
    if not height_is_valid(subject.height_cm):
        faltas.append(LIMIT_MISSING_HEIGHT)
    if positive_number(subject.age_years) is None:
        faltas.append(LIMIT_MISSING_AGE)
    return tuple(faltas)


def bmr(
    subject: Subject, formula: BmrFormula = BmrFormula.AUTO, ffm_age_days: Optional[int] = None
) -> BmrResult:
    """TMB do sujeito pela fórmula pedida (ou pela seleção automática)."""
    efetiva = select_formula(subject, formula, ffm_age_days)
    mlg = resolved_ffm_kg(subject)
    if efetiva in (BmrFormula.KATCH_MCARDLE, BmrFormula.CUNNINGHAM):
        kcal = katch_mcardle(mlg) if efetiva == BmrFormula.KATCH_MCARDLE else cunningham(mlg)
        limitacoes: tuple[str, ...] = ()
        if mlg is None:
            limitacoes = (LIMIT_MISSING_FFM,)
        elif not ffm_is_fresh(ffm_age_days):
            limitacoes = (LIMIT_FFM_STALE,)
        return BmrResult(kcal=kcal, formula=efetiva, requested_formula=formula, ffm_kg=mlg, limitations=limitacoes)
    if efetiva == BmrFormula.HARRIS_BENEDICT:
        kcal = harris_benedict(subject.sex, subject.weight_kg, subject.height_cm, subject.age_years)
    else:
        kcal = mifflin_st_jeor(subject.sex, subject.weight_kg, subject.height_cm, subject.age_years)
    limitacoes = () if kcal is not None else _limitations_sex_based(subject)
    return BmrResult(kcal=kcal, formula=efetiva, requested_formula=formula, ffm_kg=mlg, limitations=limitacoes)


@dataclass(frozen=True)
class ScaleComparison:
    """TMB da balança × TMB da fórmula: diferença relativa e se vira aviso."""

    formula_kcal: float
    scale_kcal: float
    deviation_share: float
    warn: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "formula_kcal": self.formula_kcal,
            "scale_kcal": self.scale_kcal,
            "deviation_share": self.deviation_share,
            "warn": self.warn,
        }


def compare_with_scale(formula_kcal: Any, scale_kcal: Any) -> Optional[ScaleComparison]:
    """Compara a TMB informada pela balança com a da fórmula (só exibição)."""
    formula = positive_number(formula_kcal)
    balanca = positive_number(scale_kcal)
    if formula is None or balanca is None:
        return None
    desvio = (balanca - formula) / formula
    return ScaleComparison(
        formula_kcal=formula,
        scale_kcal=balanca,
        deviation_share=desvio,
        warn=abs(desvio) > SCALE_BMR_DEVIATION_WARN_SHARE,
    )
