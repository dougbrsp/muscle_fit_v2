"""Categorias da avaliação de série por IA no vocabulário do MOTOR (RPE 10
Lote 6/P9, "uma voz no Modo Treino", 2026-10-11).

Antes a IA por série respondia num vocabulário próprio (PROGREDIR / MANTER /
APRIMORAR TECNICA / REDUZIR / REGREDIR), ao lado da decisão do motor
("Manter", "Aumentar carga") e do desempenho ("Progredindo"/"Regredindo") na
mesma tela. Agora a categoria da IA é uma AÇÃO do motor para a PRÓXIMA série
da sessão atual, com o mesmo código e o mesmo rótulo de
`exercise_briefing.DECISION_LABELS`.

Subconjunto aplicável a UMA série (decisão documentada no mapa):

- `HOLD` ("Manter"), `INCREASE_LOAD` ("Aumentar carga"), `DECREASE_LOAD`
  ("Reduzir carga"): é o que se ajusta de uma série para a outra.
- Fora: `ADD_SET`, `APPLY_TECHNIQUE`, `SET_REP_RANGE_10_12` e `SKIP` são
  decisões de SESSÃO/fase do motor (séries da ficha, técnica, faixa de reps,
  recuperação) que a IA não toma no meio do treino. "Aprimorar técnica" não
  existe no motor e não vira categoria; técnica ruim é dita na nota.

Coerência com a carga (regra deste dono): a categoria é uma direção de CARGA.
Quando a sugestão final (já no passo e na direção do motor,
`engine_decision_context.bound_ai_load`) e a carga executada são conhecidas,
a direção entre as duas decide a categoria - "Aumentar carga" com a carga
sugerida igual à executada seria a IA contradizendo o número que a tela
aplica. Sem carga sugerida, vale a categoria da IA.

O frontend recebe códigos, rótulos e direção por
`motor-constants.generated.json` (bloco `set_evaluation`, gerado por
`api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`).

Python puro, sem I/O.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Final, Mapping, Optional

from .exercise_briefing import DECISION_LABELS
from .progression_engine import DECREASE_LOAD, HOLD, INCREASE_LOAD

# Ordem do enum do schema (e da lista no prompt).
SET_EVALUATION_CATEGORIES: Final[tuple[str, ...]] = (HOLD, INCREASE_LOAD, DECREASE_LOAD)

SET_EVALUATION_CATEGORY_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {code: DECISION_LABELS[code] for code in SET_EVALUATION_CATEGORIES}
)

# Direção de carga de cada categoria (a tela escolhe a cor por ela).
DIRECTION_UP = "UP"
DIRECTION_HOLD = "HOLD"
DIRECTION_DOWN = "DOWN"
SET_EVALUATION_CATEGORY_DIRECTIONS: Final[Mapping[str, str]] = MappingProxyType(
    {HOLD: DIRECTION_HOLD, INCREASE_LOAD: DIRECTION_UP, DECREASE_LOAD: DIRECTION_DOWN}
)

# Motivo gravado quando a categoria da IA é trocada pela direção da carga.
REASON_LOAD_DIRECTION = "LOAD_DIRECTION"


def category_label(code: Optional[str]) -> Optional[str]:
    return SET_EVALUATION_CATEGORY_LABELS.get(code) if code else None


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def category_for_load(category: str, suggested_load_kg: Any, executed_load_kg: Any) -> str:
    """Categoria coerente com a carga sugerida FINAL x a carga executada.

    Sem as duas cargas (ou carga executada <= 0): a categoria da IA."""
    suggested = _decimal(suggested_load_kg)
    executed = _decimal(executed_load_kg)
    if suggested is None or executed is None or executed <= 0:
        return category
    if suggested > executed:
        return INCREASE_LOAD
    if suggested < executed:
        return DECREASE_LOAD
    return HOLD


def category_rule_text(executed_load_text: str) -> str:
    """A regra de `category_for_load` em texto, para o prompt da IA por série
    (set-eval-v5): a categoria é a direção da carga sugerida x a executada, e a
    nota diz essa mesma direção com o rótulo da categoria. Gerado dos códigos e
    rótulos deste dono - nunca escrito à mão no prompt."""
    hold = f'{HOLD} ("{SET_EVALUATION_CATEGORY_LABELS[HOLD]}")'
    up = f'{INCREASE_LOAD} ("{SET_EVALUATION_CATEGORY_LABELS[INCREASE_LOAD]}")'
    down = f'{DECREASE_LOAD} ("{SET_EVALUATION_CATEGORY_LABELS[DECREASE_LOAD]}")'
    return (
        "A categoria é a DIREÇÃO da carga que você sugerir em next.load_kg em relação à carga EXECUTADA nesta "
        f"série ({executed_load_text}): maior → {up}; igual → {hold}; menor → {down}. O servidor reclassifica a "
        "categoria por essa regra. A nota tem de dizer essa mesma direção, com o rótulo da categoria: se a carga "
        "sugerida for diferente da executada, nunca diga para manter ou repetir a carga."
    )


def export_for_frontend() -> dict[str, Any]:
    """Bloco `set_evaluation` de `motor-constants.generated.json`."""
    return {
        "categories": list(SET_EVALUATION_CATEGORIES),
        "category_labels": dict(SET_EVALUATION_CATEGORY_LABELS),
        "category_directions": dict(SET_EVALUATION_CATEGORY_DIRECTIONS),
    }
