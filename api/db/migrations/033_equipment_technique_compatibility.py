"""033 - matriz de compatibilidade de tecnica por equipamento e override por
par (PR-07 da refatoracao dos motores; PLANO_20260912.md secao 8.2, la
numerada "025" - o numero real e o proximo livre).

    equipment_technique_compatibility            default do EQUIPAMENTO
      PK (equipment_id, technique_code)
    equipment_exercise_technique_overrides       override por PAR (vence o default)
      PK (equipment_exercise_id, technique_code)
    ambas: is_compatible TINYINT(1) NOT NULL, updated_by_user_id BIGINT NULL,
           updated_at TIMESTAMP (ON UPDATE CURRENT_TIMESTAMP)

Ausencia de linha = tecnica sem marcacao = INCOMPATIVEL (D3). Linha com
`is_compatible = 0` existe para o override negar um default compativel.

FKs (tipos conferidos na Aurora em 2026-09-17, somente leitura):
- equipment_id          BIGINT  -> equipment(id)            ON DELETE CASCADE
- equipment_exercise_id BIGINT  -> equipment_exercises(id)  ON DELETE CASCADE
  (id canonico da relacao desde o PR-05 / PR #56)
- technique_code VARCHAR(20) utf8mb4_0900_ai_ci -> advanced_techniques(code)
  (mesma collation declarada pela 031; sem CASCADE: codigo de tecnica nao se
  apaga, desativa-se por `is_active`)
- updated_by_user_id    BIGINT  -> users(id)                ON DELETE SET NULL

Fluxo: `plan(db)` so LE `information_schema` e devolve os statements;
`up(db)` executa o plano.

- tabela ausente -> 1 `CREATE TABLE` (no maximo 2 statements no total);
- tabela presente no formato esperado -> nada;
- tabela presente com coluna/tipo/nulabilidade/PK/ENGINE/FK diferente, ou
  tabela/coluna REFERENCIADA ausente ou de outro tipo/collation ->
  `EquipmentTechniqueCompatibilityError` antes do primeiro statement.

`advanced_techniques` ausente (banco em que a 031 ainda esta pendente; o runner
a aplica ANTES desta): numa execucao real a ausencia e erro (falha segura). So
em DRY-RUN (`db.dry_run`), em que a 031 pendente nao cria a tabela, a ausencia
e aceita para o plano do lote 031..033 poder ser exibido; o formato e conferido
de novo na execucao real. Na Aurora a 031 ja foi aplicada (versao 31).

Reexecucao: 0 statements. Data API: sem PREPARE/SET/multi-statement; um
statement por chamada; modulo autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

COMPATIBILITY_TABLE = "equipment_technique_compatibility"
OVERRIDES_TABLE = "equipment_exercise_technique_overrides"
TECHNIQUES_TABLE = "advanced_techniques"
TECHNIQUE_COLLATION = "utf8mb4_0900_ai_ci"

_COMMON_COLUMNS = f"""
  technique_code VARCHAR(20) CHARACTER SET utf8mb4 COLLATE {TECHNIQUE_COLLATION} NOT NULL,
  is_compatible TINYINT(1) NOT NULL,
  updated_by_user_id BIGINT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
"""
_TABLE_OPTIONS = f"ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE={TECHNIQUE_COLLATION}"

CREATE_COMPATIBILITY_SQL = f"""
CREATE TABLE IF NOT EXISTS {COMPATIBILITY_TABLE} (
  equipment_id BIGINT NOT NULL,{_COMMON_COLUMNS}
  PRIMARY KEY (equipment_id, technique_code),
  KEY idx_etc_technique (technique_code),
  KEY idx_etc_updated_by (updated_by_user_id),
  CONSTRAINT fk_etc_equipment FOREIGN KEY (equipment_id) REFERENCES equipment(id) ON DELETE CASCADE,
  CONSTRAINT fk_etc_technique FOREIGN KEY (technique_code) REFERENCES {TECHNIQUES_TABLE}(code),
  CONSTRAINT fk_etc_updated_by FOREIGN KEY (updated_by_user_id) REFERENCES users(id) ON DELETE SET NULL
) {_TABLE_OPTIONS}
"""

CREATE_OVERRIDES_SQL = f"""
CREATE TABLE IF NOT EXISTS {OVERRIDES_TABLE} (
  equipment_exercise_id BIGINT NOT NULL,{_COMMON_COLUMNS}
  PRIMARY KEY (equipment_exercise_id, technique_code),
  KEY idx_eeto_technique (technique_code),
  KEY idx_eeto_updated_by (updated_by_user_id),
  CONSTRAINT fk_eeto_equipment_exercise FOREIGN KEY (equipment_exercise_id) REFERENCES equipment_exercises(id) ON DELETE CASCADE,
  CONSTRAINT fk_eeto_technique FOREIGN KEY (technique_code) REFERENCES {TECHNIQUES_TABLE}(code),
  CONSTRAINT fk_eeto_updated_by FOREIGN KEY (updated_by_user_id) REFERENCES users(id) ON DELETE SET NULL
) {_TABLE_OPTIONS}
"""

# tabela nova -> (SQL, coluna dona, tabela referenciada pela coluna dona)
TARGETS = {
    COMPATIBILITY_TABLE: (CREATE_COMPATIBILITY_SQL, "equipment_id", "equipment"),
    OVERRIDES_TABLE: (CREATE_OVERRIDES_SQL, "equipment_exercise_id", "equipment_exercises"),
}

# (tabela, coluna) referenciada -> COLUMN_TYPE esperado
REFERENCED = {
    ("equipment", "id"): "bigint",
    ("equipment_exercises", "id"): "bigint",
    ("users", "id"): "bigint",
    (TECHNIQUES_TABLE, "code"): "varchar(20)",
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class EquipmentTechniqueCompatibilityError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise EquipmentTechniqueCompatibilityError(f"033: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _expected_columns(owner_column: str) -> dict[str, tuple[str, str]]:
    return {
        owner_column: ("bigint", "NO"),
        "technique_code": ("varchar(20)", "NO"),
        "is_compatible": ("tinyint", "NO"),
        "updated_by_user_id": ("bigint", "YES"),
        "updated_at": ("timestamp", "NO"),
    }


def _check_referenced(engines: dict[str, str], columns: dict[tuple[str, str], dict], dry_run: bool) -> None:
    for (table, column), expected_type in REFERENCED.items():
        if table not in engines:
            if table == TECHNIQUES_TABLE and dry_run:
                continue  # 031 pendente no mesmo lote: ver docstring
            _fail(f"tabela referenciada {table} nao existe")
        if engines[table].lower() != "innodb":
            _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")
        info = columns.get((table, column))
        if info is None:
            _fail(f"{table}.{column} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{table}.{column} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["COLUMN_KEY"]) != "PRI":
            _fail(f"{table}.{column} nao e PRIMARY KEY")
    code = columns.get((TECHNIQUES_TABLE, "code"))
    if code is not None and _text(code["COLLATION_NAME"]) != TECHNIQUE_COLLATION:
        _fail(f"{TECHNIQUES_TABLE}.code usa collation {_text(code['COLLATION_NAME'])}, esperado {TECHNIQUE_COLLATION}")


def _check_existing(db, table: str, owner_column: str, owner_table: str, engines, columns) -> None:
    if engines[table].lower() != "innodb":
        _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")
    for name, (expected_type, expected_nullable) in _expected_columns(owner_column).items():
        info = columns.get((table, name))
        if info is None:
            _fail(f"{table}.{name} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{table}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{table}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
    primary = [
        _text(row["COLUMN_NAME"])
        for row in db.query(
            "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s) ORDER BY INDEX_NAME, SEQ_IN_INDEX",
            (table,),
        )
        if _text(row["TABLE_NAME"]) == table and _text(row["INDEX_NAME"]) == "PRIMARY"
    ]
    if primary != [owner_column, "technique_code"]:
        _fail(f"PRIMARY KEY de {table} e {primary}, esperado {[owner_column, 'technique_code']}")
    fks = {
        _text(row["COLUMN_NAME"]): _text(row["REFERENCED_TABLE_NAME"])
        for row in db.query(
            "SELECT k.TABLE_NAME, k.CONSTRAINT_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME "
            "FROM information_schema.KEY_COLUMN_USAGE k "
            "WHERE k.TABLE_SCHEMA = DATABASE() AND k.TABLE_NAME IN (%s) AND k.REFERENCED_TABLE_NAME IS NOT NULL",
            (table,),
        )
        if _text(row["TABLE_NAME"]) == table
    }
    expected_fks = {owner_column: owner_table, "technique_code": TECHNIQUES_TABLE, "updated_by_user_id": "users"}
    if fks != expected_fks:
        _fail(f"FKs de {table} sao {fks}, esperado {expected_fks}")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    names = tuple(sorted({*TARGETS, *(table for table, _ in REFERENCED)}))
    placeholders = ", ".join(["%s"] * len(names))
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            names,
        )
    }
    columns = {
        (_text(row["TABLE_NAME"]), _text(row["COLUMN_NAME"])): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA, COLLATION_NAME "
            f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            names,
        )
    }
    _check_referenced(engines, columns, bool(getattr(db, "dry_run", False)))

    statements: list[tuple[str, tuple | None]] = []
    for table, (create_sql, owner_column, owner_table) in TARGETS.items():
        if table in engines:
            _check_existing(db, table, owner_column, owner_table, engines, columns)
        else:
            statements.append((create_sql, None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)
