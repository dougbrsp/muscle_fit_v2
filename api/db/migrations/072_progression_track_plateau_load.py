"""072 - aumento de carga POR PLATO DA TRILHA (Lote 4/P6 da investigacao RPE 10,
2026-10-11).

    progression_tracks
      + pending_load_source      VARCHAR(20) NULL  origem do aumento pendente
                                                   (`excess_reps` | `track_plateau`)
      + plateau_load_applied_at  DATETIME NULL     instante em que a trilha EXECUTOU
                                                   o ultimo aumento por plato dela

Por que: a decisao do usuario de 2026-10-11 (P6, reabre a D2) torna o plato que
sobe a carga POR TRILHA. O aumento usa o mesmo mecanismo da regra de excesso
(071: `pending_load_kg`/`pending_load_since`), entao a trilha precisa saber a
ORIGEM do aumento pendente: so o aumento por plato conta para a fase do grupo,
que avanca F2 -> F3 quando a maioria das trilhas ativas o efetivou desde a
entrada na fase (`progression_engine.group_load_phase_complete`, a partir de
`plateau_load_applied_at`).

--- Decisoes do DDL -------------------------------------------------------

1. **VARCHAR(20) NULL**: os codigos sao constantes do motor
   (`PENDING_LOAD_SOURCE_*`, ate 13 caracteres). NULL com `pending_load_kg`
   preenchido = excesso (o unico caso anterior a esta migracao).
2. **DATETIME NULL**, como as demais marcas da trilha.
3. **Sem indice.** A leitura e sempre pelas trilhas do usuario.
4. **Sem DML/backfill.** O replay (`db-migrations.yml action=replay-progression`)
   recompoe o estado. O codigo ANTERIOR (o deploy aplica o schema antes do
   codigo) continua funcionando: o UPDATE/INSERT dele nao toca as colunas novas.

--- Fluxo -----------------------------------------------------------------

`plan(db)` so LE `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` sao o inverso.

- coluna ausente -> `ADD COLUMN`; presente com outro tipo/nulabilidade ->
  `TrackPlateauLoadError` antes do primeiro statement;
- estado convergido -> 0 statements;
- `progression_tracks` ausente: erro numa execucao real; aceito em DRY-RUN.

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
modulo autocontido (nao importa codigo do app).
"""

from __future__ import annotations

TABLE = "progression_tracks"

ADD_SOURCE_SQL = f"ALTER TABLE {TABLE} ADD COLUMN pending_load_source VARCHAR(20) NULL DEFAULT NULL"
ADD_APPLIED_SQL = f"ALTER TABLE {TABLE} ADD COLUMN plateau_load_applied_at DATETIME NULL DEFAULT NULL"
DROP_SOURCE_SQL = f"ALTER TABLE {TABLE} DROP COLUMN pending_load_source"
DROP_APPLIED_SQL = f"ALTER TABLE {TABLE} DROP COLUMN plateau_load_applied_at"

# coluna -> (tipo normalizado, IS_NULLABLE, ADD, DROP)
COLUMNS = {
    "pending_load_source": ("varchar(20)", "YES", ADD_SOURCE_SQL, DROP_SOURCE_SQL),
    "plateau_load_applied_at": ("datetime", "YES", ADD_APPLIED_SQL, DROP_APPLIED_SQL),
}


class TrackPlateauLoadError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise TrackPlateauLoadError(f"072: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return "".join(_text(column_type).split()).lower()


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
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME IN (%s, %s)",
        (TABLE, *COLUMNS),
    ):
        columns[_text(row["COLUMN_NAME"])] = row
    return True, columns


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    exists, columns = _read(db)
    if not exists:
        if bool(getattr(db, "dry_run", False)):
            return [(add_sql, None) for _type, _nullable, add_sql, _drop in COLUMNS.values()]
        _fail(f"tabela {TABLE} nao existe (a 036 precisa estar aplicada)")
    statements: list[tuple[str, tuple | None]] = []
    for name, (expected_type, expected_nullable, add_sql, _drop) in COLUMNS.items():
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
    """Inverso de `plan`: solta as colunas (o aumento pendente perde a origem e
    vira excesso; a fase do grupo deixa de ter a marca da maioria)."""
    exists, columns = _read(db)
    if not exists:
        return []
    return [(drop_sql, None) for name, (_t, _n, _a, drop_sql) in reversed(COLUMNS.items()) if name in columns]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
