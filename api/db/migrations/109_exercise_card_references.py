"""109 - Fotos de referencia dos cards de exercicios (V3 fotorrealista).

    exercise_card_references                       (tabela nova)
      id            BIGINT AUTO_INCREMENT PK
      exercise_id   BIGINT UNSIGNED NOT NULL   ref. LOGICA a exercise_catalog(id), SEM FK
      s3_key        VARCHAR(255) NOT NULL      exercise-card-references/{exercise_id}/{id}.png
      content_type  VARCHAR(40)  NOT NULL      sempre 'image/png' (re-encode no upload)
      width/height/bytes  INT NOT NULL         da imagem JA normalizada
      note          VARCHAR(160) NULL          dica do admin (opcional)
      created_by    BIGINT NOT NULL            users.id, SEM FK
      created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
      deleted_at    DATETIME NULL              soft-delete (NULL = ativa)
      KEY idx_ecr_exercise_active (exercise_id, deleted_at)

--- Decisoes do DDL -------------------------------------------------------

1. **Sem FOREIGN KEY.** `exercise_id` aponta para `exercise_catalog(id)`, e a
   028 ENUMERA as FKs validas para essa tabela e recusa qualquer FK nova (mesma
   decisao da 064). O tipo e o certo (`BIGINT UNSIGNED`, conferido abaixo
   contra `exercise_catalog.id`), sem a constraint. `created_by` tambem sem FK:
   a referencia pertence ao CATALOGO, nao ao admin.

2. **Soft-delete (`deleted_at`)**: a foto some da lista e do worker na hora; o
   objeto do S3 e removido em melhor esforco pela rota. O limite de 3 fotos
   ATIVAS e do servico (transacao com a linha do exercicio travada + contagem),
   nao do banco.

3. **Sem DML**: a tabela nasce vazia. Nenhum dado de DEV vai para producao.

--- Fluxo -----------------------------------------------------------------

`plan(db)` so LE `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` sao o inverso.

- tabela ausente -> 1 `CREATE TABLE`;
- presente no formato esperado -> nada (reexecucao: 0 statements);
- `exercise_catalog.id` que nao e BIGINT UNSIGNED PK numa tabela InnoDB, ENGINE
  da tabela != InnoDB ou coluna/tipo/nulabilidade/indice diferente ->
  `ExerciseCardReferencesError` ANTES do primeiro statement.

`down` -> `DROP TABLE IF EXISTS` (reversivel: perde so as fotos anexadas; o
codigo anterior nao le a tabela).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
modulo autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

TABLE = "exercise_card_references"
CATALOG_TABLE = "exercise_catalog"

ACTIVE_INDEX = "idx_ecr_exercise_active"

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  exercise_id BIGINT UNSIGNED NOT NULL,
  s3_key VARCHAR(255) NOT NULL,
  content_type VARCHAR(40) NOT NULL,
  width INT NOT NULL,
  height INT NOT NULL,
  bytes INT NOT NULL,
  note VARCHAR(160) NULL,
  created_by BIGINT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at DATETIME NULL,
  KEY {ACTIVE_INDEX} (exercise_id, deleted_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_TABLE_SQL = f"DROP TABLE IF EXISTS {TABLE}"

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "exercise_id": ("bigint unsigned", "NO"),
    "s3_key": ("varchar(255)", "NO"),
    "content_type": ("varchar(40)", "NO"),
    "width": ("int", "NO"),
    "height": ("int", "NO"),
    "bytes": ("int", "NO"),
    "note": ("varchar(160)", "YES"),
    "created_by": ("bigint", "NO"),
    "created_at": ("datetime", "NO"),
    "deleted_at": ("datetime", "YES"),
}

# indice -> (colunas na ordem, unico?)
EXPECTED_INDEXES: dict[str, tuple[tuple[str, ...], bool]] = {
    ACTIVE_INDEX: (("exercise_id", "deleted_at"), False),
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class ExerciseCardReferencesError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise ExerciseCardReferencesError(f"109: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    # `bigint(20) unsigned` -> `bigint unsigned`; `int(11)` -> `int`
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read(db) -> tuple[dict[str, str], dict[tuple[str, str], dict], dict[str, list[dict]]]:
    names = (TABLE, CATALOG_TABLE)
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)",
            names,
        )
    }
    columns = {
        (_text(row["TABLE_NAME"]), _text(row["COLUMN_NAME"])): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)",
            names,
        )
    }
    indexes: dict[str, list[dict]] = {}
    if TABLE in engines:
        # A ordem das colunas de cada indice vem do ORDER BY (SEQ_IN_INDEX).
        for row in db.query(
            "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s) ORDER BY INDEX_NAME, SEQ_IN_INDEX",
            (TABLE,),
        ):
            if _text(row["TABLE_NAME"]) == TABLE:
                indexes.setdefault(_text(row["INDEX_NAME"]), []).append(row)
    return engines, columns, indexes


def _check_catalog(engines: dict[str, str], columns: dict[tuple[str, str], dict]) -> None:
    """`exercise_catalog.id` tem de ser BIGINT UNSIGNED e PRIMARY KEY, numa
    tabela InnoDB: e o tipo que `exercise_id` copia (referencia LOGICA)."""
    if CATALOG_TABLE not in engines:
        _fail(f"tabela {CATALOG_TABLE} nao existe")
    if engines[CATALOG_TABLE].lower() != "innodb":
        _fail(f"{CATALOG_TABLE} usa ENGINE={engines[CATALOG_TABLE]}, esperado InnoDB")
    info = columns.get((CATALOG_TABLE, "id"))
    if info is None:
        _fail(f"{CATALOG_TABLE}.id nao existe")
    if _normalize_type(info["COLUMN_TYPE"]) != "bigint unsigned":
        _fail(f"{CATALOG_TABLE}.id e {_text(info['COLUMN_TYPE'])}, esperado bigint unsigned")
    if _text(info["COLUMN_KEY"]) != "PRI":
        _fail(f"{CATALOG_TABLE}.id nao e PRIMARY KEY")


def _check_existing_table(
    engines: dict[str, str],
    columns: dict[tuple[str, str], dict],
    indexes: dict[str, list[dict]],
) -> None:
    if engines[TABLE].lower() != "innodb":
        _fail(f"{TABLE} usa ENGINE={engines[TABLE]}, esperado InnoDB")
    for name, (expected_type, expected_nullable) in EXPECTED_COLUMNS.items():
        info = columns.get((TABLE, name))
        if info is None:
            _fail(f"{TABLE}.{name} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{TABLE}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{TABLE}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
    for name, (expected_columns, unique) in EXPECTED_INDEXES.items():
        rows = indexes.get(name)
        if not rows:
            _fail(f"indice {TABLE}.{name} nao existe")
        found = tuple(_text(row["COLUMN_NAME"]) for row in rows)
        if found != expected_columns:
            _fail(f"indice {TABLE}.{name} cobre {found}, esperado {expected_columns}")
        if (int(_text(rows[0]["NON_UNIQUE"]) or 1) == 0) != unique:
            _fail(f"indice {TABLE}.{name} deveria ser {'UNIQUE' if unique else 'nao unico'}")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar. Toda validacao
    acontece ANTES de montar a lista: se algo esta incompativel, nenhum
    statement e devolvido."""
    engines, columns, indexes = _read(db)
    _check_catalog(engines, columns)
    if TABLE in engines:
        _check_existing_table(engines, columns, indexes)
        return []
    return [(CREATE_SQL, None)]


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: `DROP TABLE` se a tabela existe, senao nada."""
    engines, _columns, _indexes = _read(db)
    return [(DROP_TABLE_SQL, None)] if TABLE in engines else []


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
