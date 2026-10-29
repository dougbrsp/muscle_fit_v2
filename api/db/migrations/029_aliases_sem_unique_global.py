"""029 - `exercise_aliases` sem o UNIQUE global por `alias` (formato da Aurora).

`uq_exercise_aliases_alias` (UNIQUE (alias)) nasce no 001 e existe em todo
banco criado pelas migracoes do repositorio (DEV, CI, `bootstrap_local_db.py`).
A 005 trocou o UNIQUE antigo (mapping_id, alias) por
`uq_exercise_aliases_exercise_alias` (exercise_id, alias), mas nunca removeu o
global. Na Aurora de producao (information_schema extraido em 2026-09-14) a
tabela tem so PRIMARY, `idx_exercise_aliases_exercise` (exercise_id) e
`uq_exercise_aliases_exercise_alias` (exercise_id, alias). Efeito da
divergencia: o mesmo apelido para OUTRO exercicio dava erro de chave duplicada
em DEV/CI e era gravado em producao.

Decisao do usuario (2026-09-14): apelido repetido em exercicios diferentes e
aceito, como em producao; o mesmo apelido no mesmo exercicio continua barrado
por `uq_exercise_aliases_exercise_alias`. Nenhum indice novo e criado. Busca por `alias` (`WHERE alias = %s`) fica sem indice, como na Aurora
(~412 linhas).

Fluxo: `plan(db)` so LE `information_schema` (2 leituras, nenhuma contagem) e
devolve os statements; `up(db)` executa o plano. Falha antes de qualquer
statement se:

- a tabela nao existe ou nao e InnoDB;
- `uq_exercise_aliases_exercise_alias` nao e UNIQUE (exercise_id, alias): sem
  ele, remover o global deixaria de barrar ate o apelido repetido no MESMO
  exercicio;
- `uq_exercise_aliases_alias` existe com outro formato (nao UNIQUE (alias)).

Na Aurora: 0 statements. DEV/CI: 1 statement (`DROP INDEX`). Reexecucao: 0.
Data API: sem PREPARE/SET/multi-statement; um statement por chamada.
"""

from __future__ import annotations

TABLE = "exercise_aliases"
GLOBAL_UNIQUE = "uq_exercise_aliases_alias"
EXERCISE_ALIAS_UNIQUE = "uq_exercise_aliases_exercise_alias"


class AliasesUniqueGlobalError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise AliasesUniqueGlobalError(f"029: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _read_indexes(db) -> dict[str, dict]:
    indexes: dict[str, dict] = {}
    for row in db.query(
        "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s) ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX",
        (TABLE,),
    ):
        entry = indexes.setdefault(_text(row["INDEX_NAME"]), {"columns": [], "unique": int(row["NON_UNIQUE"]) == 0})
        entry["columns"].append(_text(row["COLUMN_NAME"]))
    return indexes


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    tables = db.query(
        "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
        (TABLE,),
    )
    if not tables:
        _fail(f"tabela {TABLE} nao existe")
    engine = _text(tables[0]["ENGINE"])
    if engine.lower() != "innodb":
        _fail(f"{TABLE} usa ENGINE={engine}, esperado InnoDB")

    indexes = _read_indexes(db)
    per_exercise = indexes.get(EXERCISE_ALIAS_UNIQUE)
    if per_exercise is None or not per_exercise["unique"] or per_exercise["columns"] != ["exercise_id", "alias"]:
        _fail(f"{EXERCISE_ALIAS_UNIQUE} ausente ou diferente de UNIQUE (exercise_id, alias) em {TABLE}")

    global_unique = indexes.get(GLOBAL_UNIQUE)
    if global_unique is None:
        return []
    if not global_unique["unique"] or global_unique["columns"] != ["alias"]:
        _fail(f"{GLOBAL_UNIQUE} existe como {global_unique}, esperado UNIQUE (alias)")
    return [(f"ALTER TABLE {TABLE} DROP INDEX {GLOBAL_UNIQUE}", None)]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)
