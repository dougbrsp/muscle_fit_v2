"""Dashboard de nutrição `/dashboard/nutricao` (reformulação N5, L7 do plano
`docs/nutricao/PLANO_NUTRICAO_2026-10-14.md`; mapa `docs/motores/FONTES_DA_VERDADE.md` §82).

Dono da AGREGAÇÃO da janela do dashboard: o que a rota e a tela mostram sobre
aderência por dia e por semana, kcal e macros × meta, pontos de corpo, o gasto
médio da fórmula e o bloco do TDEE adaptativo. Não cria regra de nutrição:
compõe os donos (`weekly_budget` para meta × ingestão, banco e aderência;
`adaptive_tdee` para tendência de peso e gasto observado). A projeção até o
alvo NÃO é daqui: é a do plano (`plan.project_plan_goal`), que a rota chama.

Regras deste módulo:
- Agrega pelos TOTAIS GRAVADOS de cada dia (`NutritionTotals` do `meal_math`),
  nunca somando nutrientes de refeição por conta própria.
- Estado do dia (`DayState`): ANTES do plano (antes do `effective_from` da primeira
  versão publicada, ou sem plano) fica fora da conta; HOJE é dia não fechado e não
  entra na aderência nem no TDEE observado; dia sem refeição é "sem registro" (contado
  à parte, nunca "descumprido"); refeição de total INCOMPLETO fica fora da aderência e
  na lista de incompletos; só o dia de total completo é "registrado".
- Semana segunda a domingo (`weekly_budget.week_start`); semana nas pontas da janela
  é PARCIAL (`partial`), com o relatório só dos dias dentro da janela.
- TDEE observado: sempre sobre os últimos `ADAPTIVE_WINDOW_DAYS` dias
  (`adaptive_tdee.PREFERRED_WINDOW_DAYS`), qualquer que seja o período da tela; o
  gasto "pela fórmula" é a média do TDEE das metas desses mesmos dias.
- Ritmo semanal do peso: inclinação de `adaptive_tdee.weight_trend` (mínimos quadrados).
  A média móvel do peso é do dono do dashboard de corpo (`body_service.moving_average_points`,
  limiar `weight_ma_days`), que a rota chama; aqui só se junta ao ponto do dia.
- % do caminho até o alvo: o ponto de partida é o `effective_from` da versão MAIS ANTIGA
  da sequência contínua de versões do plano com a mesma estratégia e o mesmo alvo
  (`goal_sequence_start`), para uma nova publicação (N7) não zerar o progresso.
- TDEE adaptativo só SUGERE (`adaptive_tdee.reconcile`): `applied` é sempre falso.

Módulo PURO: sem banco, sem FastAPI, sem I/O, sem relógio.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Final, Iterable, Mapping, Optional, Sequence

from .adaptive_tdee import PREFERRED_WINDOW_DAYS, AdaptiveTdee, Reconciliation, WeightTrend, observed_tdee, reconcile, weight_trend
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
# 28 dias é a janela preferida do TDEE adaptativo e 90 o teto para a consulta continuar leve.
DASHBOARD_DEFAULT_DAYS: Final[int] = 28
DASHBOARD_MIN_DAYS: Final[int] = 7
DASHBOARD_MAX_DAYS: Final[int] = 90
# Janelas que a tela oferece (a API as devolve; todas dentro de [MIN, MAX]).
WINDOW_CHOICES_DAYS: Final[tuple[int, ...]] = (7, 28, 90)

# O TDEE observado olha sempre para esta janela (dias corridos até hoje).
ADAPTIVE_WINDOW_DAYS: Final[int] = PREFERRED_WINDOW_DAYS

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
    ou não numérica = o default (a rota recusa valor não inteiro com 422 antes de chegar aqui)."""
    try:
        valor = int(raw)
    except (TypeError, ValueError):
        return DASHBOARD_DEFAULT_DAYS
    return min(max(valor, DASHBOARD_MIN_DAYS), DASHBOARD_MAX_DAYS)


def window_dates(end: date, days: int) -> tuple[date, ...]:
    """Os `days` dias corridos que terminam em `end` (inclusive), em ordem."""
    return tuple(end - timedelta(days=days - 1 - indice) for indice in range(days))


def fetch_days(window: int) -> int:
    """Dias que a rota precisa ler: a janela da tela ou a do TDEE observado, a maior."""
    return max(window, ADAPTIVE_WINDOW_DAYS)


# ---------------------------------------------------------------------------
# Alinhamento por dia
# ---------------------------------------------------------------------------


def group_by_day(days: Sequence[date], pairs: Iterable[tuple[date, Any]]) -> list[list[Any]]:
    """Valores agrupados por dia, alinhados a `days` (dia sem valor = lista vazia; dia fora de `days` é ignorado)."""
    grupos: dict[date, list[Any]] = {dia: [] for dia in days}
    for dia, valor in pairs:
        if dia in grupos:
            grupos[dia].append(valor)
    return [grupos[dia] for dia in days]


def last_by_day(days: Sequence[date], pairs: Iterable[tuple[date, Any]]) -> list[Optional[Any]]:
    """O ÚLTIMO valor não nulo de cada dia (a ordem de `pairs` decide), alinhado a `days`; sem valor = `None`."""
    ultimos: dict[date, Any] = {}
    for dia, valor in pairs:
        if valor is not None:
            ultimos[dia] = valor
    return [ultimos.get(dia) for dia in days]


def daily_values(pairs: Iterable[tuple[Optional[date], Any]]) -> list[tuple[date, Any]]:
    """Um valor por dia (o último não nulo, na ordem recebida), em ordem de data."""
    por_dia: dict[date, Any] = {}
    for dia, valor in pairs:
        if dia is not None and valor is not None:
            por_dia[dia] = valor
    return sorted(por_dia.items())


def count_present(values: Iterable[Any]) -> int:
    """Quantos valores existem (não nulos)."""
    return sum(1 for valor in values if valor is not None)


# ---------------------------------------------------------------------------
# Estado do dia e aderência
# ---------------------------------------------------------------------------


class DayState(str, Enum):
    OUT_OF_PLAN = "out_of_plan"
    OPEN_TODAY = "open_today"
    NO_RECORD = "no_record"
    INCOMPLETE = "incomplete"
    LOGGED = "logged"


COUNTED_STATES: Final[frozenset[DayState]] = frozenset({DayState.NO_RECORD, DayState.INCOMPLETE, DayState.LOGGED})


def day_states(
    days: Sequence[date],
    today: date,
    plan_start: Optional[date],
    day_totals: Sequence[Optional[NutritionTotals]],
) -> list[DayState]:
    """Estado de cada dia (ver o docstring do módulo). Sem plano, todo dia é "antes do plano"."""
    estados: list[DayState] = []
    for dia, total in zip(days, day_totals):
        if plan_start is None or dia < plan_start:
            estados.append(DayState.OUT_OF_PLAN)
        elif dia >= today:
            estados.append(DayState.OPEN_TODAY)
        elif total is None:
            estados.append(DayState.NO_RECORD)
        elif intake_macros(total) is None:
            estados.append(DayState.INCOMPLETE)
        else:
            estados.append(DayState.LOGGED)
    return estados


@dataclass(frozen=True)
class DashboardDay:
    """Um dia da janela: estado, meta e ingestão por macro (partes do gráfico do motor) e a aderência do dia."""

    local_date: date
    state: DayState
    has_target: bool
    macros: Mapping[str, BalanceRow]
    kcal_share: Optional[float]
    protein_share: Optional[float]
    kcal_within_tolerance: Optional[bool]
    protein_ok: Optional[bool]

    @property
    def logged(self) -> bool:
        return self.state == DayState.LOGGED

    @property
    def intake_incomplete(self) -> bool:
        return self.state == DayState.INCOMPLETE

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_date": self.local_date.isoformat(),
            "state": self.state.value,
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
    states: Sequence[DayState],
) -> tuple[DashboardDay, ...]:
    """Meta × ingestão de cada dia pelo `weekly_budget`. Dia fora da conta (antes do plano ou hoje,
    ainda aberto) não leva balanço nem aderência."""
    saida: list[DashboardDay] = []
    for dia, meta, total, estado in zip(days, day_targets, day_totals, states):
        if estado in (DayState.OUT_OF_PLAN, DayState.OPEN_TODAY):
            meta, total = None, None
        ingestao = intake_macros(total)
        balanco = day_balance(meta, total)
        aderencia = weekly_adherence([meta], [ingestao])
        saida.append(
            DashboardDay(
                local_date=dia,
                state=estado,
                has_target=meta is not None and meta.computable,
                macros={linha.field: linha for linha in balanco.rows if linha.field in MACRO_FIELDS},
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
    counted_days: int
    logged_days: int
    no_record_days: int
    meal_days_without_target: int
    kcal_within_tolerance_days: int
    protein_ok_days: int
    open_today: bool
    incomplete_days: tuple[date, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_days": self.window_days,
            "counted_days": self.counted_days,
            "logged_days": self.logged_days,
            "no_record_days": self.no_record_days,
            "meal_days_without_target": self.meal_days_without_target,
            "kcal_within_tolerance_days": self.kcal_within_tolerance_days,
            "protein_ok_days": self.protein_ok_days,
            "open_today": self.open_today,
            "incomplete_days": [dia.isoformat() for dia in self.incomplete_days],
        }


def window_adherence(rows: Sequence[DashboardDay]) -> WindowAdherence:
    """Aderência da janela sobre os dias FECHADOS e dentro do plano. "Sem registro" é contado à
    parte; dia com refeição e sem meta é avisado (`meal_days_without_target`)."""
    return WindowAdherence(
        window_days=len(rows),
        counted_days=sum(1 for linha in rows if linha.state in COUNTED_STATES),
        logged_days=sum(1 for linha in rows if linha.state == DayState.LOGGED),
        no_record_days=sum(1 for linha in rows if linha.state == DayState.NO_RECORD),
        meal_days_without_target=sum(
            1 for linha in rows if linha.state in (DayState.LOGGED, DayState.INCOMPLETE) and not linha.has_target
        ),
        kcal_within_tolerance_days=sum(1 for linha in rows if linha.kcal_within_tolerance is True),
        protein_ok_days=sum(1 for linha in rows if linha.protein_ok is True),
        open_today=any(linha.state == DayState.OPEN_TODAY for linha in rows),
        incomplete_days=tuple(linha.local_date for linha in rows if linha.state == DayState.INCOMPLETE),
    )


# ---------------------------------------------------------------------------
# Semana a semana
# ---------------------------------------------------------------------------


def week_blocks(
    days: Sequence[date],
    day_targets: Sequence[Optional[MacroTargets]],
    day_totals: Sequence[Optional[NutritionTotals]],
    states: Sequence[DayState],
) -> list[dict[str, Any]]:
    """Um relatório do `weekly_budget.week_report` por semana (segunda a domingo) que toca a janela,
    da mais antiga para a mais nova. Dia antes do plano fica fora; hoje (aberto) entra sem ingestão.
    `partial`: a janela só cobre parte da semana. `adherence.valid_week` (do dono) diz se a semana
    tem dias registrados suficientes."""
    grupos: dict[date, list[int]] = {}
    for indice, dia in enumerate(days):
        if states[indice] != DayState.OUT_OF_PLAN:
            grupos.setdefault(week_start(dia), []).append(indice)
    blocos: list[dict[str, Any]] = []
    for inicio in sorted(grupos):
        indices = grupos[inicio]
        dias_semana = [days[i] for i in indices]
        totais = [None if states[i] == DayState.OPEN_TODAY else day_totals[i] for i in indices]
        relatorio = week_report(dias_semana, [day_targets[i] for i in indices], totais)
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


def body_points(
    days: Sequence[date],
    weight_kg: Sequence[Optional[float]],
    weight_average_kg: Mapping[date, float],
    body_fat_percent: Sequence[Optional[float]],
    muscle_mass_kg: Sequence[Optional[float]],
) -> list[dict[str, Any]]:
    """Pontos do gráfico de corpo: só os dias com alguma medida. `weight_average_kg` é a média móvel
    do dono do dashboard de corpo (`body_service.moving_average_points`), por dia pesado."""
    pontos: list[dict[str, Any]] = []
    for indice, dia in enumerate(days):
        if weight_kg[indice] is None and body_fat_percent[indice] is None and muscle_mass_kg[indice] is None:
            continue
        pontos.append(
            {
                "local_date": dia.isoformat(),
                "weight_kg": weight_kg[indice],
                "weight_trend_kg": weight_average_kg.get(dia),
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
# Projeção: ponto de partida do caminho
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanVersion:
    """Uma versão publicada do plano, só o que a sequência do alvo usa."""

    plan_id: int
    effective_from: date
    published_at: Optional[datetime]
    strategy: str
    goal_weight_kg: Optional[float]
    goal_body_fat_percent: Optional[float]


def _version_order(versao: PlanVersion) -> tuple[date, datetime, int]:
    # Mesma ordem de `plan.plan_in_force`: início, publicação, id.
    return (versao.effective_from, versao.published_at or datetime.min, versao.plan_id)


def first_plan_start(versions: Sequence[PlanVersion]) -> Optional[date]:
    """Início da primeira versão publicada (antes dele os dias ficam fora da conta)."""
    return min((versao.effective_from for versao in versions), default=None)


def goal_sequence_start(versions: Sequence[PlanVersion], today: date) -> Optional[date]:
    """`effective_from` da versão mais antiga da sequência contínua, até a versão em vigor hoje, com a
    MESMA estratégia e o MESMO alvo (peso e % de gordura). Mudar de estratégia ou de alvo recomeça o
    caminho; republicar o mesmo plano (N7, nova versão) não. Sem versão em vigor = `None`."""
    ordenadas = sorted((v for v in versions if v.effective_from <= today), key=_version_order)
    if not ordenadas:
        return None
    atual = ordenadas[-1]
    inicio = atual.effective_from
    for versao in reversed(ordenadas[:-1]):
        igual = (
            versao.strategy == atual.strategy
            and versao.goal_weight_kg == atual.goal_weight_kg
            and versao.goal_body_fat_percent == atual.goal_body_fat_percent
        )
        if not igual:
            break
        inicio = versao.effective_from
    return inicio


# ---------------------------------------------------------------------------
# TDEE adaptativo (só sugere)
# ---------------------------------------------------------------------------


def window_tdee_kcal(
    days: Sequence[date], today: date, tdee_kcal: Sequence[Optional[float]]
) -> Optional[float]:
    """Gasto médio da fórmula: média do TDEE das metas dos últimos `ADAPTIVE_WINDOW_DAYS` dias FECHADOS
    que têm meta (hoje, dia aberto, não entra; nenhum = `None`). `days` e `tdee_kcal` alinhados."""
    pares = list(zip(days, tdee_kcal))[-ADAPTIVE_WINDOW_DAYS:]
    valores = [v for dia, x in pares if dia < today and (v := positive_number(x)) is not None]
    return sum(valores) / len(valores) if valores else None


def adaptive_intake_kcal(
    days: Sequence[date], today: date, day_totals: Sequence[Optional[NutritionTotals]]
) -> list[Optional[float]]:
    """kcal de cada dia FECHADO com total completo; hoje (aberto), dia incompleto ou sem refeição = `None`
    (a série mantém o tamanho)."""
    saida: list[Optional[float]] = []
    for dia, total in zip(days, day_totals):
        ingestao = intake_macros(total) if dia < today else None
        saida.append(None if ingestao is None else ingestao.kcal)
    return saida


def adaptive_block(
    intake_kcal: Sequence[Optional[float]],
    weight_kg: Sequence[Optional[float]],
    fat_mass_kg: Any,
    formula_tdee_kcal: Any,
) -> dict[str, Any]:
    """Gasto observado e a sugestão do `adaptive_tdee.reconcile`, sobre os últimos
    `ADAPTIVE_WINDOW_DAYS` das séries recebidas. `applied` é sempre falso: o dashboard não altera
    nem o plano nem as metas."""
    observado: AdaptiveTdee = observed_tdee(
        list(intake_kcal)[-ADAPTIVE_WINDOW_DAYS:], list(weight_kg)[-ADAPTIVE_WINDOW_DAYS:], fat_mass_kg
    )
    sugestao: Reconciliation = reconcile(formula_tdee_kcal, observado)
    return {"observed": observado.to_dict(), "suggestion": sugestao.to_dict(), "applied": False}


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
    meal_days_without_target: int,
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
    if has_plan and logged_days < FEW_LOGGED_DAYS:  # sem plano nenhum dia conta: o aviso certo é o do plano
        codigos.append(EMPTY_FEW_LOGGED_DAYS)
    if has_plan and meal_days_without_target > 0:
        codigos.append(EMPTY_DAYS_WITHOUT_TARGET)
    return tuple(codigos)


def positive_series(values: Sequence[Any]) -> list[Optional[float]]:
    """Série de leituras -> floats positivos (ausente, zero ou inválido = `None`)."""
    return [positive_number(valor) for valor in values]


__all__ = [
    "ADAPTIVE_WINDOW_DAYS",
    "COUNTED_STATES",
    "DASHBOARD_DEFAULT_DAYS",
    "DASHBOARD_MAX_DAYS",
    "DASHBOARD_MIN_DAYS",
    "EMPTY_DAYS_WITHOUT_TARGET",
    "EMPTY_FEW_LOGGED_DAYS",
    "EMPTY_NO_BIOIMPEDANCE",
    "EMPTY_NO_GOAL",
    "EMPTY_NO_PLAN",
    "EMPTY_NO_WEIGHT",
    "FEW_LOGGED_DAYS",
    "MACRO_FIELDS",
    "WINDOW_CHOICES_DAYS",
    "DashboardDay",
    "DayState",
    "PlanVersion",
    "WindowAdherence",
    "adaptive_block",
    "adaptive_intake_kcal",
    "body_points",
    "count_present",
    "daily_values",
    "day_rows",
    "day_states",
    "empty_states",
    "fetch_days",
    "first_plan_start",
    "goal_sequence_start",
    "group_by_day",
    "last_by_day",
    "normalize_window_days",
    "positive_series",
    "week_blocks",
    "weight_trend_public",
    "window_adherence",
    "window_dates",
    "window_tdee_kcal",
]
