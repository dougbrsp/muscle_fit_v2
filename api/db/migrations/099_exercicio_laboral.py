"""099 - Exercício laboral (M100, 2026-10-27; dono da regra: `app/domain/laboral.py`).

    exercise_catalog
      + is_laboral               TINYINT(1) NOT NULL DEFAULT 0   (1 = laboral: sem progressão)
      + laboral_default_sets     INT NULL                        (séries padrão do laboral)
      + laboral_default_reps     INT NULL                        (reps padrão do laboral)
      + laboral_default_load_kg  DECIMAL(8,2) NULL               (carga TOTAL padrão; NULL = sem carga)

Por que: o usuário pediu exercício laboral (aquecimento/alongamento, ex.:
manguito rotador) sem progressão; no Modo Treino ele só mostra séries, reps e
carga e oferece "Concluir" ou "Pular". As séries gravadas usam o `set_type`
preparatório `laboral` (`app/set_types.py`): contam só no tempo de treino.

--- Decisões do DDL -------------------------------------------------------

1. **`is_laboral NOT NULL DEFAULT 0`, sem backfill.** Todo exercício existente
   continua não laboral; só o admin marca. O histórico gravado antes da
   marcação continua `working` (não é reescrito).
2. **Padrões NULL, sem default.** NULL = sem padrão; o dono cai no que a linha
   da ficha tem e depois no fallback nomeado (`laboral_prescription`).
3. **Um `ALTER TABLE ... ADD COLUMN ..., ALGORITHM=INSTANT`** com as colunas
   que faltam: só metadado (MySQL 8.0 / Aurora MySQL 3, colunas no fim), uma
   versão de linha (`INNODB_TABLES.TOTAL_ROW_VERSIONS`, limite 64). O ALGORITHM
   explícito falha em vez de copiar a tabela em silêncio.
4. **Nenhuma FK, nenhum índice.** As colunas não referenciam nada (nenhuma FK
   para `exercise_catalog`, a 028 recusa); o filtro `is_laboral = 1` roda sobre
   `id IN (...)` (PK).

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` são o inverso (DEV e testes; em produção, reverter é
uma migração nova).

- colunas ausentes -> 1 ALTER com as que faltam; convergido -> 0;
- coluna presente com outro tipo, nulabilidade ou default ->
  `ExercicioLaboralError` antes de qualquer statement;
- tabela ausente: erro numa execução real; aceito em DRY-RUN.

`down`: `DROP COLUMN` das que existirem (perde a marcação e os padrões; as
séries `laboral` gravadas continuam e seguem preparatórias).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
nenhuma literal com ':'; módulo autocontido (não importa código do app).
"""

from __future__ import annotations

TABLE = "exercise_catalog"
# (nome, DDL, tipo normalizado esperado, IS_NULLABLE esperado, default esperado), na ordem do ALTER.
COLUMNS: tuple[tuple[str, str, str, str, str | None], ...] = (
    ("is_laboral", "TINYINT(1) NOT NULL DEFAULT 0", "tinyint(1)", "NO", "0"),
    ("laboral_default_sets", "INT NULL DEFAULT NULL", "int", "YES", None),
    ("laboral_default_reps", "INT NULL DEFAULT NULL", "int", "YES", None),
    ("laboral_default_load_kg", "DECIMAL(8,2) NULL DEFAULT NULL", "decimal(8,2)", "YES", None),
)
NAMES: tuple[str, ...] = tuple(name for name, *_rest in COLUMNS)


class ExercicioLaboralError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise ExercicioLaboralError(f"099: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    """`int(11)` (MySQL < 8.0.19) e `int` são o mesmo tipo; o resto mantém o tamanho."""
    text = "".join(_text(column_type).split()).lower()
    return "int" if text.startswith("int(") else text


def add_sql(names) -> str:
    ddl = {name: column_ddl for name, column_ddl, *_rest in COLUMNS}
    parts = ", ".join(f"ADD COLUMN {name} {ddl[name]}" for name in names)
    return f"ALTER TABLE {TABLE} {parts}, ALGORITHM=INSTANT"


def drop_sql(names) -> str:
    return f"ALTER TABLE {TABLE} " + ", ".join(f"DROP COLUMN {name}" for name in names)


def _read(db):
    """(tabela existe?, {coluna: linha do information_schema})."""
    exists = bool(
        list(
            db.query(
                "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                (TABLE,),
            )
        )
    )
    placeholders = ", ".join(["%s"] * len(NAMES))
    rows = list(
        db.query(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME IN ({placeholders})",
            (TABLE, *NAMES),
        )
    )
    return exists, {_text(row["COLUMN_NAME"]): row for row in rows if _text(row["COLUMN_NAME"]) in NAMES}


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    exists, found = _read(db)
    if not exists:
        if not bool(getattr(db, "dry_run", False)):
            _fail(f"tabela {TABLE} nao existe")
        return [(add_sql(NAMES), None)]
    missing = []
    for name, _ddl, expected_type, expected_nullable, expected_default in COLUMNS:
        info = found.get(name)
        if info is None:
            missing.append(name)
            continue
        actual = _normalize_type(info["COLUMN_TYPE"])
        if actual != expected_type:
            _fail(f"{TABLE}.{name} e {actual}, esperado {expected_type}")
        nullable = _text(info["IS_NULLABLE"]).upper()
        if nullable != expected_nullable:
            _fail(f"{TABLE}.{name} tem IS_NULLABLE={nullable}, esperado {expected_nullable}")
        raw_default = info.get("COLUMN_DEFAULT")
        found_default = None if raw_default is None else _text(raw_default)
        if found_default != expected_default:
            _fail(f"{TABLE}.{name} tem default {found_default!r}, esperado {expected_default!r}")
    return [(add_sql(missing), None)] if missing else []


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: solta as colunas que existirem."""
    exists, found = _read(db)
    present = [name for name in NAMES if name in found]
    if not exists or not present:
        return []
    return [(drop_sql(present), None)]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
