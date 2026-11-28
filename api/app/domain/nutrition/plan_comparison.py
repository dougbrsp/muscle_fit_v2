"""Comparação plano vigente × rascunho (#344), Python puro.

Recebe os dois payloads da prévia (`POST /nutrition/plans/{id}/preview`, mesmo `today` e mesma semana) e devolve,
para a meta de hoje e para cada `local_date` presente nos DOIS lados, os valores de cada lado, a diferença absoluta
(rascunho − vigente) e a percentual sobre o vigente, o contexto do dia de cada lado e a flag `differs`. Datas de um
lado só ficam listadas como daquele lado (nunca pareadas com outra data). Também decide o status da comparação.

Nenhum cálculo de meta aqui: os valores vêm prontos do motor (`daily_target` via a prévia)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Optional

COMPARED_FIELDS: tuple[str, ...] = ("kcal", "protein_g", "carbs_g", "fat_g")
CONTEXT_FIELDS: tuple[str, ...] = ("day_kind", "is_training_day", "carb_cycle_policy")
DIFF_DECIMALS = 1  # diferença absoluta (kcal e gramas)
PERCENT_DECIMALS = 1  # diferença percentual (pontos de %)

# Motivos do status `partial` (códigos; o lado vem no prefixo).
REASON_CURRENT_FAILED = "current_failed"  # + ":<código do erro>"
REASON_DRAFT_FAILED = "draft_failed"
REASON_CURRENT_BLOCKING = "current_blocking"
REASON_DRAFT_BLOCKING = "draft_blocking"
REASON_REFERENCE_MISMATCH = "reference_date_mismatch"


class ComparisonStatus(str, Enum):
    OK = "ok"
    NO_CURRENT = "no_current"
    SAME_PLAN = "same_plan"
    PARTIAL = "partial"


def _round(value: float, decimals: int) -> float:
    return round(float(value), decimals) + 0.0  # + 0.0 tira o -0.0


def compare_value(current: Optional[float], draft: Optional[float]) -> dict[str, Any]:
    """`diff` = rascunho − vigente; `diff_percent` sobre o vigente, `None` com base 0 ou ausente."""
    if current is None or draft is None:
        return {"current": current, "draft": draft, "diff": None, "diff_percent": None}
    diff = float(draft) - float(current)
    percent = None if float(current) == 0 else _round(diff / float(current) * 100, PERCENT_DECIMALS)
    return {"current": current, "draft": draft, "diff": _round(diff, DIFF_DECIMALS), "diff_percent": percent}


def compare_day(current: Mapping[str, Any], draft: Mapping[str, Any]) -> dict[str, Any]:
    valores = {campo: compare_value(current.get(campo), draft.get(campo)) for campo in COMPARED_FIELDS}
    ctx_atual = {campo: current.get(campo) for campo in CONTEXT_FIELDS}
    ctx_rascunho = {campo: draft.get(campo) for campo in CONTEXT_FIELDS}
    differs = ctx_atual != ctx_rascunho or any(
        v["diff"] != 0 if v["diff"] is not None else v["current"] != v["draft"] for v in valores.values()
    )
    return {
        "local_date": draft.get("local_date"),
        "values": valores,
        "current_context": ctx_atual,
        "draft_context": ctx_rascunho,
        "differs": differs,
    }


def compare_previews(current: Mapping[str, Any], draft: Mapping[str, Any]) -> dict[str, Any]:
    """Compara meta de hoje (`target`) e a semana (`week`) das duas prévias. Pareia só pela mesma `local_date`."""
    dias_atual = {d["local_date"]: d for d in current.get("week") or ()}
    dias_rascunho = {d["local_date"]: d for d in draft.get("week") or ()}
    comuns = sorted(dias_atual.keys() & dias_rascunho.keys())
    return {
        "today": draft.get("today"),
        "target": compare_day(current["target"], draft["target"]),
        "days": [compare_day(dias_atual[dia], dias_rascunho[dia]) for dia in comuns],
        "only_current": sorted(dias_atual.keys() - dias_rascunho.keys()),
        "only_draft": sorted(dias_rascunho.keys() - dias_atual.keys()),
    }


def build_comparison(
    current: Optional[Mapping[str, Any]],
    draft: Optional[Mapping[str, Any]],
    *,
    has_current: bool,
    same_plan: bool = False,
    current_error: Optional[str] = None,
    draft_error: Optional[str] = None,
) -> dict[str, Any]:
    """Status + motivos + comparação. `*_error` = código do erro do lado que falhou (o payload dele vem `None`)."""
    if not has_current:
        return {"status": ComparisonStatus.NO_CURRENT.value, "reasons": [], "comparison": None}
    if same_plan:
        return {"status": ComparisonStatus.SAME_PLAN.value, "reasons": [], "comparison": None}
    motivos: list[str] = []
    if current is None:
        motivos.append(f"{REASON_CURRENT_FAILED}:{current_error}" if current_error else REASON_CURRENT_FAILED)
    elif current.get("blocking"):
        motivos.append(REASON_CURRENT_BLOCKING)
    if draft is None:
        motivos.append(f"{REASON_DRAFT_FAILED}:{draft_error}" if draft_error else REASON_DRAFT_FAILED)
    elif draft.get("blocking"):
        motivos.append(REASON_DRAFT_BLOCKING)
    comparacao = None
    if current is not None and draft is not None:
        if current.get("today") != draft.get("today"):
            motivos.append(REASON_REFERENCE_MISMATCH)
        else:
            comparacao = compare_previews(current, draft)
    status = ComparisonStatus.PARTIAL if motivos else ComparisonStatus.OK
    return {"status": status.value, "reasons": motivos, "comparison": comparacao}

