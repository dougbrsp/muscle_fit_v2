"""059 - vínculo da série com a técnica avançada e com a série âncora
(Fase 5, PR 2 - dados).

    exercise_sets
      + technique_code     VARCHAR(20) utf8mb4_0900_ai_ci NULL
                           -> advanced_techniques(code)  (FK fk_sets_technique)
      + anchor_set_number  INT NULL
      + KEY idx_sets_technique_code (technique_code)
      + KEY idx_sets_exercise_anchor (exercise_id, anchor_set_number)

Por que: cada aplicação de técnica (mini-bloco de rest-pause, drop, reps
além da falha) já vira uma linha em `exercise_sets` com `set_type`
`rest_pause`/`drop_set`/`forced_rep`, mas a linha não diz QUAL técnica do
catálogo foi aplicada (as quatro "além da falha" gravam todas `forced_rep`)
nem de QUAL série de trabalho ela saiu. A regra de contabilização da Fase 5
(`app/domain/technique_accounting.py`) pondera cada aplicação pelas reps da
série âncora; sem o vínculo, não há âncora.

--- Decisões do DDL -------------------------------------------------------

1. **FK para `advanced_techniques(code)`**, que é PRIMARY KEY VARCHAR(20)
   utf8mb4_0900_ai_ci (031). A regra da 028 (FKs para `exercise_catalog`)
   não se aplica. A coluna nova declara charset/collation explícitos, iguais
   aos de `advanced_techniques.code` (mesmo padrão da 033), para a FK não
   depender do default da tabela.

2. **NULL em tudo.** Séries de trabalho, aquecimento e ajuste ficam NULL.
   Linhas antigas são preenchidas pelo backfill da 060, não aqui: esta
   migração é só DDL aditivo.

3. **`anchor_set_number` é o `set_number` da série de trabalho**, sem FK: o
   par (exercise_id, set_number) já é UNIQUE (`uq_exercise_set_number`), e a
   edição por /registro-exercicios apaga e recria as séries do exercício -
   uma FK composta para a própria tabela travaria esse caminho.

4. **Índices.** `idx_sets_technique_code` sustenta a FK (o InnoDB exige um
   índice com a coluna na frente; declarado para o nome ser conhecido e o
   `plan_down` ser exato). `idx_sets_exercise_anchor` agrupa as aplicações
   de uma âncora na leitura por exercício.

5. **Sem `AFTER`**: as colunas vão para o fim (ADD COLUMN INSTANT no MySQL 8
   para coluna NULL no fim). A FK é um ALTER à parte; sem poder desligar
   `foreign_key_checks` (Data API: sem SET), o MySQL a cria por cópia da
   tabela - aceitável no tamanho atual de `exercise_sets`.

Fluxo (padrão 055/058): `plan(db)` só LÊ `information_schema`; `up(db)`
executa o plano; `plan_down`/`down` são o inverso.

- nada existe       -> 2 ADD COLUMN + 2 ADD KEY + 1 ADD CONSTRAINT;
- convergido        -> 0 statements;
- parcial           -> só o que falta, cada peça num statement;
- coluna em outro formato, `advanced_techniques.code` que não seja PK
  VARCHAR(20) utf8mb4_0900_ai_ci, `exercise_sets` ausente
                    -> `TechniqueLinkError` ANTES do primeiro statement.

`advanced_techniques` ausente (031 pendente no mesmo lote): erro numa
execução real; aceito só em DRY-RUN (`db.dry_run`), como na 033/057, para o
plano do lote poder ser exibido.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; módulo
autocontido (não importa código do app).
"""

from __future__ import annotations

import re

SETS_TABLE = "exercise_sets"
TECHNIQUES_TABLE = "advanced_techniques"
TECHNIQUE_COLLATION = "utf8mb4_0900_ai_ci"

COL_TECHNIQUE = "technique_code"
COL_ANCHOR = "anchor_set_number"
IDX_TECHNIQUE = "idx_sets_technique_code"
IDX_ANCHOR = "idx_sets_exercise_anchor"
FK_TECHNIQUE = "fk_sets_technique"

ADD_TECHNIQUE_SQL = (
    f"ALTER TABLE {SETS_TABLE} ADD COLUMN {COL_TECHNIQUE} VARCHAR(20) "
    f"CHARACTER SET utf8mb4 COLLATE {TECHNIQUE_COLLATION} NULL"
)
ADD_ANCHOR_SQL = f"ALTER TABLE {SETS_TABLE} ADD COLUMN {COL_ANCHOR} INT NULL"
ADD_IDX_TECHNIQUE_SQL = f"ALTER TABLE {SETS_TABLE} ADD KEY {IDX_TECHNIQUE} ({COL_TECHNIQUE})"
ADD_IDX_ANCHOR_SQL = f"ALTER TABLE {SETS_TABLE} ADD KEY {IDX_ANCHOR} (exercise_id, {COL_ANCHOR})"
ADD_FK_SQL = (
    f"ALTER TABLE {SETS_TABLE} ADD CONSTRAINT {FK_TECHNIQUE} FOREIGN KEY ({COL_TECHNIQUE}) "
    f"REFERENCES {TECHNIQUES_TABLE}(code)"
)

DROP_FK_SQL = f"ALTER TABLE {SETS_TABLE} DROP FOREIGN KEY {FK_TECHNIQUE}"
DROP_IDX_TECHNIQUE_SQL = f"ALTER TABLE {SETS_TABLE} DROP KEY {IDX_TECHNIQUE}"
DROP_IDX_ANCHOR_SQL = f"ALTER TABLE {SETS_TABLE} DROP KEY {IDX_ANCHOR}"
DROP_TECHNIQUE_SQL = f"ALTER TABLE {SETS_TABLE} DROP COLUMN {COL_TECHNIQUE}"
DROP_ANCHOR_SQL = f"ALTER TABLE {SETS_TABLE} DROP COLUMN {COL_ANCHOR}"

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED = {
    COL_TECHNIQUE: ("varchar(20)", "YES"),
    COL_ANCHOR: ("int", "YES"),
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class TechniqueLinkError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise TechniqueLinkError(f"059: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read(db):
    names = (SETS_TABLE, TECHNIQUES_TABLE)
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
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA, COLLATION_NAME "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)",
            names,
        )
    }
    indexes = {
        _text(row["INDEX_NAME"])
        for row in db.query(
            "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s) ORDER BY INDEX_NAME, SEQ_IN_INDEX",
            (SETS_TABLE,),
        )
        if _text(row["TABLE_NAME"]) == SETS_TABLE
    }
    fks = {
        _text(row["CONSTRAINT_NAME"])
        for row in db.query(
            "SELECT k.TABLE_NAME, k.CONSTRAINT_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME "
            "FROM information_schema.KEY_COLUMN_USAGE k "
            "WHERE k.TABLE_SCHEMA = DATABASE() AND k.TABLE_NAME IN (%s) AND k.REFERENCED_TABLE_NAME IS NOT NULL",
            (SETS_TABLE,),
        )
        if _text(row["TABLE_NAME"]) == SETS_TABLE
    }
    return engines, columns, indexes, fks


def _check_techniques(engines, columns, dry_run: bool) -> None:
    if TECHNIQUES_TABLE not in engines:
        if dry_run:
            return  # 031 pendente no mesmo lote: ver docstring
        _fail(f"tabela {TECHNIQUES_TABLE} nao existe (a 031 precisa estar aplicada)")
    code = columns.get((TECHNIQUES_TABLE, "code"))
    if code is None:
        _fail(f"{TECHNIQUES_TABLE}.code nao existe")
    if _normalize_type(code["COLUMN_TYPE"]) != "varchar(20)":
        _fail(f"{TECHNIQUES_TABLE}.code e {_text(code['COLUMN_TYPE'])}, esperado varchar(20)")
    if _text(code["COLUMN_KEY"]) != "PRI":
        _fail(f"{TECHNIQUES_TABLE}.code nao e PRIMARY KEY")
    if _text(code.get("COLLATION_NAME")) != TECHNIQUE_COLLATION:
        _fail(f"{TECHNIQUES_TABLE}.code usa collation {_text(code.get('COLLATION_NAME'))}, esperado {TECHNIQUE_COLLATION}")


def _check_sets(engines, columns) -> dict[str, bool]:
    if SETS_TABLE not in engines:
        _fail(f"tabela {SETS_TABLE} nao existe")
    if engines[SETS_TABLE].lower() != "innodb":
        _fail(f"{SETS_TABLE} usa ENGINE={engines[SETS_TABLE]}, esperado InnoDB")
    present: dict[str, bool] = {}
    for name, (expected_type, expected_nullable) in EXPECTED.items():
        info = columns.get((SETS_TABLE, name))
        present[name] = info is not None
        if info is None:
            continue
        tipo = _normalize_type(info["COLUMN_TYPE"])
        if tipo != expected_type:
            _fail(f"{SETS_TABLE}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{SETS_TABLE}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
        if name == COL_TECHNIQUE and _text(info.get("COLLATION_NAME")) != TECHNIQUE_COLLATION:
            _fail(f"{SETS_TABLE}.{name} usa collation {_text(info.get('COLLATION_NAME'))}, esperado {TECHNIQUE_COLLATION}")
    return present


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, columns, indexes, fks = _read(db)
    _check_techniques(engines, columns, bool(getattr(db, "dry_run", False)))
    present = _check_sets(engines, columns)

    statements: list[tuple[str, tuple | None]] = []
    if not present[COL_TECHNIQUE]:
        statements.append((ADD_TECHNIQUE_SQL, None))
    if not present[COL_ANCHOR]:
        statements.append((ADD_ANCHOR_SQL, None))
    if IDX_TECHNIQUE not in indexes:
        statements.append((ADD_IDX_TECHNIQUE_SQL, None))
    if IDX_ANCHOR not in indexes:
        statements.append((ADD_IDX_ANCHOR_SQL, None))
    if FK_TECHNIQUE not in fks:
        statements.append((ADD_FK_SQL, None))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: FK, índices e colunas, só o que existe."""
    engines, columns, indexes, fks = _read(db)
    if SETS_TABLE not in engines:
        return []
    statements: list[tuple[str, tuple | None]] = []
    if FK_TECHNIQUE in fks:
        statements.append((DROP_FK_SQL, None))
    if IDX_TECHNIQUE in indexes:
        statements.append((DROP_IDX_TECHNIQUE_SQL, None))
    if IDX_ANCHOR in indexes:
        statements.append((DROP_IDX_ANCHOR_SQL, None))
    if (SETS_TABLE, COL_TECHNIQUE) in columns:
        statements.append((DROP_TECHNIQUE_SQL, None))
    if (SETS_TABLE, COL_ANCHOR) in columns:
        statements.append((DROP_ANCHOR_SQL, None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
