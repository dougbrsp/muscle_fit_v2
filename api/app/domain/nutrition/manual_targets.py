"""Gramas manuais de macros validadas pelo motor (N1, decisão D6, 2026-11-02).

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7 e a seção do N1; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`). O usuário digita as gramas de
proteína, carboidrato e gordura; o motor devolve a kcal resultante (Atwater de
`macros.kcal_from_macros`) e o resultado de CADA campo:

- `WITHIN_RANGE`: dentro da faixa recomendada da estratégia (as de `strategy`);
- `WARNING`: fora da faixa recomendada, mas plausível: salva, com aviso;
- `BLOCKED`: valor absurdo ou perigoso: não salva.

A faixa de AVISO é a da fonte de cada estratégia (`strategy.protein_range_for`,
`strategy.fat_share_range_for`, piso de carboidrato do bulking). Os limites de
BLOQUEIO abaixo são deste módulo, cada um com a razão. Onde a literatura não fixa
um número, está dito "default de desenho" (o ajuste é decisão do dono do produto).

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Final, Optional

from .anthropometrics import non_negative_number, positive_number
from .macros import KCAL_PER_G_FAT, MIN_CARB_G_PER_KG, Macros, kcal_from_macros
from .strategy import (
    CUT_MIN_INTAKE_BMR_MULTIPLE,
    ProteinBasis,
    Strategy,
    StrategyParams,
    fat_share_range_for,
    protein_range_for,
)

# --- Limites de BLOQUEIO -------------------------------------------------------
# Valor digitado acima disto (em gramas) é erro de digitação: trata-se como inválido, sem kcal.
# Default de desenho (5 kg de qualquer macro por dia já passa de 20.000 kcal).
MANUAL_GRAMS_MAX: Final[float] = 5000.0

# Proteína (g/kg). BASES: o PISO é sempre sobre o PESO corporal (a RDA de 0,8 g/kg do IOM 2005 é
# por kg de peso), quando há peso. O TETO usa a MESMA base da faixa de aviso da estratégia (peso
# na manutenção e no bulking, MLG no cutting; no cutting sem MLG, o peso), para que nenhum valor
# dentro da faixa recomendada seja bloqueado: com a MLG como base única, 154 g de um aluno de 70 kg
# com 40% de gordura (2,2 g/kg de peso, 3,67 g/kg de MLG) bloquearia na manutenção.
# Piso 0,8: abaixo da RDA, com déficit ou treino, o plano trabalha contra o objetivo.
PROTEIN_BLOCK_MIN_G_PER_KG: Final[float] = 0.8
# Teto 3,5: Antonio 2014 e 2016 testaram 3,3-3,4 g/kg (8 semanas a 1 ano) em treinados sem dano
# observado; acima disso não há evidência de segurança nem de benefício (a faixa de aviso da
# fonte termina em 3,1 g/kg de MLG no cutting e 2,2 nos demais).
PROTEIN_BLOCK_MAX_G_PER_KG: Final[float] = 3.5

# Gordura (fração das kcal). DEFAULT DE DESENHO: FAO/WHO (2010, "Fats and fatty acids in human
# nutrition") indica mínimo de 15-20% da energia, então o piso de bloqueio de 10% fica ABAIXO da
# fonte de propósito: entre 10% e a faixa da estratégia o valor salva com aviso. Teto 60%: acima da
# faixa 20-35% (IOM 2005) e de qualquer faixa do motor; o MuscleFit não prescreve dieta cetogênica.
FAT_BLOCK_MIN_KCAL_SHARE: Final[float] = 0.10
FAT_BLOCK_MAX_KCAL_SHARE: Final[float] = 0.60

# Carboidrato (g/kg de peso). DEFAULT DE DESENHO. Burke 2011 recomenda 6-10 g/kg para 1 a 3 horas de
# esporte por dia e 8-12 g/kg para 4 a 5 horas ou mais; essas são faixas de ENDURANCE, não de
# musculação, e servem só de contexto. Aviso acima de 8 e bloqueio acima de 12: fora disso é
# quase sempre erro de digitação.
CARB_WARN_MAX_G_PER_KG: Final[float] = 8.0
CARB_BLOCK_MAX_G_PER_KG: Final[float] = 12.0

# Energia do dia. Piso 800 kcal: definição de dieta de muito baixa caloria (VLCD, NIH 1998;
# Tsai & Wadden 2006), que exige supervisão médica. Teto 8.000 kcal: DEFAULT DE DESENHO; o gasto
# medido no Tour de France (cerca de 6.500 kcal/dia, Westerterp 1986) é só contexto do extremo da
# literatura, e em treino de força passar disso é erro.
KCAL_BLOCK_MIN: Final[float] = 800.0
KCAL_BLOCK_MAX: Final[float] = 8000.0

# Aviso (default de desenho): kcal das gramas manuais mais de 10% distante da meta
# calórica da estratégia, o que muda o déficit ou o superávit que o plano promete.
KCAL_TARGET_DEVIATION_WARN_SHARE: Final[float] = 0.10

FIELD_PROTEIN = "protein_g"
FIELD_CARB = "carb_g"
FIELD_FAT = "fat_g"
FIELD_KCAL = "kcal"

MANUAL_INVALID_VALUE = "manual_invalid_value"
MANUAL_BELOW_RANGE = "manual_below_range"
MANUAL_ABOVE_RANGE = "manual_above_range"
MANUAL_BLOCKED_BELOW = "manual_blocked_below"
MANUAL_BLOCKED_ABOVE = "manual_blocked_above"
MANUAL_BELOW_BMR = "manual_below_bmr"
MANUAL_DIFFERS_FROM_TARGET = "manual_differs_from_target"
MISSING_BASE = "manual_missing_base_for_field"  # `<código>:<campo>`: sem base de g/kg, a faixa não foi conferida
LIMIT_MISSING_BASE = MISSING_BASE


class ManualTrainingMode(str, Enum):
    """Como as gramas manuais convivem com o treino do dia (escolha do usuário por plano, N2 rodada 4):
    `FIXED` = os números digitados valem todo dia (o padrão); `ADD_TRAINING` = valem para dia SEM treino e o
    gasto do treino do dia soma em carboidrato (`daily_target`), só no modo de gasto com o treino à parte."""

    FIXED = "fixed"
    ADD_TRAINING = "add_training"


DEFAULT_MANUAL_TRAINING_MODE: Final[ManualTrainingMode] = ManualTrainingMode.FIXED


class RangeStatus(str, Enum):
    WITHIN_RANGE = "within_range"
    WARNING = "warning"
    BLOCKED = "blocked"


_SEVERITY: Final[dict[RangeStatus, int]] = {
    RangeStatus.WITHIN_RANGE: 0,
    RangeStatus.WARNING: 1,
    RangeStatus.BLOCKED: 2,
}


@dataclass(frozen=True)
class FieldCheck:
    """Resultado de um campo. `metric` diz o que foi medido (`g_per_kg`,
    `kcal_share`, `kcal`); `recommended` é a faixa de aviso NA MESMA BASE de `metric_value`
    (None = sem faixa, ou a base do valor difere da base da faixa); `codes` são `<código>:<campo>` (rótulos em `labels.py`)."""

    field: str
    value: Optional[float]
    status: RangeStatus
    metric: Optional[str] = None
    metric_value: Optional[float] = None
    recommended: Optional[tuple[float, float]] = None
    codes: tuple[str, ...] = ()
    base: Optional[str] = None  # base do g/kg de `metric_value` (`body_weight` ou `fat_free_mass`)

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "value": self.value,
            "status": self.status.value,
            "metric": self.metric,
            "metric_value": self.metric_value,
            "recommended": list(self.recommended) if self.recommended else None,
            "codes": list(self.codes),
            "base": self.base,
        }


@dataclass(frozen=True)
class ManualTargetsCheck:
    """kcal resultante, resultado por campo e o pior status (`can_save` = não bloqueado)."""

    kcal: Optional[float]
    macros: Optional[Macros]
    fields: tuple[FieldCheck, ...]
    status: RangeStatus
    limitations: tuple[str, ...] = ()

    @property
    def can_save(self) -> bool:
        return self.status != RangeStatus.BLOCKED

    def to_dict(self) -> dict[str, Any]:
        return {
            "kcal": self.kcal,
            "fields": [f.to_dict() for f in self.fields],
            "status": self.status.value,
            "can_save": self.can_save,
            "limitations": list(self.limitations),
        }


def _grade(
    field: str,
    value: Optional[float],
    metric: str,
    metric_value: Optional[float],
    warn: Optional[tuple[float, float]],
    block: tuple[Optional[float], Optional[float]],
    extra_codes: tuple[str, ...] = (),
    base: Optional[str] = None,
) -> FieldCheck:
    """Classifica `metric_value` contra a faixa de aviso e os limites de bloqueio. Sem a
    métrica (falta a base do g/kg) o campo NUNCA fica dentro da faixa: vira aviso com
    `manual_missing_base_for_field:<campo>`."""
    if metric_value is None:
        return FieldCheck(
            field, value, RangeStatus.WARNING, metric, None, warn, (f"{MISSING_BASE}:{field}", *extra_codes), base
        )
    piso, teto = block
    if piso is not None and metric_value < piso:
        return FieldCheck(
            field, value, RangeStatus.BLOCKED, metric, metric_value, warn, (f"{MANUAL_BLOCKED_BELOW}:{field}",), base
        )
    if teto is not None and metric_value > teto:
        return FieldCheck(
            field, value, RangeStatus.BLOCKED, metric, metric_value, warn, (f"{MANUAL_BLOCKED_ABOVE}:{field}",), base
        )
    codes = list(extra_codes)
    if warn is not None and metric_value < warn[0]:
        codes.append(f"{MANUAL_BELOW_RANGE}:{field}")
    elif warn is not None and metric_value > warn[1]:
        codes.append(f"{MANUAL_ABOVE_RANGE}:{field}")
    status = RangeStatus.WARNING if codes else RangeStatus.WITHIN_RANGE
    return FieldCheck(field, value, status, metric, metric_value, warn, tuple(codes), base)


def _valid_grams(value: Any) -> Optional[float]:
    """Gramas finitas, >= 0 e até `MANUAL_GRAMS_MAX`; o resto é inválido."""
    gramas = non_negative_number(value)
    return gramas if gramas is not None and gramas <= MANUAL_GRAMS_MAX else None


_BASE_WEIGHT: Final[str] = "body_weight"
_BASE_FFM: Final[str] = "fat_free_mass"


def _protein_check(proteina: float, params: StrategyParams, peso: Optional[float], mlg: Optional[float]) -> FieldCheck:
    """Proteína. Aviso pela faixa da estratégia, na base dela (peso em todas as estratégias desde nutrition-v4; MLG só se um plano legado trouxer `FAT_FREE_MASS`).
    Bloqueio: piso sobre o PESO (se há; senão a MLG) e teto na base da estratégia, que desde nutrition-v4 é sempre o
    peso. O ramo de MLG (e o "sem MLG, o peso") só existe para parâmetros legados com `FAT_FREE_MASS`. Cada FieldCheck só leva a faixa quando o valor e a faixa estão na mesma base."""
    usa_mlg = params.protein_basis == ProteinBasis.FAT_FREE_MASS
    base_estrategia, nome_estrategia = (mlg, _BASE_FFM) if usa_mlg else (peso, _BASE_WEIGHT)
    faixa = protein_range_for(params.strategy)

    def _resultado(base_kg: float, nome: str, bloqueio: tuple[Optional[float], Optional[float]]) -> FieldCheck:
        return _grade(
            FIELD_PROTEIN,
            proteina,
            "g_per_kg",
            proteina / base_kg,
            faixa if nome == nome_estrategia else None,
            bloqueio,
            base=nome,
        )

    base_piso, nome_piso = (peso, _BASE_WEIGHT) if peso is not None else (mlg, _BASE_FFM)
    if base_piso is not None and proteina / base_piso < PROTEIN_BLOCK_MIN_G_PER_KG:
        return _resultado(base_piso, nome_piso, (PROTEIN_BLOCK_MIN_G_PER_KG, None))
    base_teto, nome_teto = (base_estrategia, nome_estrategia)
    if base_teto is None and usa_mlg and peso is not None:
        base_teto, nome_teto = peso, _BASE_WEIGHT  # cutting sem MLG: o teto usa o peso
    if base_teto is not None and proteina / base_teto > PROTEIN_BLOCK_MAX_G_PER_KG:
        return _resultado(base_teto, nome_teto, (None, PROTEIN_BLOCK_MAX_G_PER_KG))
    if base_estrategia is None:
        return _grade(FIELD_PROTEIN, proteina, "g_per_kg", None, faixa, (None, None), base=nome_estrategia)
    return _resultado(base_estrategia, nome_estrategia, (None, None))


def check_manual_targets(
    protein_g: Any,
    carb_g: Any,
    fat_g: Any,
    params: StrategyParams,
    weight_kg: Any = None,
    ffm_kg: Any = None,
    target_kcal: Any = None,
    bmr_kcal: Any = None,
) -> ManualTargetsCheck:
    """Valida as gramas manuais do dia contra a estratégia do plano.

    `weight_kg`/`ffm_kg` dão a base dos g/kg (proteína: ver `_protein_check`; carboidrato: o
    peso). Sem peso e sem MLG, 0 g de proteína só gera aviso: quem salva (N2) deve exigir o peso. Sem a base, o campo sai em AVISO `manual_missing_base_for_field` (nunca "dentro da
    faixa") e a limitação é devolvida; o peso nunca é inventado. `target_kcal` (meta da estratégia)
    e `bmr_kcal` só geram AVISOS. Texto numérico ("160") é aceito como número, como em
    `anthropometrics.non_negative_number`; valor negativo, não numérico, infinito ou acima de
    `MANUAL_GRAMS_MAX` é inválido."""
    valores = {
        FIELD_PROTEIN: _valid_grams(protein_g),
        FIELD_CARB: _valid_grams(carb_g),
        FIELD_FAT: _valid_grams(fat_g),
    }
    invalidos = [campo for campo, v in valores.items() if v is None]
    if invalidos:
        campos = tuple(
            FieldCheck(
                campo,
                valores[campo],
                RangeStatus.BLOCKED if valores[campo] is None else RangeStatus.WITHIN_RANGE,
                codes=(f"{MANUAL_INVALID_VALUE}:{campo}",) if valores[campo] is None else (),
            )
            for campo in valores
        )
        return ManualTargetsCheck(kcal=None, macros=None, fields=campos, status=RangeStatus.BLOCKED)

    proteina, carbo, gordura = valores[FIELD_PROTEIN], valores[FIELD_CARB], valores[FIELD_FAT]
    kcal = kcal_from_macros(proteina, carbo, gordura)
    assert kcal is not None and proteina is not None and carbo is not None and gordura is not None
    limitacoes: list[str] = []
    peso = positive_number(weight_kg)
    mlg = positive_number(ffm_kg)

    checagem_proteina = _protein_check(proteina, params, peso, mlg)
    if checagem_proteina.metric_value is None:
        limitacoes.append(f"{MISSING_BASE}:{FIELD_PROTEIN}")

    # Carboidrato: piso do bulking (o do plano) ou o piso geral; teto de aviso e de bloqueio.
    if peso is None:
        limitacoes.append(f"{MISSING_BASE}:{FIELD_CARB}")
    carbo_min = MIN_CARB_G_PER_KG
    if params.strategy == Strategy.BULKING and params.carb_min_g_per_kg is not None:
        carbo_min = params.carb_min_g_per_kg
    checagem_carbo = _grade(
        FIELD_CARB,
        carbo,
        "g_per_kg",
        None if peso is None else carbo / peso,
        (carbo_min, CARB_WARN_MAX_G_PER_KG),
        (None, CARB_BLOCK_MAX_G_PER_KG),
        base=_BASE_WEIGHT,
    )

    # Gordura: fração das kcal resultantes. Sem energia nenhuma não há fração (e a kcal bloqueia).
    fracao_gordura = gordura * KCAL_PER_G_FAT / kcal if kcal > 0 else None
    checagem_gordura = _grade(
        FIELD_FAT,
        gordura,
        "kcal_share",
        fracao_gordura,
        fat_share_range_for(params.strategy),
        (FAT_BLOCK_MIN_KCAL_SHARE, FAT_BLOCK_MAX_KCAL_SHARE),
    )

    # Energia resultante.
    avisos_kcal: list[str] = []
    tmb = positive_number(bmr_kcal)
    if tmb is not None and kcal < CUT_MIN_INTAKE_BMR_MULTIPLE * tmb:
        avisos_kcal.append(f"{MANUAL_BELOW_BMR}:{FIELD_KCAL}")
    meta = positive_number(target_kcal)
    if meta is not None and abs(kcal - meta) > KCAL_TARGET_DEVIATION_WARN_SHARE * meta:
        avisos_kcal.append(f"{MANUAL_DIFFERS_FROM_TARGET}:{FIELD_KCAL}")
    checagem_kcal = _grade(
        FIELD_KCAL, kcal, "kcal", kcal, None, (KCAL_BLOCK_MIN, KCAL_BLOCK_MAX), tuple(avisos_kcal)
    )

    campos = (checagem_proteina, checagem_carbo, checagem_gordura, checagem_kcal)
    pior = max((c.status for c in campos), key=lambda s: _SEVERITY[s])
    return ManualTargetsCheck(
        kcal=kcal,
        macros=Macros(kcal=kcal, protein_g=proteina, carb_g=carbo, fat_g=gordura),
        fields=campos,
        status=pior,
        limitations=tuple(limitacoes),
    )
