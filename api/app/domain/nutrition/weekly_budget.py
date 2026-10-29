"""Semana: orçamento, banco de kcal e aderência.

Dono único (mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7; guarda
`api/tests/unit/test_guarda_fontes_da_verdade.py`). Plano:
`docs/nutricao/PLANO_NUTRICAO_2026-10-14.md` §3.5 e §7 (decisão D-H, E3).

- Semana começa na segunda (`WEEK_STARTS_ON_WEEKDAY` = 0 em `date.weekday()`).
- Orçamento semanal = soma das 7 metas materializadas (semana derivada, sem
  tabela própria: E3).
- Banco: transferência entre dias ≤ 10% do alvo diário e ≤ 5% do semanal.
- Aderência = Σ ingestão / Σ alvo nos dias registrados; tolerância de 5% nas
  kcal; proteína ≥ 90% do alvo. Semana "válida" com ≥ 5 dias registrados.

Diário (Lote L4, mapa §52):
- Dia "registrado" = tem refeição E o total de energia, proteína, carboidrato
  e gordura COMPLETO (`intake_macros`). Dia com refeição de total incompleto
  (alimento sem energia ou macro na tabela, `meal_math`) fica FORA do banco e
  da aderência (`incomplete_days`, `intake_incomplete_days`): um mínimo não é
  a ingestão do dia e entraria como falta de comida.
- Balanço do dia (`day_balance`): por nutriente, meta − ingestão (`remaining`)
  e ingestão / meta (`share`), só com meta e ingestão COMPLETA daquele
  nutriente; e o balanço energético ingestão − gasto (TDEE da meta).
- `week_report` reúne orçamento, banco e aderência da semana do diário.

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final, Optional, Sequence

from .anthropometrics import non_negative_number, positive_number
from .energy_density import DAYS_PER_WEEK
from .macros import Macros, MacroTargets
from .meal_math import CARB_FIELD, ENERGY_FIELD, FAT_FIELD, FIBER_FIELD, PROTEIN_FIELD, NutritionTotals

WEEK_STARTS_ON_WEEKDAY: Final[int] = 0  # segunda-feira em `date.weekday()`
MAX_DAILY_CARRYOVER_SHARE: Final[float] = 0.10
MAX_WEEKLY_BANK_SHARE: Final[float] = 0.05
ADHERENCE_KCAL_TOLERANCE_SHARE: Final[float] = 0.05
ADHERENCE_PROTEIN_MIN_SHARE: Final[float] = 0.90
MIN_LOGGED_DAYS_FOR_WEEK: Final[int] = 5

CLAMP_DAILY_CARRYOVER = "max_daily_carryover_share"
CLAMP_WEEKLY_BANK = "max_weekly_bank_share"
LIMIT_NO_TARGETS = "no_daily_targets"
LIMIT_NO_LOGGED_DAYS = "no_logged_days"


def week_start(day: date) -> date:
    """Segunda-feira da semana de `day`."""
    return day - timedelta(days=(day.weekday() - WEEK_STARTS_ON_WEEKDAY) % DAYS_PER_WEEK)


def week_days(day: date) -> tuple[date, ...]:
    inicio = week_start(day)
    return tuple(inicio + timedelta(days=i) for i in range(DAYS_PER_WEEK))


@dataclass(frozen=True)
class WeeklyBudget:
    kcal: Optional[float]
    protein_g: Optional[float]
    carb_g: Optional[float]
    fat_g: Optional[float]
    days_with_target: int
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kcal": self.kcal,
            "protein_g": self.protein_g,
            "carb_g": self.carb_g,
            "fat_g": self.fat_g,
            "days_with_target": self.days_with_target,
            "limitations": list(self.limitations),
        }


def weekly_budget(day_targets: Sequence[Optional[MacroTargets]]) -> WeeklyBudget:
    """Soma das metas dos dias que têm meta calculável."""
    validas = [t for t in day_targets if t is not None and t.computable]
    if not validas:
        return WeeklyBudget(kcal=None, protein_g=None, carb_g=None, fat_g=None, days_with_target=0, limitations=(LIMIT_NO_TARGETS,))
    return WeeklyBudget(
        kcal=sum(float(t.kcal) for t in validas),  # type: ignore[arg-type]
        protein_g=sum(float(t.protein_g) for t in validas),  # type: ignore[arg-type]
        carb_g=sum(float(t.carb_g) for t in validas),  # type: ignore[arg-type]
        fat_g=sum(float(t.fat_g) for t in validas),  # type: ignore[arg-type]
        days_with_target=len(validas),
    )


@dataclass(frozen=True)
class BankState:
    """Saldo da semana (positivo = kcal sobrando) e quanto pode ir para o dia seguinte."""

    balance_kcal: Optional[float]
    carryover_kcal: Optional[float]
    max_daily_carryover_kcal: Optional[float]
    max_weekly_bank_kcal: Optional[float]
    logged_days: int
    clamped_by: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "balance_kcal": self.balance_kcal,
            "carryover_kcal": self.carryover_kcal,
            "max_daily_carryover_kcal": self.max_daily_carryover_kcal,
            "max_weekly_bank_kcal": self.max_weekly_bank_kcal,
            "logged_days": self.logged_days,
            "clamped_by": list(self.clamped_by),
            "limitations": list(self.limitations),
        }


def bank_state(targets_kcal: Sequence[Optional[float]], intakes_kcal: Sequence[Optional[float]]) -> BankState:
    """Saldo acumulado (alvo − ingestão) nos dias registrados e a transferência
    permitida para o próximo dia, limitada por dia e pela semana."""
    pares = [
        (positive_number(alvo), non_negative_number(ingestao))
        for alvo, ingestao in zip(targets_kcal, intakes_kcal)
    ]
    alvos = [alvo for alvo, _ in pares if alvo is not None]
    if not alvos:
        return BankState(None, None, None, None, 0, limitations=(LIMIT_NO_TARGETS,))
    registrados = [(alvo, ing) for alvo, ing in pares if alvo is not None and ing is not None]
    teto_dia = MAX_DAILY_CARRYOVER_SHARE * (sum(alvos) / len(alvos))
    teto_semana = MAX_WEEKLY_BANK_SHARE * sum(alvos)
    if not registrados:
        return BankState(None, None, teto_dia, teto_semana, 0, limitations=(LIMIT_NO_LOGGED_DAYS,))
    saldo = sum(alvo - ing for alvo, ing in registrados)
    clamps: list[str] = []
    transferencia = saldo
    if abs(transferencia) > teto_semana:
        transferencia = teto_semana if transferencia > 0 else -teto_semana
        clamps.append(CLAMP_WEEKLY_BANK)
    if abs(transferencia) > teto_dia:
        transferencia = teto_dia if transferencia > 0 else -teto_dia
        clamps.append(CLAMP_DAILY_CARRYOVER)
    return BankState(
        balance_kcal=saldo,
        carryover_kcal=transferencia,
        max_daily_carryover_kcal=teto_dia,
        max_weekly_bank_kcal=teto_semana,
        logged_days=len(registrados),
        clamped_by=tuple(clamps),
    )


@dataclass(frozen=True)
class Adherence:
    kcal_share: Optional[float]
    protein_share: Optional[float]
    logged_days: int
    valid_week: bool
    kcal_within_tolerance: Optional[bool]
    protein_ok: Optional[bool]
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kcal_share": self.kcal_share,
            "protein_share": self.protein_share,
            "logged_days": self.logged_days,
            "valid_week": self.valid_week,
            "kcal_within_tolerance": self.kcal_within_tolerance,
            "protein_ok": self.protein_ok,
            "limitations": list(self.limitations),
        }


def weekly_adherence(day_targets: Sequence[Optional[MacroTargets]], intakes: Sequence[Optional[Macros]]) -> Adherence:
    """Σ ingestão / Σ alvo nos dias com meta E registro."""
    pares = [
        (alvo, ing)
        for alvo, ing in zip(day_targets, intakes)
        if alvo is not None and alvo.computable and ing is not None
    ]
    registrados = len(pares)
    valida = registrados >= MIN_LOGGED_DAYS_FOR_WEEK
    if not pares:
        return Adherence(None, None, 0, False, None, None, limitations=(LIMIT_NO_LOGGED_DAYS,))
    alvo_kcal = sum(float(a.kcal) for a, _ in pares)  # type: ignore[arg-type]
    alvo_prot = sum(float(a.protein_g) for a, _ in pares)  # type: ignore[arg-type]
    ing_kcal = sum(i.kcal for _, i in pares)
    ing_prot = sum(i.protein_g for _, i in pares)
    share_kcal = ing_kcal / alvo_kcal if alvo_kcal > 0 else None
    share_prot = ing_prot / alvo_prot if alvo_prot > 0 else None
    # Limites comparados em kcal/g, não pela razão: 2.100 de 2.000 (5% exatos)
    # dá razão 1,0500000000000000444 e cairia fora da tolerância por arredondamento.
    return Adherence(
        kcal_share=share_kcal,
        protein_share=share_prot,
        logged_days=registrados,
        valid_week=valida,
        kcal_within_tolerance=(
            None if share_kcal is None else abs(ing_kcal - alvo_kcal) <= ADHERENCE_KCAL_TOLERANCE_SHARE * alvo_kcal
        ),
        protein_ok=None if share_prot is None else ing_prot >= ADHERENCE_PROTEIN_MIN_SHARE * alvo_prot,
    )


# ---------------------------------------------------------------------------
# Diário (Lote L4, mapa §52): dia registrado, balanço do dia e relatório da semana
# ---------------------------------------------------------------------------

LIMIT_INCOMPLETE_INTAKE_DAYS = "intake_incomplete_days"
LIMIT_NO_DAY_TARGET = "no_day_target"
LIMIT_NO_INTAKE = "no_intake_logged"

# Nutriente do diário (código da RDC 429, `meal_math`) -> campo da meta
# (`MacroTargets`): a correspondência é daqui, nenhum consumidor a refaz.
BALANCE_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    (ENERGY_FIELD, "kcal"),
    (PROTEIN_FIELD, "protein_g"),
    (CARB_FIELD, "carb_g"),
    (FAT_FIELD, "fat_g"),
    (FIBER_FIELD, "fiber_g"),
)


def target_macros(
    kcal: Any, protein_g: Any, carb_g: Any, fat_g: Any, fiber_g: Any = None
) -> Optional[MacroTargets]:
    """Meta materializada (colunas da 079) como `MacroTargets`; sem nenhum
    valor -> `None` (sem meta)."""
    valores = [non_negative_number(v) for v in (kcal, protein_g, carb_g, fat_g, fiber_g)]
    if all(valor is None for valor in valores):
        return None
    energia, proteina, carbo, gordura, fibra = valores
    return MacroTargets(
        kcal=energia,
        protein_g=proteina,
        carb_g=carbo,
        fat_g=gordura,
        fiber_g=fibra,
        protein_basis=None,
        protein_basis_kg=None,
        protein_g_per_kg=None,
    )


def intake_macros(totals: Optional[NutritionTotals]) -> Optional[Macros]:
    """Ingestão do dia para a semana: só com energia e os três macros
    COMPLETOS (sem refeição ou com total incompleto -> `None`)."""
    if totals is None:
        return None
    return totals.complete_nutrients().as_macros()


@dataclass(frozen=True)
class BalanceRow:
    """Um nutriente do dia: meta, ingestão (mínimo, se incompleta), quanto
    falta (meta − ingestão) e a fração da meta já ingerida."""

    field: str
    target: Optional[float]
    intake: Optional[float]
    intake_complete: bool
    remaining: Optional[float]
    share: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "target": self.target,
            "intake": self.intake,
            "intake_complete": self.intake_complete,
            "remaining": self.remaining,
            "share": self.share,
        }


@dataclass(frozen=True)
class DayBalance:
    rows: tuple[BalanceRow, ...]
    tdee_kcal: Optional[float]
    energy_balance_kcal: Optional[float]  # ingestão − gasto do dia (negativo = déficit)
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [linha.to_dict() for linha in self.rows],
            "tdee_kcal": self.tdee_kcal,
            "energy_balance_kcal": self.energy_balance_kcal,
            "limitations": list(self.limitations),
        }


def day_balance(target: Optional[MacroTargets], totals: Optional[NutritionTotals], tdee_kcal: Any = None) -> DayBalance:
    """Meta × ingestão do dia, por nutriente; só calcula o que tem meta E
    ingestão completa (incompleta é um mínimo: o "falta" seria falso)."""
    linhas: list[BalanceRow] = []
    for campo, campo_meta in BALANCE_FIELDS:
        meta = None if target is None else non_negative_number(getattr(target, campo_meta))
        ingestao = None if totals is None else totals.nutrients.get(campo)
        completa = totals is not None and totals.is_complete(campo)
        calculavel = meta is not None and completa and ingestao is not None
        linhas.append(
            BalanceRow(
                field=campo,
                target=meta,
                intake=ingestao,
                intake_complete=completa,
                remaining=meta - ingestao if calculavel else None,  # type: ignore[operator]
                share=ingestao / meta if calculavel and meta > 0 else None,  # type: ignore[operator]
            )
        )
    gasto = positive_number(tdee_kcal)
    energia_completa = totals is not None and totals.is_complete(ENERGY_FIELD)
    balanco = totals.nutrients.energy_kcal - gasto if gasto is not None and energia_completa else None  # type: ignore[union-attr, operator]
    limitacoes: list[str] = []
    if target is None:
        limitacoes.append(LIMIT_NO_DAY_TARGET)
    if totals is None:
        limitacoes.append(LIMIT_NO_INTAKE)
    else:
        limitacoes.extend(totals.limitations)
    return DayBalance(rows=tuple(linhas), tdee_kcal=gasto, energy_balance_kcal=balanco, limitations=tuple(limitacoes))


@dataclass(frozen=True)
class WeekReport:
    """Semana do diário: orçamento, banco e aderência, os dias registrados
    (total completo) e os dias com refeição de total incompleto."""

    days: tuple[date, ...]
    budget: WeeklyBudget
    bank: BankState
    adherence: Adherence
    logged_days: tuple[date, ...]
    incomplete_days: tuple[date, ...]
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "days": [dia.isoformat() for dia in self.days],
            "budget": self.budget.to_dict(),
            "bank": self.bank.to_dict(),
            "adherence": self.adherence.to_dict(),
            "logged_days": [dia.isoformat() for dia in self.logged_days],
            "incomplete_days": [dia.isoformat() for dia in self.incomplete_days],
            "limitations": list(self.limitations),
        }


def week_report(
    days: Sequence[date],
    day_targets: Sequence[Optional[MacroTargets]],
    day_totals: Sequence[Optional[NutritionTotals]],
) -> WeekReport:
    """Orçamento, banco e aderência dos dias pedidos (a semana do diário)."""
    ingestoes = [intake_macros(total) for total in day_totals]
    registrados = tuple(dia for dia, ingestao in zip(days, ingestoes) if ingestao is not None)
    incompletos = tuple(
        dia for dia, total, ingestao in zip(days, day_totals, ingestoes) if total is not None and ingestao is None
    )
    orcamento = weekly_budget(day_targets)
    banco = bank_state(
        [None if meta is None else meta.kcal for meta in day_targets],
        [None if ingestao is None else ingestao.kcal for ingestao in ingestoes],
    )
    aderencia = weekly_adherence(day_targets, ingestoes)
    limitacoes: list[str] = []
    for codigo in (
        *orcamento.limitations,
        *banco.limitations,
        *banco.clamped_by,
        *aderencia.limitations,
        *((LIMIT_INCOMPLETE_INTAKE_DAYS,) if incompletos else ()),
    ):
        if codigo not in limitacoes:
            limitacoes.append(codigo)
    return WeekReport(
        days=tuple(days),
        budget=orcamento,
        bank=banco,
        adherence=aderencia,
        logged_days=registrados,
        incomplete_days=incompletos,
        limitations=tuple(limitacoes),
    )
