"""Classificacao canonica de series (`exercise_sets.set_type`).

Fonte unica de verdade para o vocabulario de `set_type` (plano de
refatoracao, secao 5.3):

| set_type                                   | progressao / agregados          |
|--------------------------------------------|---------------------------------|
| working                                    | conta (carga, reps, RPE)        |
| warmup, feeler (serie de ajuste)           | ignora (series preparatorias)   |
| laboral (M100: aquecimento/alongamento)    | ignora (preparatoria): so conta |
|                                            | no tempo de treino              |
| rest_pause, drop_set, forced_rep           | aplicacao de tecnica: nunca e   |
|                                            | carga base de progressao        |
| backoff                                    | serie de trabalho reduzida      |
| dropset (legado)                           | normalizado para drop_set       |

A coluna `exercise_sets.set_type` continua sendo string livre no banco. A
normalizacao acontece NA LEITURA (e na gravacao de series novas) — nao ha
UPDATE em massa aqui. Valores legados sem `set_type` (NULL/vazio) sao
tratados como `working`, que era o default historico da coluna.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Optional

WORKING = "working"
WARMUP = "warmup"
FEELER = "feeler"
REST_PAUSE = "rest_pause"
DROP_SET = "drop_set"
FORCED_REP = "forced_rep"
BACKOFF = "backoff"
# M100 (2026-10-27): serie de exercicio LABORAL (catalogo `is_laboral`).
# Preparatoria: fora de progressao, recorde/e1RM, volume, ativacao,
# recuperacao e insights - conta so no tempo de treino (`domain/laboral.py`).
LABORAL = "laboral"

# Aliases legados/variantes de escrita -> valor canonico.
_ALIASES: dict[str, str] = {
    "dropset": DROP_SET,
    "drop-set": DROP_SET,
    "drop set": DROP_SET,
    "restpause": REST_PAUSE,
    "rest-pause": REST_PAUSE,
    "rest pause": REST_PAUSE,
    "forced_reps": FORCED_REP,
    "warm_up": WARMUP,
    "warm-up": WARMUP,
    "feeler_set": FEELER,
}

PREPARATORY_SET_TYPES: frozenset[str] = frozenset({WARMUP, FEELER, LABORAL})
TECHNIQUE_APPLICATION_SET_TYPES: frozenset[str] = frozenset({REST_PAUSE, DROP_SET, FORCED_REP})
KNOWN_SET_TYPES: frozenset[str] = frozenset({WORKING, BACKOFF}) | PREPARATORY_SET_TYPES | TECHNIQUE_APPLICATION_SET_TYPES


def normalize_set_type(value: Any) -> str:
    """Normaliza um `set_type` para o vocabulario canonico.

    - None/vazio -> `working` (default historico da coluna);
    - caixa/espacos ignorados;
    - aliases legados (`dropset` etc.) -> valor canonico;
    - valores desconhecidos sao devolvidos normalizados (lower/strip), sem
      virar `working` silenciosamente — quem le decide como tratar.
    """
    if value is None:
        return WORKING
    text = str(value).strip().lower()
    if not text:
        return WORKING
    return _ALIASES.get(text, text)


def _type_of(set_or_type: Any) -> str:
    if isinstance(set_or_type, Mapping):
        return normalize_set_type(set_or_type.get("set_type"))
    return normalize_set_type(set_or_type)


def is_working_set(set_or_type: Any) -> bool:
    """True para series de trabalho (`working`, incluindo legado sem set_type)."""
    return _type_of(set_or_type) == WORKING


def is_laboral_set(set_or_type: Any) -> bool:
    """True para serie de exercicio laboral (M100)."""
    return _type_of(set_or_type) == LABORAL


def is_technique_application(set_or_type: Any) -> bool:
    """True para aplicacoes de tecnica avancada (rest_pause/drop_set/forced_rep)."""
    return _type_of(set_or_type) in TECHNIQUE_APPLICATION_SET_TYPES


def is_preparatory_set(set_or_type: Any) -> bool:
    """True para series preparatorias (aquecimento/serie de ajuste/laboral)."""
    return _type_of(set_or_type) in PREPARATORY_SET_TYPES


def _field(set_or_row: Any, name: str) -> Any:
    if isinstance(set_or_row, Mapping):
        return set_or_row.get(name)
    return getattr(set_or_row, name, None)


def is_linked_application(set_or_row: Any) -> bool:
    """Linha gravada como `working` (ou sem tipo) que na verdade e APLICACAO
    de tecnica: tem `technique_code` de tecnica com aplicacoes (nao FST-7/GVT)
    E o vinculo com a serie de trabalho que a originou (`anchor_set_number`,
    059). Serie de FST-7/GVT e serie de trabalho comum; `technique_code` sem
    ancora (a propria ancora marcada, registro antigo) nao muda nada.

    Aceita dicionario (linha do banco) ou objeto com os atributos
    (`training_history.SetRecord`). M76 Lote 3b: a definicao morava so no
    motor de progressao (`progression_engine`); agora e do dono do
    vocabulario e o motor importa daqui."""
    # Import local: `technique_accounting` importa este modulo.
    from .domain.technique_accounting import is_sets_only_technique

    code = str(_field(set_or_row, "technique_code") or "").strip()
    return bool(code) and _field(set_or_row, "anchor_set_number") is not None and not is_sets_only_technique(code)


# ---------------------------------------------------------------------------
# Fragmentos SQL (MySQL) — mesma regra das funcoes acima, para filtrar direto
# nas queries agregadas. Legado NULL = working. Uso recomendado no ON de um
# LEFT JOIN de exercise_sets (preserva a linha do exercicio/atividade quando
# todas as series forem filtradas).
# ---------------------------------------------------------------------------

_SQL_COLUMN_RE = re.compile(r"^[A-Za-z0-9_`]+(\.[A-Za-z0-9_`]+)?$")


def _sql_quote_list(values: Iterable[str]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in sorted(values)) + ")"


def sql_set_type_expr(column: str = "es.set_type") -> str:
    """`COALESCE(LOWER(TRIM(<column>)), 'working')`. `column` e um
    identificador fixo do codigo (nunca entrada do usuario) — validado."""
    if not _SQL_COLUMN_RE.match(column or ""):
        raise ValueError(f"Coluna SQL invalida para set_type: {column!r}")
    return f"COALESCE(LOWER(TRIM({column})), '{WORKING}')"


def _raw_values_of(canonical: Iterable[str]) -> set[str]:
    """Valores CRUS (lower/trim) que `normalize_set_type` resolve para algum
    dos tipos canonicos `canonical`: o proprio canonico e seus apelidos de
    `_ALIASES`. E o que um predicado SQL precisa listar para dar a mesma
    resposta que a funcao Python (M76 Lote 3b)."""
    wanted = set(canonical)
    return wanted | {alias for alias, target in _ALIASES.items() if target in wanted}


def sql_not_preparatory(column: str = "es.set_type") -> str:
    """Predicado SQL: serie NAO e aquecimento/ajuste - INCLUINDO as grafias
    legadas (`warm-up`, `feeler_set`...), a mesma resposta de
    `not is_preparatory_set` (M76 Lote 3b)."""
    return f"{sql_set_type_expr(column)} NOT IN {_sql_quote_list(_raw_values_of(PREPARATORY_SET_TYPES))}"


def sql_canonical_set_type_case(column: str = "es.set_type", codes: Optional[Mapping[str, str]] = None) -> str:
    """`CASE` SQL que devolve o `set_type` CANONICO da linha (apelidos
    resolvidos, NULL/vazio = `working`) ou, com `codes`, o codigo curto de cada
    canonico (`{"working": "0", ...}`) - para quem empacota series em texto
    (`recovery_read_model`). Valor fora do vocabulario cai no `ELSE`: o
    proprio texto normalizado sem `codes`, o codigo de `working` com `codes`
    (o modelo de recuperacao trata desconhecido como fator 1,00, igual a
    trabalho). Mesmo mapeamento de `normalize_set_type`."""
    expr = sql_set_type_expr(column)
    pairs: list[tuple[str, str]] = []
    for canonical in sorted(KNOWN_SET_TYPES):
        for raw in sorted(_raw_values_of({canonical})):
            pairs.append((raw, canonical))
    pairs.append(("", WORKING))
    if codes is not None:
        missing = KNOWN_SET_TYPES - set(codes)
        if missing:
            raise ValueError(f"codigos ausentes para set_type: {sorted(missing)}")
        whens = "\n".join(f"    WHEN '{raw}' THEN '{codes[canonical]}'" for raw, canonical in pairs)
        fallback = f"'{codes[WORKING]}'"
    else:
        whens = "\n".join(f"    WHEN '{raw}' THEN '{canonical}'" for raw, canonical in pairs)
        fallback = expr
    return f"CASE {expr}\n{whens}\n    ELSE {fallback}\n  END"


def sql_is_technique_application(column: str = "es.set_type") -> str:
    """Predicado SQL: aplicacao de tecnica (rest_pause/drop_set/forced_rep),
    INCLUINDO as grafias legadas de `_ALIASES` (`dropset`, `rest-pause`...)
    - a mesma lista que `normalize_set_type` resolve na leitura."""
    raw_values = set(TECHNIQUE_APPLICATION_SET_TYPES) | {
        alias for alias, canonical in _ALIASES.items() if canonical in TECHNIQUE_APPLICATION_SET_TYPES
    }
    return f"{sql_set_type_expr(column)} IN {_sql_quote_list(raw_values)}"


def sql_is_working(column: str = "es.set_type") -> str:
    """Predicado SQL: serie de trabalho (`working`, NULL ou vazio)."""
    return f"{sql_set_type_expr(column)} IN ('', '{WORKING}')"


# Tudo que `normalize_set_type` sabe interpretar: o vocabulario canonico, os
# aliases legados (`dropset`, `warm-up`...) e a string vazia (que a expressao
# SQL produz para NULL/vazio e que a leitura trata como `working`). Qualquer
# outro valor e desconhecido de fato.
KNOWN_SET_TYPE_SQL_VALUES: frozenset[str] = KNOWN_SET_TYPES | frozenset(_ALIASES) | frozenset({""})


def sql_is_unknown_set_type(column: str = "es.set_type") -> str:
    """Predicado SQL: `set_type` FORA do vocabulario (nem canonico, nem alias).

    Usado pela regra de qualidade `SETS_WITH_UNKNOWN_SET_TYPE` (plano §8.3):
    a coluna e string livre no banco, entao um valor novo entra sem erro e so
    aparece aqui."""
    return f"{sql_set_type_expr(column)} NOT IN {_sql_quote_list(KNOWN_SET_TYPE_SQL_VALUES)}"


# ---------------------------------------------------------------------------
# Série com repetições PENDENTES (Modo Intensivo, migração 092)
# ---------------------------------------------------------------------------
# No Modo Intensivo (FST-7 e afins, descanso de 30 s) a série pode ser
# concluída com um toque e as reps informadas depois: a linha é gravada com
# `reps` NULL e `reps_pending = 1`. Até a confirmação (PATCH da série) ela NÃO
# existe para nenhum motor nem leitura analítica (volume, e1RM/recorde,
# comparabilidade, `technique_accounting`, fadiga, ativação, progressão,
# relatórios e agregados de `exercises`): sem isso o fallback de dose
# (`e1rm.dose_inputs`, 8 reps quando falta) contaria uma série que ninguém
# sabe quantas reps teve. Regra ÚNICA: toda leitura de `exercise_sets` passa
# por `sql_set_confirmed` (SQL) ou `is_pending_set`/`confirmed_sets` (Python).
# Guarda: `tests/unit/test_guarda_series_pendentes.py`.

REPS_PENDING_COLUMN = "reps_pending"
# Forma do predicado com a coluna padrão (`es.`); `sql_set_confirmed` troca o
# identificador. 0 = confirmada (DEFAULT da coluna na migração).
_SQL_SET_CONFIRMED_DEFAULT_COLUMN = "es.reps_pending"
_SQL_SET_CONFIRMED_TEMPLATE = "es.reps_pending = 0"


def sql_set_confirmed(column: str = "es.reps_pending") -> str:
    """Predicado SQL: série CONFIRMADA (`reps_pending = 0`). Linha anterior à
    092 tem 0 pelo DEFAULT, então o histórico inteiro passa. Uso: no ON de um
    LEFT JOIN de `exercise_sets` (preserva o exercício cujas séries estão todas
    pendentes) ou no WHERE de um JOIN/FROM. `column` é identificador fixo do
    código, validado como os demais fragmentos."""
    if not _SQL_COLUMN_RE.match(column or ""):
        raise ValueError(f"Coluna SQL invalida para reps_pending: {column!r}")
    return _SQL_SET_CONFIRMED_TEMPLATE.replace(_SQL_SET_CONFIRMED_DEFAULT_COLUMN, column, 1)


def is_pending_set(set_or_row: Any) -> bool:
    """True para série com reps pendentes (`reps_pending` verdadeiro). Linha
    sem a chave (leitura antiga, payload de importação) = confirmada."""
    value = _field(set_or_row, REPS_PENDING_COLUMN)
    if value is None or isinstance(value, str) and not value.strip():
        return False
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return bool(value)


def confirmed_sets(sets: Iterable[Any] | None) -> list[Any]:
    """Remove as séries com reps pendentes (mesma regra de `sql_set_confirmed`)."""
    return [s for s in (sets or []) if not is_pending_set(s)]


def working_sets(sets: Iterable[Mapping[str, Any]] | None) -> list[Mapping[str, Any]]:
    """Filtra apenas series `working` (legado sem set_type conta como working)."""
    return [s for s in (sets or []) if is_working_set(s)]


def non_preparatory_sets(sets: Iterable[Mapping[str, Any]] | None) -> list[Mapping[str, Any]]:
    """Remove aquecimento/ajuste, mantendo trabalho e aplicacoes de tecnica."""
    return [s for s in (sets or []) if not is_preparatory_set(s)]
