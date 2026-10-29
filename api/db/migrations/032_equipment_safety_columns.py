"""032 - colunas de seguranca em `equipment` e `equipment_exercises` (PR-07 da
refatoracao dos motores; PLANO_20260912.md secao 8.2, la numerada "024" - o
numero real e o proximo livre).

    equipment.max_safe_rpe                    TINYINT NULL       6-10; NULL = desconhecido -> 9 (D3)
    equipment.load_increment_kg               DECIMAL(5,2) NULL  menor incremento de carga TOTAL (D12)
    equipment.safety_notes                    TEXT NULL
    equipment_exercises.max_safe_rpe_override TINYINT NULL       override por par; NULL = herda do equipamento
    equipment_exercises.safety_notes          TEXT NULL

Todas NULL e sem default: nenhuma linha existente muda de significado e o
codigo atual (que nao conhece as colunas) segue funcionando. A faixa 6-10 e
validada na API (`app/training_safety_admin.py`) e o resolvedor
(`app/domain/training_safety.py`) trata valor fora da faixa como desconhecido;
sem CHECK fisico (o plano nao pede e o formato segue identico nos dois bancos).

Fluxo: `plan(db)` so LE `information_schema` e devolve os statements;
`up(db)` executa o plano.

- coluna ausente -> entra no `ALTER TABLE ... ADD COLUMN` da sua tabela (UM
  statement por tabela, so com as colunas que faltam);
- coluna presente no formato esperado -> nada;
- tabela ausente, ENGINE != InnoDB, ou coluna presente com tipo/nulabilidade
  diferente -> `EquipmentSafetyColumnsError` antes do primeiro statement.

Aurora hoje (recorte tests/unit/data/aurora_schema_2026_09_17_032_033.json):
nenhuma das 5 colunas existe -> 2 statements. Reexecucao: 0 statements.
Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

# tabela -> [(coluna, DDL, COLUMN_TYPE normalizado)]
COLUMNS = {
    "equipment": (
        ("max_safe_rpe", "TINYINT NULL", "tinyint"),
        ("load_increment_kg", "DECIMAL(5,2) NULL", "decimal(5,2)"),
        ("safety_notes", "TEXT NULL", "text"),
    ),
    "equipment_exercises": (
        ("max_safe_rpe_override", "TINYINT NULL", "tinyint"),
        ("safety_notes", "TEXT NULL", "text"),
    ),
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class EquipmentSafetyColumnsError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise EquipmentSafetyColumnsError(f"032: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    names = tuple(COLUMNS)
    placeholders = ", ".join(["%s"] * len(names))
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            names,
        )
    }
    for table in names:
        if table not in engines:
            _fail(f"tabela {table} nao existe")
        if engines[table].lower() != "innodb":
            _fail(f"{table} usa ENGINE={engines[table]}, esperado InnoDB")

    existing: dict[tuple[str, str], dict] = {
        (_text(row["TABLE_NAME"]), _text(row["COLUMN_NAME"])): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA "
            f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            names,
        )
    }

    statements: list[tuple[str, tuple | None]] = []
    for table, columns in COLUMNS.items():
        missing = []
        for column, ddl, expected_type in columns:
            info = existing.get((table, column))
            if info is None:
                missing.append(f"ADD COLUMN {column} {ddl}")
                continue
            if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
                _fail(f"{table}.{column} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
            if _text(info["IS_NULLABLE"]) != "YES":
                _fail(f"{table}.{column} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado YES")
        if missing:
            statements.append((f"ALTER TABLE {table} " + ", ".join(missing), None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)
