"""DONO do "Tempo total de treino" de um slot de Modelo no modo
`equipment_series` (Fase 7, Frente C; dono no backend desde o M76, Lote 4c — E7).

Quem consome:
- o preview do Modelo (`POST /workout-plan-templates/preview-activation`)
  devolve `slot_times[]` com `estimate_slot_time` de cada slot;
- o complemento determinístico de volume da geração por IA (M46), que não pode
  empurrar um slot além do limite de tempo (`limits.SLOT_TIME_LIMIT_SECONDS`)
  pela MESMA conta que o usuário vê;
- o builder (`frontend/src/lib/template-time-estimate.ts`) mantém um ESPELHO,
  porque o número muda a cada tecla enquanto o slot é editado e o preview só
  responde depois do debounce. O espelho não tem constante própria (lê
  `motor-constants.generated.json`, seção `time_estimate`) e roda contra
  `template-time-estimate.parity.json` — ambos GERADOS daqui por
  `api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`, com o resultado
  COMPLETO (total, parcelas, itens sem descanso, reps estimadas).

A conta:

    execucao(reps) = reps x (EXEC_SECONDS_PER_REP + pico de contracao)
    aquecimento    = series x (execucao(reps) + intervalo do aquecimento)
    ajuste         = series x (execucao(reps) + intervalo do ajuste)
    trabalho       = series x (execucao(reps alvo) + descanso do exercicio)
                     (medida por tempo: o tempo por serie no lugar da execucao;
                     maximo de reps com janela: a janela - Lote T2, dono
                     `domain/set_measures.planned_set_execution_seconds`)
    tecnica        = aplicacoes x duracao (rest-pause sem duracao: o padrao do
                     dono `progression_engine.REST_PAUSE_DEFAULT_PAUSE_SECONDS`)
    cardio         = tempo total da prescricao (dono
                     `domain/cardio_prescription.total_seconds`: aquecimento +
                     parte principal + recuperacao; item antigo = minutos x 60)

Decisões do usuário (Fase 7, 2026-09-28): tempo total = descanso + execução;
3 s por repetição; reps vazias/ilegíveis = 10; exercício SEM descanso definido
conta 0 s nas séries de trabalho (`REST_WHEN_UNDEFINED_SECONDS`) e entra em
`items_without_rest`, para a tela avisar "N sem descanso definido". Isso é
"descanso não definido" na ESTIMATIVA — não é o default de prescrição (60 s,
`domain/prescription_defaults.default_rest_seconds()`), que é o que a ficha
grava e o timer usa. O complemento passa o próprio valor
(`limits.COMPLEMENT_DEFAULT_REST_SECONDS`, 90 s); a fórmula é uma só.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Final, Iterable, Mapping, Optional, TypedDict

from ..domain import cardio_prescription
from ..domain.progression_engine import (
    REST_PAUSE_DEFAULT_PAUSE_SECONDS,
    REST_PAUSE_TECHNIQUE_CODE,
    technique_pause_seconds,
)
from ..domain.set_measures import planned_set_execution_seconds

# Premissas da estimativa (decisões da Fase 7).
EXEC_SECONDS_PER_REP: Final[int] = 3
DEFAULT_ESTIMATED_REPS: Final[int] = 10
# Descanso das séries de trabalho quando o item NÃO tem `rest_seconds` - a tela.
REST_WHEN_UNDEFINED_SECONDS: Final[int] = 0

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")

# Parcelas que somam o total (as chaves são as do espelho TS). A ordem só evita
# o par "aquecimento, ajuste" lado a lado, que a guarda de `set_type` lê como
# classificação de série (não é: aqui são parcelas de tempo).
_TOTAL_PARTS: Final[tuple[str, ...]] = ("work", "warmup", "technique", "feeler", "cardio")
# Execução/descanso são outro corte das mesmas parcelas (tooltip do builder).
BREAKDOWN_KEYS: Final[tuple[str, ...]] = _TOTAL_PARTS + ("exec_seconds", "rest_seconds")


class SlotTimeEstimate(TypedDict):
    total_seconds: int
    breakdown: dict[str, int]
    items_without_rest: int
    has_estimated_reps: bool


def _field(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _positive(value: Any) -> float:
    """Número finito e > 0, senão 0."""
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def parse_reps_target(value: Optional[str]) -> Optional[float]:
    """Número simples, ou a média dos DOIS primeiros números de uma faixa
    ("8-12", "8 a 12"). Vazio/ilegível -> None."""
    if value is None:
        return None
    numbers = [float(match.replace(",", ".")) for match in _NUMBER_RE.findall(str(value))]
    valid = [n for n in numbers if math.isfinite(n) and n > 0]
    if not valid:
        return None
    if len(valid) == 1:
        return valid[0]
    return (valid[0] + valid[1]) / 2


def _js_round(value: float) -> int:
    """Meio para cima (o `Math.round` do espelho), não o arredondamento
    bancário do `round()` do Python - senão 2,5 s dariam 2 aqui e 3 na tela."""
    return int(math.floor(value + 0.5))


def estimate_slot_time(
    items: Iterable[Any],
    default_rest_seconds: float = REST_WHEN_UNDEFINED_SECONDS,
    default_rest_for_item: Optional[Callable[[Any], float]] = None,
) -> SlotTimeEstimate:
    """Estimativa completa de um slot: total, parcelas (cada uma arredondada
    antes da soma), quantos exercícios de equipamento com séries de trabalho
    estão sem descanso e se alguma repetição foi estimada.

    `default_rest_seconds`: descanso das séries de trabalho quando o item NÃO
    tem `rest_seconds` (nulo). Default = o que a tela mostra (0 s).

    `default_rest_for_item` (M97 F2): o mesmo descanso, decidido POR ITEM pelo
    objetivo dele (o complemento passa `limits.complement_default_rest_seconds`);
    vence `default_rest_seconds`. Ausente = a conta de sempre."""
    parts = dict.fromkeys(BREAKDOWN_KEYS, 0.0)
    items_without_rest = 0
    has_estimated_reps = False

    for item in items:
        if _field(item, "item_kind") == "cardio":
            # M98 C0: a conta do cardio (Normal/HIIT, aquecimento e recuperação)
            # é do dono da prescrição; aqui só soma na parcela.
            parts["cardio"] += cardio_prescription.total_seconds(item)
            continue

        per_rep = EXEC_SECONDS_PER_REP + _positive(_field(item, "peak_contraction_seconds"))

        warmup_sets = _positive(_field(item, "warmup_sets_count"))
        warmup_exec = warmup_sets * _positive(_field(item, "warmup_reps")) * per_rep
        warmup_rest = warmup_sets * _positive(_field(item, "warmup_rest_seconds"))

        feeler_sets = _positive(_field(item, "feeler_set_sets_count"))
        feeler_exec = feeler_sets * _positive(_field(item, "feeler_set_reps")) * per_rep
        feeler_rest = feeler_sets * _positive(_field(item, "feeler_set_rest_seconds"))

        work_sets = _positive(_field(item, "sets_count"))
        work_exec = work_rest = 0.0
        if work_sets > 0:
            # Medida da série (Lote T2): tempo por série / janela do máximo de
            # reps vêm do dono (`set_measures.planned_set_execution_seconds`);
            # None = a conta por repetições de sempre.
            planned = planned_set_execution_seconds(
                _field(item, "measure"),
                _field(item, "duration_seconds_target"),
                _field(item, "time_window_seconds"),
            )
            if planned is not None:
                set_exec = _positive(planned)
            else:
                reps = parse_reps_target(_field(item, "reps_target"))
                if reps is None:
                    reps = DEFAULT_ESTIMATED_REPS
                    has_estimated_reps = True
                set_exec = reps * per_rep
            rest = _field(item, "rest_seconds")
            if rest is not None:
                rest_value = _positive(rest)
            elif default_rest_for_item is not None:
                rest_value = _positive(default_rest_for_item(item))
            else:
                rest_value = _positive(default_rest_seconds)
            work_exec = work_sets * set_exec
            work_rest = work_sets * rest_value
            if _field(item, "item_kind") == "equipment" and _positive(rest) == 0:
                items_without_rest += 1

        # Pausa da tecnica pelo dono (`progression_engine.technique_pause_seconds`):
        # a duracao do item ou, no rest-pause sem duracao, o padrao do dono.
        pause = _positive(_field(item, "advanced_technique_duration_seconds")) or _positive(
            technique_pause_seconds(_field(item, "advanced_technique"), None)
        )
        technique = _positive(_field(item, "advanced_technique_applications")) * pause

        parts["warmup"] += warmup_exec + warmup_rest
        parts["feeler"] += feeler_exec + feeler_rest
        parts["work"] += work_exec + work_rest
        parts["technique"] += technique
        parts["exec_seconds"] += warmup_exec + feeler_exec + work_exec
        parts["rest_seconds"] += warmup_rest + feeler_rest + work_rest + technique

    breakdown = {key: _js_round(value) for key, value in parts.items()}
    return {
        "total_seconds": sum(breakdown[key] for key in _TOTAL_PARTS),
        "breakdown": breakdown,
        "items_without_rest": items_without_rest,
        "has_estimated_reps": has_estimated_reps,
    }


def estimate_slot_seconds(
    items: Iterable[Any],
    default_rest_seconds: float = REST_WHEN_UNDEFINED_SECONDS,
    default_rest_for_item: Optional[Callable[[Any], float]] = None,
) -> int:
    """Só o total de `estimate_slot_time` (o que o complemento confere)."""
    return estimate_slot_time(items, default_rest_seconds, default_rest_for_item)["total_seconds"]


def export_for_frontend() -> dict[str, int]:
    """Seção `time_estimate` do `motor-constants.generated.json`."""
    return {
        "exec_seconds_per_rep": EXEC_SECONDS_PER_REP,
        "default_estimated_reps": DEFAULT_ESTIMATED_REPS,
        "rest_when_undefined_seconds": REST_WHEN_UNDEFINED_SECONDS,
        # Dono: `domain/progression_engine.py` (pausa padrao do rest-pause).
        "rest_pause_technique_code": REST_PAUSE_TECHNIQUE_CODE,
        "rest_pause_default_pause_seconds": REST_PAUSE_DEFAULT_PAUSE_SECONDS,
    }


__all__ = [
    "BREAKDOWN_KEYS",
    "DEFAULT_ESTIMATED_REPS",
    "EXEC_SECONDS_PER_REP",
    "REST_WHEN_UNDEFINED_SECONDS",
    "SlotTimeEstimate",
    "estimate_slot_seconds",
    "estimate_slot_time",
    "export_for_frontend",
    "parse_reps_target",
]
