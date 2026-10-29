"""088 - conjunto de regras de carga da próxima série, versionado (regras de carga, PR R2, 2026-10-20).

    next_set_load_rule_sets          uma versão (rascunho ou publicada) do conjunto GLOBAL
      id                 BIGINT AUTO_INCREMENT PK
      version_number     INT NULL           1, 2, ... na publicação (rascunho = NULL); UNIQUE
      status             VARCHAR(16)        'draft' | 'published'
      reps_floor         TINYINT            piso de reps do conjunto (token `floor`)
      reps_ceiling       TINYINT            teto de reps do conjunto (token `ceiling`)
      rules_json         JSON               lista de regras (`next_set_load.rule_to_dict`)
      notes              VARCHAR(1000) NULL
      engine_version     VARCHAR(32)        `next_set_load.RULESET_ENGINE_VERSION` na gravação
      created_by         BIGINT NULL        quem criou; referência LÓGICA (sem FK)
      published_by       BIGINT NULL        quem publicou; referência LÓGICA (sem FK)
      published_at       DATETIME NULL
      superseded_at      DATETIME NULL      instante (relógio do banco) em que foi substituído
      created_at / updated_at
      current_published  TINYINT GENERATED ALWAYS AS
                         (IF(status = 'published' AND superseded_at IS NULL, 1, NULL)) VIRTUAL
      current_draft      TINYINT GENERATED ALWAYS AS (IF(status = 'draft', 1, NULL)) VIRTUAL
      UNIQUE uq_nslrs_version (version_number)
      UNIQUE uq_nslrs_current_published (current_published)   no máximo UM publicado vigente
      UNIQUE uq_nslrs_current_draft (current_draft)           no máximo UM rascunho

--- Decisões do DDL -------------------------------------------------------

1. **Uma tabela, conjunto inteiro em `rules_json`** (padrão da 078 com o
   `carb_cycling_json`): o conjunto é pequeno (até `RULESET_MAX_RULES`
   regras), lido inteiro a cada avaliação e trocado inteiro na publicação.
   Quem valida a forma e a semântica é o dono (`domain/next_set_load.py`); a
   migração não enumera vocabulário.
2. **Vigência no banco** (padrão das 049/052/078): `current_published` vale 1
   enquanto a versão está publicada e não substituída, NULL no resto, e
   `current_draft` vale 1 no rascunho; os UNIQUE recusam um segundo vigente e
   um segundo rascunho (NULL não colide). O conjunto é GLOBAL (não há coluna
   de usuário). Publicar = UPDATE do vigente (`superseded_at`) + UPDATE do
   rascunho (`status`, `version_number`) na mesma transação; duas publicações
   simultâneas batem num UNIQUE e a rota responde 409.
3. **Sem FK**: `created_by`/`published_by` são referências lógicas (um admin
   apagado não apaga o histórico). Nenhuma FK para `exercise_catalog` (a 028
   recusa).
4. **Sem `effective_from`**: a versão vale no instante da publicação.

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ `information_schema`; `up(db)` executa; `down`/`plan_down`
fazem DROP TABLE (nada referencia a tabela).

- tabela ausente -> 1 `CREATE TABLE` (também em DRY-RUN: não há dependência);
- presente na forma esperada -> 0 statements (reexecução);
- coluna/tipo/nulabilidade diferente, expressão gerada diferente, UNIQUE
  ausente ou ENGINE != InnoDB -> `NextSetLoadRuleSetsError` ANTES do primeiro
  statement.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; nenhuma
literal com ':' (viraria parâmetro no shim); módulo autocontido (não importa
código do app).
"""

from __future__ import annotations

import re

TABLE = "next_set_load_rule_sets"

COL_CURRENT_PUBLISHED = "current_published"
COL_CURRENT_DRAFT = "current_draft"
IDX_VERSION = "uq_nslrs_version"
IDX_CURRENT_PUBLISHED = "uq_nslrs_current_published"
IDX_CURRENT_DRAFT = "uq_nslrs_current_draft"

# Expressões das colunas geradas, como NÓS as escrevemos.
CURRENT_PUBLISHED_EXPRESSION = "IF(status = 'published' AND superseded_at IS NULL, 1, NULL)"
CURRENT_DRAFT_EXPRESSION = "IF(status = 'draft', 1, NULL)"

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  version_number INT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'draft',
  reps_floor TINYINT NOT NULL,
  reps_ceiling TINYINT NOT NULL,
  rules_json JSON NOT NULL,
  notes VARCHAR(1000) NULL,
  engine_version VARCHAR(32) NOT NULL,
  created_by BIGINT NULL,
  published_by BIGINT NULL,
  published_at DATETIME NULL,
  superseded_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  {COL_CURRENT_PUBLISHED} TINYINT GENERATED ALWAYS AS ({CURRENT_PUBLISHED_EXPRESSION}) VIRTUAL,
  {COL_CURRENT_DRAFT} TINYINT GENERATED ALWAYS AS ({CURRENT_DRAFT_EXPRESSION}) VIRTUAL,
  UNIQUE KEY {IDX_VERSION} (version_number),
  UNIQUE KEY {IDX_CURRENT_PUBLISHED} ({COL_CURRENT_PUBLISHED}),
  UNIQUE KEY {IDX_CURRENT_DRAFT} ({COL_CURRENT_DRAFT})
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_SQL = f"DROP TABLE IF EXISTS {TABLE}"

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_COLUMNS: dict[str, tuple[str, str]] = {
    "id": ("bigint", "NO"),
    "version_number": ("int", "YES"),
    "status": ("varchar(16)", "NO"),
    "reps_floor": ("tinyint", "NO"),
    "reps_ceiling": ("tinyint", "NO"),
    "rules_json": ("json", "NO"),
    "notes": ("varchar(1000)", "YES"),
    "engine_version": ("varchar(32)", "NO"),
    "created_by": ("bigint", "YES"),
    "published_by": ("bigint", "YES"),
    "published_at": ("datetime", "YES"),
    "superseded_at": ("datetime", "YES"),
    "created_at": ("datetime", "NO"),
    "updated_at": ("datetime", "NO"),
    COL_CURRENT_PUBLISHED: ("tinyint", "YES"),
    COL_CURRENT_DRAFT: ("tinyint", "YES"),
}

# coluna gerada -> expressão
GENERATED_COLUMNS: dict[str, str] = {
    COL_CURRENT_PUBLISHED: CURRENT_PUBLISHED_EXPRESSION,
    COL_CURRENT_DRAFT: CURRENT_DRAFT_EXPRESSION,
}

# índice -> (UNIQUE?, colunas)
EXPECTED_INDEXES: dict[str, tuple[bool, tuple[str, ...]]] = {
    IDX_VERSION: (True, ("version_number",)),
    IDX_CURRENT_PUBLISHED: (True, (COL_CURRENT_PUBLISHED,)),
    IDX_CURRENT_DRAFT: (True, (COL_CURRENT_DRAFT,)),
}

# Largura de exibição dos inteiros some no MySQL 8, MENOS a de `tinyint(1)`
# (o booleano). `tinyint(4)` -> `tinyint`.
_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\((?!1\))\d+\)")
_CHARSET_INTRODUCER_RE = re.compile(r"_(?:utf8mb4|utf8mb3|utf8|latin1|binary)(?=\s*\\?')")
_NAO_PALAVRA_RE = re.compile(r"[^a-z0-9_]+")


class NextSetLoadRuleSetsError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise NextSetLoadRuleSetsError(f"088: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _normalize_expression(expression) -> str:
    """Compara a expressão IGNORANDO o que o MySQL reescreve: crases, aspas,
    parênteses, espaços, caixa e o introdutor de charset da literal
    (`_utf8mb4'published'`)."""
    texto = _CHARSET_INTRODUCER_RE.sub("", _text(expression).strip().lower())
    return _NAO_PALAVRA_RE.sub("", texto)


def _read(db):
    tables = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
            (TABLE,),
        )
    }
    columns = {
        _text(row["COLUMN_NAME"]): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, GENERATION_EXPRESSION "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
            (TABLE,),
        )
    }
    indexes: dict[str, list[dict]] = {}
    for row in db.query(
        "SELECT TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
        (TABLE,),
    ):
        indexes.setdefault(_text(row["INDEX_NAME"]), []).append(row)
    return tables, columns, indexes


def _check_index(indexes, name: str, *, unique: bool, cols: tuple[str, ...]) -> None:
    rows = indexes.get(name)
    if not rows:
        _fail(f"{TABLE}.{name} nao existe")
    found = tuple(_text(r["COLUMN_NAME"]) for r in sorted(rows, key=lambda r: int(r["SEQ_IN_INDEX"])))
    if found != cols:
        _fail(f"{TABLE}.{name} cobre {found}, esperado {cols}")
    if (int(rows[0]["NON_UNIQUE"]) == 0) != unique:
        _fail(f"{TABLE}.{name} {'NAO e' if unique else 'e'} UNIQUE")


def _check_existing(tables, columns, indexes) -> None:
    if tables[TABLE].lower() != "innodb":
        _fail(f"{TABLE} usa ENGINE={tables[TABLE]}, esperado InnoDB")
    for name, (expected_type, expected_nullable) in EXPECTED_COLUMNS.items():
        info = columns.get(name)
        if info is None:
            _fail(f"{TABLE}.{name} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{TABLE}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{TABLE}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
    for name, expression in GENERATED_COLUMNS.items():
        found = _normalize_expression(columns[name].get("GENERATION_EXPRESSION"))
        if not found:
            _fail(f"{TABLE}.{name} existe mas NAO e coluna gerada")
        if found != _normalize_expression(expression):
            _fail(
                f"{TABLE}.{name} e gerada por {_text(columns[name].get('GENERATION_EXPRESSION'))!r}, "
                f"esperado {expression!r}"
            )
    for name, (unique, cols) in EXPECTED_INDEXES.items():
        _check_index(indexes, name, unique=unique, cols=cols)


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    tables, columns, indexes = _read(db)
    if TABLE in tables:
        _check_existing(tables, columns, indexes)
        return []
    return [(CREATE_SQL, None)]


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: 1 `DROP TABLE` se a tabela existe, senão nada."""
    tables, _columns, _indexes = _read(db)
    if TABLE not in tables:
        return []
    return [(DROP_SQL, None)]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
