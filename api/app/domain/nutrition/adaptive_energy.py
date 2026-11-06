"""Gasto energético adaptativo com estado (modo "sombra" no Lote A).

Dono único do estimador adaptativo (mapa `docs/motores/FONTES_DA_VERDADE.md`
§3.7; guarda `api/tests/unit/test_guarda_fontes_da_verdade.py`). Spec §7 e
§9–§13 e §18 em `.omc/research/NUTRICAO-spec-2026-11-05.md`; plano em
`.omc/plans/nutricao-adaptativo-2026-11-05.md`.

Forma de "fold": `step(estado_anterior, entrada_do_dia) -> estado` e
`replay(entradas) -> lista de estados`. A sombra (recalculada em memória) e o
estado persistido (lote futuro) usam a MESMA função, logo dão o mesmo
resultado. O estado carrega a janela de `WINDOW_DAYS` dias e a EMA do peso.

Regras (todas as constantes abaixo são nomeadas; nada é escrito na conta):

- Status do dia: COMPLETE / PARTIAL / MISSING (`classify_day`). Só `LOGGED`
  vira COMPLETE, e apenas se a ingestão for >= `COMPLETE_MIN_INTAKE_BMR_SHARE`
  da TMB (decisão D3 do plano). Dia PARCIAL nunca entra na média de ingestão.
  `OUT_OF_PLAN` e `OPEN_TODAY` contam como MISSING (a sombra não classifica o
  que o dashboard não classifica).
- Janela válida (spec §10): >= `MIN_COMPLETE_DAYS_IN_WINDOW` COMPLETE em `WINDOW_DAYS`,
  peso suficiente (`weight_trend.assess_window`), inclinação plausível
  (`MAX_PLAUSIBLE_WEEKLY_CHANGE_KG`), densidade calculável (massa gorda
  conhecida) e gasto observado dentro de `OBSERVED_BMR_SHARE_RANGE` da TMB.
  Janela inválida: mantém o adaptativo anterior, limita a confiança a
  `SUSPENDED_CONFIDENCE_CAP` (e nunca acima da anterior) e lista o que falta
  (`suspension_reasons`, com o que se tem e o que se precisa).
- Gasto observado = média de ingestão dos dias COMPLETE da janela − inclinação
  (kg/dia) × densidade do dono `energy_density.tissue_energy_density`.
- Suavização (spec §11): `EXPENDITURE_ALPHA` e passo máximo
  `EXPENDITURE_MAX_STEP_KCAL` em relação ao valor anterior. SEMENTE: a primeira
  atualização válida adota o observado bruto (sem suavizar), porque a
  transição bootstrap -> adaptativo é feita pela confiança (spec §12) e semear
  com o bootstrap faria a confiança misturar dois valores quase iguais.
- Confiança (spec §12): clamp((dias_válidos − `CONFIDENCE_FLOOR_DAYS`) /
  `CONFIDENCE_RAMP_DAYS`, 0, 1), com dias_válidos = dias COMPLETE na janela.
  Sem adaptativo ainda, a confiança é 0 e a base é o bootstrap médio.
  base = lerp(bootstrap médio da janela, adaptativo, confiança).
- Bootstrap do dia (spec §7): TMB × fator sedentário (dono:
  `anthropometrics.NON_EXERCISE_ACTIVITY_FACTORS`) + kcal ativas do dia. Dia sem
  atividade confirmada usa a média das atividades confirmadas recentes da
  janela (nunca "zero por ausência"); sem nenhuma, só o fator e a flag.
- Ajuste diário (spec §13): ajuste = clamp((hoje − média da janela) ×
  `ACTIVITY_ADJUSTMENT_COEFFICIENT`, `MIN`, `MAX`). Descanso CONFIRMADO entra
  como zero; dia não confirmado fica fora da média e, se for hoje, o ajuste
  é 0 com a flag `activity_pending`.
- Déficit e meta de consumo NÃO são calculados aqui (strategy.py/daily_target.py).

Defaults de desenho, SEM fonte bibliográfica (a confirmar pelo usuário):
`COMPLETE_MIN_INTAKE_BMR_SHARE`, `MAX_PLAUSIBLE_WEEKLY_CHANGE_KG`,
`OBSERVED_BMR_SHARE_RANGE`, `SUSPENDED_CONFIDENCE_CAP`. Da spec:
`WINDOW_DAYS`, `MIN_COMPLETE_DAYS_IN_WINDOW`, `EXPENDITURE_*`, `CONFIDENCE_*`,
`ACTIVITY_ADJUSTMENT_*`.

Módulo PURO: sem banco, sem FastAPI, sem I/O. Importa `DayState` de
`dashboard.py`: `dashboard.py` NÃO pode importar este módulo (ciclo).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Any, Final, Iterable, Mapping, Optional, Sequence

from .activity_energy import ROUNDING_DIGITS_PUBLIC
from .anthropometrics import NON_EXERCISE_ACTIVITY_FACTORS, ActivityLevel, non_negative_number, positive_number
from .dashboard import DayState
from .energy_density import DAYS_PER_WEEK, tissue_energy_density
from .weight_trend import (
    MIN_HISTORY_DAYS,
    MIN_WEIGHINS_PER_WEEK,
    TREND_WINDOW_DAYS,
    TrendPoint,
    TrendState,
    WeightWindow,
    assess_window,
    history_days_of,
    trend_step,
)

ALGORITHM_VERSION: Final[str] = "adaptive-v1"

# Spec §10–§13.
WINDOW_DAYS: Final[int] = TREND_WINDOW_DAYS
MIN_COMPLETE_DAYS_IN_WINDOW: Final[int] = 14
EXPENDITURE_ALPHA: Final[float] = 0.20
EXPENDITURE_MAX_STEP_KCAL: Final[float] = 75.0
CONFIDENCE_FLOOR_DAYS: Final[int] = 7
CONFIDENCE_RAMP_DAYS: Final[int] = 14
ACTIVITY_ADJUSTMENT_COEFFICIENT: Final[float] = 0.60
ACTIVITY_ADJUSTMENT_MIN_KCAL: Final[float] = -250.0
ACTIVITY_ADJUSTMENT_MAX_KCAL: Final[float] = 300.0
BOOTSTRAP_ACTIVITY_FACTOR: Final[float] = NON_EXERCISE_ACTIVITY_FACTORS[ActivityLevel.SEDENTARY]

# Defaults de desenho (a confirmar). Dia fechado abaixo de 70% da TMB é quase
# sempre refeição faltando; o piso do plano é 1,0 x TMB, então quem segue o plano
# fica acima. Variação plausível: 1,5 kg/semana é o teto de qualquer protocolo
# razoável. Gasto observado: Katch tem erro de ~10%, daí o piso de 90% da TMB.
COMPLETE_MIN_INTAKE_BMR_SHARE: Final[float] = 0.70
MAX_PLAUSIBLE_WEEKLY_CHANGE_KG: Final[float] = 1.5
OBSERVED_BMR_SHARE_RANGE: Final[tuple[float, float]] = (0.9, 3.0)
SUSPENDED_CONFIDENCE_CAP: Final[float] = 0.5

# Forma pública (API/tela, que não arredondam nem classificam). kcal seguem o padrão do pacote
# (`activity_energy.ROUNDING_DIGITS_PUBLIC`). Limiares do nível de confiança: default de desenho a confirmar
# (14 dias completos dão 0,50 = médio; a partir de 19 dias completos, alto).
CONFIDENCE_PUBLIC_DECIMALS: Final[int] = 2
WEIGHT_KG_PUBLIC_DECIMALS: Final[int] = 2
SLOPE_KG_PER_DAY_PUBLIC_DECIMALS: Final[int] = 4
CONFIDENCE_MEDIUM_FROM: Final[float] = 0.40
CONFIDENCE_HIGH_FROM: Final[float] = 0.80
# Horizonte de histórico que a sombra reprocessa a cada leitura (default de desenho a confirmar): ~4 meias-vidas
# da EMA do peso (28 d) + a janela de 21 d já passam de 49 d; 90 d deixam o erro de aquecimento abaixo de 1% e dão
# ~40 atualizações suavizadas para o adaptativo convergir sem o limite de passo.
REPLAY_HISTORY_DAYS: Final[int] = 90

FLAG_ACTIVITY_PENDING = "activity_pending"
FLAG_BOOTSTRAP_ACTIVITY_UNKNOWN = "bootstrap_activity_unknown"
FLAG_BMR_MISSING = "bmr_missing"
FLAG_PARTIAL_DAYS_EXCLUDED = "partial_days_excluded"
FLAG_UPDATE_SUSPENDED = "update_suspended"
FLAG_ADAPTIVE_UNAVAILABLE = "adaptive_unavailable"
FLAG_ADJUSTMENT_CLAMPED = "activity_adjustment_clamped"
FLAG_UPDATE_CLAMPED = "adaptive_update_clamped"

REASON_COMPLETE_DAYS_BELOW_MIN = "complete_days_below_min"
REASON_WEIGHT_HISTORY_SHORT = "weight_history_short"
REASON_WEIGHINS_BELOW_MIN = "weighins_below_min"
REASON_SLOPE_UNAVAILABLE = "weight_slope_unavailable"
REASON_SLOPE_IMPLAUSIBLE = "weight_slope_implausible"
REASON_FAT_MASS_MISSING = "fat_mass_missing"
REASON_OBSERVED_IMPLAUSIBLE = "observed_tdee_implausible"

# Motivos que são CONTAGEM (a tela mostra "have de need unit"); os demais não têm número para exibir.
REASON_COUNT_UNITS: Final[Mapping[str, str]] = {
    REASON_COMPLETE_DAYS_BELOW_MIN: "days",
    REASON_WEIGHT_HISTORY_SHORT: "days",
    REASON_WEIGHINS_BELOW_MIN: "per_week",
}


class ConfidenceLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def confidence_level(confidence: float) -> ConfidenceLevel:
    if confidence >= CONFIDENCE_HIGH_FROM:
        return ConfidenceLevel.HIGH
    if confidence >= CONFIDENCE_MEDIUM_FROM:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


def _pub(value: Optional[float], digits: int) -> Optional[float]:
    return None if value is None else round(float(value), digits)


class DayStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    MISSING = "missing"


@dataclass(frozen=True)
class DayInput:
    """Entrada de um dia local.

    `activity_confirmed` True = a sincronização cobre o dia (kcal ativas valem;
    0/None = descanso confirmado); False/None = não confirmado (dia fora da
    média, ajuste pendente). kcal inválida com dia confirmado -> não confirmado.
    """

    day: date
    day_state: DayState
    intake_kcal: Optional[float] = None
    bmr_kcal: Optional[float] = None
    weights_kg: tuple[float, ...] = ()
    fat_mass_kg: Optional[float] = None
    active_kcal: Optional[float] = None
    activity_confirmed: Optional[bool] = None


@dataclass(frozen=True)
class WindowDay:
    day: date
    status: DayStatus
    intake_kcal: Optional[float]
    active_kcal: Optional[float]
    bootstrap_kcal: Optional[float]
    weight: TrendPoint


@dataclass(frozen=True)
class Shortfall:
    """O que falta para a janela atualizar: `have` do que existe, `need` do que se exige."""

    code: str
    have: float
    need: float

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "have": self.have, "need": self.need}

    def to_public_dict(self) -> dict[str, Any]:
        """Forma para a tela: `have`/`need`/`unit` só nos motivos de CONTAGEM (`REASON_COUNT_UNITS`); nos demais
        (sim/não e faixa plausível) saem `null`, porque "0 de 1" ou "4.900 de 4.800" enganam."""
        unidade = REASON_COUNT_UNITS.get(self.code)
        if unidade is None:
            return {"code": self.code, "have": None, "need": None, "unit": None}
        return {
            "code": self.code,
            "have": round(self.have, WEIGHT_KG_PUBLIC_DECIMALS),
            "need": round(self.need, WEIGHT_KG_PUBLIC_DECIMALS),
            "unit": unidade,
        }


@dataclass(frozen=True)
class AdaptiveEnergyState:
    """Estado do dia (retrato no espírito do `DailyEnergySnapshot`, spec §18)."""

    day: date
    window: tuple[WindowDay, ...]
    trend: TrendState
    bmr_kcal: Optional[float]
    fat_mass_kg: Optional[float]
    bootstrap_today_kcal: Optional[float]
    bootstrap_mean_kcal: Optional[float]
    observed_kcal: Optional[float]
    adaptive_kcal: Optional[float]
    last_update_day: Optional[date]
    base_kcal: Optional[float]
    confidence: float
    valid_days: int
    window_days: int
    activity_today_kcal: Optional[float]
    activity_average_kcal: Optional[float]
    activity_adjustment_kcal: float
    estimated_today_kcal: Optional[float]
    weight: WeightWindow
    flags: tuple[str, ...] = ()
    suspension_reasons: tuple[Shortfall, ...] = ()
    algorithm_version: str = ALGORITHM_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.day.isoformat(),
            "bmr": self.bmr_kcal,
            "bootstrap_tdee": self.bootstrap_today_kcal,
            "bootstrap_mean_tdee": self.bootstrap_mean_kcal,
            "observed_tdee": self.observed_kcal,
            "adaptive_tdee": self.adaptive_kcal,
            "base_tdee": self.base_kcal,
            "activity_calories_today": self.activity_today_kcal,
            "average_activity_calories": self.activity_average_kcal,
            "activity_adjustment": self.activity_adjustment_kcal,
            "estimated_tdee_today": self.estimated_today_kcal,
            "confidence": self.confidence,
            "valid_days": self.valid_days,
            "window_days": self.window_days,
            "last_update_date": self.last_update_day.isoformat() if self.last_update_day else None,
            "trend_weight_kg": self.trend.trend_kg,
            "weight_slope_kg_per_day": self.weight.slope_kg_per_day,
            "suspension_reasons": [r.to_dict() for r in self.suspension_reasons],
            "algorithm_version": self.algorithm_version,
            "data_quality_flags": list(self.flags),
        }

    def to_public_dict(self) -> dict[str, Any]:
        """Forma para API/tela: kcal em `ROUNDING_DIGITS_PUBLIC`, confiança com 2 casas + `confidence_level`,
        inclinação por dia e por semana. Mesmas chaves de `to_dict` mais `confidence_level` e
        `weight_slope_kg_per_week`."""
        d = self.to_dict()
        for chave in (
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
        ):
            d[chave] = _pub(d[chave], ROUNDING_DIGITS_PUBLIC)
        inclinacao = self.weight.slope_kg_per_day
        d["confidence"] = round(self.confidence, CONFIDENCE_PUBLIC_DECIMALS)
        d["confidence_level"] = confidence_level(self.confidence).value
        d["trend_weight_kg"] = _pub(d["trend_weight_kg"], WEIGHT_KG_PUBLIC_DECIMALS)
        d["weight_slope_kg_per_day"] = _pub(inclinacao, SLOPE_KG_PER_DAY_PUBLIC_DECIMALS)
        d["weight_slope_kg_per_week"] = _pub(None if inclinacao is None else inclinacao * DAYS_PER_WEEK, WEIGHT_KG_PUBLIC_DECIMALS)
        d["suspension_reasons"] = [r.to_public_dict() for r in self.suspension_reasons]
        return d


def resolve_day_bmr(recorded_bmr_kcal: Any, fallback_bmr_kcal: Any) -> Optional[float]:
    """TMB do dia para a sombra (decisão de desenho): a gravada na meta do dia, se válida (> 0); senão a
    calculada (`fallback`), se válida; senão `None`. Dono da precedência: o serviço só entrega os dois valores."""
    gravada = positive_number(recorded_bmr_kcal)
    return gravada if gravada is not None else positive_number(fallback_bmr_kcal)


def latest_bmr(bmrs: Iterable[Any]) -> Optional[float]:
    """TMB mais recente (decisão de desenho): a ÚLTIMA válida (> 0) na ordem dada (cronológica); `None` sem nenhuma.
    É a TMB usada no desconto do repouso das atividades brutas."""
    ultima: Optional[float] = None
    for valor in bmrs:
        valida = positive_number(valor)
        if valida is not None:
            ultima = valida
    return ultima


def shadow_day_state(day: date, today: date, has_record: bool, is_complete: bool) -> DayState:
    """Estado do dia para a sombra, INDEPENDENTE de haver plano: hoje/futuro = `OPEN_TODAY` (MISSING);
    registro completo (energia + 3 macros, `weekly_budget.intake_macros`) = `LOGGED`; registro sem
    completude = `INCOMPLETE`; sem registro = `NO_RECORD`."""
    if day >= today:
        return DayState.OPEN_TODAY
    if not has_record:
        return DayState.NO_RECORD
    return DayState.LOGGED if is_complete else DayState.INCOMPLETE


def classify_day(day_state: DayState, intake_kcal: Any, bmr_kcal: Any) -> DayStatus:
    """COMPLETE só para dia LOGGED com ingestão >= fração da TMB (decisão D3)."""
    if day_state is DayState.INCOMPLETE:
        return DayStatus.PARTIAL
    if day_state is not DayState.LOGGED:
        return DayStatus.MISSING
    ingestao = non_negative_number(intake_kcal)
    tmb = positive_number(bmr_kcal)
    if ingestao is None or tmb is None or ingestao < COMPLETE_MIN_INTAKE_BMR_SHARE * tmb:
        return DayStatus.PARTIAL
    return DayStatus.COMPLETE


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _confirmed_activity(entry: DayInput) -> Optional[float]:
    """kcal ativas do dia se confirmado e válido; None se não confirmado."""
    if entry.activity_confirmed is not True:
        return None
    if entry.active_kcal is None:
        return 0.0
    return non_negative_number(entry.active_kcal)


def _empty_input(day: date) -> DayInput:
    return DayInput(day=day, day_state=DayState.NO_RECORD)


def step(prev: Optional[AdaptiveEnergyState], entry: DayInput) -> AdaptiveEnergyState:
    """Avança o estado um dia. Dias pulados entre o anterior e `entry` viram MISSING."""
    if prev is not None:
        if entry.day <= prev.day:
            raise ValueError("step exige dia posterior ao do estado anterior")
        dia = prev.day + timedelta(days=1)
        while dia < entry.day:
            prev = _advance(prev, _empty_input(dia))
            dia += timedelta(days=1)
    return _advance(prev, entry)


def replay(entries: Iterable[DayInput], initial: Optional[AdaptiveEnergyState] = None) -> list[AdaptiveEnergyState]:
    """Dobra as entradas (ordenadas por dia) a partir de `initial`; um estado por entrada."""
    estados: list[AdaptiveEnergyState] = []
    atual = initial
    for entrada in sorted(entries, key=lambda e: e.day):
        atual = step(atual, entrada)
        estados.append(atual)
    return estados


def _advance(prev: Optional[AdaptiveEnergyState], entry: DayInput) -> AdaptiveEnergyState:
    janela_anterior = prev.window if prev else ()
    tmb = positive_number(entry.bmr_kcal) or (prev.bmr_kcal if prev else None)
    massa_gorda = non_negative_number(entry.fat_mass_kg)
    if massa_gorda is None and prev is not None:
        massa_gorda = prev.fat_mass_kg
    estado_peso, ponto = trend_step(prev.trend if prev else TrendState(), entry.day, entry.weights_kg)
    status = classify_day(entry.day_state, entry.intake_kcal, tmb)
    ativas = _confirmed_activity(entry)
    flags: list[str] = []

    esperada = _mean([w.active_kcal for w in janela_anterior if w.active_kcal is not None])
    bootstrap: Optional[float] = None
    if tmb is None:
        flags.append(FLAG_BMR_MISSING)
    else:
        bootstrap = tmb * BOOTSTRAP_ACTIVITY_FACTOR
        usada = ativas if ativas is not None else esperada
        if usada is None:
            flags.append(FLAG_BOOTSTRAP_ACTIVITY_UNKNOWN)
        else:
            bootstrap += usada

    ingestao = non_negative_number(entry.intake_kcal) if status is DayStatus.COMPLETE else None
    novo = WindowDay(entry.day, status, ingestao, ativas, bootstrap, ponto)
    janela = (tuple(janela_anterior) + (novo,))[-WINDOW_DAYS:]

    completos = [w for w in janela if w.status is DayStatus.COMPLETE]
    dias_validos = len(completos)
    if any(w.status is DayStatus.PARTIAL for w in janela):
        flags.append(FLAG_PARTIAL_DAYS_EXCLUDED)
    peso = assess_window([w.weight for w in janela], history_days_of(estado_peso, entry.day))
    flags.extend(peso.flags)

    # Janela: observado e validação.
    motivos: list[Shortfall] = []
    if dias_validos < MIN_COMPLETE_DAYS_IN_WINDOW:
        motivos.append(Shortfall(REASON_COMPLETE_DAYS_BELOW_MIN, dias_validos, MIN_COMPLETE_DAYS_IN_WINDOW))
    por_peso = False
    if peso.history_days < MIN_HISTORY_DAYS:
        motivos.append(Shortfall(REASON_WEIGHT_HISTORY_SHORT, peso.history_days, MIN_HISTORY_DAYS))
        por_peso = True
    if peso.weighins_per_week < MIN_WEIGHINS_PER_WEEK:
        motivos.append(Shortfall(REASON_WEIGHINS_BELOW_MIN, peso.weighins_per_week, MIN_WEIGHINS_PER_WEEK))
        por_peso = True
    inclinacao = peso.slope_kg_per_day
    if inclinacao is None and not por_peso:
        motivos.append(Shortfall(REASON_SLOPE_UNAVAILABLE, 0, 1))
    limite_dia = MAX_PLAUSIBLE_WEEKLY_CHANGE_KG / DAYS_PER_WEEK
    if inclinacao is not None and abs(inclinacao) > limite_dia:
        motivos.append(Shortfall(REASON_SLOPE_IMPLAUSIBLE, abs(inclinacao) * DAYS_PER_WEEK, MAX_PLAUSIBLE_WEEKLY_CHANGE_KG))
    densidade = tissue_energy_density(massa_gorda)
    if densidade is None:
        motivos.append(Shortfall(REASON_FAT_MASS_MISSING, 0, 1))
    media_ingestao = _mean([w.intake_kcal for w in completos if w.intake_kcal is not None])
    observado: Optional[float] = None
    if media_ingestao is not None and inclinacao is not None and densidade is not None:
        observado = media_ingestao - inclinacao * densidade
        if tmb is not None:
            minimo, maximo = OBSERVED_BMR_SHARE_RANGE
            if observado < minimo * tmb or observado > maximo * tmb:
                violado = minimo * tmb if observado < minimo * tmb else maximo * tmb
                motivos.append(Shortfall(REASON_OBSERVED_IMPLAUSIBLE, observado, violado))

    observado_anterior = prev.observed_kcal if prev else None
    adaptativo_anterior = prev.adaptive_kcal if prev else None
    ultima_atualizacao = prev.last_update_day if prev else None
    adaptativo = adaptativo_anterior
    obs_guardado = observado_anterior
    if not motivos and observado is not None:
        obs_guardado = observado
        ultima_atualizacao = entry.day
        if adaptativo_anterior is None:
            adaptativo = observado
        else:
            suave = EXPENDITURE_ALPHA * observado + (1.0 - EXPENDITURE_ALPHA) * adaptativo_anterior
            adaptativo = _clamp(
                suave,
                adaptativo_anterior - EXPENDITURE_MAX_STEP_KCAL,
                adaptativo_anterior + EXPENDITURE_MAX_STEP_KCAL,
            )
            if adaptativo != suave:
                flags.append(FLAG_UPDATE_CLAMPED)
    else:
        flags.append(FLAG_UPDATE_SUSPENDED)

    # Confiança e base.
    media_bootstrap = _mean([w.bootstrap_kcal for w in janela if w.bootstrap_kcal is not None])
    if adaptativo is None:
        confianca = 0.0
        flags.append(FLAG_ADAPTIVE_UNAVAILABLE)
    else:
        confianca = _clamp((dias_validos - CONFIDENCE_FLOOR_DAYS) / CONFIDENCE_RAMP_DAYS, 0.0, 1.0)
        if motivos:
            confianca = min(confianca, SUSPENDED_CONFIDENCE_CAP, prev.confidence if prev else confianca)
    if adaptativo is None:
        base = media_bootstrap
    elif media_bootstrap is None:
        base = adaptativo
    else:
        base = media_bootstrap + (adaptativo - media_bootstrap) * confianca

    # Ajuste diário.
    media_atividade = _mean([w.active_kcal for w in janela if w.active_kcal is not None])
    ajuste = 0.0
    if ativas is None:
        flags.append(FLAG_ACTIVITY_PENDING)
    elif base is not None and media_atividade is not None:
        bruto = (ativas - media_atividade) * ACTIVITY_ADJUSTMENT_COEFFICIENT
        ajuste = _clamp(bruto, ACTIVITY_ADJUSTMENT_MIN_KCAL, ACTIVITY_ADJUSTMENT_MAX_KCAL)
        if ajuste != bruto:
            flags.append(FLAG_ADJUSTMENT_CLAMPED)
    estimado = base + ajuste if base is not None else None

    return AdaptiveEnergyState(
        day=entry.day,
        window=janela,
        trend=estado_peso,
        bmr_kcal=tmb,
        fat_mass_kg=massa_gorda,
        bootstrap_today_kcal=bootstrap,
        bootstrap_mean_kcal=media_bootstrap,
        observed_kcal=obs_guardado,
        adaptive_kcal=adaptativo,
        last_update_day=ultima_atualizacao,
        base_kcal=base,
        confidence=confianca,
        valid_days=dias_validos,
        window_days=len(janela),
        activity_today_kcal=ativas,
        activity_average_kcal=media_atividade,
        activity_adjustment_kcal=ajuste,
        estimated_today_kcal=estimado,
        weight=peso,
        flags=tuple(dict.fromkeys(flags)),
        suspension_reasons=tuple(motivos),
    )
