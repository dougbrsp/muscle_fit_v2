"""Perfil de resistência do par exercício+equipamento (issue #288, lote 41).

Dono do vocabulário controlado, da validação, da assinatura e das transições
de revisão. Telas, SQL, rotas e prompts só consomem (`export_vocabulary`).
Sem número de torque, braço de momento ou vantagem mecânica: só classes.

Regras:
- Campo ausente vira `unknown`; nunca se presume "constante".
- IA só cria `suggested`; só o revisado (`reviewed`/`verified`) pesa em decisão
  automática (`is_reliable`).
- Transições (`can_transition`): o admin grava direto em qualquer estado
  (inclusive `verified` direto, pois a gravação pelo admin já é revisão humana);
  `verified`/`rejected` só por humano; `rejected` só sai por humano.
  A IA só vai de nada/`suggested` para `suggested` (nova sugestão), nunca
  sobrescreve um estado revisado, verificado ou rejeitado.

Python puro, sem I/O.
"""

from __future__ import annotations

from typing import Any, Final, Mapping, NamedTuple, Optional

RESISTANCE_VOCABULARY_VERSION: Final = "resistance-v1"
UNKNOWN: Final = "unknown"
# `async_jobs.JOB_TYPE_LAMBDA` (musclefit-workout-ai); no dono para a api-core nao importar a cadeia da IA.
SUGGEST_JOB_TYPE: Final = "resistance-profile-suggest"

SOURCES: Final[dict[str, str]] = {
    "free_weight": "Peso livre",
    "weight_stack": "Pilha de placas",
    "plate_loaded": "Anilhas na máquina",
    "cable": "Cabo",
    "bodyweight": "Peso corporal",
    "elastic": "Elástico",
    "chain": "Corrente",
    "pneumatic": "Pneumático",
    "hydraulic": "Hidráulico",
    "manual": "Resistência manual",
    "mixed": "Misto",
    UNKNOWN: "Desconhecido",
}
MECHANISMS: Final[dict[str, str]] = {
    "direct_gravity": "Gravidade direta",
    "simple_pulley": "Polia simples",
    "movable_pulley": "Polia móvel",
    "compound_pulley": "Polia composta",
    "lever": "Alavanca",
    "cam": "Came",
    "rail_guide": "Guia/trilho",
    "wheel_axle": "Roda e eixo",
    "combined": "Combinado",
    UNKNOWN: "Desconhecido",
}
TRAJECTORIES: Final[dict[str, str]] = {
    "free": "Livre",
    "cable_free_path": "Cabo, trajeto livre",
    "semi_guided": "Semiguiada",
    "guided_linear": "Guiada linear",
    "guided_arc": "Guiada em arco",
    "variable_imposed": "Variável imposta",
    "isometric": "Isométrica",
    UNKNOWN: "Desconhecida",
}
PROFILES: Final[dict[str, str]] = {
    "approximately_constant": "Aproximadamente constante",
    "ascending": "Ascendente",
    "descending": "Descendente",
    "mid_peak": "Pico no meio",
    "ends_peak": "Pico nas extremidades",
    "variable_custom": "Variável personalizada",
    "setup_dependent": "Depende da regulagem",
    UNKNOWN: "Desconhecido",
}
LINES: Final[dict[str, str]] = {
    "vertical_gravity": "Vertical (gravidade)",
    "horizontal": "Horizontal",
    "cable_angle_variable": "Ângulo do cabo variável",
    "joint_axis_relative": "Relativa ao eixo da articulação",
    UNKNOWN: "Desconhecida",
}
DIRECTION_CHANGES: Final[dict[str, str]] = {
    "yes": "Sim",
    "no": "Não",
    UNKNOWN: "Desconhecido",
}
SENSITIVITY_FLAGS: Final[dict[str, str]] = {
    "bench_adjustment": "Ajuste do banco",
    "foot_position": "Posição dos pés",
    "trunk_hip_position": "Posição de tronco/quadril",
    "anthropometry": "Antropometria",
    "reaction_forces": "Forças de reação",
    "friction": "Atrito",
    "setup_dependent_range": "Amplitude dependente da regulagem",
}
CONFIDENCES: Final[dict[str, str]] = {"low": "Baixa", "medium": "Média", "high": "Alta"}
STATUS_SUGGESTED: Final = "suggested"
REVIEW_STATUSES: Final[dict[str, str]] = {
    STATUS_SUGGESTED: "Sugerido",
    "reviewed": "Revisado",
    "verified": "Verificado",
    "rejected": "Rejeitado",
}
ORIGIN_AI: Final = "ai"
ORIGIN_ADMIN: Final = "admin"
ORIGINS: Final[dict[str, str]] = {ORIGIN_AI: "IA", ORIGIN_ADMIN: "Admin"}

RELIABLE_STATUSES: Final = frozenset({"reviewed", "verified"})
PENDING_STATUSES: Final = frozenset({"suggested"})
HUMAN_REVIEW_STATUSES: Final = frozenset(REVIEW_STATUSES) - PENDING_STATUSES
# Estados que as ações "aprovar" e "rejeitar" da tela gravam (a tela só consome, via export_vocabulary).
APPROVE_STATUS: Final = "reviewed"
REJECT_STATUS: Final = "rejected"
# Estado inicial do editor para um par sem perfil: o admin que cria à mão já é a revisão humana.
ADMIN_DEFAULT_STATUS: Final = APPROVE_STATUS

# Teto de pares por job de sugestão da IA. Limite operacional (não é regra de domínio): o job roda na Lambda de
# 60 s (`async_jobs.LAMBDA_TIMEOUT_SECONDS`), uma chamada leve por par, ~52 s úteis (60 menos a reserva do worker).
# 6 pares deixam ~8 s por chamada (p95 de um modelo leve); 10 não cabem. Mora no dono para a rota, o corpo da
# requisição e o worker compartilharem o mesmo valor.
SUGGEST_MAX_PAIRS: Final = 6

_VOCAB_FIELDS: Final[dict[str, dict[str, str]]] = {
    "source": SOURCES,
    "mechanism": MECHANISMS,
    "trajectory": TRAJECTORIES,
    "profile": PROFILES,
    "line": LINES,
    "direction_changes": DIRECTION_CHANGES,
}
_SIGNATURE_FIELDS: Final = ("source", "mechanism", "trajectory", "profile", "line")


class ResistanceProfileError(ValueError):
    def __init__(self, code: str, field: str) -> None:
        super().__init__(f"{code}:{field}")
        self.code = code
        self.field = field


class ValidatedProfile(NamedTuple):
    source: str
    mechanism: str
    trajectory: str
    profile: str
    line: str
    direction_changes: str
    flags: frozenset[str]
    confidence: str
    status: str
    origin: str


def validate_profile(data: Optional[Mapping[str, Any]]) -> ValidatedProfile:
    d = data or {}
    out: dict[str, Any] = {}
    for field, vocab in _VOCAB_FIELDS.items():
        v = d.get(field)
        v = UNKNOWN if v is None else v
        if not isinstance(v, str) or v not in vocab:
            raise ResistanceProfileError("invalid_code", field)
        out[field] = v

    raw_flags = d.get("flags")
    flags = () if raw_flags is None else raw_flags
    if isinstance(flags, str) or not hasattr(flags, "__iter__"):
        raise ResistanceProfileError("invalid_flag", "flags")
    for f in flags:
        if not isinstance(f, str) or f not in SENSITIVITY_FLAGS:
            raise ResistanceProfileError("invalid_flag", "flags")
    out["flags"] = frozenset(flags)

    # ausente: confiança mais baixa e origem IA (a mais restritiva)
    for field, vocab, default in (
        ("confidence", CONFIDENCES, "low"),
        ("status", REVIEW_STATUSES, "suggested"),
        ("origin", ORIGINS, "ai"),
    ):
        v = d.get(field)
        v = default if v is None else v
        if not isinstance(v, str) or v not in vocab:
            raise ResistanceProfileError("invalid_code", field)
        out[field] = v

    if out["origin"] == "ai" and out["status"] != "suggested":
        raise ResistanceProfileError("ai_cannot_review", "status")
    return ValidatedProfile(**out)


def can_transition(from_status: Optional[str], to_status: str, actor_origin: str) -> bool:
    """`from_status=None` é a criação do registro."""
    if actor_origin not in ORIGINS or to_status not in REVIEW_STATUSES:
        return False
    if from_status is not None and from_status not in REVIEW_STATUSES:
        return False
    if actor_origin == "ai":
        return to_status == "suggested" and from_status in (None, "suggested")
    return True


def stamps_review(status: str) -> bool:
    """Estados que carimbam revisão humana (reviewed_by/reviewed_at)."""
    return status in HUMAN_REVIEW_STATUSES


def is_pending(status: Optional[str]) -> bool:
    """None = sem perfil = pendente de revisão."""
    return status is None or status in PENDING_STATUSES


def is_unknown(profile_or_components: Optional[Mapping[str, Any] | ValidatedProfile]) -> bool:
    """Sem perfil, ou todos os componentes `unknown` (ausente conta como unknown)."""
    if profile_or_components is None:
        return True
    if isinstance(profile_or_components, ValidatedProfile):
        get = lambda f: getattr(profile_or_components, f)  # noqa: E731
    else:
        get = lambda f: profile_or_components.get(f)  # noqa: E731
    return all(get(f) in (None, UNKNOWN) for f in _VOCAB_FIELDS)


def is_reliable(status: str) -> bool:
    """Só o revisado/verificado pesa em decisões automáticas."""
    return status in RELIABLE_STATUSES


def signature(profile: Mapping[str, Any] | ValidatedProfile) -> str:
    p = profile if isinstance(profile, ValidatedProfile) else validate_profile(profile)
    return ":".join(getattr(p, f) for f in _SIGNATURE_FIELDS)


def signature_label(profile: Mapping[str, Any] | ValidatedProfile) -> str:
    p = profile if isinstance(profile, ValidatedProfile) else validate_profile(profile)
    return " · ".join(_VOCAB_FIELDS[f][getattr(p, f)] for f in _SIGNATURE_FIELDS)


def export_vocabulary() -> dict[str, Any]:
    return {
        "version": RESISTANCE_VOCABULARY_VERSION,
        "source": dict(SOURCES),
        "mechanism": dict(MECHANISMS),
        "trajectory": dict(TRAJECTORIES),
        "profile": dict(PROFILES),
        "line": dict(LINES),
        "direction_changes": dict(DIRECTION_CHANGES),
        "flags": dict(SENSITIVITY_FLAGS),
        "confidence": dict(CONFIDENCES),
        "status": dict(REVIEW_STATUSES),
        "origin": dict(ORIGINS),
        "default_status": ADMIN_DEFAULT_STATUS,
        "actions": {"approve": APPROVE_STATUS, "reject": REJECT_STATUS},
        "pending_statuses": sorted(PENDING_STATUSES),
        "human_review_statuses": sorted(HUMAN_REVIEW_STATUSES),
        "suggest_max_pairs": SUGGEST_MAX_PAIRS,
    }


def suggestion_output_schema() -> dict[str, Any]:
    """JSON Schema (strict) da saída da IA, gerado do vocabulário: enums dos componentes, das flags, da confiança
    e do status (só os pendentes: a IA nunca confirma). A semântica é de `validate_profile`."""

    def enum(codes: Any) -> dict[str, Any]:
        return {"type": "string", "enum": list(codes)}

    properties: dict[str, Any] = {f: enum(vocab) for f, vocab in _VOCAB_FIELDS.items()}
    properties["flags"] = {"type": "array", "items": enum(SENSITIVITY_FLAGS)}
    properties["confidence"] = enum(CONFIDENCES)
    properties["status"] = enum(sorted(PENDING_STATUSES))
    properties["rationale"] = {"type": "string"}
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}
