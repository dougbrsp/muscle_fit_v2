"""069 - origem da FC de repouso e do HRV em `intervals_wellness` (edicao
manual na tela /intervals-icu, 2026-10-08).

    intervals_wellness
      + resting_hr_source  ENUM('intervals','manual') NOT NULL DEFAULT 'intervals'
      + hrv_source         ENUM('intervals','manual') NOT NULL DEFAULT 'intervals'
      + manual_updated_at  TIMESTAMP NULL   ultima edicao manual (auditoria)

Por que: o usuario pode digitar a FC de repouso e o HRV de um dia. A regra de
precedencia (dona: `app/intervals_store.upsert_wellness`) e: se o Intervals.icu
enviar o dado DAQUELE dia, ele sobrescreve a edicao; senao, a edicao fica. Para
isso o sync precisa saber, por metrica, de onde veio o valor guardado.

--- Decisoes do DDL -------------------------------------------------------

1. **ENUM**, como `context_events.source` (048): o vocabulario e fechado e
   pequeno, e o banco recusa um valor errado.
2. **DEFAULT 'intervals'.** Toda linha existente veio do Intervals.icu; o
   codigo ANTERIOR a esta migracao (o deploy aplica o schema antes do codigo)
   continua funcionando: o INSERT dele cai no default e o UPDATE dele nao
   toca as colunas novas.
3. **Sem DML/backfill.** Nenhuma linha nasce 'manual'; o comportamento do sync
   so muda para linhas editadas depois do deploy.

--- Fluxo -----------------------------------------------------------------

`plan(db)` so LE `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` sao o inverso.

- coluna ausente -> `ADD COLUMN`; presente com outro tipo/nulabilidade ->
  `WellnessManualSourceError` antes do primeiro statement;
- estado convergido -> 0 statements;
- `intervals_wellness` ausente: erro numa execucao real; aceito em DRY-RUN
  (027 pendente no mesmo lote, mesmo padrao da 068).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
modulo autocontido (nao importa codigo do app).
"""

from __future__ import annotations

TABLE = "intervals_wellness"

ADD_RESTING_HR_SOURCE_SQL = (
    f"ALTER TABLE {TABLE} ADD COLUMN resting_hr_source ENUM('intervals','manual') NOT NULL DEFAULT 'intervals'"
)
ADD_HRV_SOURCE_SQL = f"ALTER TABLE {TABLE} ADD COLUMN hrv_source ENUM('intervals','manual') NOT NULL DEFAULT 'intervals'"
ADD_MANUAL_UPDATED_AT_SQL = f"ALTER TABLE {TABLE} ADD COLUMN manual_updated_at TIMESTAMP NULL DEFAULT NULL"

# coluna -> (tipo normalizado, IS_NULLABLE, ADD) - na ordem de aplicacao
COLUMNS = {
    "resting_hr_source": ("enum('intervals','manual')", "NO", ADD_RESTING_HR_SOURCE_SQL),
    "hrv_source": ("enum('intervals','manual')", "NO", ADD_HRV_SOURCE_SQL),
    "manual_updated_at": ("timestamp", "YES", ADD_MANUAL_UPDATED_AT_SQL),
}


def _drop_sql(column: str) -> str:
    return f"ALTER TABLE {TABLE} DROP COLUMN {column}"


class WellnessManualSourceError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise WellnessManualSourceError(f"069: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return "".join(_text(column_type).split()).lower().replace('"', "'")


def _read(db) -> tuple[bool, dict[str, dict]]:
    tables = db.query(
        "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (TABLE,),
    )
    exists = any(_text(row["TABLE_NAME"]) == TABLE for row in tables)
    columns: dict[str, dict] = {}
    if not exists:
        return False, columns
    for row in db.query(
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME IN (%s, %s, %s)",
        (TABLE, *COLUMNS),
    ):
        columns[_text(row["COLUMN_NAME"])] = row
    return True, columns


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    exists, columns = _read(db)
    if not exists:
        if bool(getattr(db, "dry_run", False)):
            return [(add_sql, None) for _, _, add_sql in COLUMNS.values()]
        _fail(f"tabela {TABLE} nao existe (a 027 precisa estar aplicada)")
    statements: list[tuple[str, tuple | None]] = []
    for name, (expected_type, expected_nullable, add_sql) in COLUMNS.items():
        column = columns.get(name)
        if column is None:
            statements.append((add_sql, None))
            continue
        if _normalize_type(column["COLUMN_TYPE"]) != expected_type:
            _fail(f"{TABLE}.{name} e {_text(column['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(column["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{TABLE}.{name} tem IS_NULLABLE={_text(column['IS_NULLABLE'])}, esperado {expected_nullable}")
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: solta as colunas presentes (a edicao manual vira
    um valor comum do cache - o valor em si fica)."""
    exists, columns = _read(db)
    if not exists:
        return []
    return [(_drop_sql(name), None) for name in reversed(list(COLUMNS)) if name in columns]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
