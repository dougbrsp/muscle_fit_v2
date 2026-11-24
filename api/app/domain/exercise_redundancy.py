"""Redundância entre dois exercícios+equipamento (issue #288, lote 45). Dono único da comparação.

Telas, SQL, prompts e RAG só consomem `compare`/`to_dict`; nenhuma regra mora fora daqui. Python puro, sem I/O,
sem escore opaco: o nível sai de regras auditáveis e cada decisão devolve motivos, componentes e limitações.
Reutiliza (não recalcula): perfil de resistência de `resistance_profile` e contribuição muscular no formato do
`muscle_contribution_resolver` (`[{muscle_group_id, role, ...}]`; aqui já reduzida a grupo -> papel).

Regras, em ordem (a primeira que casa decide):
1. mesmo `exercise_id` e mesmo `equipment_id` (inclusive os dois nulos) -> `near_duplicate`, sempre.
2. sem contribuição muscular, sem grupo primário ou sem `biomechanical_pattern` em um dos lados -> `unknown`
   (dado insuficiente nunca vira `low`).
3. nenhum grupo primário em comum -> `low` (compartilhar só músculo secundário, ou nenhum, não é redundância;
   o motivo diz qual).
4. mesmo grupo primário mas padrão biomecânico ou articulação principal diferentes -> `low`.
5. mesmo grupo primário + padrão + articulação:
   - sem perfil de resistência CONFIÁVEL (`resistance_profile.is_reliable`, revisado/verificado) nos DOIS lados
     -> no máximo `low` (decisão do usuário, 24/11/2026): perfil sugerido, ausente ou inválido nunca eleva;
   - com perfis confiáveis nos dois: `high` se todos os componentes da assinatura são conhecidos e iguais e
     movimento, posição, trajetória e lateralidade do exercício não divergem; senão `moderate`.
   `moderate`/`high` só existem com perfis confiáveis nos dois lados.
Ordem dos níveis (`LEVEL_ORDER`, `level_rank`, `at_least`) e piso de exibição (`DISPLAY_MIN_LEVEL`) moram aqui:
rota e tela não decidem ordem nem piso.
Confiança: perfis confiáveis nos dois lados -> a menor confiança entre eles; caso contrário `low`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Final, Iterable, Mapping, Optional

from . import resistance_profile as rp

REDUNDANCY_VERSION: Final = "redundancy-v1"

UNKNOWN: Final = "unknown"
LOW: Final = "low"
MODERATE: Final = "moderate"
HIGH: Final = "high"
NEAR_DUPLICATE: Final = "near_duplicate"
LEVELS: Final[dict[str, str]] = {
    UNKNOWN: "Desconhecida",
    LOW: "Baixa",
    MODERATE: "Moderada",
    HIGH: "Alta",
    NEAR_DUPLICATE: "Quase duplicado",
}

# Do menos ao mais redundante.
LEVEL_ORDER: Final = (UNKNOWN, LOW, MODERATE, HIGH, NEAR_DUPLICATE)
DISPLAY_MIN_LEVEL: Final = MODERATE  # piso padrão de exibição do aviso


def level_rank(level: str) -> int:
    return LEVEL_ORDER.index(level)


def at_least(level: str, min_level: str) -> bool:
    return level_rank(level) >= level_rank(min_level)


# Nível a partir do qual a geração penaliza o candidato. HIGH exige a mesma assinatura de resistência confiável (par
# revisado), então não pune só por dado ausente nem por complementaridade; NEAR_DUPLICATE já entra por ser mais alto.
GENERATION_PENALTY_MIN_LEVEL: Final = HIGH


ROLE_PRIMARY: Final = "primary"

REASONS: Final[dict[str, str]] = {
    "same_exercise_equipment": "Mesmo exercício no mesmo equipamento",
    "no_contribution": "Sem contribuição muscular cadastrada",
    "no_primary_group": "Sem grupo muscular primário",
    "no_pattern": "Sem padrão biomecânico",
    "no_shared_muscle": "Não compartilham músculo",
    "shared_secondary_only": "Compartilham só músculo secundário",
    "different_primary_group": "Grupos primários diferentes",
    "different_pattern": "Padrão biomecânico diferente",
    "different_joint": "Articulação principal diferente",
    "same_primary_pattern_joint": "Mesmo grupo primário, padrão e articulação",
    "same_resistance_signature": "Mesma assinatura de resistência confiável",
    "resistance_differs": "Perfil de resistência diferente (complementar)",
    "exercise_traits_differ": "Movimento, posição, trajetória ou lateralidade diferentes",
    "no_reliable_profile": "Sem perfil de resistência revisado em um dos lados",
    "profile_suggested_ignored": "Perfil apenas sugerido não conta para o nível",
    "profile_incomplete": "Perfil de resistência com componentes desconhecidos",
}

_PATTERN: Final = "biomechanical_pattern"
_JOINT: Final = "primary_joint"
_CORE_LABELS: Final[dict[str, str]] = {_PATTERN: "Padrão biomecânico", _JOINT: "Articulação principal"}
_TRAIT_LABELS: Final[dict[str, str]] = {
    "movement": "Movimento",
    "body_position": "Posição do corpo",
    "trajectory": "Trajetória",
    "unilateral": "Lateralidade (unilateral)",
}
_RESISTANCE_LABELS: Final[dict[str, str]] = {
    "source": "Resistência: fonte",
    "mechanism": "Resistência: mecanismo",
    "trajectory": "Resistência: trajetória",
    "profile": "Resistência: perfil",
    "line": "Resistência: linha",
}
_CONFIDENCE_ORDER: Final = ("low", "medium", "high")
_LIMITED_CONFIDENCE: Final = "low"
_NO_DATA: Final = "Sem dado: "


@dataclass(frozen=True)
class Candidate:
    exercise_id: Optional[int] = None
    equipment_id: Optional[int] = None
    contributions: Mapping[Any, str] = field(default_factory=dict)  # grupo -> papel
    biomechanical_pattern: Optional[str] = None
    primary_joint: Optional[str] = None
    movement: Optional[str] = None
    body_position: Optional[str] = None
    trajectory: Optional[str] = None
    unilateral: Optional[bool] = None
    resistance: Optional[Mapping[str, Any]] = None  # componentes + status do par, ou None


@dataclass(frozen=True)
class RedundancyResult:
    level: str
    confidence: str
    reasons: tuple[str, ...]
    matching: tuple[str, ...]
    diverging: tuple[str, ...]
    limitations: tuple[str, ...]
    version: str = REDUNDANCY_VERSION


_CANDIDATE_FIELDS: Final = frozenset(f.name for f in fields(Candidate))


def _cand(x: Candidate | Mapping[str, Any]) -> Candidate:
    if isinstance(x, Candidate):
        return x
    return Candidate(**{k: v for k, v in x.items() if k in _CANDIDATE_FIELDS})


def _primary_groups(c: Candidate) -> frozenset:
    return frozenset(g for g, role in c.contributions.items() if str(role).lower() == ROLE_PRIMARY)


def _profile(c: Candidate) -> Optional[rp.ValidatedProfile]:
    if c.resistance is None:
        return None
    try:
        return rp.validate_profile(c.resistance)
    except rp.ResistanceProfileError:
        return None  # perfil inválido não é evidência


def _res(level, confidence, reasons, matching=(), diverging=(), limitations=()) -> RedundancyResult:
    return RedundancyResult(level, confidence, tuple(reasons), tuple(matching), tuple(diverging), tuple(limitations))


def _blank(v: Any) -> bool:
    return v is None or v == ""


def compare(a: Candidate | Mapping[str, Any], b: Candidate | Mapping[str, Any]) -> RedundancyResult:
    a, b = _cand(a), _cand(b)

    if a.exercise_id is not None and (a.exercise_id, a.equipment_id) == (b.exercise_id, b.equipment_id):
        return _res(NEAR_DUPLICATE, "high", ["same_exercise_equipment"])

    pa, pb = _primary_groups(a), _primary_groups(b)
    missing = []
    if not a.contributions or not b.contributions:
        missing.append("no_contribution")
    elif not pa or not pb:
        missing.append("no_primary_group")
    if _blank(a.biomechanical_pattern) or _blank(b.biomechanical_pattern):
        missing.append("no_pattern")
    if missing:
        return _res(UNKNOWN, _LIMITED_CONFIDENCE, missing, limitations=missing)

    if not (pa & pb):
        shared_any = set(a.contributions) & set(b.contributions)
        reason = "shared_secondary_only" if shared_any else "no_shared_muscle"
        return _res(LOW, "medium", [reason, "different_primary_group"])

    matching: list[str] = ["Grupo muscular primário"]
    diverging: list[str] = []
    limitations: list[str] = []
    for key, label in _CORE_LABELS.items():
        va, vb = getattr(a, key), getattr(b, key)
        if _blank(va) or _blank(vb):
            limitations.append(_NO_DATA + label)
        elif va == vb:
            matching.append(label)
        else:
            diverging.append(label)
    if diverging:
        reason = "different_pattern" if _CORE_LABELS[_PATTERN] in diverging else "different_joint"
        return _res(LOW, "medium", [reason], matching, diverging, limitations)

    traits_differ = False
    for key, label in _TRAIT_LABELS.items():
        va, vb = getattr(a, key), getattr(b, key)
        if _blank(va) or _blank(vb):
            limitations.append(_NO_DATA + label)
        elif va == vb:
            matching.append(label)
        else:
            diverging.append(label)
            traits_differ = True

    reasons = ["same_primary_pattern_joint"]
    prof_a, prof_b = _profile(a), _profile(b)
    reliable = all(p is not None and rp.is_reliable(p.status) and not rp.is_unknown(p) for p in (prof_a, prof_b))
    if not reliable:
        reasons.append("no_reliable_profile")
        if any(p is not None and p.status == rp.STATUS_SUGGESTED for p in (prof_a, prof_b)):
            reasons.append("profile_suggested_ignored")
        if traits_differ:
            reasons.append("exercise_traits_differ")
        limitations.append(REASONS["no_reliable_profile"])
        return _res(LOW, _LIMITED_CONFIDENCE, reasons, matching, diverging, limitations)

    incomplete = resistance_differs = False
    for comp, label in _RESISTANCE_LABELS.items():
        va, vb = getattr(prof_a, comp), getattr(prof_b, comp)
        if rp.UNKNOWN in (va, vb):
            incomplete = True
            limitations.append(_NO_DATA + label)
        elif va == vb:
            matching.append(label)
        else:
            diverging.append(label)
            resistance_differs = True
    confidence = min((prof_a.confidence, prof_b.confidence), key=_CONFIDENCE_ORDER.index)

    if resistance_differs:
        reasons.append("resistance_differs")
    if traits_differ:
        reasons.append("exercise_traits_differ")
    if incomplete:
        reasons.append("profile_incomplete")
    if resistance_differs or traits_differ or incomplete:
        return _res(MODERATE, confidence, reasons, matching, diverging, limitations)
    reasons.append("same_resistance_signature")
    return _res(HIGH, confidence, reasons, matching, diverging, limitations)


def max_level_against(
    candidate: Candidate | Mapping[str, Any], chosen: Iterable[Candidate | Mapping[str, Any]]
) -> Optional[tuple[str, Candidate | Mapping[str, Any], RedundancyResult]]:
    """O mais redundante de `chosen` contra `candidate`: (nível, outro, resultado); None se `chosen` vazio.
    Empate: vence o primeiro da lista."""
    best = None
    for other in chosen:
        result = compare(candidate, other)
        if best is None or level_rank(result.level) > level_rank(best[2].level):
            best = (result.level, other, result)
    return best


def to_dict(r: RedundancyResult) -> dict[str, Any]:
    return {
        "version": r.version,
        "level": r.level,
        "level_label": LEVELS[r.level],
        "confidence": r.confidence,
        "reasons": [{"code": c, "label": REASONS.get(c, c)} for c in r.reasons],
        "matching": list(r.matching),
        "diverging": list(r.diverging),
        "limitations": [REASONS.get(x, x) for x in r.limitations],
    }
