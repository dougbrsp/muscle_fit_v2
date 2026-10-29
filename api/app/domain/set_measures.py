"""Medidas de série: repetições, tempo e máximo de repetições (Lote T1,
2026-10-20).

Fonte única do vocabulário de `measure` (colunas da migração 089) e das regras
de forma de uma série medida por tempo ou por máximo de repetições. Pedido do
usuário (2026-10-20): exercício POR TEMPO (prancha, 3 séries de 1 minuto; o
Modo Treino cronometra) e série "RM" (máximo de repetições, podendo definir
uma janela de tempo em que se pede o máximo; o Modo Treino registra quantas).

| measure    | item do Modelo / ficha                              | série gravada                         |
|------------|-----------------------------------------------------|---------------------------------------|
| `reps`     | `reps_target` como hoje; sem campos de tempo        | `reps` (como hoje); sem `duration`    |
| `duration` | `duration_seconds_target` obrigatório; sem reps,    | `duration_seconds` obrigatório;       |
|            | sem bloco avançado                                  | `reps` NULL                           |
| `max_reps` | `time_window_seconds` opcional (janela); sem reps   | `reps` obrigatório (≥ 1);             |
|            | alvo, sem bloco avançado                            | `duration_seconds` opcional = janela  |

Semântica:

- **Medida ausente = `reps`.** As colunas nascem `NOT NULL DEFAULT 'reps'` (e
  `exercise_catalog.default_measure` NULL = sem sugestão). Toda linha anterior
  à 089 é `reps`; `normalize_measure(None)` devolve `reps`.
- **Nunca inferir a medida da série pelo item.** O relógio antigo e os
  importadores só mandam `reps`: uma série sem `measure` é uma série de
  repetições, mesmo que o item da ficha seja por tempo. Quem grava decide a
  medida explicitamente (Modo Treino, PR T3); quem lê usa `normalize_measure`
  ou `sql_measure_expr`/`sql_is_rep_based`.
- **`reps` é a única medida "baseada em repetições"** para progressão,
  comparabilidade, e1RM, tonelagem e carga da próxima série
  (`is_rep_based`). `duration` e `max_reps` são NEUTRAS nesses motores
  (motivo `REASON_MEASURE_NOT_REP_BASED`, consumido no PR T3): uma série
  "máximo de reps em 30 s" não é a mesma pergunta que "8-12 reps a RPE 8", e
  comparar as duas produziria progresso/regressão falsos.
- **Dose de recuperação.** Série por tempo não tem reps: `dose_reps_for_measure`
  devolve None e o consumidor cai no `DOSE_REPS_WHEN_MISSING` de
  `domain/e1rm.py` (8 reps, a mesma assunção de hoje para série sem reps).
  Decisão nomeada e revisável (uma série de 60 s de prancha pode merecer dose
  própria; medir antes de mudar).
- **Tempo de treino.** `planned_set_execution_seconds` dá o tempo de execução
  planejado da série quando a medida o define (alvo em `duration`; janela em
  `max_reps`); None = o estimador usa a fórmula de reps de sempre
  (`periodization/time_estimate.py`, consumidor no PR T2).

Este lote (T1) cria só o dono, o schema (089), o espelho do FE
(`set-measures.ts` lendo `motor-constants.generated.json` + paridade), a guarda
(`medida_serie`) e o mapa (§60). Nenhum consumidor: validação do Modelo/ficha
(T2), gravação da série e Modo Treino (T3), contagem de séries no volume e
"última vez" (T4), progressão/comparabilidade neutras (T5) vêm nos PRs seguintes.

Lote T2 (Modelo -> ficha -> prescrição congelada): `validate_item_measure` e
`item_validation_message` no 422 do Modelo (`workout_plan_templates.py`);
`normalize_measure` na gravação do item (versões do Modelo) e da ficha
(`services.py`); `is_rep_based` na geração do Modo B (sem alvo de reps fora de
`reps`), no congelamento e na Intensidade × Densidade; `planned_set_execution_seconds`
no tempo de treino (`periodization/time_estimate.py`); `parse_measure` no
`default_measure` do catálogo.

Lote T3 (registro da série e motores neutros, mapa §60.2): `validate_set_measure`
nos payloads da série (`set_measure_input.py` -> 422); `is_rep_based` /
`has_non_rep_based` na carga da próxima série, na posição das reps, na
progressão (NEUTRAL `REASON_MEASURE_NOT_REP_BASED`, sem adoção de carga), na
comparabilidade (NOT_COMPARABLE) e nos agregados de exposição; `sql_is_rep_based`
/ `sql_if_rep_based` na tonelagem, no `volume_kg`, no e1RM e no recorde;
`dose_reps_for_measure` na dose de recuperação; `format_set_measure` na
"última vez", no detalhe da atividade e no relatório (`sql_duration_seconds`).

Módulo puro: sem I/O, sem import de `app.*`.
"""

from __future__ import annotations

import re
from enum import Enum
from types import MappingProxyType
from typing import Any, Final, Mapping, Optional


class SetMeasure(str, Enum):
    """Valores gravados em `measure` (089). A ordem é a de exibição."""

    REPS = "reps"
    DURATION = "duration"
    MAX_REPS = "max_reps"

    @property
    def label(self) -> str:
        return MEASURE_LABELS[self]


DEFAULT_MEASURE: Final[SetMeasure] = SetMeasure.REPS

MEASURE_LABELS: Final[Mapping[SetMeasure, str]] = MappingProxyType(
    {
        SetMeasure.REPS: "Repetições",
        SetMeasure.DURATION: "Tempo",
        SetMeasure.MAX_REPS: "Máximo de repetições",
    }
)

# Sufixo curto ao lado do número ("12 reps", "60 s", "15 reps máx.").
MEASURE_SHORT_LABELS: Final[Mapping[SetMeasure, str]] = MappingProxyType(
    {
        SetMeasure.REPS: "reps",
        SetMeasure.DURATION: "s",
        SetMeasure.MAX_REPS: "reps máx.",
    }
)

# Limites (inclusivos) do que o Modelo/ficha/série aceitam.
DURATION_SECONDS_MIN: Final[int] = 1  # alvo/registro de série por tempo
DURATION_SECONDS_MAX: Final[int] = 3600
TIME_WINDOW_SECONDS_MIN: Final[int] = 5  # janela do "máximo de reps em N s"
TIME_WINDOW_SECONDS_MAX: Final[int] = 600
MAX_REPS_MIN_REPS: Final[int] = 1  # série de máximo gravada com 0 reps não é série

# Motivo (código estável) para progressão/comparabilidade/carga da próxima
# série declararem a série NEUTRA por causa da medida (consumido no PR T3).
REASON_MEASURE_NOT_REP_BASED: Final[str] = "measure_not_rep_based"

MEASURE_REASON_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        REASON_MEASURE_NOT_REP_BASED: "Série por tempo ou por máximo de repetições: não entra na comparação de carga",
    }
)

# Códigos estáveis das validações (a tela traduz; o teste fixa a lista).
MEASURE_UNKNOWN: Final[str] = "measure_unknown"
DURATION_TARGET_REQUIRED: Final[str] = "duration_target_required"
DURATION_TARGET_OUT_OF_RANGE: Final[str] = "duration_target_out_of_range"
DURATION_TARGET_NOT_ALLOWED: Final[str] = "duration_target_not_allowed"
TIME_WINDOW_OUT_OF_RANGE: Final[str] = "time_window_out_of_range"
TIME_WINDOW_NOT_ALLOWED: Final[str] = "time_window_not_allowed"
REPS_TARGET_NOT_ALLOWED: Final[str] = "reps_target_not_allowed"
ADVANCED_BLOCK_NOT_ALLOWED: Final[str] = "advanced_block_not_allowed"
DURATION_REQUIRED: Final[str] = "duration_required"
DURATION_OUT_OF_RANGE: Final[str] = "duration_out_of_range"
DURATION_NOT_ALLOWED: Final[str] = "duration_not_allowed"
REPS_REQUIRED: Final[str] = "reps_required"
REPS_BELOW_MIN: Final[str] = "reps_below_min"
REPS_NOT_ALLOWED: Final[str] = "reps_not_allowed"

ITEM_VALIDATION_CODES: Final[tuple[str, ...]] = (
    MEASURE_UNKNOWN,
    DURATION_TARGET_REQUIRED,
    DURATION_TARGET_OUT_OF_RANGE,
    DURATION_TARGET_NOT_ALLOWED,
    TIME_WINDOW_OUT_OF_RANGE,
    TIME_WINDOW_NOT_ALLOWED,
    REPS_TARGET_NOT_ALLOWED,
    ADVANCED_BLOCK_NOT_ALLOWED,
)
SET_VALIDATION_CODES: Final[tuple[str, ...]] = (
    MEASURE_UNKNOWN,
    DURATION_REQUIRED,
    DURATION_OUT_OF_RANGE,
    DURATION_NOT_ALLOWED,
    TIME_WINDOW_OUT_OF_RANGE,
    REPS_REQUIRED,
    REPS_BELOW_MIN,
    REPS_NOT_ALLOWED,
)


# Medida fora de `reps` (ou campo de tempo) num item que não é de equipamento
# (Lote T2): a medida só existe no card do modo 'equipment_series'. Fora de
# `ITEM_VALIDATION_CODES` porque não é forma do item: quem a aplica é o
# validador do Modelo, com `declares_non_rep_measure`.
MEASURE_ONLY_ON_EQUIPMENT: Final[str] = "measure_only_on_equipment"

# Campos cuja PRESENÇA marca o "bloco avançado" do card (técnica, aquecimento
# configurado, série de ajuste, pico de contração) — o que
# `validate_item_measure` recebe como `has_advanced_block`. Os companions
# tolerantes (intervalos, aplicações, nº de séries) não contam sozinhos: são os
# mesmos campos-gatilho do 422 "só em equipment" do Modelo.
ADVANCED_BLOCK_PRESENCE_FIELDS: Final[tuple[str, ...]] = (
    "advanced_technique",
    "warmup_reps",
    "feeler_set_reps",
    "peak_contraction_seconds",
)

# Mensagens pt-BR dos códigos de item (a API devolve no 422 do Modelo; lote
# T2). Os limites são interpolados das constantes acima, nunca escritos à mão.
ITEM_VALIDATION_MESSAGES: Final[Mapping[str, str]] = MappingProxyType(
    {
        MEASURE_UNKNOWN: "medida desconhecida (use " + ", ".join(m.value for m in SetMeasure) + ")",
        DURATION_TARGET_REQUIRED: "medida por tempo exige o tempo por série (duration_seconds_target)",
        DURATION_TARGET_OUT_OF_RANGE: (
            f"o tempo por série deve estar entre {DURATION_SECONDS_MIN} e {DURATION_SECONDS_MAX} s"
        ),
        DURATION_TARGET_NOT_ALLOWED: "tempo por série (duration_seconds_target) só existe na medida por tempo",
        TIME_WINDOW_OUT_OF_RANGE: (
            f"a janela do máximo de repetições deve estar entre {TIME_WINDOW_SECONDS_MIN} e "
            f"{TIME_WINDOW_SECONDS_MAX} s"
        ),
        TIME_WINDOW_NOT_ALLOWED: "janela (time_window_seconds) só existe na medida de máximo de repetições",
        REPS_TARGET_NOT_ALLOWED: "alvo de repetições (reps_target) só existe na medida por repetições",
        ADVANCED_BLOCK_NOT_ALLOWED: (
            "técnica avançada, aquecimento, série de ajuste e pico de contração só existem na medida por repetições"
        ),
        MEASURE_ONLY_ON_EQUIPMENT: (
            "medida da série ("
            + " / ".join(MEASURE_LABELS[m].lower() for m in SetMeasure if m is not DEFAULT_MEASURE)
            + ") só é suportada em item_kind='equipment' (modo 'equipment_series')"
        ),
    }
)


def item_validation_message(code: str) -> str:
    """Texto pt-BR de um código de `ITEM_VALIDATION_CODES` (o próprio código
    quando não há texto)."""
    return ITEM_VALIDATION_MESSAGES.get(code, code)


# ---------------------------------------------------------------------------
# Leitura do valor
# ---------------------------------------------------------------------------


def parse_measure(value: Any) -> Optional[SetMeasure]:
    """Valor cru -> medida, ou None quando ausente/vazio/fora do vocabulário.

    Estrita: é o que a validação de entrada usa (um valor desconhecido vindo
    do cliente deve virar 400, nunca `reps` em silêncio)."""
    if value is None:
        return None
    if isinstance(value, SetMeasure):
        return value
    text = str(value).strip().lower()
    if not text:
        return None
    try:
        return SetMeasure(text)
    except ValueError:
        return None


def normalize_measure(value: Any) -> SetMeasure:
    """Leitura tolerante: None/vazio/desconhecido -> `reps` (a medida de toda
    linha anterior à 089 e do relógio antigo). Caixa e espaços ignorados."""
    return parse_measure(value) or DEFAULT_MEASURE


def is_rep_based(measure: Any) -> bool:
    """Só `reps` (ou ausente) alimenta progressão, comparabilidade, e1RM,
    tonelagem e carga da próxima série."""
    return normalize_measure(measure) is SetMeasure.REPS


def measure_label(value: Any) -> Optional[str]:
    """Rótulo pt-BR da medida; None quando o valor é ausente ou desconhecido
    (a tela decide se mostra "Repetições" pelo default ou nada)."""
    measure = parse_measure(value)
    return None if measure is None else MEASURE_LABELS[measure]


# ---------------------------------------------------------------------------
# Fragmentos SQL (MySQL) — mesma regra das funções acima, para quem agrega no
# banco. NULL = `reps` (LEFT JOIN sem série, `exercise_catalog.default_measure`).
# ---------------------------------------------------------------------------

# Mesma validação de identificador fixo de `set_types.py` (`_SQL_COLUMN_RE`):
# repetida aqui porque este módulo é puro (não importa `app.*`). Não é regra de
# motor — só recusa `column` que não seja um identificador do código.
_SQL_COLUMN_RE = re.compile(r"^[A-Za-z0-9_`]+(\.[A-Za-z0-9_`]+)?$")


def _sql_column(column: str) -> str:
    if not _SQL_COLUMN_RE.match(column or ""):
        raise ValueError(f"Coluna SQL inválida para measure: {column!r}")
    return column


def sql_measure_expr(column: str = "es.measure") -> str:
    """`COALESCE(<column>, 'reps')`: a medida efetiva da linha."""
    return f"COALESCE({_sql_column(column)}, '{DEFAULT_MEASURE.value}')"


def sql_is_rep_based(column: str = "es.measure") -> str:
    """Predicado SQL de `is_rep_based`: `(<column> IS NULL OR <column> = 'reps')`."""
    col = _sql_column(column)
    return f"({col} IS NULL OR {col} = '{SetMeasure.REPS.value}')"


def sql_if_rep_based(expr: str, column: str = "es.measure", otherwise: str = "0") -> str:
    """`CASE WHEN <rep-based> THEN <expr> ELSE <otherwise> END` (Lote T3): o
    termo de uma agregação que só vale para série de repetições (tonelagem,
    `volume_kg`, reps × kg). `expr`/`otherwise` são fragmentos fixos do código."""
    return f"CASE WHEN {sql_is_rep_based(column)} THEN {expr} ELSE {otherwise} END"


def sql_duration_seconds(column: str = "es.measure", seconds_column: str = "es.duration_seconds") -> str:
    """Segundos de uma série POR TEMPO (`duration`); NULL nas outras medidas
    (em `max_reps` a coluna guarda a janela, que não é tempo de execução
    medido). Para somar o tempo cronometrado num agregado (Lote T3)."""
    col = _sql_column(column)
    seconds = _sql_column(seconds_column)
    return f"CASE WHEN {col} = '{SetMeasure.DURATION.value}' THEN {seconds} END"


# ---------------------------------------------------------------------------
# Validação de forma (códigos estáveis; a tela traduz)
# ---------------------------------------------------------------------------


def _as_int(value: Any) -> Optional[int]:
    """Inteiro do valor (int, float integral, texto numérico); None quando
    ausente ou ilegível. `bool` não é número aqui."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        number = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")) or number != int(number):
        return None
    return int(number)


def _present(value: Any) -> bool:
    """Campo enviado de fato (None e texto vazio contam como ausentes)."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _within(value: int, low: int, high: int) -> bool:
    return low <= value <= high


def _item_field(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def has_advanced_block(item: Any) -> bool:
    """O item (dict ou objeto) tem o bloco avançado do card: algum campo de
    `ADVANCED_BLOCK_PRESENCE_FIELDS` preenchido."""
    return any(_item_field(item, name) is not None for name in ADVANCED_BLOCK_PRESENCE_FIELDS)


def declares_non_rep_measure(measure: Any, duration_seconds_target: Any, time_window_seconds: Any) -> bool:
    """O item fala de medida diferente de `reps` (ou desconhecida — leitura
    estrita) ou traz campo de tempo da medida."""
    if _present(measure) and parse_measure(measure) is not SetMeasure.REPS:
        return True
    return duration_seconds_target is not None or time_window_seconds is not None


def validate_item_measure(
    measure: Any,
    duration_seconds_target: Any,
    time_window_seconds: Any,
    reps_target: Any,
    has_advanced_block: bool,
) -> list[str]:
    """Forma de um item do Modelo/ficha segundo a medida. Lista de códigos
    (vazia = válido), na ordem dos campos.

    - `reps`: campos de tempo proibidos (`reps_target` segue as regras de hoje).
    - `duration`: `duration_seconds_target` obrigatório em
      [DURATION_SECONDS_MIN, DURATION_SECONDS_MAX]; sem janela, sem
      `reps_target`, sem bloco avançado (técnica/aquecimento/ajuste).
    - `max_reps`: sem alvo de tempo; `time_window_seconds` opcional em
      [TIME_WINDOW_SECONDS_MIN, TIME_WINDOW_SECONDS_MAX]; sem `reps_target`
      ("máximo" não tem alvo), sem bloco avançado.
    `measure` ausente vale `reps`; desconhecido -> `measure_unknown` (só)."""
    if _present(measure) and parse_measure(measure) is None:
        return [MEASURE_UNKNOWN]
    kind = normalize_measure(measure)
    errors: list[str] = []
    if kind is SetMeasure.REPS:
        if _present(duration_seconds_target):
            errors.append(DURATION_TARGET_NOT_ALLOWED)
        if _present(time_window_seconds):
            errors.append(TIME_WINDOW_NOT_ALLOWED)
        return errors
    if kind is SetMeasure.DURATION:
        target = _as_int(duration_seconds_target)
        if not _present(duration_seconds_target):
            errors.append(DURATION_TARGET_REQUIRED)
        elif target is None or not _within(target, DURATION_SECONDS_MIN, DURATION_SECONDS_MAX):
            errors.append(DURATION_TARGET_OUT_OF_RANGE)
        if _present(time_window_seconds):
            errors.append(TIME_WINDOW_NOT_ALLOWED)
    else:  # MAX_REPS
        if _present(duration_seconds_target):
            errors.append(DURATION_TARGET_NOT_ALLOWED)
        if _present(time_window_seconds):
            window = _as_int(time_window_seconds)
            if window is None or not _within(window, TIME_WINDOW_SECONDS_MIN, TIME_WINDOW_SECONDS_MAX):
                errors.append(TIME_WINDOW_OUT_OF_RANGE)
    if _present(reps_target):
        errors.append(REPS_TARGET_NOT_ALLOWED)
    if has_advanced_block:
        errors.append(ADVANCED_BLOCK_NOT_ALLOWED)
    return errors


def validate_set_measure(measure: Any, reps: Any, duration_seconds: Any) -> list[str]:
    """Forma de uma série gravada segundo a medida. Lista de códigos (vazia =
    válido).

    - `reps`: `duration_seconds` proibido (`reps` pode faltar, como hoje).
    - `duration`: `duration_seconds` obrigatório em
      [DURATION_SECONDS_MIN, DURATION_SECONDS_MAX]; `reps` NULL.
    - `max_reps`: `reps` obrigatório, ≥ MAX_REPS_MIN_REPS; `duration_seconds`
      (a janela usada) opcional em [TIME_WINDOW_SECONDS_MIN, TIME_WINDOW_SECONDS_MAX].
    `measure` ausente vale `reps`; desconhecido -> `measure_unknown` (só)."""
    if _present(measure) and parse_measure(measure) is None:
        return [MEASURE_UNKNOWN]
    kind = normalize_measure(measure)
    errors: list[str] = []
    if kind is SetMeasure.REPS:
        if _present(duration_seconds):
            errors.append(DURATION_NOT_ALLOWED)
        return errors
    if kind is SetMeasure.DURATION:
        duration = _as_int(duration_seconds)
        if not _present(duration_seconds):
            errors.append(DURATION_REQUIRED)
        elif duration is None or not _within(duration, DURATION_SECONDS_MIN, DURATION_SECONDS_MAX):
            errors.append(DURATION_OUT_OF_RANGE)
        if _present(reps):
            errors.append(REPS_NOT_ALLOWED)
        return errors
    # MAX_REPS
    count = _as_int(reps)
    if not _present(reps):
        errors.append(REPS_REQUIRED)
    elif count is None or count < MAX_REPS_MIN_REPS:
        errors.append(REPS_BELOW_MIN)
    if _present(duration_seconds):
        window = _as_int(duration_seconds)
        if window is None or not _within(window, TIME_WINDOW_SECONDS_MIN, TIME_WINDOW_SECONDS_MAX):
            errors.append(TIME_WINDOW_OUT_OF_RANGE)
    return errors


# ---------------------------------------------------------------------------
# O que os motores consomem
# ---------------------------------------------------------------------------


def has_non_rep_based(measures: Any) -> bool:
    """Alguma das medidas (iterável de valores crus/`SetMeasure`) não é
    baseada em reps? É o gatilho de neutralidade dos motores (progressão,
    comparabilidade, adoção de carga) no Lote T3."""
    return any(not is_rep_based(measure) for measure in measures or ())


def dose_reps_for_measure(measure: Any, reps: Any) -> Optional[int]:
    """Reps que a dose de recuperação usa para a série: as gravadas em `reps`
    e `max_reps`; None em `duration` (sem reps: o consumidor cai no
    `DOSE_REPS_WHEN_MISSING` de `domain/e1rm.py` — decisão revisável)."""
    if normalize_measure(measure) is SetMeasure.DURATION:
        return None
    return _as_int(reps)


def planned_set_execution_seconds(
    measure: Any, duration_seconds_target: Any, time_window_seconds: Any
) -> Optional[float]:
    """Tempo de execução planejado de UMA série quando a medida o define:
    o alvo em `duration`, a janela em `max_reps` (se houver). None = o
    estimador de tempo usa a fórmula de reps de sempre."""
    kind = normalize_measure(measure)
    if kind is SetMeasure.DURATION:
        target = _as_int(duration_seconds_target)
        return None if target is None else float(target)
    if kind is SetMeasure.MAX_REPS:
        window = _as_int(time_window_seconds)
        return None if window is None else float(window)
    return None


# ---------------------------------------------------------------------------
# Exibição
# ---------------------------------------------------------------------------

EMPTY_MEASURE_TEXT: Final[str] = "—"


def _seconds_text(seconds: int) -> str:
    return f"{seconds} {MEASURE_SHORT_LABELS[SetMeasure.DURATION]}"


def _reps_text(reps: int) -> str:
    return f"{reps} {MEASURE_SHORT_LABELS[SetMeasure.REPS]}"


def format_set_measure(measure: Any, reps: Any, duration_seconds: Any) -> str:
    """Texto pt-BR da série pela medida: "10 reps" / "60 s" /
    "15 reps em 30 s" (`max_reps` com janela; sem janela, "15 reps").
    Sem o dado que a medida exige, `EMPTY_MEASURE_TEXT`. O espelho
    `set-measures.ts` (`formatSetMeasureText`) produz o MESMO texto (paridade)."""
    kind = normalize_measure(measure)
    count = _as_int(reps)
    seconds = _as_int(duration_seconds)
    if kind is SetMeasure.DURATION:
        return EMPTY_MEASURE_TEXT if seconds is None else _seconds_text(seconds)
    if count is None:
        return EMPTY_MEASURE_TEXT
    if kind is SetMeasure.MAX_REPS and seconds is not None:
        return f"{_reps_text(count)} em {_seconds_text(seconds)}"
    return _reps_text(count)


# ---------------------------------------------------------------------------
# Espelho do frontend
# ---------------------------------------------------------------------------


def export_for_frontend() -> dict[str, Any]:
    """Bloco `set_measures` de `motor-constants.generated.json`
    (`test_m76_lote1b_espelhos_fe.py --gravar`); `set-measures.ts` lê daqui."""
    return {
        "measures": [
            {
                "value": measure.value,
                "label": MEASURE_LABELS[measure],
                "short_label": MEASURE_SHORT_LABELS[measure],
                "rep_based": is_rep_based(measure),
            }
            for measure in SetMeasure
        ],
        "default_measure": DEFAULT_MEASURE.value,
        # Valor de cada medida pelo nome, para o espelho comparar sem literal
        # (`kind === VALUES.duration`), como `set-types.ts` faz com `working`.
        "values": {measure.name.lower(): measure.value for measure in SetMeasure},
        "limits": {
            "duration_seconds_min": DURATION_SECONDS_MIN,
            "duration_seconds_max": DURATION_SECONDS_MAX,
            "time_window_seconds_min": TIME_WINDOW_SECONDS_MIN,
            "time_window_seconds_max": TIME_WINDOW_SECONDS_MAX,
            "max_reps_min_reps": MAX_REPS_MIN_REPS,
        },
        # Campos cuja presença marca o bloco avançado (Lote T4): o editor do
        # Modelo limpa ao menos estes ao sair de `reps` (teste no FE).
        "advanced_block_presence_fields": list(ADVANCED_BLOCK_PRESENCE_FIELDS),
        "empty_text": EMPTY_MEASURE_TEXT,
        "reason_not_rep_based": REASON_MEASURE_NOT_REP_BASED,
        "reason_labels": dict(MEASURE_REASON_LABELS),
    }


__all__ = [
    "ADVANCED_BLOCK_NOT_ALLOWED",
    "ADVANCED_BLOCK_PRESENCE_FIELDS",
    "MEASURE_ONLY_ON_EQUIPMENT",
    "declares_non_rep_measure",
    "has_advanced_block",
    "DEFAULT_MEASURE",
    "DURATION_NOT_ALLOWED",
    "DURATION_OUT_OF_RANGE",
    "DURATION_REQUIRED",
    "DURATION_SECONDS_MAX",
    "DURATION_SECONDS_MIN",
    "DURATION_TARGET_NOT_ALLOWED",
    "DURATION_TARGET_OUT_OF_RANGE",
    "DURATION_TARGET_REQUIRED",
    "EMPTY_MEASURE_TEXT",
    "ITEM_VALIDATION_CODES",
    "ITEM_VALIDATION_MESSAGES",
    "MAX_REPS_MIN_REPS",
    "MEASURE_LABELS",
    "MEASURE_REASON_LABELS",
    "MEASURE_SHORT_LABELS",
    "MEASURE_UNKNOWN",
    "REASON_MEASURE_NOT_REP_BASED",
    "REPS_BELOW_MIN",
    "REPS_NOT_ALLOWED",
    "REPS_REQUIRED",
    "REPS_TARGET_NOT_ALLOWED",
    "SET_VALIDATION_CODES",
    "SetMeasure",
    "TIME_WINDOW_NOT_ALLOWED",
    "TIME_WINDOW_OUT_OF_RANGE",
    "TIME_WINDOW_SECONDS_MAX",
    "TIME_WINDOW_SECONDS_MIN",
    "dose_reps_for_measure",
    "export_for_frontend",
    "format_set_measure",
    "has_non_rep_based",
    "is_rep_based",
    "item_validation_message",
    "measure_label",
    "normalize_measure",
    "parse_measure",
    "planned_set_execution_seconds",
    "sql_duration_seconds",
    "sql_if_rep_based",
    "sql_is_rep_based",
    "sql_measure_expr",
    "validate_item_measure",
    "validate_set_measure",
]
