"""095 - prescrição de cardio do item do Modelo (M98 C0; dono
`app/domain/cardio_prescription.py`).

    workout_plan_template_slot_items
      + cardio_mode              ENUM('continuous','hiit') NULL   (NULL = Normal, item antigo)
      + cardio_warmup_minutes    SMALLINT NULL                    aquecimento (min)
      + cardio_cooldown_minutes  SMALLINT NULL                    recuperação/desaquecimento (min)
      + cardio_target_zone       TINYINT NULL                     zona-alvo do Normal (1..5)
      + hiit_low_seconds         SMALLINT NULL                    tempo em zona baixa (s)
      + hiit_high_seconds        SMALLINT NULL                    tempo em zona alta (s)
      + hiit_low_zone            TINYINT NULL
      + hiit_high_zone           TINYINT NULL
      + hiit_rounds              SMALLINT NULL                    nº de séries
      backfill: item de cardio com `cardio_duration_minutes` -> cardio_mode = 'continuous'

Por que: o item de cardio do Modelo só tinha a duração. O usuário passa a
prescrever Normal (aquecimento, tempo, zona-alvo, recuperação) e HIIT
(aquecimento, tempo em zona baixa e alta em SEGUNDOS, as duas zonas, nº de
séries, recuperação). `cardio_duration_minutes` (007) continua sendo o tempo
de cardio do Normal. Plano: `docs/cardio/PLANO_MOTOR_CARDIO_2026-10-26.md`.

--- Decisões do DDL -------------------------------------------------------

1. **Todas NULL, sem default.** Nenhum comportamento muda para quem não
   conhece as colunas: item antigo = Normal sem aquecimento/recuperação/zona
   (leitura do dono). Os mínimos, tetos e a regra "zona alta > zona baixa" são
   do dono (`cardio_prescription.validate`), não do banco (sem CHECK).
2. **`cardio_mode` ENUM, não VARCHAR.** Vocabulário fechado do dono
   (`CARDIO_MODE_VALUES`); o teste `test_migration_095_plano.py` prova que o
   ENUM daqui é o do dono (a migração é autocontida e não importa o app).
3. **Backfill só do modo.** Item de cardio com duração vira
   `cardio_mode='continuous'`: o que ele sempre foi. Zona nula = "não
   definida"; aquecimento/recuperação nulos = 0. O dono lê NULL como Normal, e
   a forma canônica da versão do Modelo trata as duas formas como iguais (o
   backfill não abre versão). O UPDATE só toca `cardio_mode IS NULL`:
   reexecutar não muda nada (idempotente) e não sobrescreve um modo gravado.
   As linhas de versões antigas também recebem o modo: o significado delas
   não muda (Normal era o único modo).
4. **Um só `ALTER TABLE ... ADD COLUMN ..., ADD COLUMN ..., ALGORITHM=INSTANT`.**
   Só metadado (MySQL 8.0 / Aurora MySQL 3, colunas no fim da tabela) e um
   só statement = uma só versão de linha (`INNODB_TABLES.TOTAL_ROW_VERSIONS`,
   limite 64; ver 089). O ALGORITHM explícito falha em vez de copiar a tabela
   em silêncio. Com parte das colunas já criadas, o ALTER leva só as que
   faltam.
5. **Nenhuma FK, nenhum índice.** Nenhuma FK para `exercise_catalog` (a 028
   recusa); as colunas não referenciam nada e nenhuma leitura filtra por elas.

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ (`information_schema` e a contagem do backfill); `up(db)`
executa o plano; `down(db)`/`plan_down(db)` são o inverso (o runner de produção
não tem ação `down`: em produção, reverter é uma migração nova; `down` serve
ao DEV e aos testes).

- colunas ausentes -> 1 ALTER com as que faltam + o UPDATE do backfill;
- coluna presente com outro tipo/nulabilidade -> `PrescricaoCardioError`
  antes do primeiro statement;
- colunas presentes e itens sem modo -> só o UPDATE; convergido -> 0;
- tabela ausente: erro numa execução real; aceito em DRY-RUN (lista o ALTER e
  o UPDATE).

`down`: 1 ALTER com `DROP COLUMN` de cada coluna nova que existir (perde a
prescrição estruturada; a duração da 007 fica, e o item volta a ser só
duração).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
nenhuma literal com ':' (viraria parâmetro no shim); módulo autocontido (não
importa código do app).
"""

from __future__ import annotations

import re

TABLE = "workout_plan_template_slot_items"
MODE_COLUMN = "cardio_mode"
CARDIO_KIND = "cardio"
DURATION_COLUMN = "cardio_duration_minutes"

MODE_VALUES: tuple[str, ...] = ("continuous", "hiit")
BACKFILL_MODE = "continuous"
_MODE_DDL = "ENUM(" + ",".join(f"'{value}'" for value in MODE_VALUES) + ") NULL DEFAULT NULL"
_MODE_TYPE = "enum(" + ",".join(f"'{value}'" for value in MODE_VALUES) + ")"

# coluna -> (DDL, tipo normalizado esperado), na ordem do ALTER. Todas NULL.
COLUMNS: dict[str, tuple[str, str]] = {
    MODE_COLUMN: (_MODE_DDL, _MODE_TYPE),
    "cardio_warmup_minutes": ("SMALLINT NULL DEFAULT NULL", "smallint"),
    "cardio_cooldown_minutes": ("SMALLINT NULL DEFAULT NULL", "smallint"),
    "cardio_target_zone": ("TINYINT NULL DEFAULT NULL", "tinyint"),
    "hiit_low_seconds": ("SMALLINT NULL DEFAULT NULL", "smallint"),
    "hiit_high_seconds": ("SMALLINT NULL DEFAULT NULL", "smallint"),
    "hiit_low_zone": ("TINYINT NULL DEFAULT NULL", "tinyint"),
    "hiit_high_zone": ("TINYINT NULL DEFAULT NULL", "tinyint"),
    "hiit_rounds": ("SMALLINT NULL DEFAULT NULL", "smallint"),
}

# Itens que o backfill ainda precisa tocar (o mesmo WHERE do UPDATE).
_BACKFILL_WHERE = f"item_kind = %s AND {MODE_COLUMN} IS NULL AND {DURATION_COLUMN} IS NOT NULL"
BACKFILL_SQL = f"UPDATE {TABLE} SET {MODE_COLUMN} = %s WHERE {_BACKFILL_WHERE}"
BACKFILL_PARAMS: tuple = (BACKFILL_MODE, CARDIO_KIND)
PENDING_SQL = f"SELECT COUNT(*) AS n FROM {TABLE} WHERE {_BACKFILL_WHERE}"

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\((?!1\))\d+\)")


def add_columns_sql(columns) -> str:
    # INSTANT explícito: se não for elegível, falha na hora em vez de copiar a
    # tabela em silêncio. No deploy o migrate roda antes de as Lambdas
    # receberem o código novo: falhar aqui é seguro.
    adds = ", ".join(f"ADD COLUMN {name} {COLUMNS[name][0]}" for name in columns)
    return f"ALTER TABLE {TABLE} {adds}, ALGORITHM=INSTANT"


def drop_columns_sql(columns) -> str:
    return f"ALTER TABLE {TABLE} " + ", ".join(f"DROP COLUMN {name}" for name in columns)


class PrescricaoCardioError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise PrescricaoCardioError(f"095: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    text = "".join(_text(column_type).split()).lower()
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", text)


def _read(db):
    """(tabela existe?, {coluna: linha}) das colunas novas."""
    exists = bool(
        list(
            db.query(
                "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                (TABLE,),
            )
        )
    )
    names = tuple(COLUMNS)
    placeholders = ", ".join(["%s"] * len(names))
    columns = {
        _text(row["COLUMN_NAME"]): row
        for row in db.query(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME IN ({placeholders})",
            (TABLE, *names),
        )
    }
    return exists, columns


def _check_column(name: str, info) -> None:
    expected = COLUMNS[name][1]
    found = _normalize_type(info["COLUMN_TYPE"])
    if found != expected:
        _fail(f"{TABLE}.{name} e {found}, esperado {expected}")
    if _text(info["IS_NULLABLE"]) != "YES":
        _fail(f"{TABLE}.{name} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado YES")


def _pending(db) -> int:
    rows = list(db.query(PENDING_SQL, (CARDIO_KIND,)))
    return int(rows[0]["n"]) if rows else 0


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    exists, columns = _read(db)
    if not exists:
        if not bool(getattr(db, "dry_run", False)):
            _fail(f"tabela {TABLE} nao existe")
        return [(add_columns_sql(COLUMNS), None), (BACKFILL_SQL, BACKFILL_PARAMS)]

    for name, info in columns.items():
        _check_column(name, info)
    missing = [name for name in COLUMNS if name not in columns]
    statements: list[tuple[str, tuple | None]] = []
    if missing:
        statements.append((add_columns_sql(missing), None))
    # Sem a coluna do modo, não há o que contar: o UPDATE vai depois do ALTER.
    if MODE_COLUMN in missing or _pending(db) > 0:
        statements.append((BACKFILL_SQL, BACKFILL_PARAMS))
    return statements


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: solta as colunas novas que existirem (num ALTER só)."""
    exists, columns = _read(db)
    present = [name for name in COLUMNS if name in columns]
    if not exists or not present:
        return []
    return [(drop_columns_sql(present), None)]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
