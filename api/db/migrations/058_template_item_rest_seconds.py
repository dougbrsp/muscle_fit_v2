"""058 - descanso entre series definido por exercicio no Modelo (Fase 7,
Frente B).

    workout_plan_template_slot_items
      + rest_seconds  SMALLINT NULL

Por que: ate aqui o descanso entre series de TRABALHO so existia na geracao
da ficha (intervalo min/max do modal, ponto medio aplicado a todo exercicio).
Um Modelo nao conseguia dizer "neste agachamento, 150 s; nesta elevacao
lateral, 45 s". A coluna guarda o valor escolhido no builder; a geracao
(Modo B) usa o valor do item quando existe e o intervalo da geracao quando
nao existe - o valor do Modelo SOBREPOE o intervalo, sem ser limitado por ele.

--- Decisoes do DDL -------------------------------------------------------

1. **NULL = "da geracao".** A coluna nasce NULL em todas as linhas e o codigo
   trata NULL como o comportamento de hoje (ponto medio do intervalo). Sem
   backfill, sem DEFAULT: nenhum plano gerado de Modelo existente muda.

2. **SMALLINT**, como `warmup_rest_seconds`/`feeler_set_rest_seconds` (017).
   O limite de 1 a 600 s e da API (Pydantic), igual ao do aquecimento.

3. **Sem `AFTER`.** O recorte da Aurora lista a tabela mas nao as colunas
   dela; um `AFTER <coluna>` dependeria de um nome que o plano nao consegue
   conferir. A coluna vai para o fim - a ordem fisica nao importa ao codigo,
   que sempre nomeia as colunas.

Dependencias: so `workout_plan_template_slot_items` (007). Nenhuma migracao
recente (055-057) e pre-requisito, entao o DRY-RUN - que planeja as pendentes
SEM aplicar as anteriores - planeja a 058 igual ao deploy real, sem precisar
do tratamento especial da 057.

Fluxo (mesmo padrao da 055/056): `plan(db)` so LE `information_schema`;
`up(db)` executa o plano; `down(db)`/`plan_down(db)` sao o inverso.

- coluna ausente -> exatamente 1 `ALTER TABLE ... ADD COLUMN`;
- coluna presente como `smallint` NULL -> nada (reexecucao: 0 statements);
- coluna presente com outro tipo/nulabilidade, ou tabela ausente ->
  `TemplateItemRestError` ANTES do primeiro statement.

Aurora (recorte 2026-09-17, sem a coluna) -> 1 statement. Reexecucao: 0.
`down` -> 1 `DROP COLUMN` (reversivel de verdade: perder a coluna devolve
todo exercicio ao intervalo da geracao, o comportamento pre-Fase 7).

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app).
"""

from __future__ import annotations

import re

TABLE = "workout_plan_template_slot_items"
COLUMN = "rest_seconds"

ADD_COLUMN_SQL = f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} SMALLINT NULL"
DROP_COLUMN_SQL = f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}"

EXPECTED_TYPE = "smallint"
EXPECTED_NULLABLE = "YES"

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class TemplateItemRestError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise TemplateItemRestError(f"058: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    # `smallint(6)` -> `smallint`
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _read(db) -> tuple[dict[str, str], dict | None]:
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
            (TABLE,),
        )
    }
    column = None
    for row in db.query(
        "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME IN (%s) AND COLUMN_NAME IN (%s)",
        (TABLE, COLUMN),
    ):
        if _text(row["TABLE_NAME"]) == TABLE and _text(row["COLUMN_NAME"]) == COLUMN:
            column = row
    return engines, column


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines, column = _read(db)
    if TABLE not in engines:
        _fail(f"tabela {TABLE} nao existe")
    if column is None:
        return [(ADD_COLUMN_SQL, None)]
    if _normalize_type(column["COLUMN_TYPE"]) != EXPECTED_TYPE:
        _fail(f"{TABLE}.{COLUMN} e {_text(column['COLUMN_TYPE'])}, esperado {EXPECTED_TYPE}")
    if _text(column["IS_NULLABLE"]) != EXPECTED_NULLABLE:
        _fail(f"{TABLE}.{COLUMN} tem IS_NULLABLE={_text(column['IS_NULLABLE'])}, esperado {EXPECTED_NULLABLE}")
    return []


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """Inverso de `plan`: DROP COLUMN so se a coluna existe."""
    engines, column = _read(db)
    if TABLE in engines and column is not None:
        return [(DROP_COLUMN_SQL, None)]
    return []


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)


def down(db):
    for sql, params in plan_down(db):
        db.execute(sql, params)
