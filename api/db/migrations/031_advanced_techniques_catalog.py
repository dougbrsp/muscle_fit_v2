"""031 - catalogo `advanced_techniques` (PR-06 da refatoracao dos motores).

Ate aqui os 8 codigos de tecnica avancada existiam so como `Literal` em
`app/models.py` e como listas no frontend. Esta migracao cria o catalogo
(PLANO_20260912.md, secoes 5.9 e 8.2 - la numerada "023"; o numero real e o
proximo livre) e semeia os 8 codigos:

    code                      VARCHAR(20) NOT NULL PRIMARY KEY
    name                      VARCHAR(80) NOT NULL
    failure_requirement       ENUM('failure_not_required','may_reach_failure',
                                   'requires_failure','beyond_failure') NOT NULL
    requires_partner          TINYINT(1) NOT NULL DEFAULT 0
    auto_applicable           TINYINT(1) NOT NULL DEFAULT 0
    min_experience            ENUM('beginner','intermediate','advanced') NOT NULL
    set_type                  VARCHAR(40) NOT NULL
    activation_set_equivalent DECIMAL(4,2) NOT NULL DEFAULT 0.00  (D7: 0 ate calibrar)
    recovery_cost_factor      DECIMAL(4,2) NULL                   (NULL = nao calibrado)
    is_active                 TINYINT(1) NOT NULL DEFAULT 1

Sem FK fisica de `workout_plan_template_slot_items.advanced_technique` /
`workout_plan_exercises.advanced_technique` para ca (FK logica; valores
legados fora do catalogo saem no relatorio somente leitura
`python -m app.advanced_techniques_report`). Esta migracao NAO le nem altera
essas tabelas.

Fluxo: `plan(db)` so LE (`information_schema` + `SELECT code` do proprio
catalogo, <= 8 linhas) e devolve os statements; `up(db)` executa o plano.

- tabela ausente (Aurora, DEV e CI hoje) -> 1 `CREATE TABLE` + 8 `INSERT`
  (um por codigo, valores por parametro);
- tabela presente e no formato esperado -> so os `INSERT` dos codigos que
  faltam; linha existente NUNCA e alterada (preserva ajuste posterior de
  `is_active`, `activation_set_equivalent` etc.);
- tabela presente com coluna ausente/tipo/nulabilidade diferente, PK diferente
  de (code) ou ENGINE != InnoDB -> `AdvancedTechniquesCatalogError` antes do
  primeiro statement.

Reexecucao: 0 statements. Data API: sem PREPARE/SET/multi-statement; um
statement por chamada; modulo autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

TABLE = "advanced_techniques"

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  code VARCHAR(20) NOT NULL,
  name VARCHAR(80) NOT NULL,
  failure_requirement ENUM('failure_not_required','may_reach_failure','requires_failure','beyond_failure') NOT NULL,
  requires_partner TINYINT(1) NOT NULL DEFAULT 0,
  auto_applicable TINYINT(1) NOT NULL DEFAULT 0,
  min_experience ENUM('beginner','intermediate','advanced') NOT NULL,
  set_type VARCHAR(40) NOT NULL,
  activation_set_equivalent DECIMAL(4,2) NOT NULL DEFAULT 0.00,
  recovery_cost_factor DECIMAL(4,2) NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  PRIMARY KEY (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

INSERT_SQL = (
    f"INSERT INTO {TABLE} (code, name, failure_requirement, requires_partner, auto_applicable, "
    "min_experience, set_type) VALUES (%s, %s, %s, %s, %s, %s, %s)"
)

# (code, name, failure_requirement, requires_partner, auto_applicable, min_experience, set_type)
# Tabela da secao 5.9 do plano; `name` = rotulo ja exibido pelo builder de
# modelos; `set_type` = o que o Modo Treino ja grava (app/set_types.py).
SEED = (
    ("rest_pause", "Rest-pause", "requires_failure", 0, 1, "intermediate", "rest_pause"),
    ("drop_set", "Drop-set", "requires_failure", 0, 1, "intermediate", "drop_set"),
    ("fst7", "FST-7", "requires_failure", 0, 0, "advanced", "working"),
    ("gvt", "GVT (German Volume Training)", "failure_not_required", 0, 0, "intermediate", "working"),
    ("forced_reps", "Repetições forçadas", "beyond_failure", 1, 0, "advanced", "forced_rep"),
    ("negative_reps", "Negativas", "beyond_failure", 1, 0, "advanced", "forced_rep"),
    ("partial_reps", "Parciais", "beyond_failure", 0, 0, "advanced", "forced_rep"),
    ("cheat_reps", "Cheat reps", "beyond_failure", 0, 0, "advanced", "forced_rep"),
)

# coluna -> (COLUMN_TYPE normalizado, IS_NULLABLE)
EXPECTED_COLUMNS = {
    "code": ("varchar(20)", "NO"),
    "name": ("varchar(80)", "NO"),
    "failure_requirement": (
        "enum('failure_not_required','may_reach_failure','requires_failure','beyond_failure')",
        "NO",
    ),
    "requires_partner": ("tinyint", "NO"),
    "auto_applicable": ("tinyint", "NO"),
    "min_experience": ("enum('beginner','intermediate','advanced')", "NO"),
    "set_type": ("varchar(40)", "NO"),
    "activation_set_equivalent": ("decimal(4,2)", "NO"),
    "recovery_cost_factor": ("decimal(4,2)", "YES"),
    "is_active": ("tinyint", "NO"),
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class AdvancedTechniquesCatalogError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise AdvancedTechniquesCatalogError(f"031: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _check_existing_table(db, engine: str) -> None:
    if engine.lower() != "innodb":
        _fail(f"{TABLE} usa ENGINE={engine}, esperado InnoDB")
    columns = {
        _text(row["COLUMN_NAME"]): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
            (TABLE,),
        )
    }
    for name, (expected_type, expected_nullable) in EXPECTED_COLUMNS.items():
        info = columns.get(name)
        if info is None:
            _fail(f"{TABLE}.{name} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{TABLE}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{TABLE}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
    primary = [name for name, info in columns.items() if _text(info["COLUMN_KEY"]) == "PRI"]
    if primary != ["code"]:
        _fail(f"PRIMARY KEY de {TABLE} e {primary}, esperado ['code']")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    tables = db.query(
        "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
        (TABLE,),
    )
    statements: list[tuple[str, tuple | None]] = []
    existing: set[str] = set()
    if not tables:
        statements.append((CREATE_TABLE_SQL, None))
    else:
        _check_existing_table(db, _text(tables[0]["ENGINE"]))
        existing = {_text(row["code"]) for row in db.query(f"SELECT code FROM {TABLE}")}
    statements += [(INSERT_SQL, row) for row in SEED if row[0] not in existing]
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)
