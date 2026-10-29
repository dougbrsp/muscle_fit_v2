"""Reps e RPE por lado em exercício unilateral (M102) — dono único.

Decisão do usuário (2026-10-27): uma linha por série, com colunas por lado
(`exercise_sets.reps_left/reps_right/rpe_left/rpe_right`, migração 100). A
carga continua única e TOTAL em `weight_kg` (D12).

Regra: os valores OFICIAIS da série (`reps`/`rpe`, os que todo motor lê) são os
do **pior lado** — menos repetições e maior RPE. Lado vazio é ignorado; sem
nenhum lado informado, o valor enviado vale como sempre. Consequência aceita:
contagem de séries, qualificação, queda de reps, e1RM, recorde, carga da
próxima série e tonelagem usam o lado fraco, e a progressão o segue.

Quem grava (POST/PATCH de série do Modo Treino, `PUT /exercises/logs/{id}`,
`upsert_activity`) chama `apply_canonical`; ninguém mais calcula min/max dos
lados (guarda `pior_lado`). O frontend mostra o canônico devolvido pela API.

Módulo puro: sem banco, sem FastAPI.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, MutableMapping, Optional

REPS_SIDE_FIELDS: tuple[str, str] = ("reps_left", "reps_right")
RPE_SIDE_FIELDS: tuple[str, str] = ("rpe_left", "rpe_right")
SIDE_FIELDS: tuple[str, ...] = (*REPS_SIDE_FIELDS, *RPE_SIDE_FIELDS)

# Rótulos curtos dos lados ("E 10 · D 8") usados nas telas, no relatório e
# nos prompts.
LEFT_LABEL = "E"
RIGHT_LABEL = "D"
SIDES_SEPARATOR = " · "

# Frase da metodologia (relatório Markdown e prompts): interpolada, nunca
# reescrita à mão.
WORST_SIDE_RULE_TEXT = (
    "Em exercício unilateral com lados registrados, reps e RPE oficiais da série são os do pior lado "
    "(menos repetições, maior RPE); a carga é a total registrada e a tonelagem usa as reps do lado fraco."
)


def canonical_reps(reps_left: Optional[int], reps_right: Optional[int]) -> Optional[int]:
    """Reps oficiais pelos lados: o MENOR. `None` sem nenhum lado."""
    return min((v for v in (reps_left, reps_right) if v is not None), default=None)


def canonical_rpe(rpe_left: Any, rpe_right: Any) -> Any:
    """RPE oficial pelos lados: o MAIOR. `None` sem nenhum lado."""
    return max((v for v in (rpe_left, rpe_right) if v is not None), default=None)


def canonical_from_sides(
    reps: Optional[int],
    rpe: Any,
    *,
    reps_left: Optional[int] = None,
    reps_right: Optional[int] = None,
    rpe_left: Any = None,
    rpe_right: Any = None,
) -> tuple[Optional[int], Any]:
    """(reps, rpe) oficiais. Cada medida é independente: com algum lado
    informado, vale o pior lado; sem lado, vale o valor enviado."""
    side_reps = canonical_reps(reps_left, reps_right)
    side_rpe = canonical_rpe(rpe_left, rpe_right)
    return (
        side_reps if side_reps is not None else reps,
        side_rpe if side_rpe is not None else rpe,
    )


def has_sides(row: Any) -> bool:
    """A série tem algum lado registrado (dict ou objeto)."""
    return any(_field(row, name) is not None for name in SIDE_FIELDS)


def apply_canonical(values: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Recalcula `reps`/`rpe` de um dict de série a partir dos lados presentes.
    Muta e devolve `values`. Sem lado, nada muda."""
    if not has_sides(values):
        return values
    values["reps"], values["rpe"] = canonical_from_sides(
        values.get("reps"),
        values.get("rpe"),
        reps_left=values.get("reps_left"),
        reps_right=values.get("reps_right"),
        rpe_left=values.get("rpe_left"),
        rpe_right=values.get("rpe_right"),
    )
    return values


def _field(row: Any, name: str) -> Any:
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)


def _number_text(value: Any) -> str:
    if isinstance(value, (Decimal, float)):
        text = f"{Decimal(str(value)).normalize():f}"
        return text.replace(".", ",")
    return str(value)


def format_sides(left: Any, right: Any) -> Optional[str]:
    """"E 10 · D 8"; um lado só -> "E 10"; nenhum -> None."""
    parts = []
    if left is not None:
        parts.append(f"{LEFT_LABEL} {_number_text(left)}")
    if right is not None:
        parts.append(f"{RIGHT_LABEL} {_number_text(right)}")
    return SIDES_SEPARATOR.join(parts) if parts else None


def sides_text(row: Any) -> Optional[str]:
    """Texto dos lados de uma série para tela/prompt: "reps E 10 · D 8; RPE
    E 8 · D 9". `None` quando a série não tem lados."""
    reps = format_sides(_field(row, "reps_left"), _field(row, "reps_right"))
    rpe = format_sides(_field(row, "rpe_left"), _field(row, "rpe_right"))
    parts = []
    if reps:
        parts.append(f"reps {reps}")
    if rpe:
        parts.append(f"RPE {rpe}")
    return "; ".join(parts) if parts else None


def attach_sides_text(row: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """`sides_text` pronto numa série lida do banco (tela não formata)."""
    row["sides_text"] = sides_text(row)
    return row


def apply_canonical_to_model(model: Any) -> Any:
    """Mesmo que `apply_canonical`, num modelo Pydantic de série: só reescreve
    a medida (reps ou RPE) que tem algum lado, para que a outra continue
    "não enviada" (`model_dump(exclude_unset=True)` da edição)."""
    if not has_sides(model):
        return model
    reps, rpe = canonical_from_sides(
        model.reps,
        model.rpe,
        reps_left=model.reps_left,
        reps_right=model.reps_right,
        rpe_left=model.rpe_left,
        rpe_right=model.rpe_right,
    )
    if model.reps_left is not None or model.reps_right is not None:
        model.reps = reps
    if model.rpe_left is not None or model.rpe_right is not None:
        model.rpe = rpe
    return model


# Sufixo da versão dos prompts de IA (resumo de exercício, resumo de treino,
# avaliação por série) quando a entrada traz lados: o texto só muda nesse
# ramo; sem lados, versão e texto de sempre (mesma convenção de `+goal-v1`).
PROMPT_VERSION_SUFFIX = "+sides-v1"


def prompt_version_with_sides(version: str, has_any_sides: bool) -> str:
    return f"{version}{PROMPT_VERSION_SUFFIX}" if has_any_sides else version
