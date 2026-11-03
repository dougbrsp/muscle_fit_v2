"""Dashboard de nutrição `/dashboard/nutricao` (reformulação N5, L7 do plano
`docs/nutricao/PLANO_NUTRICAO_2026-10-14.md`; mapa `docs/motores/FONTES_DA_VERDADE.md` §82).

Dono da AGREGAÇÃO da janela do dashboard: o que a rota e a tela mostram sobre
aderência por dia e por semana, kcal e macros × meta, tendência de peso/%G/massa
muscular e o bloco do TDEE adaptativo. Não cria regra de nutrição: compõe os
donos (`weekly_budget` para meta × ingestão, banco e aderência; `adaptive_tdee`
para tendência de peso e gasto observado; `phases` para a projeção, que a rota
chama direto).

Regras deste módulo:
- Agrega pelos TOTAIS GRAVADOS de cada dia (`NutritionTotals` do `meal_math`),
  nunca somando nutrientes de refeição por conta própria. Dia com total
  INCOMPLETO fica fora da aderência e do TDEE observado e entra em
  `incomplete_days` (a ingestão é um mínimo).
- Semana segunda a domingo (`weekly_budget.week_start`); semana nas pontas da
  janela é PARCIAL (`partial`), com o relatório só dos dias dentro da janela.
- Tendência do peso: média móvel dos últimos `TREND_WINDOW_DAYS` dias corridos
  (default de desenho, a mesma janela de 7 dias do dashboard de corpo); o ritmo
  semanal vem da inclinação de `adaptive_tdee.weight_trend` (mínimos quadrados).
- TDEE adaptativo só SUGERE (`adaptive_tdee.reconcile`): `applied` é sempre falso.

Módulo PURO: sem banco, sem FastAPI, sem I/O, sem relógio.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final, Mapping, Optional, Sequence

from .adaptive_tdee import AdaptiveTdee, Reconciliation, WeightTrend, observed_tdee, reconcile, weight_trend
from .anthropometrics import positive_number
from .energy_density import DAYS_PER_WEEK
from .macros import MacroTargets
from .meal_math import CARB_FIELD, ENERGY_FIELD, FAT_FIELD, PROTEIN_FIELD, NutritionTotals
from .weekly_budget import (
    BalanceRow,
    day_balance,
    intake_macros,
    week_report,
    week_start,
    weekly_adherence,
)

# Janela do dashboard em dias corridos (terminando hoje). Defaults de desenho:
# 28 dias é a janela preferida do TDEE adaptativo (`adaptive_tdee.PREFERRED_WINDOW_DAYS`)
# e 90 o teto para a consulta continuar leve.
DASHBOARD_DEFAULT_DAYS: Final[int] = 28
DASHBOARD_MIN_DAYS: Final[int] = 7
DASHBOARD_MAX_DAYS: Final[int] = 90

# Janela da média móvel do peso (default de desenho; a mesma do dashboard de corpo).
TREND_WINDOW_DAYS: Final[int] = 7

# Abaixo disso o resumo de aderência é pouco confiável: o estado vazio avisa
# (default de desenho; a semana válida do motor pede 5, `weekly_budget.MIN_LOGGED_DAYS_FOR_WEEK`).
FEW_LOGGED_DAYS: Final[int] = 3

# Macros que o dashboard mostra, na ordem da tela (códigos do `meal_math`).
MACRO_FIELDS: Final[tuple[str, ...]] = (ENERGY_FIELD, PROTEIN_FIELD, CARB_FIELD, FAT_FIELD)

# Estados vazios (rotulados em `labels.LIMITATION_LABELS`).
EMPTY_NO_PLAN = "dashboard_no_plan"
EMPTY_NO_GOAL = "dashboard_no_goal"
EMPTY_NO_WEIGHT = "dashboard_no_weight"
EMPTY_NO_BIOIMPEDANCE = "dashboard_no_bioimpedance"
EMPTY_FEW_LOGGED_DAYS = "dashboard_few_logged_days"
EMPTY_DAYS_WITHOUT_TARGET = "dashboard_days_without_target"


def normalize_window_days(raw: Any) -> int:
    """Janela pedida -> dentro de [`DASHBOARD_MIN_DAYS`, `DASHBOARD_MAX_DAYS`]; ausente
    ou inválida = o default."""
    try:
        valor = int(raw)
    except (TypeError, ValueError):
        return DASHBOARD_DEFAULT_DAYS
    return min(max(valor, DASHBOARD_MIN_DAYS), DASHBOARD_MAX_DAYS)


def window_dates(end: date, days: int) -> tuple[date, ...]:
    """Os `days` dias corridos que terminam em `end` (inclusive), em ordem."""
    return tuple(end - timedelta(days=days - 1 - indice) for indice in range(days))


# ---------------------------------------------------------------------------
# Dia a dia
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DashboardDay:
    """Um dia da janela: meta e ingestão por macro (ingestão só se COMPLETA),
    as partes do gráfico do motor e a aderência do dia."""

    local_date: date
    has_target: bool
    logged: bool
    intake_incomplete: bool
    macros: Mapping[str, BalanceRow]
    kcal_share: Optional[float]
    protein_share: Optional[float]
    kcal_within_tolerance: Optional[bool]
    protein_ok: Optional[bool]

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_date": self.local_date.isoformat(),
            "has_target": self.has_target,
            "logged": self.logged,
            "intake_incomplete": self.intake_incomplete,
            "macros": {campo: linha.to_dict() for campo, linha in self.macros.items()},
            "kcal_share": self.kcal_share,
            "protein_share": self.protein_share,
            "kcal_within_tolerance": self.kcal_within_tolerance,
            "protein_ok": self.protein_ok,
        }


def day_rows(
    days: Sequence[date],
    day_targets: Sequence[Optional[MacroTargets]],
    day_totals: Sequence[Optional[NutritionTotals]],
) -> tuple[DashboardDay, ...]:
    """Meta × ingestão de cada dia pelo `weekly_budget` (balanço e aderência do dia)."""
    saida: list[DashboardDay] = []
    for dia, meta, total in zip(days, day_targets, day_totals):
        ingestao = intake_macros(total)
        balanco = day_balance(meta, total)
        linhas = {linha.field: linha for linha in balanco.rows if linha.field in MACRO_FIELDS}
        aderencia = weekly_adherence([meta], [ingestao])
        saida.append(
            DashboardDay(
                local_date=dia,
                has_target=meta is not None and meta.computable,
                logged=ingestao is not None,
                intake_incomplete=total is not None and ingestao is None,
                macros=linhas,
                kcal_share=aderencia.kcal_share,
                protein_share=aderencia.protein_share,
                kcal_within_tolerance=aderencia.kcal_within_tolerance,
                protein_ok=aderencia.protein_ok,
            )
        )
    return tuple(saida)


@dataclass(frozen=True)
class WindowAdherence:
    """Contagem de dias da janela (os limiares de tolerância são do `weekly_budget`)."""

    window_days: int
    logged_days: int
    days_with_target: int
    days_without_target: int
    kcal_within_tolerance_days: int
    protein_ok_days: int
    incomplete_days: tuple[date, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_days": self.window_days,
            "logged_days": self.logged_days,
            "days_with_target": self.days_with_target,
            "days_without_target": self.days_without_target,
            "kcal_within_tolerance_days": self.kcal_within_tolerance_days,
            "protein_ok_days": self.protein_ok_days,
            "incomplete_days": [dia.isoformat() for dia in self.incomplete_days],
        }


def window_adherence(rows: Sequence[DashboardDay]) -> WindowAdherence:
    """Aderência diária da janela: dias registrados, com meta, dentro da tolerância
    de kcal e com a proteína mínima (cada marca vem do dia, do `weekly_budget`)."""
    return WindowAdherence(
        window_days=len(rows),
        logged_days=sum(1 for linha in rows if linha.logged),
        days_with_target=sum(1 for linha in rows if linha.has_target),
        days_without_target=sum(1 for linha in rows if not linha.has_target),
        kcal_within_tolerance_days=sum(1 for linha in rows if linha.kcal_within_tolerance is True),
        protein_ok_days=sum(1 for linha in rows if linha.protein_ok is True),
        incomplete_days=tuple(linha.local_date for linha in rows if linha.intake_incomplete),
    )


# ---------------------------------------------------------------------------
# Semana a semana
# ---------------------------------------------------------------------------


def week_blocks(
    days: Sequence[date],
    day_targets: Sequence[Optional[MacroTargets]],
    day_totals: Sequence[Optional[NutritionTotals]],
) -> list[dict[str, Any]]:
    """Um relatório do `weekly_budget.week_report` por semana (segunda a domingo)
    que toca a janela, da mais antiga para a mais nova. `partial`: a janela só
    cobre parte da semana (o relatório vale para os dias dentro dela)."""
    grupos: dict[date, list[int]] = {}
    for indice, dia in enumerate(days):
        grupos.setdefault(week_start(dia), []).append(indice)
    blocos: list[dict[str, Any]] = []
    for inicio in sorted(grupos):
        indices = grupos[inicio]
        dias_semana = [days[i] for i in indices]
        relatorio = week_report(dias_semana, [day_targets[i] for i in indices], [day_totals[i] for i in indices])
        corpo = relatorio.to_dict()
        corpo["limitations"] = list(relatorio.limitations)
        blocos.append(
            {
                "start": inicio.isoformat(),
                "end": (inicio + timedelta(days=DAYS_PER_WEEK - 1)).isoformat(),
                "partial": len(dias_semana) < DAYS_PER_WEEK,
                "report": corpo,
            }
        )
    return blocos


# ---------------------------------------------------------------------------
# Corpo: peso, %G, massa muscular
# ---------------------------------------------------------------------------


def trailing_mean(days: Sequence[date], values: Sequence[Optional[float]], window: int = TREND_WINDOW_DAYS) -> list[Optional[float]]:
    """Média dos valores presentes nos `window` dias corridos que terminam em cada
    dia (o próprio dia incluso); `None` onde o dia não tem valor."""
    saida: list[Optional[float]] = []
    for indice, dia in enumerate(days):
        if values[indice] is None:
            saida.append(None)
            continue
        inicio = dia - timedelta(days=window - 1)
        janela = [v for d, v in zip(days[: indice + 1], values[: indice + 1]) if v is not None and d >= inicio]
        saida.append(sum(janela) / len(janela))
    return saida


def body_points(
    days: Sequence[date],
    weight_kg: Sequence[Optional[float]],
    body_fat_percent: Sequence[Optional[float]],
    muscle_mass_kg: Sequence[Optional[float]],
) -> list[dict[str, Any]]:
    """Pontos do gráfico de corpo: só os dias com alguma medida; o peso traz a média móvel."""
    medias = trailing_mean(days, weight_kg)
    pontos: list[dict[str, Any]] = []
    for indice, dia in enumerate(days):
        if weight_kg[indice] is None and body_fat_percent[indice] is None and muscle_mass_kg[indice] is None:
            continue
        pontos.append(
            {
                "local_date": dia.isoformat(),
                "weight_kg": weight_kg[indice],
                "weight_trend_kg": medias[indice],
                "body_fat_percent": body_fat_percent[indice],
                "muscle_mass_kg": muscle_mass_kg[indice],
            }
        )
    return pontos


def weight_trend_public(weight_kg: Sequence[Optional[float]]) -> dict[str, Any]:
    """Ritmo do peso na janela (kg por semana) pela inclinação do `adaptive_tdee`;
    `None` quando faltam pesagens ou vão entre elas (a limitação diz qual)."""
    tendencia: WeightTrend = weight_trend(weight_kg)
    corpo = tendencia.to_dict()
    inclinacao = tendencia.slope_kg_per_day
    corpo["weekly_change_kg"] = None if inclinacao is None else inclinacao * DAYS_PER_WEEK
    return corpo


# ---------------------------------------------------------------------------
# TDEE adaptativo (só sugere)
# ---------------------------------------------------------------------------


def adaptive_block(
    intake_kcal: Sequence[Optional[float]],
    weight_kg: Sequence[Optional[float]],
    fat_mass_kg: Any,
    formula_tdee_kcal: Any,
) -> dict[str, Any]:
    """Gasto observado e a sugestão do `adaptive_tdee.reconcile`. `applied` é
    sempre falso: o dashboard não altera nem o plano nem as metas."""
    observado: AdaptiveTdee = observed_tdee(intake_kcal, weight_kg, fat_mass_kg)
    sugestao: Reconciliation = reconcile(formula_tdee_kcal, observado)
    return {"observed": observado.to_dict(), "suggestion": sugestao.to_dict(), "applied": False}


def complete_intake_kcal(day_totals: Sequence[Optional[NutritionTotals]]) -> list[Optional[float]]:
    """kcal de cada dia com total COMPLETO (o dia incompleto ou sem refeição vira `None`)."""
    saida: list[Optional[float]] = []
    for total in day_totals:
        ingestao = intake_macros(total)
        saida.append(None if ingestao is None else ingestao.kcal)
    return saida


# ---------------------------------------------------------------------------
# Estados vazios
# ---------------------------------------------------------------------------


def empty_states(
    *,
    has_plan: bool,
    has_goal: bool,
    weight_readings: int,
    bioimpedance_readings: int,
    logged_days: int,
    days_without_target: int,
) -> tuple[str, ...]:
    """Códigos dos estados vazios úteis (a tela diz o que falta para o gráfico aparecer)."""
    codigos: list[str] = []
    if not has_plan:
        codigos.append(EMPTY_NO_PLAN)
    elif not has_goal:
        codigos.append(EMPTY_NO_GOAL)
    if weight_readings == 0:
        codigos.append(EMPTY_NO_WEIGHT)
    if bioimpedance_readings == 0:
        codigos.append(EMPTY_NO_BIOIMPEDANCE)
    if logged_days < FEW_LOGGED_DAYS:
        codigos.append(EMPTY_FEW_LOGGED_DAYS)
    if has_plan and days_without_target > 0:
        codigos.append(EMPTY_DAYS_WITHOUT_TARGET)
    return tuple(codigos)


def positive_series(values: Sequence[Any]) -> list[Optional[float]]:
    """Série de leituras -> floats positivos (ausente, zero ou inválido = `None`)."""
    return [positive_number(valor) for valor in values]


__all__ = [
    "DASHBOARD_DEFAULT_DAYS",
    "EMPTY_DAYS_WITHOUT_TARGET",
    "EMPTY_FEW_LOGGED_DAYS",
    "EMPTY_NO_BIOIMPEDANCE",
    "EMPTY_NO_GOAL",
    "EMPTY_NO_PLAN",
    "EMPTY_NO_WEIGHT",
    "FEW_LOGGED_DAYS",
    "MACRO_FIELDS",
    "DASHBOARD_MAX_DAYS",
    "DASHBOARD_MIN_DAYS",
    "TREND_WINDOW_DAYS",
    "DashboardDay",
    "WindowAdherence",
    "adaptive_block",
    "body_points",
    "complete_intake_kcal",
    "day_rows",
    "empty_states",
    "normalize_window_days",
    "positive_series",
    "trailing_mean",
    "week_blocks",
    "weight_trend_public",
    "window_adherence",
    "window_dates",
]
