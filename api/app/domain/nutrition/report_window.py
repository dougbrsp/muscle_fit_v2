"""Nutrição no relatório Markdown (`GET /reports/markdown`; mapa `docs/motores/FONTES_DA_VERDADE.md` §3.7 e §91).

Dono do RESUMO da janela do relatório: médias de meta × consumido por nutriente, gasto
(TDEE das metas gravadas, que já inclui treino e atividade extra conforme a base de cada
dia), balanço energético e o quadro por semana (segunda a domingo). O relatório só lê o
resultado; nenhuma conta de nutrição mora em `intelligence/report`.

Não cria regra de nutrição: compõe os donos.
- estado do dia (antes do plano, hoje aberto, sem registro, incompleto, registrado):
  `dashboard.day_states`; só o dia REGISTRADO (fechado e com total completo) entra nas médias,
  como na aderência do dashboard;
- meta × ingestão e balanço energético do dia (ingestão − gasto): `weekly_budget.day_balance`;
- semana: `weekly_budget.week_start`.

Médias sobre os dias REGISTRADOS. A meta e o gasto são médias dos dias registrados que têm
meta e gasto gravados (dia sem meta fica de fora da média de meta, não vira zero). O balanço
total é a soma dos balanços diários (negativo = déficit). Sem dado, o campo é `None`, nunca zero.

Módulo PURO: sem banco, sem FastAPI, sem I/O, sem relógio.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final, Mapping, Optional, Sequence

from .dashboard import DayState, day_states
from .energy_density import DAYS_PER_WEEK
from .macros import MacroTargets
from .meal_math import CARB_FIELD, ENERGY_FIELD, FAT_FIELD, FIBER_FIELD, PROTEIN_FIELD, NutritionTotals
from .weekly_budget import day_balance, week_start

# Nutrientes do quadro, na ordem do relatório: código do diário -> chave pública.
REPORT_NUTRIENTS: Final[tuple[tuple[str, str], ...]] = (
    (ENERGY_FIELD, "kcal"),
    (PROTEIN_FIELD, "protein_g"),
    (CARB_FIELD, "carbs_g"),
    (FAT_FIELD, "fat_g"),
    (FIBER_FIELD, "fiber_g"),
)


@dataclass(frozen=True)
class ReportDay:
    """Um dia REGISTRADO: meta e ingestão por nutriente, gasto, base do gasto e balanço."""

    local_date: date
    target: Mapping[str, Optional[float]]
    intake: Mapping[str, Optional[float]]
    tdee_kcal: Optional[float]
    tdee_basis: Optional[str]
    energy_balance_kcal: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_date": self.local_date,
            "target": dict(self.target),
            "intake": dict(self.intake),
            "tdee_kcal": self.tdee_kcal,
            "tdee_basis": self.tdee_basis,
            "energy_balance_kcal": self.energy_balance_kcal,
        }


def _mean(values: Sequence[Optional[float]]) -> Optional[float]:
    presentes = [v for v in values if v is not None]
    return sum(presentes) / len(presentes) if presentes else None


def _stats(rows: Sequence[ReportDay]) -> dict[str, Any]:
    """Médias de meta e consumo por nutriente, gasto médio e balanço (média, total e dias) dos dias dados."""
    balancos = [r.energy_balance_kcal for r in rows if r.energy_balance_kcal is not None]
    bases: dict[str, int] = {}
    for linha in rows:
        if linha.tdee_basis is not None and linha.tdee_kcal is not None:
            bases[linha.tdee_basis] = bases.get(linha.tdee_basis, 0) + 1
    return {
        "logged_days": len(rows),
        "nutrients": {
            chave: {
                "target_avg": _mean([r.target.get(chave) for r in rows]),
                "intake_avg": _mean([r.intake.get(chave) for r in rows]),
            }
            for _, chave in REPORT_NUTRIENTS
        },
        "tdee_avg_kcal": _mean([r.tdee_kcal for r in rows]),
        "tdee_basis_days": bases,
        "balance_days": len(balancos),
        "balance_avg_kcal": _mean(balancos),
        "balance_total_kcal": sum(balancos) if balancos else None,
    }


def _report_day(
    dia: date,
    meta: Optional[MacroTargets],
    total: Optional[NutritionTotals],
    tdee_kcal: Any,
    basis: Optional[str],
) -> ReportDay:
    balanco = day_balance(meta, total, tdee_kcal)
    linhas = {linha.field: linha for linha in balanco.rows}
    return ReportDay(
        local_date=dia,
        target={chave: linhas[campo].target for campo, chave in REPORT_NUTRIENTS},
        intake={
            chave: linhas[campo].intake if linhas[campo].intake_complete else None for campo, chave in REPORT_NUTRIENTS
        },
        tdee_kcal=balanco.tdee_kcal,
        tdee_basis=basis,
        energy_balance_kcal=balanco.energy_balance_kcal,
    )


def window_report(
    days: Sequence[date],
    today: date,
    plan_start: Optional[date],
    day_targets: Sequence[Optional[MacroTargets]],
    day_totals: Sequence[Optional[NutritionTotals]],
    day_tdee_kcal: Sequence[Any],
    day_tdee_basis: Sequence[Optional[str]],
) -> dict[str, Any]:
    """Resumo da janela, quadro por semana e linhas dos dias registrados. As listas vêm alinhadas a `days`.

    `has_records`: há alguma refeição na janela (a seção do relatório só diz "sem registros" quando não há)."""
    estados = day_states(days, today, plan_start, day_totals)
    registrados: list[tuple[int, ReportDay]] = [
        (i, _report_day(days[i], day_targets[i], day_totals[i], day_tdee_kcal[i], day_tdee_basis[i]))
        for i, estado in enumerate(estados)
        if estado == DayState.LOGGED
    ]
    linhas = [r for _, r in registrados]

    semanas: list[dict[str, Any]] = []
    inicios = sorted({week_start(d) for d in days})
    for inicio in inicios:
        na_semana = [i for i, d in enumerate(days) if week_start(d) == inicio]
        da_semana = [r for i, r in registrados if i in na_semana]
        if not da_semana and not any(estados[i] != DayState.OUT_OF_PLAN for i in na_semana):
            continue  # semana inteira antes do plano: fora da conta
        semanas.append(
            {
                "start": inicio,
                "end": inicio + timedelta(days=DAYS_PER_WEEK - 1),
                "partial": len(na_semana) < DAYS_PER_WEEK,
                "days_in_window": len(na_semana),
                **_stats(da_semana),
            }
        )

    return {
        "window_days": len(days),
        "has_records": any(t is not None for t in day_totals),
        "counted_days": sum(1 for e in estados if e in (DayState.NO_RECORD, DayState.INCOMPLETE, DayState.LOGGED)),
        "logged_days": len(linhas),
        "no_record_days": sum(1 for e in estados if e == DayState.NO_RECORD),
        "incomplete_days": sum(1 for e in estados if e == DayState.INCOMPLETE),
        "open_today": any(e == DayState.OPEN_TODAY for e in estados),
        "summary": _stats(linhas),
        "weeks": semanas,
        "days": [r.to_dict() for r in linhas],
    }


__all__ = ["REPORT_NUTRIENTS", "ReportDay", "window_report"]
