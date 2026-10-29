"""TDEE observado (adaptativo): ingestão média + variação de peso × densidade.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`). Plano:
`docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §3.2 e §7 (decisão D-J).

TDEE_obs = média da ingestão registrada − inclinação do peso (kg/dia) ×
densidade energética de Hall/Forbes (`energy_density`, exige massa gorda).

Tendência do peso = inclinação por mínimos quadrados das pesagens válidas contra
o dia da janela (`weight_trend`). Até 2026-10-16 o rascunho do L1 usava a
diferença entre o primeiro e o último ponto de uma média móvel exponencial
(α 0,3) dividida pela janela inteira: com pesagem esparsa (dias 7 e 13 de 14)
via −0,3 kg no lugar de −1 kg e ainda dividia por 14 dias em vez de 6, e com
pesagem diária o atraso da média encolhia a variação em ~2,3 dias. A
inclinação não tem atraso, usa o tempo real entre as pesagens e é a mesma com
pesagem diária ou semanal.

Calculável só com: janela ≥ `MIN_WINDOW_DAYS`; ≥ `MIN_LOGGED_DAYS_SHARE` dos
dias com ingestão registrada; ≥ `MIN_WEIGHT_READINGS` pesagens cobrindo ≥
`MIN_WEIGHT_SPAN_DAYS` dias; massa gorda conhecida. Sem isso: `kcal` None e
qualidade `INSUFFICIENT` (vocabulário comum de `domain/data_nature.py`).
Qualidade alta a partir de `PREFERRED_WINDOW_DAYS`.

`reconcile` NÃO recalcula o plano (D-J): devolve a SUGESTÃO de TDEE — a fórmula
deslocada em direção ao observado pelo peso da qualidade
(`RECONCILE_WEIGHT_BY_QUALITY`: alta 1,0 = adota o observado; média 0,5 = meio
caminho; baixa/insuficiente 0 = não sugere) — e o admin/PT aciona o recálculo.

Defaults de desenho, SEM fonte bibliográfica (a confirmar pelo usuário):
`MIN_WEIGHT_SPAN_DAYS` e `RECONCILE_WEIGHT_BY_QUALITY`.

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Mapping, Optional, Sequence

from ..data_nature import DataNature, DataQuality
from .anthropometrics import non_negative_number, positive_number
from .energy_density import tissue_energy_density

MIN_WINDOW_DAYS: Final[int] = 14
PREFERRED_WINDOW_DAYS: Final[int] = 28
MIN_LOGGED_DAYS_SHARE: Final[float] = 0.8
MIN_WEIGHT_READINGS: Final[int] = 2
# Default de desenho, a confirmar pelo usuário: as pesagens precisam cobrir 7
# dias; duas pesagens no mesmo par de dias dão uma inclinação dominada pela água.
MIN_WEIGHT_SPAN_DAYS: Final[int] = 7

# Default de desenho, sem fonte bibliográfica: quanto a sugestão anda da
# fórmula rumo ao observado, por qualidade do observado.
RECONCILE_WEIGHT_BY_QUALITY: Final[Mapping[DataQuality, float]] = {
    DataQuality.HIGH: 1.0,
    DataQuality.MEDIUM: 0.5,
    DataQuality.LOW: 0.0,
    DataQuality.INSUFFICIENT: 0.0,
}

LIMIT_WINDOW_TOO_SHORT = "window_too_short"
LIMIT_TOO_FEW_LOGGED_DAYS = "too_few_logged_days"
LIMIT_TOO_FEW_WEIGHTS = "too_few_weight_readings"
LIMIT_WEIGHT_SPAN_TOO_SHORT = "weight_span_too_short"
LIMIT_MISSING_FAT_MASS = "missing_fat_mass"
LIMIT_FORMULA_TDEE_MISSING = "formula_tdee_missing"
LIMIT_SERIES_LENGTH_MISMATCH = "intake_and_weight_series_length_mismatch"


@dataclass(frozen=True)
class WeightTrend:
    """Inclinação do peso (kg/dia; None = não calculável), nº de pesagens e
    dias entre a primeira e a última."""

    slope_kg_per_day: Optional[float]
    readings: int
    span_days: Optional[int]
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "slope_kg_per_day": self.slope_kg_per_day,
            "readings": self.readings,
            "span_days": self.span_days,
            "limitations": list(self.limitations),
        }


def weight_trend(weight_kg: Sequence[Optional[float]]) -> WeightTrend:
    """Mínimos quadrados de (dia da janela, peso) sobre as pesagens válidas
    (um elemento por dia; None/0/inválido = sem pesagem)."""
    pontos = [(float(dia), peso) for dia, valor in enumerate(weight_kg) for peso in (positive_number(valor),) if peso is not None]
    if len(pontos) < MIN_WEIGHT_READINGS:
        return WeightTrend(slope_kg_per_day=None, readings=len(pontos), span_days=None, limitations=(LIMIT_TOO_FEW_WEIGHTS,))
    vao = int(pontos[-1][0] - pontos[0][0])
    if vao < MIN_WEIGHT_SPAN_DAYS:
        return WeightTrend(slope_kg_per_day=None, readings=len(pontos), span_days=vao, limitations=(LIMIT_WEIGHT_SPAN_TOO_SHORT,))
    media_dia = sum(dia for dia, _ in pontos) / len(pontos)
    media_peso = sum(peso for _, peso in pontos) / len(pontos)
    sxx = sum((dia - media_dia) ** 2 for dia, _ in pontos)
    sxy = sum((dia - media_dia) * (peso - media_peso) for dia, peso in pontos)
    return WeightTrend(slope_kg_per_day=sxy / sxx, readings=len(pontos), span_days=vao)


@dataclass(frozen=True)
class AdaptiveTdee:
    """Gasto observado (None = janela insuficiente) com qualidade e insumos."""

    kcal: Optional[float]
    nature: DataNature
    quality: DataQuality
    window_days: int
    logged_days: int
    mean_intake_kcal: Optional[float]
    weight_trend: WeightTrend
    limitations: tuple[str, ...] = ()

    @property
    def computable(self) -> bool:
        return self.kcal is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kcal": self.kcal,
            "nature": self.nature.value,
            "quality": self.quality.value,
            "window_days": self.window_days,
            "logged_days": self.logged_days,
            "mean_intake_kcal": self.mean_intake_kcal,
            "weight_trend": self.weight_trend.to_dict(),
            "limitations": list(self.limitations),
        }


def observed_tdee(
    intake_kcal: Sequence[Optional[float]],
    weight_kg: Sequence[Optional[float]],
    fat_mass_kg: Any,
) -> AdaptiveTdee:
    """Um elemento por dia da janela nas DUAS séries (ingestão e peso; None =
    sem registro). Séries de tamanhos diferentes são erro do chamador."""
    if len(weight_kg) != len(intake_kcal):
        raise ValueError(LIMIT_SERIES_LENGTH_MISMATCH)
    janela = len(intake_kcal)
    ingestoes = [v for v in (non_negative_number(x) for x in intake_kcal) if v is not None]
    registrados = len(ingestoes)
    limitacoes: list[str] = []
    if janela < MIN_WINDOW_DAYS:
        limitacoes.append(LIMIT_WINDOW_TOO_SHORT)
    if janela == 0 or registrados / janela < MIN_LOGGED_DAYS_SHARE:
        limitacoes.append(LIMIT_TOO_FEW_LOGGED_DAYS)
    tendencia = weight_trend(weight_kg)
    limitacoes.extend(tendencia.limitations)
    densidade = tissue_energy_density(fat_mass_kg)
    if densidade is None:
        limitacoes.append(LIMIT_MISSING_FAT_MASS)
    media = sum(ingestoes) / registrados if registrados else None
    if limitacoes or media is None or tendencia.slope_kg_per_day is None or densidade is None:
        return AdaptiveTdee(
            kcal=None,
            nature=DataNature.ESTIMATED,
            quality=DataQuality.INSUFFICIENT,
            window_days=janela,
            logged_days=registrados,
            mean_intake_kcal=media,
            weight_trend=tendencia,
            limitations=tuple(limitacoes),
        )
    armazenado_por_dia = tendencia.slope_kg_per_day * densidade
    qualidade = DataQuality.HIGH if janela >= PREFERRED_WINDOW_DAYS else DataQuality.MEDIUM
    return AdaptiveTdee(
        kcal=media - armazenado_por_dia,
        nature=DataNature.ESTIMATED,
        quality=qualidade,
        window_days=janela,
        logged_days=registrados,
        mean_intake_kcal=media,
        weight_trend=tendencia,
    )


@dataclass(frozen=True)
class Reconciliation:
    """Sugestão de TDEE (não aplicada): fórmula deslocada rumo ao observado."""

    formula_kcal: Optional[float]
    observed_kcal: Optional[float]
    suggested_kcal: Optional[float]
    delta_kcal: Optional[float]
    weight: float
    should_suggest: bool
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "formula_kcal": self.formula_kcal,
            "observed_kcal": self.observed_kcal,
            "suggested_kcal": self.suggested_kcal,
            "delta_kcal": self.delta_kcal,
            "weight": self.weight,
            "should_suggest": self.should_suggest,
            "limitations": list(self.limitations),
        }


def reconcile(formula_tdee_kcal: Any, observed: AdaptiveTdee) -> Reconciliation:
    """Só SUGERE (D-J). Sem fórmula ou sem observado calculável -> sem sugestão."""
    formula = positive_number(formula_tdee_kcal)
    peso = RECONCILE_WEIGHT_BY_QUALITY[observed.quality] if observed.computable else 0.0
    limitacoes = list(observed.limitations)
    if formula is None:
        limitacoes.append(LIMIT_FORMULA_TDEE_MISSING)
    if formula is None or not observed.computable or peso <= 0:
        return Reconciliation(
            formula_kcal=formula,
            observed_kcal=observed.kcal,
            suggested_kcal=None,
            delta_kcal=None,
            weight=peso,
            should_suggest=False,
            limitations=tuple(limitacoes),
        )
    observado = float(observed.kcal)  # type: ignore[arg-type]
    sugerido = formula + peso * (observado - formula)
    return Reconciliation(
        formula_kcal=formula,
        observed_kcal=observado,
        suggested_kcal=sugerido,
        delta_kcal=sugerido - formula,
        weight=peso,
        should_suggest=sugerido != formula,
        limitations=tuple(limitacoes),
    )
