"""Rotulo LEGIVEL de uma serie para os prompts de IA (M95).

Os prompts nunca recebem codigo cru (`drop_set`, `forced_rep`, `partial_reps`):
serie de tecnica e rotulada com o NOME do catalogo `advanced_techniques`
(`advanced_techniques.list_techniques`), resolvido pelo `technique_code` real
da linha (`technique_accounting.row_technique_code`; linha sem codigo cai na
derivacao pelo `set_type`, a mesma do backfill da 060). Assim "Parciais"
gravada como `forced_rep` aparece como "Parciais", nao como `forced_rep`.

Nenhuma regra mora aqui: a classificacao da serie e de `set_types` (dono do
vocabulario) e o codigo da tecnica de `domain/technique_accounting.py`. Os
textos abaixo sao so rotulos de exibicao dos `set_type` que nao sao tecnica
(sem numero) e o fallback quando o catalogo nao respondeu.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from .domain.technique_accounting import row_technique_code
from .set_types import (
    BACKOFF,
    FEELER,
    LABORAL,
    WARMUP,
    WORKING,
    is_linked_application,
    is_technique_application,
    normalize_set_type,
)

# Rotulo de exibicao dos `set_type` que nao sao aplicacao de tecnica.
SET_TYPE_DISPLAY_LABELS: dict[str, str] = {
    WARMUP: "aquecimento",
    FEELER: "série de ajuste",
    BACKOFF: "série reduzida (back-off)",
    LABORAL: "laboral (aquecimento/alongamento)",
}
# Tecnica sem nome resolvido (catalogo indisponivel ou codigo fora dele).
TECHNIQUE_FALLBACK_LABEL = "aplicação de técnica avançada"
# `set_type` fora do vocabulario conhecido: nunca vaza o valor cru.
UNKNOWN_SET_TYPE_LABEL = "série não padrão"


def technique_names(catalog_items: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """`{code: name}` a partir dos itens de `advanced_techniques.list_techniques`."""
    return {
        str(item["code"]): str(item["name"]).strip()
        for item in catalog_items or []
        if item.get("code") and item.get("name") and str(item["name"]).strip()
    }


def set_display_label(row: Mapping[str, Any], names: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Rotulo legivel da serie, ou None para serie de trabalho comum.

    Aplicacao de tecnica (por `set_type` ou `working` vinculada com
    `technique_code` + ancora) -> "técnica: <nome do catalogo>"."""
    if is_technique_application(row) or is_linked_application(row):
        code = row_technique_code(row)
        name = (names or {}).get(code) if code else None
        return f"técnica: {name}" if name else TECHNIQUE_FALLBACK_LABEL
    kind = normalize_set_type(row.get("set_type"))
    if kind == WORKING:
        return None
    return SET_TYPE_DISPLAY_LABELS.get(kind, UNKNOWN_SET_TYPE_LABEL)
