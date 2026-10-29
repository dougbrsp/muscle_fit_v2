"""Vocabulario e deteccao da tabela `context_events` (migracao 048, F2.1).

Compartilhado por TRES consumidores, e por isso mora fora dos dois:
- o CRUD da api-core (`app/context_events.py`);
- a leitura da engines (`app/intelligence/dashboard/context_service.py`);
- as regras de insight, que so precisam do rotulo do tipo.

Por que o vocabulario esta AQUI e nao no router (como a 047 fez com
`MEASUREMENT_SITES` em `app/body_measurements.py`, obrigando o `body_service`
a manter uma copia dos rotulos): este modulo nao importa FastAPI nem
`security`, entao a engines pode importa-lo sem arrastar o router da api-core.
Um enum, um lugar - o teste de plano da 048 compara esta tupla com o ENUM da
migracao.

Janela de deploy: o codigo novo pode atender requisicoes ANTES de a 048
rodar. Nesse intervalo a leitura devolve vazio com a limitacao declarada e a
escrita responde 503 citando a migracao - nunca 500, nunca DDL por aqui (quem
cria a tabela e a 048, pelo pipeline).

Cache SO DO POSITIVO, chaveado por SCHEMA - a mesma regra (e a mesma licao,
PR #182) de `load_conventions.has_load_convention_column` e de
`body_measurements_schema`: "a tabela existe" e permanente; "nao existe" e
apenas "a migracao ainda nao rodou" e memoriza-la envenenaria o processo ate
um restart.
"""

from __future__ import annotations

from typing import Any

from .load_conventions import current_schema

TABLE = "context_events"
MIGRATION = "048"

# Espelho do ENUM da 048, na ordem canonica.
EVENT_TYPES: tuple[str, ...] = (
    "alcohol",
    "travel",
    "illness",
    "atypical_sleep",
    "gym_change",
    "plan_change",
    "diet_phase",
    "supplement",
    "injury_pain",
    "other",
)

EVENT_SOURCES: tuple[str, ...] = ("manual", "derived")

EVENT_TYPE_LABELS: dict[str, str] = {
    "alcohol": "Álcool",
    "travel": "Viagem",
    "illness": "Doença",
    "atypical_sleep": "Sono atípico",
    "gym_change": "Troca de academia",
    "plan_change": "Troca de ficha/modelo",
    "diet_phase": "Fase de dieta",
    "supplement": "Suplementação",
    "injury_pain": "Lesão ou dor",
    "other": "Outro",
}

# Tipos que a F2.1 DERIVA na leitura (nunca materializa): o registro manual
# deles continua permitido - o usuario pode anotar uma troca de academia que o
# sistema nao tem como ver (mudou de unidade da mesma rede, por exemplo) -, mas
# a timeline marca a origem de cada um.
DERIVED_EVENT_TYPES: tuple[str, ...] = ("plan_change", "gym_change")

INTENSITY_MIN = 1
INTENSITY_MAX = 5

TABLE_MISSING_LIMITATION = (
    "Eventos de contexto indisponíveis: a tabela `context_events` (migração 048) ainda não "
    "existe neste banco."
)

_TABLE_EXISTS_SQL = (
    "SELECT COUNT(*) AS n FROM information_schema.TABLES "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
)

_schemas_with_table: set[str] = set()


def normalize_event_type(value: Any) -> str:
    """`event_type` no vocabulario da 048 (minusculo, sem espacos) ou
    `ValueError`."""
    normalized = str(value).strip().lower()
    if normalized not in EVENT_TYPES:
        raise ValueError(f"event_type inválido: {value!r}; use um de {', '.join(EVENT_TYPES)}")
    return normalized


def event_type_label(value: Any) -> str:
    """Rotulo em portugues; tipo desconhecido devolve o proprio codigo (a tela
    nunca fica em branco por causa de um valor novo no banco)."""
    return EVENT_TYPE_LABELS.get(str(value), str(value))


def has_context_events_table(cursor: Any) -> bool:
    """`context_events` ja existe NESTE schema? Sem DDL."""
    schema = current_schema(cursor)
    if schema in _schemas_with_table:
        return True
    cursor.execute(_TABLE_EXISTS_SQL, (TABLE,))
    row = cursor.fetchone()
    exists = int((row or {}).get("n") or 0) > 0
    if exists:
        _schemas_with_table.add(schema)
    return exists


def reset_context_events_table_cache() -> None:
    """Esquece a deteccao de TODOS os schemas (so importa para um teste que
    DESFAZ a 048 e volta o schema a um estado anterior a ela)."""
    _schemas_with_table.clear()


__all__ = [
    "DERIVED_EVENT_TYPES",
    "EVENT_SOURCES",
    "EVENT_TYPES",
    "EVENT_TYPE_LABELS",
    "INTENSITY_MAX",
    "INTENSITY_MIN",
    "MIGRATION",
    "TABLE",
    "TABLE_MISSING_LIMITATION",
    "event_type_label",
    "has_context_events_table",
    "normalize_event_type",
    "reset_context_events_table_cache",
]
