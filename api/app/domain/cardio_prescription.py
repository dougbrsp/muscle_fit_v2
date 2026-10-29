"""Prescrição de cardio do item do Modelo (M98 C0; plano
`docs/cardio/PLANO_MOTOR_CARDIO_2026-10-26.md` §4.1).

Dono ÚNICO de:

- os modos (`continuous` = Normal, `hiit`) e os campos de cada um, com as
  unidades (aquecimento, tempo de cardio e recuperação em MINUTOS; tempos em
  zona baixa/alta do HIIT em SEGUNDOS);
- a validação (campos obrigatórios por modo, zona alta > zona baixa, mínimos e
  tetos por campo e o teto da sessão inteira);
- os padrões (valores iniciais do editor);
- o tempo total da prescrição em segundos (`total_seconds`), que o "Tempo
  total de treino" do Modelo (`periodization/time_estimate.py`) consome;
- o texto canônico de exibição (`describe`) e o resumo curto do item
  recolhido no editor (`describe_short`, M98 C1).

Decisões do usuário (2026-10-26):

- Normal: aquecimento (min), tempo de cardio (min), zona-alvo Z1–Z5 e
  recuperação (min). "Recuperação" = desaquecimento (volta à calma no fim).
- HIIT: aquecimento (min), tempo em zona baixa (s), tempo em zona alta (s),
  zona baixa, zona alta (alta > baixa), nº de séries e recuperação (min). O
  tempo em zona baixa é o intervalo entre os tiros.
- Sem equipamento.

Retrocompatibilidade: item de cardio antigo só tem `cardio_duration_minutes`.
Modo ausente = Normal (`DEFAULT_CARDIO_MODE`); aquecimento/recuperação
ausentes = 0; zona ausente = "não definida". A migração 095 grava
`cardio_mode='continuous'` nesses itens; a leitura daqui dá o mesmo resultado
com ou sem o backfill.

As zonas válidas (Z1–Z5) são do dono das zonas (`domain/hr_zones.py`).

Nada disso (limite, padrão, conta de tempo, "duração > 0") mora fora daqui.
Mapa: `docs/motores/FONTES_DA_VERDADE.md` §3.5. Guarda:
`api/tests/unit/test_guarda_fontes_da_verdade.py` (`cardio_prescricao_paralela`).

Módulo PURO: sem banco, sem FastAPI, sem I/O.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Final, Mapping, Optional

from . import cardio_policy
from .hr_zones import HR_ZONE_MAX, HR_ZONE_MIN, is_valid_zone, zone_label, zone_number

CARDIO_ITEM_KIND: Final[str] = "cardio"


class CardioMode(str, Enum):
    CONTINUOUS = "continuous"
    HIIT = "hiit"


CARDIO_MODE_VALUES: Final[tuple[str, ...]] = tuple(mode.value for mode in CardioMode)
# Item antigo (só duração) e modo ausente = Normal.
DEFAULT_CARDIO_MODE: Final[CardioMode] = CardioMode.CONTINUOUS

SECONDS_PER_MINUTE: Final[int] = 60

# --- Campos (nomes das colunas da migração 095 + a da 007) -----------------
MODE_FIELD: Final[str] = "cardio_mode"
DURATION_FIELD: Final[str] = "cardio_duration_minutes"  # tempo de cardio do Normal (min)
WARMUP_FIELD: Final[str] = "cardio_warmup_minutes"  # aquecimento (min), os dois modos
COOLDOWN_FIELD: Final[str] = "cardio_cooldown_minutes"  # recuperação/desaquecimento (min), os dois modos
TARGET_ZONE_FIELD: Final[str] = "cardio_target_zone"  # zona-alvo do Normal
LOW_SECONDS_FIELD: Final[str] = "hiit_low_seconds"  # tempo em zona baixa (s)
HIGH_SECONDS_FIELD: Final[str] = "hiit_high_seconds"  # tempo em zona alta (s)
LOW_ZONE_FIELD: Final[str] = "hiit_low_zone"
HIGH_ZONE_FIELD: Final[str] = "hiit_high_zone"
ROUNDS_FIELD: Final[str] = "hiit_rounds"  # nº de séries

# Todos os campos da prescrição, na ordem das colunas.
PRESCRIPTION_FIELDS: Final[tuple[str, ...]] = (
    MODE_FIELD,
    DURATION_FIELD,
    WARMUP_FIELD,
    COOLDOWN_FIELD,
    TARGET_ZONE_FIELD,
    LOW_SECONDS_FIELD,
    HIGH_SECONDS_FIELD,
    LOW_ZONE_FIELD,
    HIGH_ZONE_FIELD,
    ROUNDS_FIELD,
)
# Colunas novas da 095 (a duração é da 007).
NEW_COLUMNS: Final[tuple[str, ...]] = tuple(f for f in PRESCRIPTION_FIELDS if f != DURATION_FIELD)

# Campos de cada modo (o resto é descartado na normalização).
MODE_FIELDS: Final[dict[CardioMode, tuple[str, ...]]] = {
    CardioMode.CONTINUOUS: (WARMUP_FIELD, DURATION_FIELD, TARGET_ZONE_FIELD, COOLDOWN_FIELD),
    CardioMode.HIIT: (
        WARMUP_FIELD,
        LOW_SECONDS_FIELD,
        HIGH_SECONDS_FIELD,
        LOW_ZONE_FIELD,
        HIGH_ZONE_FIELD,
        ROUNDS_FIELD,
        COOLDOWN_FIELD,
    ),
}
# Obrigatórios por modo (aquecimento, recuperação e a zona do Normal são opcionais).
REQUIRED_FIELDS: Final[dict[CardioMode, tuple[str, ...]]] = {
    CardioMode.CONTINUOUS: (DURATION_FIELD,),
    CardioMode.HIIT: (LOW_SECONDS_FIELD, HIGH_SECONDS_FIELD, LOW_ZONE_FIELD, HIGH_ZONE_FIELD, ROUNDS_FIELD),
}
ZONE_FIELDS: Final[frozenset[str]] = frozenset({TARGET_ZONE_FIELD, LOW_ZONE_FIELD, HIGH_ZONE_FIELD})
# Ausente = 0 (não é "não definido"): entram na conta do tempo como zero.
ZERO_WHEN_MISSING_FIELDS: Final[frozenset[str]] = frozenset({WARMUP_FIELD, COOLDOWN_FIELD})

# --- Mínimos e tetos por campo (inclusive) ---------------------------------
CARDIO_DURATION_MINUTES_MIN: Final[int] = 1
CARDIO_DURATION_MINUTES_MAX: Final[int] = 240
CARDIO_WARMUP_MINUTES_MIN: Final[int] = 0
CARDIO_WARMUP_MINUTES_MAX: Final[int] = 60
CARDIO_COOLDOWN_MINUTES_MIN: Final[int] = 0
CARDIO_COOLDOWN_MINUTES_MAX: Final[int] = 60
HIIT_INTERVAL_SECONDS_MIN: Final[int] = 5
HIIT_INTERVAL_SECONDS_MAX: Final[int] = 600
HIIT_ROUNDS_MIN: Final[int] = 1
HIIT_ROUNDS_MAX: Final[int] = 50
# Teto da prescrição inteira (aquecimento + parte principal + recuperação).
CARDIO_SESSION_MAX_MINUTES: Final[int] = 240

FIELD_LIMITS: Final[dict[str, tuple[int, int]]] = {
    DURATION_FIELD: (CARDIO_DURATION_MINUTES_MIN, CARDIO_DURATION_MINUTES_MAX),
    WARMUP_FIELD: (CARDIO_WARMUP_MINUTES_MIN, CARDIO_WARMUP_MINUTES_MAX),
    COOLDOWN_FIELD: (CARDIO_COOLDOWN_MINUTES_MIN, CARDIO_COOLDOWN_MINUTES_MAX),
    TARGET_ZONE_FIELD: (HR_ZONE_MIN, HR_ZONE_MAX),
    LOW_SECONDS_FIELD: (HIIT_INTERVAL_SECONDS_MIN, HIIT_INTERVAL_SECONDS_MAX),
    HIGH_SECONDS_FIELD: (HIIT_INTERVAL_SECONDS_MIN, HIIT_INTERVAL_SECONDS_MAX),
    LOW_ZONE_FIELD: (HR_ZONE_MIN, HR_ZONE_MAX),
    HIGH_ZONE_FIELD: (HR_ZONE_MIN, HR_ZONE_MAX),
    ROUNDS_FIELD: (HIIT_ROUNDS_MIN, HIIT_ROUNDS_MAX),
}

# --- Padrões (valores iniciais do editor) -----------------------------------
# Normal: o 1º degrau da escada de Z2 do dono da recomendação (`cardio_policy`).
DEFAULT_CONTINUOUS_TARGET_ZONE: Final[int] = 2
DEFAULT_WARMUP_MINUTES: Final[int] = 5
DEFAULT_COOLDOWN_MINUTES: Final[int] = 5
# HIIT: o protocolo preferencial do dono da recomendação (`cardio_policy`).
DEFAULT_HIIT_LOW_ZONE: Final[int] = 2
DEFAULT_HIIT_HIGH_ZONE: Final[int] = 4

DEFAULTS: Final[dict[CardioMode, dict[str, int]]] = {
    CardioMode.CONTINUOUS: {
        WARMUP_FIELD: DEFAULT_WARMUP_MINUTES,
        DURATION_FIELD: cardio_policy.Z2_DURATION_LADDER_MINUTES[0],
        TARGET_ZONE_FIELD: DEFAULT_CONTINUOUS_TARGET_ZONE,
        COOLDOWN_FIELD: DEFAULT_COOLDOWN_MINUTES,
    },
    CardioMode.HIIT: {
        WARMUP_FIELD: DEFAULT_WARMUP_MINUTES,
        LOW_SECONDS_FIELD: cardio_policy.HIIT_RECOVERY_MINUTES * SECONDS_PER_MINUTE,
        HIGH_SECONDS_FIELD: cardio_policy.HIIT_WORK_MINUTES * SECONDS_PER_MINUTE,
        LOW_ZONE_FIELD: DEFAULT_HIIT_LOW_ZONE,
        HIGH_ZONE_FIELD: DEFAULT_HIIT_HIGH_ZONE,
        ROUNDS_FIELD: cardio_policy.HIIT_PREFERRED_ROUNDS,
        COOLDOWN_FIELD: DEFAULT_COOLDOWN_MINUTES,
    },
}

# Rótulos dos campos nas mensagens de validação.
FIELD_LABELS: Final[dict[str, str]] = {
    MODE_FIELD: "tipo de cardio",
    DURATION_FIELD: "tempo de cardio (min)",
    WARMUP_FIELD: "aquecimento (min)",
    COOLDOWN_FIELD: "recuperação (min)",
    TARGET_ZONE_FIELD: "zona-alvo",
    LOW_SECONDS_FIELD: "tempo em zona baixa (s)",
    HIGH_SECONDS_FIELD: "tempo em zona alta (s)",
    LOW_ZONE_FIELD: "zona baixa",
    HIGH_ZONE_FIELD: "zona alta",
    ROUNDS_FIELD: "nº de séries",
}
MODE_LABELS: Final[dict[CardioMode, str]] = {CardioMode.CONTINUOUS: "Normal", CardioMode.HIIT: "HIIT"}

# --- Etapas da exibição (M98 C3b: Modo Treino ao encerrar a força) ----------
STEP_WARMUP: Final[str] = "warmup"
STEP_MAIN: Final[str] = "main"  # parte principal do Normal
STEP_INTERVALS: Final[str] = "intervals"  # séries do HIIT
STEP_COOLDOWN: Final[str] = "cooldown"
STEP_LABELS: Final[dict[str, str]] = {
    STEP_WARMUP: "Aquecimento",
    STEP_MAIN: "Cardio",
    STEP_INTERVALS: "Séries",
    STEP_COOLDOWN: "Recuperação",
}


class CardioPrescriptionError(ValueError):
    """Prescrição fora da forma do dono (a rota responde 422 com a mensagem)."""


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def _field(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    value = getattr(value, "value", value)  # enum
    text = str(value).strip().lower()
    return text or None


def is_cardio_item(item: Any) -> bool:
    return _text(_field(item, "item_kind")) == CARDIO_ITEM_KIND


def mode_of(item: Any) -> CardioMode:
    """Modo do item na LEITURA: ausente ou desconhecido = Normal (item antigo)."""
    text = _text(_field(item, MODE_FIELD))
    for mode in CardioMode:
        if mode.value == text:
            return mode
    return DEFAULT_CARDIO_MODE


def _number(value: Any) -> Optional[float]:
    """Número finito; senão None (inclusive `bool` e texto ilegível)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive(value: Any) -> float:
    number = _number(value)
    return number if number is not None and number > 0 else 0.0


def _int_or_none(value: Any) -> Optional[int]:
    number = _number(value)
    return int(math.floor(number + 0.5)) if number is not None else None


def normalize_fields(item: Any) -> dict[str, Any]:
    """Campos da prescrição como vão para a COLUNA.

    - item que não é cardio -> todos None;
    - cardio: o modo resolvido (ausente = Normal) e só os campos do modo,
      inteiros; os do outro modo viram None (o editor pode mandar sobras ao
      trocar de modo, como o companion tolerante da técnica)."""
    out: dict[str, Any] = dict.fromkeys(PRESCRIPTION_FIELDS)
    if not is_cardio_item(item):
        return out
    mode = mode_of(item)
    out[MODE_FIELD] = mode.value
    for name in MODE_FIELDS[mode]:
        out[name] = _int_or_none(_field(item, name))
    return out


def canonical_extras(item: Any) -> tuple[tuple[str, Any], ...]:
    """Forma canônica dos campos NOVOS (095) para a comparação de versões do
    Modelo: só entra o que um item antigo (só duração) não tem. Normal sem
    aquecimento/recuperação/zona -> `()`, a mesma forma de antes da 095: um PUT
    de cliente que não conhece os campos (ou um item com o backfill) não abre
    versão. Aquecimento/recuperação 0 = ausente."""
    fields = normalize_fields(item)
    if fields[MODE_FIELD] is None:
        return ()
    extras: list[tuple[str, Any]] = []
    if fields[MODE_FIELD] != DEFAULT_CARDIO_MODE.value:
        extras.append((MODE_FIELD, fields[MODE_FIELD]))
    for name in NEW_COLUMNS:
        if name == MODE_FIELD:
            continue
        value = fields[name]
        if value is None or (name in ZERO_WHEN_MISSING_FIELDS and value == 0):
            continue
        extras.append((name, value))
    return tuple(extras)


# ---------------------------------------------------------------------------
# Validação
# ---------------------------------------------------------------------------


def _parse_mode(item: Any) -> CardioMode:
    """Modo na ESCRITA: ausente = Normal; valor fora do vocabulário = erro."""
    text = _text(_field(item, MODE_FIELD))
    if text is None:
        return DEFAULT_CARDIO_MODE
    for mode in CardioMode:
        if mode.value == text:
            return mode
    raise CardioPrescriptionError(
        f"tipo de cardio inválido '{text}' (use {', '.join(repr(v) for v in CARDIO_MODE_VALUES)})."
    )


def _check_limits(name: str, value: Any) -> None:
    number = _number(value)
    low, high = FIELD_LIMITS[name]
    label = FIELD_LABELS[name]
    if number is None or not number.is_integer():
        raise CardioPrescriptionError(f"{label} precisa ser um número inteiro.")
    if name in ZONE_FIELDS and not is_valid_zone(number):
        raise CardioPrescriptionError(f"{label} precisa estar entre {zone_label(low)} e {zone_label(high)}.")
    if not low <= number <= high:
        raise CardioPrescriptionError(f"{label} precisa estar entre {low} e {high}.")


def validate(item: Any) -> None:
    """Valida a prescrição de um item de cardio (levanta
    `CardioPrescriptionError` com a mensagem para o usuário). Os campos do
    OUTRO modo são ignorados (a gravação os descarta)."""
    mode = _parse_mode(item)
    for name in REQUIRED_FIELDS[mode]:
        value = _field(item, name)
        if name == DURATION_FIELD and (_number(value) is None or _number(value) <= 0):
            # Mensagem de sempre (antes do M98 a rota dizia exatamente isto).
            raise CardioPrescriptionError("item de cardio exige cardio_duration_minutes > 0.")
        if value is None:
            raise CardioPrescriptionError(f"cardio {MODE_LABELS[mode]} exige {FIELD_LABELS[name]}.")
    for name in MODE_FIELDS[mode]:
        value = _field(item, name)
        if value is not None:
            _check_limits(name, value)
    if mode is CardioMode.HIIT:
        low_zone = zone_number(_field(item, LOW_ZONE_FIELD))
        high_zone = zone_number(_field(item, HIGH_ZONE_FIELD))
        if low_zone is not None and high_zone is not None and high_zone <= low_zone:
            raise CardioPrescriptionError("no HIIT a zona alta precisa ser maior que a zona baixa.")
    if total_seconds(item) > CARDIO_SESSION_MAX_MINUTES * SECONDS_PER_MINUTE:
        raise CardioPrescriptionError(
            f"a prescrição de cardio passa de {CARDIO_SESSION_MAX_MINUTES} min no total "
            "(aquecimento + parte principal + recuperação)."
        )


# ---------------------------------------------------------------------------
# Tempo e texto
# ---------------------------------------------------------------------------


def main_seconds(item: Any) -> float:
    """Só a parte principal (sem aquecimento/recuperação): o tempo de cardio do
    Normal ou `séries × (zona alta + zona baixa)` do HIIT. Tolerante: campo
    ausente/ilegível/negativo conta 0 (a estimativa roda com o slot em edição)."""
    if mode_of(item) is CardioMode.HIIT:
        high, low = hiit_phase_seconds(item)
        return high + low
    return _positive(_field(item, DURATION_FIELD)) * SECONDS_PER_MINUTE


def hiit_phase_seconds(item: Any) -> tuple[float, float]:
    """Tempo prescrito do HIIT em segundos, por fase: `(séries × zona alta,
    séries × zona baixa)`. Normal -> (0, 0). Mesma tolerância de
    `main_seconds` (M98 C2: aderência por fase em `cardio_adherence`)."""
    if mode_of(item) is not CardioMode.HIIT:
        return 0.0, 0.0
    rounds = _positive(_field(item, ROUNDS_FIELD))
    return rounds * _positive(_field(item, HIGH_SECONDS_FIELD)), rounds * _positive(_field(item, LOW_SECONDS_FIELD))


def field_value(item: Any, name: str) -> Any:
    """Valor cru de um campo da prescrição (dict ou objeto)."""
    return _field(item, name)


def total_seconds(item: Any) -> float:
    """Tempo total da prescrição em segundos: aquecimento + parte principal +
    recuperação. Item antigo (só duração) = `duração × 60`, como sempre. Sem
    arredondar (quem soma parcelas arredonda no fim, como o `time_estimate`)."""
    warmup = _positive(_field(item, WARMUP_FIELD)) * SECONDS_PER_MINUTE
    cooldown = _positive(_field(item, COOLDOWN_FIELD)) * SECONDS_PER_MINUTE
    return warmup + main_seconds(item) + cooldown


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}".replace(".", ",")


def _zone_suffix(value: Any) -> str:
    zone = zone_number(value)
    return f" {zone_label(zone)}" if zone is not None else ""


def describe(item: Any) -> str:
    """Texto canônico para exibição:
    "5 min aquec. + 20 min Z2 + 5 min recuperação" (Normal) ou
    "5 min aquec. + 5× [120 s Z4 / 120 s Z2] + 5 min recuperação" (HIIT).
    Zona não definida some do texto; aquecimento/recuperação 0 não aparecem."""
    parts: list[str] = []
    warmup = _positive(_field(item, WARMUP_FIELD))
    if warmup:
        parts.append(f"{_fmt(warmup)} min aquec.")
    if mode_of(item) is CardioMode.HIIT:
        rounds = _positive(_field(item, ROUNDS_FIELD))
        high = _positive(_field(item, HIGH_SECONDS_FIELD))
        low = _positive(_field(item, LOW_SECONDS_FIELD))
        parts.append(
            f"{_fmt(rounds)}× [{_fmt(high)} s{_zone_suffix(_field(item, HIGH_ZONE_FIELD))}"
            f" / {_fmt(low)} s{_zone_suffix(_field(item, LOW_ZONE_FIELD))}]"
        )
    else:
        duration = _positive(_field(item, DURATION_FIELD))
        if duration:
            parts.append(f"{_fmt(duration)} min{_zone_suffix(_field(item, TARGET_ZONE_FIELD))}")
    cooldown = _positive(_field(item, COOLDOWN_FIELD))
    if cooldown:
        parts.append(f"{_fmt(cooldown)} min recuperação")
    return " + ".join(parts)


def describe_short(item: Any) -> str:
    """Resumo curto do item recolhido no editor do Modelo (M98 C1):
    "Normal 20 min Z2 · 5+5 min" ou "HIIT 8× 30 s Z4 / 90 s Z2 · 5+5 min"
    (parte principal; depois do "·", aquecimento + recuperação em min, só
    quando um dos dois existe). Zona não definida some do texto."""
    mode = mode_of(item)
    if mode is CardioMode.HIIT:
        rounds = _positive(_field(item, ROUNDS_FIELD))
        high = _positive(_field(item, HIGH_SECONDS_FIELD))
        low = _positive(_field(item, LOW_SECONDS_FIELD))
        main = (
            f"{_fmt(rounds)}× {_fmt(high)} s{_zone_suffix(_field(item, HIGH_ZONE_FIELD))}"
            f" / {_fmt(low)} s{_zone_suffix(_field(item, LOW_ZONE_FIELD))}"
        )
    else:
        duration = _positive(_field(item, DURATION_FIELD))
        main = f"{_fmt(duration)} min{_zone_suffix(_field(item, TARGET_ZONE_FIELD))}"
    text = f"{MODE_LABELS[mode]} {main}"
    warmup = _positive(_field(item, WARMUP_FIELD))
    cooldown = _positive(_field(item, COOLDOWN_FIELD))
    if warmup or cooldown:
        text += f" · {_fmt(warmup)}+{_fmt(cooldown)} min"
    return text


def minutes_from_seconds(seconds: Any) -> int:
    """Segundos -> minutos inteiros (arredondado, meio para cima)."""
    return int(math.floor(_positive(seconds) / SECONDS_PER_MINUTE + 0.5))


def minutes_label(seconds: Any) -> str:
    """"N min": o tempo total que as telas mostram (recomendação e Modo Treino)."""
    return f"{minutes_from_seconds(seconds)} min"


def total_minutes(item: Any) -> int:
    """Tempo total da prescrição em minutos inteiros (arredondado)."""
    return minutes_from_seconds(total_seconds(item))


def describe_steps(item: Any) -> list[dict[str, Any]]:
    """Etapas da prescrição para quem só EXIBE (Modo Treino, M98 C3b), na
    ordem de execução: aquecimento, parte principal (Normal: tempo e
    zona-alvo; HIIT: séries × alta/baixa com as zonas) e recuperação.
    Aquecimento/recuperação 0 não aparecem; zona não definida = None. Cada
    etapa traz `zones` (as zonas que ela usa, na ordem do texto) para quem
    exibe anexar a faixa de bpm do atleta sem recalcular nada."""
    steps: list[dict[str, Any]] = []
    warmup = _positive(_field(item, WARMUP_FIELD))
    if warmup:
        steps.append(
            {"kind": STEP_WARMUP, "label": STEP_LABELS[STEP_WARMUP], "text": f"{_fmt(warmup)} min",
             "seconds": int(warmup * SECONDS_PER_MINUTE), "zones": []}
        )
    if mode_of(item) is CardioMode.HIIT:
        rounds = _positive(_field(item, ROUNDS_FIELD))
        high = _positive(_field(item, HIGH_SECONDS_FIELD))
        low = _positive(_field(item, LOW_SECONDS_FIELD))
        high_zone = zone_number(_field(item, HIGH_ZONE_FIELD))
        low_zone = zone_number(_field(item, LOW_ZONE_FIELD))
        steps.append(
            {
                "kind": STEP_INTERVALS,
                "label": STEP_LABELS[STEP_INTERVALS],
                "text": (
                    f"{_fmt(rounds)}× [{_fmt(high)} s{_zone_suffix(high_zone)}"
                    f" / {_fmt(low)} s{_zone_suffix(low_zone)}]"
                ),
                "seconds": int(math.floor(main_seconds(item) + 0.5)),
                "rounds": int(rounds),
                "high_seconds": int(high),
                "high_zone": high_zone,
                "low_seconds": int(low),
                "low_zone": low_zone,
                "zones": [z for z in (high_zone, low_zone) if z is not None],
            }
        )
    else:
        duration = _positive(_field(item, DURATION_FIELD))
        zone = zone_number(_field(item, TARGET_ZONE_FIELD))
        steps.append(
            {
                "kind": STEP_MAIN,
                "label": STEP_LABELS[STEP_MAIN],
                "text": f"{_fmt(duration)} min{_zone_suffix(zone)}",
                "seconds": int(math.floor(main_seconds(item) + 0.5)),
                "zone": zone,
                "zones": [zone] if zone is not None else [],
            }
        )
    cooldown = _positive(_field(item, COOLDOWN_FIELD))
    if cooldown:
        steps.append(
            {"kind": STEP_COOLDOWN, "label": STEP_LABELS[STEP_COOLDOWN], "text": f"{_fmt(cooldown)} min",
             "seconds": int(cooldown * SECONDS_PER_MINUTE), "zones": []}
        )
    return steps


def same_prescription(a: Any, b: Any) -> bool:
    """As duas prescrições são a mesma (modo e campos do modo, normalizados)?
    Aquecimento/recuperação ausentes = 0. É o que torna o "aplicar no Modelo"
    (M98 C3b) idempotente: aplicar de novo a mesma prescrição não muda nada."""
    def key(item: Any) -> dict[str, Any]:
        fields = normalize_fields({**_as_mapping(item), "item_kind": CARDIO_ITEM_KIND})
        for name in ZERO_WHEN_MISSING_FIELDS:
            if fields[name] is None:
                fields[name] = 0
        return fields

    return key(a) == key(b)


def _as_mapping(item: Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        return dict(item)
    return {name: getattr(item, name, None) for name in PRESCRIPTION_FIELDS}


def clamp_fields(item: Mapping[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Correção de uma prescrição vinda de fora (saída do agente de cardio,
    M98 C3a): cada campo NUMÉRICO do modo, fora de `FIELD_LIMITS`, vai para o
    mínimo/teto do campo; número fracionário é arredondado. Devolve a
    prescrição corrigida (só o modo e os campos do modo) e os campos
    corrigidos, na ordem dos campos. Não decide o resto: quem chama valida
    depois com `validate` (obrigatório ausente, zona alta <= baixa e o teto da
    sessão continuam sendo rejeição)."""
    mode = _parse_mode(item)
    out: dict[str, Any] = {MODE_FIELD: mode.value}
    clamped: list[str] = []
    for name in MODE_FIELDS[mode]:
        value = _field(item, name)
        number = _number(value)
        if number is None:
            out[name] = None if value is None else value
            continue
        low, high = FIELD_LIMITS[name]
        fixed = min(max(int(math.floor(number + 0.5)), low), high)
        if fixed != number:
            clamped.append(name)
        out[name] = fixed
    return out, tuple(clamped)


def default_prescription(mode: CardioMode) -> dict[str, Any]:
    """Padrão do modo COM o `cardio_mode` (sem ele, a leitura trata o item
    como Normal: `describe(DEFAULTS[HIIT])` sairia sem as séries)."""
    return {MODE_FIELD: mode.value, **DEFAULTS[mode]}


def limits_text() -> str:
    """Mínimo–máximo de cada campo (`FIELD_LIMITS`), para os prompts que
    descrevem a prescrição (agente de cardio, periodização): só interpolam."""
    parts = []
    for name, (low, high) in FIELD_LIMITS.items():
        if name in ZONE_FIELDS:
            parts.append(f"`{name}` {zone_label(low)}–{zone_label(high)} (número da zona)")
        else:
            parts.append(f"`{name}` {low}–{high}")
    return "; ".join(parts)


def validation_error(item: Any) -> Optional[str]:
    """Mensagem de `validate` ou None (prescrição válida). É o que o editor do
    Modelo mostra no card (`POST /workout-plan-templates/cardio-prescription-check`)."""
    try:
        validate(item)
    except CardioPrescriptionError as exc:
        return str(exc)
    return None


def summarize(item: Any) -> dict[str, Any]:
    """Prescrição pronta para quem só exibe (rascunho da ficha, relatório):
    campos normalizados + modo, tempo total (s, arredondado) e o texto."""
    fields = normalize_fields(item)
    return {
        **fields,
        "total_seconds": int(math.floor(total_seconds(item) + 0.5)),
        "text": describe(item),
    }


def summarize_slot(items: Any) -> list[dict[str, Any]]:
    """Resumo de cada item de cardio de um slot, na ordem dos itens."""
    return [summarize(item) for item in items or [] if is_cardio_item(item)]


def slot_cardio_minutes(items: Any) -> Optional[int]:
    """Minutos totais (arredondados) do cardio do slot; None sem cardio. É o
    `cardio_minutes` do rascunho da ficha, agora com aquecimento, recuperação
    e HIIT (antes era só a soma de `cardio_duration_minutes`)."""
    seconds = sum(total_seconds(item) for item in items or [] if is_cardio_item(item))
    if seconds <= 0:
        return None
    return int(math.floor(seconds / SECONDS_PER_MINUTE + 0.5))


def draft_cardio(items: Any) -> dict[str, Any]:
    """Chaves de cardio do rascunho da ficha gerado de um slot do Modelo (dia
    de treino e de descanso, todos os modos): `cardio_minutes` (total) e
    `cardio_prescriptions` (cada item, com o texto pronto)."""
    return {"cardio_minutes": slot_cardio_minutes(items), "cardio_prescriptions": summarize_slot(items)}


def export_for_frontend() -> dict[str, Any]:
    """Seção `cardio_prescription` do `motor-constants.generated.json`."""
    return {
        "item_kind": CARDIO_ITEM_KIND,
        "modes": list(CARDIO_MODE_VALUES),
        "default_mode": DEFAULT_CARDIO_MODE.value,
        "mode_labels": {mode.value: label for mode, label in MODE_LABELS.items()},
        "seconds_per_minute": SECONDS_PER_MINUTE,
        "mode_fields": {mode.value: list(fields) for mode, fields in MODE_FIELDS.items()},
        "required_fields": {mode.value: list(fields) for mode, fields in REQUIRED_FIELDS.items()},
        # M98 C1: campos de zona (o editor mostra Z1–Z5 com a faixa de bpm).
        "zone_fields": sorted(ZONE_FIELDS),
        "limits": {name: {"min": low, "max": high} for name, (low, high) in FIELD_LIMITS.items()},
        "session_max_minutes": CARDIO_SESSION_MAX_MINUTES,
        "defaults": {mode.value: dict(values) for mode, values in DEFAULTS.items()},
        "field_labels": dict(FIELD_LABELS),
    }


__all__ = [
    "CARDIO_DURATION_MINUTES_MAX",
    "CARDIO_DURATION_MINUTES_MIN",
    "CARDIO_ITEM_KIND",
    "CARDIO_MODE_VALUES",
    "CARDIO_SESSION_MAX_MINUTES",
    "COOLDOWN_FIELD",
    "CardioMode",
    "CardioPrescriptionError",
    "DEFAULTS",
    "DEFAULT_CARDIO_MODE",
    "DURATION_FIELD",
    "FIELD_LIMITS",
    "HIGH_SECONDS_FIELD",
    "HIGH_ZONE_FIELD",
    "LOW_SECONDS_FIELD",
    "LOW_ZONE_FIELD",
    "MODE_FIELD",
    "NEW_COLUMNS",
    "PRESCRIPTION_FIELDS",
    "ROUNDS_FIELD",
    "SECONDS_PER_MINUTE",
    "TARGET_ZONE_FIELD",
    "WARMUP_FIELD",
    "canonical_extras",
    "clamp_fields",
    "describe",
    "describe_short",
    "describe_steps",
    "export_for_frontend",
    "default_prescription",
    "is_cardio_item",
    "limits_text",
    "main_seconds",
    "minutes_from_seconds",
    "minutes_label",
    "mode_of",
    "normalize_fields",
    "same_prescription",
    "slot_cardio_minutes",
    "summarize",
    "summarize_slot",
    "total_minutes",
    "total_seconds",
    "validate",
    "validation_error",
]
