"""068 - trilha da progressao por contexto de fadiga previa (PR 2 da fadiga
previa na sessao, 2026-10-07).

    progression_tracks
      + fatigue_context  VARCHAR(20) NOT NULL DEFAULT 'rested'
      + calibrated_at    DATETIME NULL
      + UNIQUE KEY uk_ptr_user_pair_context (user_id, exercise_id, equipment_id, fatigue_context)
      - UNIQUE KEY uk_ptr_user_pair         (user_id, exercise_id, equipment_id)

Por que: o mesmo exercicio feito em ordens diferentes (supino 1o na segunda,
5o na quinta depois de exercicios que ja cansaram peitoral/triceps/deltoide)
nao e a mesma condicao. A trilha (carga, series, historico de regressao e
plato) passa a ser por (exercicio, equipamento, CONTEXTO), e o contexto e a
faixa do dono `app/domain/prior_fatigue.py` (`rested` / `pre_fatigued` /
`heavily_fatigued`).

Por que persistir (e nao so derivar na leitura): a trilha JA e estado
persistido (carga adotada, acao efetivada, series) que o motor le para
prescrever; sem a coluna, duas trilhas do mesmo par colidiriam na UK. O
CONTEXTO em si continua derivado do historico registrado (series executadas +
contribuicoes cadastradas) - o replay (`db-migrations.yml action=replay`)
reconstroi tudo a partir dele.

`calibrated_at`: instante da sessao que calibrou a trilha. Ela e as
anteriores nao entram no historico de regressao (D10)/plato da trilha - a
primeira exposicao num contexto novo nao conta (decisao do usuario,
2026-10-07). NULL = trilha gravada antes desta migracao e ainda nao
reprocessada (comportamento anterior).

--- Decisoes do DDL -------------------------------------------------------

1. **VARCHAR(20), nao ENUM.** O vocabulario e do dono Python
   (`PriorFatigueBand`); um ENUM no banco seria uma segunda copia dele.
2. **DEFAULT 'rested'.** Toda trilha existente vira "descansado" (o contexto
   de quem nunca mediu fadiga). O codigo ANTERIOR a esta migracao continua
   funcionando enquanto a Lambda nao e atualizada (o deploy aplica o schema
   antes do codigo): o INSERT dele cai no default e o UPDATE por par so
   encontra a linha 'rested'.
3. **UK nova ANTES de remover a antiga.** `fk_ptr_user` precisa de um indice
   iniciado por `user_id`; a UK nova e `idx_ptr_user_group_executed` cobrem,
   e a ordem garante que nunca ha um instante sem unicidade.
4. **Sem DML/backfill.** O replay reprocessa o historico e separa as trilhas
   por contexto (passo pos-deploy, ver a descricao do PR).

--- Fluxo -----------------------------------------------------------------

`plan(db)` so LE `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` sao o inverso.

- coluna ausente -> `ADD COLUMN`; presente com outro tipo/nulabilidade ->
  `TrackFatigueContextError` antes do primeiro statement;
- UK nova ausente -> `ADD UNIQUE KEY`; UK antiga presente -> `DROP INDEX`;
- estado convergido -> 0 statements;
- `progression_tracks` ausente: erro numa execucao real; aceito em DRY-RUN
  (036 pendente no mesmo lote, mesmo padrao da 063).

`down`: so possivel se nao houver duas trilhas do mesmo par (a UK antiga
voltaria a valer) - o `down` recria a UK antiga ANTES de soltar a nova e o
MySQL recusa se houver duplicata (nada e apagado em silencio).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
modulo autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

TABLE = "progression_tracks"
OLD_UK = "uk_ptr_user_pair"
NEW_UK = "uk_ptr_user_pair_context"
NEW_UK_COLUMNS = ["user_id", "exercise_id", "equipment_id", "fatigue_context"]
OLD_UK_COLUMNS = ["user_id", "exercise_id", "equipment_id"]

ADD_CONTEXT_SQL = f"ALTER TABLE {TABLE} ADD COLUMN fatigue_context VARCHAR(20) NOT NULL DEFAULT 'rested'"
ADD_CALIBRATED_SQL = f"ALTER TABLE {TABLE} ADD COLUMN calibrated_at DATETIME NULL"
ADD_NEW_UK_SQL = f"ALTER TABLE {TABLE} ADD UNIQUE KEY {NEW_UK} ({', '.join(NEW_UK_COLUMNS)})"
DROP_OLD_UK_SQL = f"ALTER TABLE {TABLE} DROP INDEX {OLD_UK}"

ADD_OLD_UK_SQL = f"ALTER TABLE {TABLE} ADD UNIQUE KEY {OLD_UK} ({', '.join(OLD_UK_COLUMNS)})"
DROP_NEW_UK_SQL = f"ALTER TABLE {TABLE} DROP INDEX {NEW_UK}"
DROP_CONTEXT_SQL = f"ALTER TABLE {TABLE} DROP COLUMN fatigue_context"
DROP_CALIBRATED_SQL = f"ALTER TABLE {TABLE} DROP COLUMN calibrated_at"

# coluna -> (tipo normalizado, IS_NULLABLE, ADD)
COLUMNS = {
    "fatigue_context": ("varchar(20)", "NO", ADD_CONTEXT_SQL),
    "calibrated_at": ("datetime", "YES", ADD_CALIBRATED_SQL),
}

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class TrackFatigueContextError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise TrackFatigueContextError(f"068: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read(db) -> tuple[bool, dict[str, dict], dict[str, list[str]]]:
    tables = db.query(
        "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (TABLE,),
    )
    exists = any(_text(row["TABLE_NAME"]) == TABLE for row in tables)
    columns: dict[str, dict] = {}
    indexes: dict[str, list[str]] = {}
    if not exists:
        return False, columns, indexes
    for row in db.query(
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME IN (%s, %s)",
        (TABLE, *COLUMNS),
    ):
        columns[_text(row["COLUMN_NAME"])] = row
    for row in db.query(
        "SELECT INDEX_NAME, COLUMN_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME IN (%s, %s) "
        "ORDER BY INDEX_NAME, SEQ_IN_INDEX",
        (TABLE, OLD_UK, NEW_UK),
    ):
        indexes.setdefault(_text(row["INDEX_NAME"]), []).append(_text(row["COLUMN_NAME"]))
    return True, columns, indexes


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    exists, columns, indexes = _read(db)
    if not exists:
        if bool(getattr(db, "dry_run", False)):
            return [(ADD_CONTEXT_SQL, None), (ADD_CALIBRATED_SQL, None), (ADD_NEW_UK_SQL, None), (DROP_OLD_UK_SQL, None)]
        _fail(f"tabela {TABLE} nao existe (a 036 precisa estar aplicada)")
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
    if NEW_UK in indexes:
        if indexes[NEW_UK] != NEW_UK_COLUMNS:
            _fail(f"{NEW_UK} cobre {indexes[NEW_UK]}, esperado {NEW_UK_COLUMNS}")
    else:
        statements.append((ADD_NEW_UK_SQL, None))
    if OLD_UK in indexes:
        statements.append((DROP_OLD_UK_SQL, None))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: UK antiga de volta (falha se houver duas trilhas do
    mesmo par), depois solta a nova e as colunas."""
    exists, columns, indexes = _read(db)
    if not exists:
        return []
    statements: list[tuple[str, tuple | None]] = []
    if OLD_UK not in indexes:
        statements.append((ADD_OLD_UK_SQL, None))
    if NEW_UK in indexes:
        statements.append((DROP_NEW_UK_SQL, None))
    if "calibrated_at" in columns:
        statements.append((DROP_CALIBRATED_SQL, None))
    if "fatigue_context" in columns:
        statements.append((DROP_CONTEXT_SQL, None))
    return statements


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
