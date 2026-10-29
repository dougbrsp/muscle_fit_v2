"""096 - FCmáx manual no perfil do usuário (M98 C1; dono `app/domain/hr_zones.py`).

    users
      + max_hr_bpm_manual  SMALLINT NULL   FCmáx informada pelo usuário (bpm)

Por que: o editor do Modelo mostra a faixa de bpm de cada zona (Z1–Z5) ao lado
do seletor de zona do cardio. A precedência das zonas é do dono
(`hr_zones.resolve_athlete_zones`): Intervals.icu (C2) -> FCmáx manual -> sem
zonas. Esta coluna é a fonte "FCmáx manual"; o usuário a edita no perfil.
Plano: `docs/cardio/PLANO_MOTOR_CARDIO_2026-10-26.md` §5.

--- Decisões do DDL -------------------------------------------------------

1. **NULL, sem default.** NULL = "não informada" = sem zonas pelo fallback.
   Nenhum comportamento muda para quem não a preenche. A faixa aceita
   (`MANUAL_MAX_HR_MIN_BPM`..`MANUAL_MAX_HR_MAX_BPM`) é do dono, validada na
   rota (`hr_zones.validate_manual_max_hr`), não do banco (sem CHECK).
2. **`ALTER TABLE ... ADD COLUMN ..., ALGORITHM=INSTANT`.** Só metadado
   (MySQL 8.0 / Aurora MySQL 3, coluna no fim da tabela), uma só versão de
   linha (`INNODB_TABLES.TOTAL_ROW_VERSIONS`, limite 64; ver 089). O
   ALGORITHM explícito falha em vez de copiar a tabela em silêncio.
3. **Nenhuma FK, nenhum índice, nenhum backfill.** A coluna não referencia
   nada e nenhuma leitura filtra por ela.

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ `information_schema`; `up(db)` executa o plano;
`down(db)`/`plan_down(db)` são o inverso (o runner de produção não tem ação
`down`: em produção, reverter é uma migração nova; `down` serve ao DEV e aos
testes).

- coluna ausente -> 1 ALTER;
- coluna presente com o tipo/nulabilidade esperados -> 0 (idempotente);
- coluna presente com outra forma -> `FcmaxManualError` antes de qualquer
  statement;
- tabela ausente: erro numa execução real; aceito em DRY-RUN (lista o ALTER).

`down`: `ALTER TABLE users DROP COLUMN max_hr_bpm_manual` se a coluna existir
(perde a FCmáx informada; o editor volta a não mostrar a faixa de bpm).

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
nenhuma literal com ':' (viraria parâmetro no shim); módulo autocontido (não
importa código do app).
"""

from __future__ import annotations

import re

TABLE = "users"
COLUMN = "max_hr_bpm_manual"
COLUMN_DDL = "SMALLINT NULL DEFAULT NULL"
COLUMN_TYPE = "smallint"

ADD_SQL = f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} {COLUMN_DDL}, ALGORITHM=INSTANT"
DROP_SQL = f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}"

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\((?!1\))\d+\)")


class FcmaxManualError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise FcmaxManualError(f"096: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    text = "".join(_text(column_type).split()).lower()
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", text)


def _read(db):
    """(tabela existe?, linha da coluna ou None)."""
    exists = bool(
        list(
            db.query(
                "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                (TABLE,),
            )
        )
    )
    rows = list(
        db.query(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
            (TABLE, COLUMN),
        )
    )
    return exists, (rows[0] if rows else None)


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    exists, info = _read(db)
    if not exists:
        if not bool(getattr(db, "dry_run", False)):
            _fail(f"tabela {TABLE} nao existe")
        return [(ADD_SQL, None)]
    if info is None:
        return [(ADD_SQL, None)]
    found = _normalize_type(info["COLUMN_TYPE"])
    if found != COLUMN_TYPE:
        _fail(f"{TABLE}.{COLUMN} e {found}, esperado {COLUMN_TYPE}")
    if _text(info["IS_NULLABLE"]) != "YES":
        _fail(f"{TABLE}.{COLUMN} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado YES")
    return []


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: solta a coluna se existir."""
    exists, info = _read(db)
    if not exists or info is None:
        return []
    return [(DROP_SQL, None)]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
