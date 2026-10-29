"""075 - locais novos de medida por fita (plano de nutrição, Lote L0, 2026-10-15).

    body_measurements
      ~ site  ENUM('waist','hip','chest','arm_l','arm_r','thigh_l','thigh_r','calf_l','calf_r',
                   'neck','forearm_l','forearm_r') NOT NULL

Acrescenta `neck` (pescoço: entra na estimativa de % de gordura por
circunferência, plano §3), `forearm_l` e `forearm_r` **no fim** do ENUM da 047.
Acrescentar no fim preserva os índices internos dos valores existentes: no
MySQL 8 é alteração de metadado (ALGORITHM=INSTANT), sem reescrever as 45
linhas medidas em produção (leitura de 2026-10-15).

O vocabulário tem dono em `app/domain/body_measurement_sites.py`
(`MEASUREMENT_SITES`); o teste de plano desta migração compara os dois lados,
e o da 047 compara a tupla dela com o prefixo. Este módulo repete a tupla
porque migrações são autocontidas (não importam código do app).

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ `information_schema.COLUMNS` (`COLUMN_TYPE`, `IS_NULLABLE` de
`site`); `up(db)` executa o plano; `down(db)`/`plan_down(db)` são o inverso,
mas o runner de produção não tem ação `down`: em produção, reverter é sempre
uma migração nova; `down`/`plan_down` servem ao DEV e aos testes.

- `COLUMN_TYPE` = ENUM da 047 -> 1 `MODIFY COLUMN`;
- `COLUMN_TYPE` = ENUM desta migração -> 0 statements (reexecução);
- qualquer outro ENUM, coluna ausente ou NULL permitido -> `BodyMeasurementSitesError`
  antes do primeiro statement;
- tabela ausente: erro numa execução real (a 047 precisa estar aplicada);
  aceito em DRY-RUN.
- `plan_down`: volta ao ENUM da 047 SOMENTE se nenhuma linha usa os locais
  novos (senão o MODIFY truncaria dados -> erro).

Ordem do deploy: com `RUN_DB_MIGRATIONS=true` o deploy de produção aplica o
schema antes de atualizar as Lambdas. Com o gate desligado, o POST de medida
com local novo (`neck`, `forearm_l`, `forearm_r`: a API já valida pelo dono)
dá 500 no INSERT; os locais antigos seguem funcionando.

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
módulo autocontido.
"""

from __future__ import annotations

TABLE = "body_measurements"
COLUMN = "site"

# ENUM da 047 (prefixo preservado) e os três locais acrescentados no fim.
SITES_047 = (
    "waist",
    "hip",
    "chest",
    "arm_l",
    "arm_r",
    "thigh_l",
    "thigh_r",
    "calf_l",
    "calf_r",
)
SITES_ADDED = ("neck", "forearm_l", "forearm_r")
SITES_075 = SITES_047 + SITES_ADDED


def _enum_values_sql(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


def _expected_type(values: tuple[str, ...]) -> str:
    return f"enum({_enum_values_sql(values)})"


EXPECTED_TYPE_BEFORE = _expected_type(SITES_047)
EXPECTED_TYPE_AFTER = _expected_type(SITES_075)

MODIFY_SQL = f"ALTER TABLE {TABLE} MODIFY COLUMN {COLUMN} ENUM({_enum_values_sql(SITES_075)}) NOT NULL"
REVERT_SQL = f"ALTER TABLE {TABLE} MODIFY COLUMN {COLUMN} ENUM({_enum_values_sql(SITES_047)}) NOT NULL"
COUNT_ADDED_SQL = f"SELECT COUNT(*) AS n FROM {TABLE} WHERE {COLUMN} IN ({', '.join(['%s'] * len(SITES_ADDED))})"


class BodyMeasurementSitesError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise BodyMeasurementSitesError(f"075: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return "".join(_text(column_type).split()).lower()


def _read(db) -> tuple[bool, dict | None]:
    tables = db.query(
        "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (TABLE,),
    )
    exists = any(_text(row["TABLE_NAME"]) == TABLE for row in tables)
    if not exists:
        return False, None
    rows = db.query(
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (TABLE, COLUMN),
    )
    return True, (rows[0] if rows else None)


def _current_type(column: dict | None) -> str:
    if column is None:
        _fail(f"{TABLE}.{COLUMN} nao existe (a 047 precisa estar aplicada)")
    if _text(column["IS_NULLABLE"]) != "NO":
        _fail(f"{TABLE}.{COLUMN} tem IS_NULLABLE={_text(column['IS_NULLABLE'])}, esperado NO")
    current = _normalize_type(column["COLUMN_TYPE"])
    if current not in (EXPECTED_TYPE_BEFORE, EXPECTED_TYPE_AFTER):
        _fail(f"{TABLE}.{COLUMN} e {_text(column['COLUMN_TYPE'])}, esperado o ENUM da 047 ou o desta migracao")
    return current


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    exists, column = _read(db)
    if not exists:
        if bool(getattr(db, "dry_run", False)):
            return [(MODIFY_SQL, None)]
        _fail(f"tabela {TABLE} nao existe (a 047 precisa estar aplicada)")
    if _current_type(column) == EXPECTED_TYPE_AFTER:
        return []
    return [(MODIFY_SQL, None)]


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: volta ao ENUM da 047 se nenhuma linha usa os locais novos."""
    exists, column = _read(db)
    if not exists or _current_type(column) == EXPECTED_TYPE_BEFORE:
        return []
    rows = db.query(COUNT_ADDED_SQL, SITES_ADDED)
    row = rows[0] if rows else None
    used = int((row["n"] if isinstance(row, dict) else row[0]) or 0) if row is not None else 0
    if used:
        _fail(f"{used} linha(s) usam {', '.join(SITES_ADDED)}; o ENUM da 047 truncaria dados")
    return [(REVERT_SQL, None)]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
