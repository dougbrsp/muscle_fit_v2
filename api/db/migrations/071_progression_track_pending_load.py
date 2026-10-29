"""071 - aumento de carga pendente DA TRILHA (Lote 3/P4 da investigacao RPE 10,
2026-10-11).

    progression_tracks
      + pending_load_kg     DECIMAL(6,2) NULL   carga alvo (TOTAL, D12) do aumento
                                                armado pela regra de excesso
      + pending_load_since  DATETIME NULL       instante em que foi armado

Por que: a regra de excesso de repeticoes (decisao do usuario, 2026-10-11) e um
ajuste de carga DA TRILHA: quando a melhor serie de uma sessao qualificante
faz `reps_max + EXCESS_REPS_FAST_TRACK` no RPE alvo, a PROXIMA ficha daquela
trilha sobe um passo de carga sem esperar o plato do grupo
(`progression_engine.arm_excess_reps_fast_track` / `prescribe`). A avaliacao
acontece no `finish`; a prescricao, depois, a partir do estado PERSISTIDO -
entao o sinal precisa morar na trilha. Nao e acao do grupo: nao cabe em
`progression_group_states.pending_action` (uma por grupo, promove a fase). O
aumento sai quando a pessoa executa a carga alvo ou expira em
`GROUP_EFFECTIVATION_DAYS` (`track_pending_load_status`).

--- Decisoes do DDL -------------------------------------------------------

1. **DECIMAL(6,2) NULL**, o mesmo tipo de `working_load_kg` (036): a carga
   alvo e uma carga da trilha.
2. **Sem indice.** A leitura e sempre pelas trilhas do usuario
   (`progression_state.load_tracks`).
3. **Sem DML/backfill.** NULL = sem aumento pendente. O replay
   (`db-migrations.yml action=replay-progression`) recompoe o estado a partir
   do historico. O codigo ANTERIOR a esta migracao (o deploy aplica o schema
   antes do codigo) continua funcionando: o UPDATE/INSERT dele nao toca as
   colunas novas.

--- Fluxo -----------------------------------------------------------------

`plan(db)` so LE `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` sao o inverso.

- coluna ausente -> `ADD COLUMN`; presente com outro tipo/nulabilidade ->
  `TrackPendingLoadError` antes do primeiro statement;
- estado convergido -> 0 statements;
- `progression_tracks` ausente: erro numa execucao real; aceito em DRY-RUN
  (036 pendente no mesmo lote, mesmo padrao da 068).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
modulo autocontido (nao importa codigo do app).
"""

from __future__ import annotations

TABLE = "progression_tracks"

ADD_LOAD_SQL = f"ALTER TABLE {TABLE} ADD COLUMN pending_load_kg DECIMAL(6,2) NULL DEFAULT NULL"
ADD_SINCE_SQL = f"ALTER TABLE {TABLE} ADD COLUMN pending_load_since DATETIME NULL DEFAULT NULL"
DROP_LOAD_SQL = f"ALTER TABLE {TABLE} DROP COLUMN pending_load_kg"
DROP_SINCE_SQL = f"ALTER TABLE {TABLE} DROP COLUMN pending_load_since"

# coluna -> (tipo normalizado, IS_NULLABLE, ADD, DROP)
COLUMNS = {
    "pending_load_kg": ("decimal(6,2)", "YES", ADD_LOAD_SQL, DROP_LOAD_SQL),
    "pending_load_since": ("datetime", "YES", ADD_SINCE_SQL, DROP_SINCE_SQL),
}


class TrackPendingLoadError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise TrackPendingLoadError(f"071: {message}. Nenhum statement executado.")


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
    """Inverso de `plan`: solta as colunas (o aumento pendente some; a trilha
    volta a subir so pelo plato do grupo)."""
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
