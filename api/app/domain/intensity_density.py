""""Intensidade × Densidade" do Modelo de treino, por grupo muscular — dono.

Regra (só exibição no editor de Modelo; nada é salvo):

Por item de TRABALHO (séries de aquecimento e de ajuste ficam fora):

- Intensidade I ∈ [0,1] = média, série a série, dos pontos de RPE
  (`RPE_INTENSITY_POINTS`; abaixo do menor limiar → 0). A rampa "8-9-10" é
  lida por `training_history.parse_rpe_ramp`; rampa mais curta que as séries
  repete o último degrau. Técnica com `failure_requirement` que exige falha
  (`training_safety.FAILURE_REQUIREMENTS_NEEDING_FAILURE`) faz a última série
  contar `FAILURE_LAST_SET_RPE`. Sem RPE, I fica AUSENTE (não entra como neutro).
- Densidade/Volume D ∈ [0,1] = média dos subíndices que existirem: reps
  (ponto médio da faixa, `time_estimate.parse_reps_target`), pico de contração,
  descanso (curto = mais denso) e séries (o lado do volume); cada um linear
  com clamp entre os extremos nomeados abaixo.
- Posição p = (I − D + 1) / 2 (0 = Densidade/Volume, 1 = Intensidade); só um
  lado com dado: I ou 1 − D. "Equilibrado" quando |p − 0,5| < `BALANCED_BAND`.

Por grupo: média de p (e de I, D e subíndices) ponderada pelo `value` da
contribuição do preview de ativação, só nos papéis `INTENSITY_DENSITY_ROLES`.
O `value` JÁ inclui `occurrences` na janela de 7 dias
(`workout_plan_templates.py`), então não é multiplicado de novo.

O cálculo roda na TELA (o corpo do preview não leva RPE, descanso nem pico, e
o builder recalcula a cada tecla): o espelho `frontend/src/lib/intensity-density.ts`
lê as constantes de `motor-constants.generated.json` e roda contra
`intensity-density.parity.json`, ambos gerados daqui por
`api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`.
"""

from __future__ import annotations

import math
import unicodedata
from typing import Any, Final, Iterable, Mapping, Optional, Sequence

from ..periodization.time_estimate import parse_reps_target
from .set_measures import is_rep_based
from .training_history import parse_rpe_ramp
from .training_safety import FAILURE_REQUIREMENTS_NEEDING_FAILURE, FAILURE_RPE

# Pontos de intensidade por RPE, em ordem decrescente de limiar: (RPE mínimo, pontos).
RPE_INTENSITY_POINTS: Final[tuple[tuple[float, float], ...]] = (
    (10.0, 1.0),
    (9.0, 0.7),
    (8.0, 0.3),
)
# RPE que a última série conta quando a técnica exige falha.
# Dono do valor: `training_safety.FAILURE_RPE` (RPE 10 = falha concêntrica).
FAILURE_LAST_SET_RPE: Final[float] = float(FAILURE_RPE)

# Reps (ponto médio da faixa): <= min -> 0, >= max -> 1.
REPS_DENSITY_MIN: Final[int] = 6
REPS_DENSITY_MAX: Final[int] = 15
# Pico de contração em segundos: <= min -> 0, >= max -> 1.
PEAK_DENSITY_MIN_SECONDS: Final[int] = 0
PEAK_DENSITY_MAX_SECONDS: Final[int] = 5
# Descanso em segundos: >= longest -> 0, <= shortest -> 1 (curto = mais denso).
REST_DENSITY_LONGEST_SECONDS: Final[int] = 180
REST_DENSITY_SHORTEST_SECONDS: Final[int] = 45
# Séries de trabalho do exercício: <= min -> 0, >= max -> 1.
SETS_VOLUME_MIN: Final[int] = 2
SETS_VOLUME_MAX: Final[int] = 5
# |p - 0,5| abaixo disto -> "Equilibrado".
BALANCED_BAND: Final[float] = 0.1
# Meio da escala de posição (nem intensidade nem densidade).
POSITION_MIDPOINT: Final[float] = 0.5

# Papéis do preview de ativação que entram na média por grupo (vocabulário de
# `workout_plan_templates.ITEM_ROLE_*`); STABILIZER fica fora.
INTENSITY_DENSITY_ROLES: Final[tuple[str, ...]] = ("primary", "secondary")

DIRECTION_INTENSITY: Final[str] = "intensity"
DIRECTION_DENSITY: Final[str] = "density"
DIRECTION_BALANCED: Final[str] = "balanced"

SUBINDEX_KEYS: Final[tuple[str, ...]] = ("reps", "peak", "rest", "sets")


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_or_none(value: Any) -> Optional[float]:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _js_round(value: float) -> int:
    """Meio para cima, como o `Math.round` do espelho."""
    return math.floor(value + 0.5)


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def _linear(value: float, start: float, end: float) -> float:
    """Linear de `start` (-> 0) a `end` (-> 1), com clamp; aceita start > end."""
    return _clamp01((value - start) / (end - start))


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    known = [v for v in values if v is not None]
    if not known:
        return None
    total = 0.0
    for value in known:
        total += value
    return total / len(known)


def _field(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


# ---------------------------------------------------------------------------
# Item
# ---------------------------------------------------------------------------


def rpe_ramp(value: Any) -> list[float]:
    """Rampa de RPE por série (`parse_rpe_ramp`) em float."""
    return [float(rpe) for rpe in parse_rpe_ramp(value)]


def rpe_intensity_points(rpe: float) -> float:
    for min_rpe, points in RPE_INTENSITY_POINTS:
        if rpe >= min_rpe:
            return points
    return 0.0


def is_failure_technique(code: Optional[str], catalog: Iterable[Mapping[str, Any]]) -> bool:
    """`catalog`: linhas com `code` e `failure_requirement`."""
    if not code:
        return False
    for entry in catalog:
        if entry.get("code") == code:
            return entry.get("failure_requirement") in FAILURE_REQUIREMENTS_NEEDING_FAILURE
    return False


def item_intensity(item: Any, catalog: Sequence[Mapping[str, Any]]) -> Optional[float]:
    """Média dos pontos de RPE por série. Séries = `sets_count` (ou o tamanho
    da rampa, se não houver); rampa curta repete o último degrau. Técnica de
    falha -> última série = `FAILURE_LAST_SET_RPE`. Sem RPE -> None."""
    ramp = rpe_ramp(_field(item, "rpe_target"))
    if not ramp:
        return None
    sets_count = _positive_or_none(_field(item, "sets_count"))
    sets = _js_round(sets_count) if sets_count is not None else len(ramp)
    if sets <= 0:
        return None
    per_set = [ramp[min(i, len(ramp) - 1)] for i in range(sets)]
    if is_failure_technique(_field(item, "advanced_technique"), catalog):
        per_set[-1] = FAILURE_LAST_SET_RPE
    total = 0.0
    for rpe in per_set:
        total += rpe_intensity_points(rpe)
    return total / len(per_set)


def density_subindices(item: Any) -> dict[str, Optional[float]]:
    # Medida da série (Lote T2): item por tempo / máximo de reps não tem alvo
    # de repetições - o subíndice de reps fica ausente (não entra como neutro).
    reps = parse_reps_target(_field(item, "reps_target")) if is_rep_based(_field(item, "measure")) else None
    peak = _number(_field(item, "peak_contraction_seconds"))
    rest = _number(_field(item, "rest_seconds"))
    sets = _positive_or_none(_field(item, "sets_count"))
    return {
        "reps": None if reps is None else _linear(reps, REPS_DENSITY_MIN, REPS_DENSITY_MAX),
        "peak": None if peak is None else _linear(peak, PEAK_DENSITY_MIN_SECONDS, PEAK_DENSITY_MAX_SECONDS),
        "rest": None if rest is None else _linear(rest, REST_DENSITY_LONGEST_SECONDS, REST_DENSITY_SHORTEST_SECONDS),
        "sets": None if sets is None else _linear(sets, SETS_VOLUME_MIN, SETS_VOLUME_MAX),
    }


def combine_position(intensity: Optional[float], density: Optional[float]) -> Optional[float]:
    if intensity is not None and density is not None:
        return _clamp01((intensity - density + 1) / 2)
    if intensity is not None:
        return intensity
    if density is not None:
        return 1 - density
    return None


def direction_of(position: Optional[float]) -> Optional[str]:
    if position is None:
        return None
    if abs(position - POSITION_MIDPOINT) < BALANCED_BAND:
        return DIRECTION_BALANCED
    return DIRECTION_INTENSITY if position > POSITION_MIDPOINT else DIRECTION_DENSITY


def compute_item(item: Any, catalog: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    intensity = item_intensity(item, catalog)
    subindices = density_subindices(item)
    density = _mean(subindices[key] for key in SUBINDEX_KEYS)
    return {
        "intensity": intensity,
        "density": density,
        "subindices": subindices,
        "position": combine_position(intensity, density),
    }


# ---------------------------------------------------------------------------
# Grupo
# ---------------------------------------------------------------------------


class _WeightedMean:
    __slots__ = ("total", "weight")

    def __init__(self) -> None:
        self.total = 0.0
        self.weight = 0.0

    def add(self, value: Optional[float], weight: float) -> None:
        if value is None:
            return
        self.total += value * weight
        self.weight += weight

    def resolve(self) -> Optional[float]:
        return self.total / self.weight if self.weight > 0 else None


_GROUP_FIELDS: Final[tuple[str, ...]] = ("position", "intensity", "density") + SUBINDEX_KEYS


# Ordem secundária dos acentos do português no ICU (`localeCompare('pt-BR')`,
# que é quem ordena na tela): agudo < grave < circunflexo < trema < til; a
# cedilha e os demais vêm depois, pelo codepoint.
_ACCENT_SECONDARY_RANK: Final[Mapping[str, int]] = {
    "́": 1,  # agudo
    "̀": 2,  # grave
    "̂": 3,  # circunflexo
    "̈": 4,  # trema
    "̃": 5,  # til
}


def _name_collation_key(name: Any) -> tuple[Any, ...]:
    """Chave de desempate por nome que aproxima `localeCompare(..., 'pt-BR')`
    do espelho TS (o TS é quem roda na tela e não muda): 1º letras sem acento
    e sem caixa; 2º acentos, da esquerda para a direita (sem acento primeiro);
    3º caixa, minúscula antes de maiúscula. Pontuação fica pelo codepoint."""
    text = "" if name is None else str(name)
    base: list[str] = []
    accents: list[tuple[int, ...]] = []
    for char in unicodedata.normalize("NFD", text.casefold()):
        if unicodedata.combining(char):
            if accents:
                accents[-1] += (_ACCENT_SECONDARY_RANK.get(char, 100 + ord(char)),)
            continue
        base.append(char)
        accents.append(())
    return ("".join(base), tuple(accents), text.swapcase())


def aggregate_by_group(
    entries: Iterable[tuple[Any, Iterable[Mapping[str, Any]]]],
    catalog: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Linhas por grupo a partir de `(item, contribuições do preview)`.

    Cada contribuição: `muscle_group`, `muscle_group_id`, `role`, `value`. Grupo
    com peso nos papéis da regra mas sem nenhum insumo fica com posição None
    ("sem dados"). Ordem: peso decrescente, depois nome."""
    groups: dict[str, dict[str, Any]] = {}
    for item, contributions in entries:
        computed = compute_item(item, catalog)
        values = {
            "position": computed["position"],
            "intensity": computed["intensity"],
            "density": computed["density"],
            **computed["subindices"],
        }
        for contribution in contributions:
            if contribution.get("role") not in INTENSITY_DENSITY_ROLES:
                continue
            weight = _number(contribution.get("value"))
            if weight is None or not weight > 0:
                continue
            name = contribution.get("muscle_group")
            acc = groups.get(name)
            if acc is None:
                acc = {
                    "muscle_group_id": contribution.get("muscle_group_id"),
                    "muscle_group": name,
                    "weight": 0.0,
                    "means": {key: _WeightedMean() for key in _GROUP_FIELDS},
                }
                groups[name] = acc
            acc["weight"] += weight
            for key in _GROUP_FIELDS:
                acc["means"][key].add(values[key], weight)

    rows = []
    for acc in groups.values():
        means = acc["means"]
        position = means["position"].resolve()
        rows.append(
            {
                "muscle_group_id": acc["muscle_group_id"],
                "muscle_group": acc["muscle_group"],
                "weight": acc["weight"],
                "position": position,
                "intensity": means["intensity"].resolve(),
                "density": means["density"].resolve(),
                "subindices": {key: means[key].resolve() for key in SUBINDEX_KEYS},
                "direction": direction_of(position),
            }
        )
    rows.sort(key=lambda row: (-row["weight"], _name_collation_key(row["muscle_group"])))
    return rows


def export_for_frontend() -> dict[str, Any]:
    """Constantes do espelho `frontend/src/lib/intensity-density.ts`."""
    return {
        "rpe_intensity_points": [{"min_rpe": min_rpe, "points": points} for min_rpe, points in RPE_INTENSITY_POINTS],
        "failure_last_set_rpe": FAILURE_LAST_SET_RPE,
        "failure_requirements": sorted(FAILURE_REQUIREMENTS_NEEDING_FAILURE),
        "reps_density_range": {"min": REPS_DENSITY_MIN, "max": REPS_DENSITY_MAX},
        "peak_density_range": {"min": PEAK_DENSITY_MIN_SECONDS, "max": PEAK_DENSITY_MAX_SECONDS},
        "rest_density_range": {"longest": REST_DENSITY_LONGEST_SECONDS, "shortest": REST_DENSITY_SHORTEST_SECONDS},
        "sets_volume_range": {"min": SETS_VOLUME_MIN, "max": SETS_VOLUME_MAX},
        "balanced_band": BALANCED_BAND,
        "position_midpoint": POSITION_MIDPOINT,
        "roles": list(INTENSITY_DENSITY_ROLES),
    }
