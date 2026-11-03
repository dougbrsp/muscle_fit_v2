"""Sugestão de metas por IA (N6, decisão D3 de 2026-11-02): o que a IA pode sugerir, as
faixas que ela enxerga, a revisão da resposta e o contexto mínimo que ela recebe.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §84; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`, padrão `sugestao_plano_ia`).

Regra D3: a IA só SUGERE estratégia, prioridade de macros e parâmetros, numa lista FECHADA
(`SUGGESTION_FIELDS`), dentro das faixas do motor (`suggestion_ranges`, lidas de `strategy`,
`macros`). Nunca devolve grama nem kcal como meta: qualquer campo fora da lista é recusado
(`suggestion_field_forbidden:<campo>`). Quem valida é `plan.build_plan_settings`, o mesmo do
`POST /nutrition/plans`, e quem calcula as gramas é a prévia de sempre
(`daily_target`/`macros`).

Sugestão fora da faixa é RECUSADA por inteiro, sem correção silenciosa: nenhum valor é
trazido para dentro da faixa. Campo que vale para a estratégia e vem `null` recebe o default
do motor (o contrato de `build_plan_settings`), e a tela mostra o valor em "o que mudou".

A justificativa é só por códigos fechados (`SuggestionReason`, rótulos em `labels`): nenhum
texto livre da IA chega à tela.

Contexto (`build_context`): perfil sem nome, e-mail nem data de nascimento; objetivo do
formulário; agregados do histórico (tendência do peso e treino), arredondados.

Módulo PURO: sem banco, sem FastAPI, sem I/O e sem relógio.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Any, Final, Mapping, Optional, Sequence

from .adaptive_tdee import PREFERRED_WINDOW_DAYS, weight_trend
from .anthropometrics import Subject
from .macros import MEALS_PER_DAY_RANGE
from .phases import resolve_stop_body_fat, should_stop
from .plan import ERR_PARAM_NOT_APPLICABLE, PlanSettings, build_plan_settings
from .strategy import (
    BULK_CARB_MIN_G_PER_KG_RANGE,
    CUT_WEEKLY_RATE_SHARE_RANGE,
    MacroPriority,
    Strategy,
    fat_share_range_for,
    protein_range_for,
    surplus_range_for,
)


class SuggestionReason(str, Enum):
    """Por que a IA sugeriu: lista fechada, com rótulo em `labels`. É o único "texto" da IA na tela."""

    GOAL_WEIGHT_BELOW_CURRENT = "goal_weight_below_current"
    GOAL_WEIGHT_ABOVE_CURRENT = "goal_weight_above_current"
    BODY_FAT_ABOVE_GOAL = "body_fat_above_goal"
    BODY_FAT_BELOW_GOAL = "body_fat_below_goal"
    WEIGHT_TREND_DOWN = "weight_trend_down"
    WEIGHT_TREND_UP = "weight_trend_up"
    WEIGHT_TREND_STABLE = "weight_trend_stable"
    HIGH_TRAINING_LOAD = "high_training_load"
    LOW_TRAINING_LOAD = "low_training_load"
    PRESERVE_LEAN_MASS = "preserve_lean_mass"
    CONSERVATIVE_PACE = "conservative_pace"
    LIMITED_DATA = "limited_data"


FIELD_STRATEGY: Final[str] = "strategy"
FIELD_ADVANCED: Final[str] = "advanced"
FIELD_MACRO_PRIORITY: Final[str] = "macro_priority"
FIELD_MEALS: Final[str] = "meals_per_day"
# Sempre um número: valem para toda estratégia.
ALWAYS_NUMBER_FIELDS: Final[tuple[str, str]] = ("protein_g_per_kg", "fat_kcal_share")
# Número ou null (default do motor): cada um vale para uma estratégia (`plan.STRATEGY_ONLY_FIELDS`).
OPTIONAL_NUMBER_FIELDS: Final[tuple[str, str, str]] = ("weekly_rate_share", "surplus_share", "carb_min_g_per_kg")

# A lista fechada do que a IA pode sugerir (D3). Sem gramas, sem kcal, sem peso-alvo, sem ciclo.
SUGGESTION_FIELDS: Final[tuple[str, ...]] = (
    FIELD_STRATEGY,
    FIELD_ADVANCED,
    *ALWAYS_NUMBER_FIELDS,
    *OPTIONAL_NUMBER_FIELDS,
    FIELD_MACRO_PRIORITY,
    FIELD_MEALS,
)
REASONS_KEY: Final[str] = "reason_codes"
# Quantos motivos a tela mostra (default de desenho).
REASONS_MAX: Final[int] = 3
# Linha "gramas manuais" em "o que mudou": aplicar a sugestão desliga as gramas manuais (as gramas mandariam nela).
FIELD_MANUAL_ACTIVE: Final[str] = "manual_targets"

# O formulário manda o estado atual: os campos sugeríveis, os alvos e as gramas manuais ligadas ou não.
CURRENT_GOAL_FIELDS: Final[tuple[str, str]] = ("goal_weight_kg", "goal_body_fat_percent")

ERR_SUGGESTION_SHAPE = "suggestion_shape_invalid"
ERR_SUGGESTION_FIELD_FORBIDDEN = "suggestion_field_forbidden"  # f"suggestion_field_forbidden:<campo>"
ERR_SUGGESTION_FIELD_MISSING = "suggestion_field_missing"  # f"suggestion_field_missing:<campo>"
ERR_SUGGESTION_FIELD_INVALID = "suggestion_field_invalid"  # f"suggestion_field_invalid:<campo>"
ERR_SUGGESTION_REASON_INVALID = "suggestion_reason_invalid"
ERR_SUGGESTION_STOP_REACHED = "suggestion_stop_reached"
# O que a tela e o job veem (o detalhe fica nos códigos, no `ai_usage_log`).
ERR_SUGGESTION_REFUSED = "plan_suggestion_refused"
ERR_SUGGESTION_FAILED = "plan_suggestion_failed"
ERR_SUGGESTION_DISPATCH_FAILED = "plan_suggestion_dispatch_failed"

# Contexto enviado: janela da tendência do peso (a preferida do TDEE adaptativo) e arredondamentos
# (default de desenho: a IA não precisa de mais resolução, e o dado sai menos identificável).
WEIGHT_TREND_WINDOW_DAYS: Final[int] = PREFERRED_WINDOW_DAYS
_DAYS_PER_WEEK: Final[int] = 7
_ROUND_ONE_DECIMAL: Final[int] = 1
_ROUND_SHARE: Final[int] = 4
_ROUND_KCAL: Final[int] = 0
# Fração -> % no texto de "o que mudou" (`labels`). Duas casas: o ritmo do cutting tem décimos de %.
SHARE_TO_PERCENT: Final[int] = 100
PERCENT_LABEL_DECIMALS: Final[int] = 2

# Tolerância de "mudou": a tela envia a fração depois de passar por % (0,7/100 != 0,007 em ponto flutuante).
_SAME_VALUE_REL_TOL: Final[float] = 1e-6
_SAFE_FIELD = re.compile(r"[^a-z0-9_]")
_FIELD_CODE_MAX: Final[int] = 32


def _field_code(prefix: str, field: Any) -> str:
    """`<prefixo>:<campo>`. O nome do campo veio da IA (não confiável): só o alfabeto do código, cortado."""
    return f"{prefix}:{_SAFE_FIELD.sub('', str(field).lower())[:_FIELD_CODE_MAX]}"


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def suggestion_ranges() -> dict[str, dict[str, Any]]:
    """As faixas que a IA enxerga, por estratégia, lidas dos donos: são as que `build_plan_settings` confere."""
    def _comum(estrategia: Strategy) -> dict[str, Any]:
        return {"protein_g_per_kg": protein_range_for(estrategia), "fat_kcal_share": fat_share_range_for(estrategia)}

    return {
        Strategy.CUTTING.value: {**_comum(Strategy.CUTTING), "weekly_rate_share": CUT_WEEKLY_RATE_SHARE_RANGE},
        Strategy.BULKING.value: {
            **_comum(Strategy.BULKING),
            "surplus_share": surplus_range_for(False),
            "surplus_share_advanced": surplus_range_for(True),
            "carb_min_g_per_kg": BULK_CARB_MIN_G_PER_KG_RANGE,
        },
        Strategy.MAINTENANCE.value: _comum(Strategy.MAINTENANCE),
    }


def meals_range() -> tuple[int, int]:
    return MEALS_PER_DAY_RANGE


def reference_settings() -> PlanSettings:
    """Configuração só para resolver o sujeito e a energia antes da sugestão: manutenção, sem nenhum parâmetro do
    formulário (a TMB e o gasto não dependem da estratégia). Sem parâmetros, `build_plan_settings` nunca recusa."""
    settings = build_plan_settings(strategy=Strategy.MAINTENANCE.value).settings
    assert settings is not None
    return settings


# ---------------------------------------------------------------------------
# Revisão da resposta da IA
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SuggestionReview:
    """Revisão do motor: as configurações aceitas e os motivos, ou os códigos da recusa."""

    settings: Optional[PlanSettings]
    reasons: tuple[SuggestionReason, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.settings is not None and not self.errors


def _shape_errors(raw: Mapping[str, Any]) -> list[str]:
    """Campos e tipos, sem faixa: fora da lista, faltando ou com tipo errado. Tipo errado não é corrigido."""
    permitidos = {*SUGGESTION_FIELDS, REASONS_KEY}
    erros = [_field_code(ERR_SUGGESTION_FIELD_FORBIDDEN, campo) for campo in raw if campo not in permitidos]
    erros.extend(_field_code(ERR_SUGGESTION_FIELD_MISSING, campo) for campo in sorted(permitidos) if campo not in raw)
    if erros:
        return erros
    tipos_ok = {
        FIELD_STRATEGY: isinstance(raw[FIELD_STRATEGY], str),
        FIELD_ADVANCED: isinstance(raw[FIELD_ADVANCED], bool),
        FIELD_MACRO_PRIORITY: isinstance(raw[FIELD_MACRO_PRIORITY], str),
        FIELD_MEALS: _is_number(raw[FIELD_MEALS]),
        **{campo: _is_number(raw[campo]) for campo in ALWAYS_NUMBER_FIELDS},
        **{campo: raw[campo] is None or _is_number(raw[campo]) for campo in OPTIONAL_NUMBER_FIELDS},
    }
    return [_field_code(ERR_SUGGESTION_FIELD_INVALID, campo) for campo, ok in tipos_ok.items() if not ok]


def _reasons(raw: Any) -> Optional[tuple[SuggestionReason, ...]]:
    """Motivos da resposta, sem repetir. Código desconhecido ou demais motivos: None (recusa)."""
    if not isinstance(raw, list) or len(raw) > REASONS_MAX:
        return None
    try:
        return tuple(dict.fromkeys(SuggestionReason(codigo) for codigo in raw))
    except (TypeError, ValueError):
        return None


def review_suggestion(
    raw: Any, *, subject: Optional[Subject] = None, goal_body_fat_percent: Any = None
) -> SuggestionReview:
    """Revisa a resposta da IA. Qualquer problema recusa a sugestão inteira. Valida com
    `build_plan_settings` (a mesma entrada do plano) e, com o `subject`, recusa a estratégia cuja
    fase já deveria parar pelo % de gordura (`phases.should_stop`), com o alvo do plano ou o default."""
    if not isinstance(raw, Mapping):
        return SuggestionReview(settings=None, errors=(ERR_SUGGESTION_SHAPE,))
    erros = _shape_errors(raw)
    if erros:
        return SuggestionReview(settings=None, errors=tuple(erros))
    motivos = _reasons(raw[REASONS_KEY])
    if motivos is None:
        return SuggestionReview(settings=None, errors=(ERR_SUGGESTION_REASON_INVALID,))

    validacao = build_plan_settings(
        strategy=raw[FIELD_STRATEGY],
        advanced=raw[FIELD_ADVANCED],
        macro_priority=raw[FIELD_MACRO_PRIORITY],
        meals_per_day=raw[FIELD_MEALS],
        **{campo: raw[campo] for campo in (*ALWAYS_NUMBER_FIELDS, *OPTIONAL_NUMBER_FIELDS)},
    )
    erros = list(validacao.errors)
    settings = validacao.settings
    if settings is not None and raw[FIELD_ADVANCED] and settings.strategy != Strategy.BULKING:
        # `advanced` só existe no bulking; a tela grava `false` nas outras estratégias.
        erros.append(_field_code(ERR_PARAM_NOT_APPLICABLE, FIELD_ADVANCED))
    if erros or settings is None:
        return SuggestionReview(settings=None, errors=tuple(erros))
    if subject is not None:
        limite, _padrao = resolve_stop_body_fat(settings.strategy, subject.sex, goal_body_fat_percent)
        if should_stop(settings.strategy, subject.body_fat_percent, limite):
            return SuggestionReview(settings=None, errors=(ERR_SUGGESTION_STOP_REACHED,))
    return SuggestionReview(settings=settings, reasons=motivos)


def suggestion_fields(settings: PlanSettings) -> dict[str, Any]:
    """Os campos sugeríveis, já com os defaults do motor: é o que a tela coloca no formulário."""
    dados = settings.to_dict()
    return {campo: dados[campo] for campo in SUGGESTION_FIELDS}


# ---------------------------------------------------------------------------
# Estado atual do formulário e "o que mudou"
# ---------------------------------------------------------------------------


def normalize_current(raw: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Estado do formulário, só para comparar e para o contexto: NÃO é validado por faixa (o formulário
    pode estar em edição), mas só passa código conhecido e número finito, para nenhum texto do cliente
    chegar ao prompt."""
    dados = raw or {}
    atual: dict[str, Any] = {}
    estrategia = dados.get(FIELD_STRATEGY)
    atual[FIELD_STRATEGY] = estrategia if estrategia in {s.value for s in Strategy} else None
    prioridade = dados.get(FIELD_MACRO_PRIORITY)
    atual[FIELD_MACRO_PRIORITY] = prioridade if prioridade in {p.value for p in MacroPriority} else None
    atual[FIELD_ADVANCED] = dados.get(FIELD_ADVANCED) is True
    for campo in (*ALWAYS_NUMBER_FIELDS, *OPTIONAL_NUMBER_FIELDS, FIELD_MEALS, *CURRENT_GOAL_FIELDS):
        valor = dados.get(campo)
        atual[campo] = float(valor) if _is_number(valor) else None
    atual[FIELD_MANUAL_ACTIVE] = dados.get(FIELD_MANUAL_ACTIVE) is True
    return atual


@dataclass(frozen=True)
class SuggestionChange:
    """Um campo que muda ao aplicar a sugestão. `before`/`after` são os valores do motor; o texto é de `labels`."""

    field: str
    before: Any
    after: Any


def _same(before: Any, after: Any) -> bool:
    if _is_number(before) and _is_number(after):
        return math.isclose(float(before), float(after), rel_tol=_SAME_VALUE_REL_TOL)
    return before == after


def suggestion_changes(current: Mapping[str, Any], settings: PlanSettings) -> tuple[SuggestionChange, ...]:
    """Campos em que a sugestão difere do formulário (`normalize_current`), na ordem de `SUGGESTION_FIELDS`.
    As gramas manuais ligadas entram como mudança: aplicar a sugestão as desliga."""
    novos = suggestion_fields(settings)
    mudancas = [
        SuggestionChange(campo, current.get(campo), novos[campo])
        for campo in SUGGESTION_FIELDS
        if not _same(current.get(campo), novos[campo])
    ]
    if current.get(FIELD_MANUAL_ACTIVE) is True:
        mudancas.append(SuggestionChange(FIELD_MANUAL_ACTIVE, True, False))
    return tuple(mudancas)


# ---------------------------------------------------------------------------
# Contexto enviado à IA (mínimo necessário)
# ---------------------------------------------------------------------------


def _rounded(value: Any, digits: int) -> Optional[float]:
    return round(float(value), digits) if _is_number(value) else None


def daily_weights(readings: Sequence[Any], today: date, window_days: int = WEIGHT_TREND_WINDOW_DAYS) -> list[Optional[float]]:
    """Uma posição por dia da janela que termina em `today` (a última pesagem do dia vence), como
    `adaptive_tdee.weight_trend` espera. `readings`: objetos com `local_date` e `number` (`subject.Reading`)."""
    por_dia = {leitura.local_date: leitura.number for leitura in readings if leitura.local_date is not None}
    return [por_dia.get(today - timedelta(days=atras)) for atras in range(window_days - 1, -1, -1)]


def weekly_weight_change_share(weights: Sequence[Optional[float]], weight_kg: Any) -> Optional[float]:
    """Tendência do peso por semana, em fração do peso (negativa = perdendo). None sem pesagens suficientes."""
    inclinacao = weight_trend(weights).slope_kg_per_day
    if inclinacao is None or not _is_number(weight_kg) or weight_kg <= 0:
        return None
    return inclinacao * _DAYS_PER_WEEK / float(weight_kg)


def training_summary(past_training_kcal: Sequence[Optional[float]]) -> dict[str, Any]:
    """Treino dos dias passados (`plan.training_kcal_of_day`): dias com treino e a média diária dos dias conhecidos."""
    conhecidos = [kcal for kcal in past_training_kcal if kcal is not None]
    dias = sum(1 for kcal in past_training_kcal if kcal is None or kcal > 0)
    media = sum(conhecidos) / len(conhecidos) if conhecidos else None
    return {
        "window_days": len(past_training_kcal),
        "training_days": dias,
        "mean_training_kcal_per_day": _rounded(media, _ROUND_KCAL),
    }


def build_context(
    subject: Subject,
    *,
    bmr_kcal: Any,
    tdee_kcal: Any,
    weights: Sequence[Optional[float]],
    past_training_kcal: Sequence[Optional[float]],
    current: Mapping[str, Any],
) -> dict[str, Any]:
    """O que a IA recebe: perfil sem identificador direto, energia do motor, agregados do histórico e o
    formulário (`normalize_current`). Lista explícita, para um campo novo do `Subject` não vazar sozinho."""
    return {
        "profile": {
            "sex": subject.sex.value if subject.sex else None,
            "age_years": subject.age_years,
            "height_cm": _rounded(subject.height_cm, _ROUND_ONE_DECIMAL),
            "weight_kg": _rounded(subject.weight_kg, _ROUND_ONE_DECIMAL),
            "body_fat_percent": _rounded(subject.body_fat_percent, _ROUND_ONE_DECIMAL),
            "activity_level": subject.activity_level.value if subject.activity_level else None,
        },
        "energy": {"bmr_kcal": _rounded(bmr_kcal, _ROUND_KCAL), "tdee_kcal": _rounded(tdee_kcal, _ROUND_KCAL)},
        "history": {
            "weight_trend_window_days": len(weights),
            "weight_readings": sum(1 for peso in weights if peso is not None),
            "weekly_weight_change_share": _rounded(weekly_weight_change_share(weights, subject.weight_kg), _ROUND_SHARE),
            "training": training_summary(past_training_kcal),
        },
        "current_form": {campo: current.get(campo) for campo in (*SUGGESTION_FIELDS, *CURRENT_GOAL_FIELDS)},
    }


__all__ = [
    "ALWAYS_NUMBER_FIELDS",
    "CURRENT_GOAL_FIELDS",
    "ERR_SUGGESTION_DISPATCH_FAILED",
    "ERR_SUGGESTION_FAILED",
    "ERR_SUGGESTION_FIELD_FORBIDDEN",
    "ERR_SUGGESTION_FIELD_INVALID",
    "ERR_SUGGESTION_FIELD_MISSING",
    "ERR_SUGGESTION_REASON_INVALID",
    "ERR_SUGGESTION_REFUSED",
    "ERR_SUGGESTION_SHAPE",
    "ERR_SUGGESTION_STOP_REACHED",
    "FIELD_ADVANCED",
    "FIELD_MANUAL_ACTIVE",
    "OPTIONAL_NUMBER_FIELDS",
    "PERCENT_LABEL_DECIMALS",
    "SHARE_TO_PERCENT",
    "REASONS_KEY",
    "REASONS_MAX",
    "SUGGESTION_FIELDS",
    "SuggestionChange",
    "SuggestionReason",
    "SuggestionReview",
    "WEIGHT_TREND_WINDOW_DAYS",
    "build_context",
    "daily_weights",
    "meals_range",
    "normalize_current",
    "reference_settings",
    "review_suggestion",
    "suggestion_changes",
    "suggestion_fields",
    "suggestion_ranges",
    "training_summary",
    "weekly_weight_change_share",
]
