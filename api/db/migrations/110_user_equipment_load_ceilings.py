"""110 - Teto de carga por usuario x par exercicio+equipamento.

    user_equipment_load_ceilings                   (tabela nova)
      user_id                BIGINT NOT NULL            users.id, SEM FK
      equipment_exercise_id  BIGINT NOT NULL            equipment_exercises.id, SEM FK
      max_load_kg            DECIMAL(6,2) NOT NULL      carga maxima do equipamento (kg, TOTAL)
      created_at / updated_at TIMESTAMP
      PRIMARY KEY (user_id, equipment_exercise_id)

O usuario marca no Modo Treino que atingiu a carga maxima do equipamento; os
motores de progressao nao sugerem acima dela. Ausencia de linha = sem teto
(comportamento anterior). Sem FK (mesma decisao da 064/109; a ancora do par e
a mesma da 035). Sem DML: a tabela nasce vazia.

`plan(db)` so LE `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` sao o inverso (`DROP TABLE IF EXISTS`; reversivel:
perde so os tetos marcados, o codigo anterior nao le a tabela).
Tabela presente em formato diferente -> `UserEquipmentLoadCeilingsError` ANTES
do primeiro statement. Data API: um statement por chamada; modulo autocontido.
"""

from __future__ import annotations

import re

TABLE = "user_equipment_load_ceilings"

CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
  user_id BIGINT NOT NULL,
  equipment_exercise_id BIGINT NOT NULL,
  max_load_kg DECIMAL(6,2) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, equipment_exercise_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""

DROP_TABLE_SQL = f"DROP TABLE IF EXISTS {TABLE}"

# coluna -> (tipo normalizado, IS_NULLABLE)
EXPECTED_COLUMNS: dict[str, tuple[str, str]] = {
    "user_id": ("bigint", "NO"),
    "equipment_exercise_id": ("bigint", "NO"),
    "max_load_kg": ("decimal(6,2)", "NO"),
    "created_at": ("timestamp", "NO"),
    "updated_at": ("timestamp", "NO"),
}

PRIMARY_COLUMNS = ("user_id", "equipment_exercise_id")

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class UserEquipmentLoadCeilingsError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise UserEquipmentLoadCeilingsError(f"110: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read(db) -> tuple[str | None, dict[str, dict], list[str]]:
    engines = db.query(
        "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (TABLE,),
    )
    if not engines:
        return None, {}, []
    columns = {
        _text(row["COLUMN_NAME"]): row
        for row in db.query(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (TABLE,),
        )
    }
    primary = [
        _text(row["COLUMN_NAME"])
        for row in db.query(
            "SELECT COLUMN_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = 'PRIMARY' "
            "ORDER BY SEQ_IN_INDEX",
            (TABLE,),
        )
    ]
    return _text(engines[0]["ENGINE"]), columns, primary


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura; valida tudo antes de devolver qualquer statement."""
    engine, columns, primary = _read(db)
    if engine is None:
        return [(CREATE_SQL, None)]
    if engine.lower() != "innodb":
        _fail(f"{TABLE} usa ENGINE={engine}, esperado InnoDB")
    for name, (expected_type, expected_nullable) in EXPECTED_COLUMNS.items():
        info = columns.get(name)
        if info is None:
            _fail(f"{TABLE}.{name} nao existe")
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{TABLE}.{name} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{TABLE}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
    if tuple(primary) != PRIMARY_COLUMNS:
        _fail(f"PRIMARY KEY de {TABLE} cobre {tuple(primary)}, esperado {PRIMARY_COLUMNS}")
    return []


def plan_down(db) -> list[tuple[str, tuple | None]]:
    engine, _columns, _primary = _read(db)
    return [(DROP_TABLE_SQL, None)] if engine is not None else []


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
