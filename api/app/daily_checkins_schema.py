"""Vocabulario e deteccao da tabela `daily_checkins` (migracao 050, F2.2).

Compartilhado por DOIS consumidores, e por isso mora fora dos dois (mesma
decisao do `context_events_schema` da F2.1):
- o CRUD da api-core (`app/daily_checkins.py`);
- a leitura da engines (`app/intelligence/dashboard/checkin_service.py`), que
  alimenta os sinais subjetivos de `recovery_signals_service`.

Este modulo NAO importa FastAPI nem `security`, entao a engines pode importa-lo
sem arrastar o router da api-core. Um vocabulario, um lugar - o teste de plano
da 050 compara esta tupla com as colunas da migracao.

Janela de deploy: o codigo novo pode atender requisicoes ANTES de a 050 rodar.
Nesse intervalo a leitura devolve vazio com a limitacao declarada e a escrita
responde 503 citando a migracao - nunca 500, nunca DDL por aqui (quem cria a
tabela e a 050, pelo pipeline).

Cache SO DO POSITIVO, chaveado por SCHEMA - a mesma regra (e a mesma licao,
PR #182) de `load_conventions.has_load_convention_column`,
`body_measurements_schema` e `context_events_schema`: "a tabela existe" e
permanente; "nao existe" e apenas "a migracao ainda nao rodou" e memoriza-la
envenenaria o processo ate um restart.
"""

from __future__ import annotations

from typing import Any

from .load_conventions import current_schema

TABLE = "daily_checkins"
MIGRATION = "050"

# Espelho das colunas de escore da 050, na ordem canonica.
SCORE_FIELDS: tuple[str, ...] = ("energy", "soreness", "stress", "mood")

SCORE_LABELS: dict[str, str] = {
    "energy": "Disposição",
    "soreness": "Dor muscular",
    "stress": "Estresse",
    "mood": "Humor",
}

# `True` = escore ALTO e bom (disposicao, humor); `False` = escore alto e ruim
# (dor, estresse). Unica fonte dessa direcao: o CRUD nao precisa dela, mas os
# sinais de recuperacao precisam, e ter duas tabelas de direcao seria a
# primeira divergencia a aparecer.
SCORE_HIGHER_IS_BETTER: dict[str, bool] = {
    "energy": True,
    "soreness": False,
    "stress": False,
    "mood": True,
}

SCORE_MIN = 1
SCORE_MAX = 5

TABLE_MISSING_LIMITATION = (
    "Check-in diário indisponível: a tabela `daily_checkins` (migração 050) ainda não existe "
    "neste banco."
)

NO_CHECKIN_LIMITATION = (
    "Nenhum check-in registrado nesta janela: ausência de check-in não é ausência de sintoma — "
    "só quer dizer que nada foi respondido em /dashboard/checkin."
)

_TABLE_EXISTS_SQL = (
    "SELECT COUNT(*) AS n FROM information_schema.TABLES "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
)

_schemas_with_table: set[str] = set()


def normalize_score(value: Any, *, field: str) -> int | None:
    """`1..5` ou `None`. Zero NAO e aceito: "nao respondido" e ausencia, nao o
    menor grau possivel - a mesma regra de `null != 0` do resto do hub (e a
    mesma de `context_events.intensity`)."""
    if value is None:
        return None
    number = int(value)
    if number < SCORE_MIN or number > SCORE_MAX:
        raise ValueError(
            f"{field} deve estar entre {SCORE_MIN} e {SCORE_MAX} (ou ausente)"
        )
    return number


def score_label(field: Any) -> str:
    """Rotulo em portugues; campo desconhecido devolve o proprio codigo (a tela
    nunca fica em branco por causa de um valor novo no banco)."""
    return SCORE_LABELS.get(str(field), str(field))


def has_daily_checkins_table(cursor: Any) -> bool:
    """`daily_checkins` ja existe NESTE schema? Sem DDL."""
    schema = current_schema(cursor)
    if schema in _schemas_with_table:
        return True
    cursor.execute(_TABLE_EXISTS_SQL, (TABLE,))
    row = cursor.fetchone()
    exists = int((row or {}).get("n") or 0) > 0
    if exists:
        _schemas_with_table.add(schema)
    return exists


def reset_daily_checkins_table_cache() -> None:
    """Esquece a deteccao de TODOS os schemas (so importa para um teste que
    DESFAZ a 050 e volta o schema a um estado anterior a ela)."""
    _schemas_with_table.clear()


__all__ = [
    "MIGRATION",
    "NO_CHECKIN_LIMITATION",
    "SCORE_FIELDS",
    "SCORE_HIGHER_IS_BETTER",
    "SCORE_LABELS",
    "SCORE_MAX",
    "SCORE_MIN",
    "TABLE",
    "TABLE_MISSING_LIMITATION",
    "has_daily_checkins_table",
    "normalize_score",
    "reset_daily_checkins_table_cache",
    "score_label",
]
