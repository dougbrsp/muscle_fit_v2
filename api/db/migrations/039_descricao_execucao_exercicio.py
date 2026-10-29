"""039 - descricao de execucao do exercicio no catalogo (M4: tela de briefing
do modo treino).

Colunas novas em `exercise_catalog`, todas ADITIVAS e todas NULLABLE (nenhuma
coluna existente e tocada, nenhuma linha muda de comportamento):

    execution_description                TEXT NULL
        texto de "como executar este exercicio", gerado UMA VEZ pela OpenAI
        (`app/exercise_description_ai.py`) e persistido. NULL = ainda nao
        gerado -> a tela de briefing simplesmente nao mostra a secao.
    execution_description_model          VARCHAR(80) NULL
        modelo OpenAI que gerou o texto (ex.: `gpt-5.4-mini`). NULL quando o
        texto foi escrito/colado a mao pelo admin.
    execution_description_prompt_version VARCHAR(40) NULL
        versao do prompt/schema usado (`EXEC_DESC_PROMPT_VERSION`). Permite
        regerar em lote so o que saiu de uma versao antiga de prompt.
    execution_description_generated_at   DATETIME NULL
        quando a geracao rodou.
    execution_description_reviewed_by    BIGINT NULL
        `users.id` do admin que revisou/editou o texto em /dominio-exercicios.
        FK LOGICA (sem constraint fisica): e um campo de AUDITORIA, e apagar
        um usuario nao pode derrubar o catalogo nem exigir ordem de DELETE.
        NULL = nunca revisado por humano.
    execution_description_reviewed_at    DATETIME NULL
        quando a revisao humana aconteceu.

Sem indice novo: nenhuma consulta filtra por estas colunas. A geracao em lote
varre `WHERE execution_description IS NULL` sobre uma tabela de ~algumas
centenas de linhas (61 equipamentos ativos / catalogo pequeno), onde um index
scan nao paga o custo de manutencao do indice.

DATETIME e nao TIMESTAMP: sao marcos de auditoria, nao "ultima alteracao da
linha" (`updated_at` ja cobre isso), e TIMESTAMP em MySQL 8 carrega regras de
default/ON UPDATE implicitas que nao queremos aqui.

`exercise_catalog.id` e BIGINT **UNSIGNED** (migracao 002/028) - armadilha
conhecida do catalogo. Nenhuma coluna desta migracao referencia
`exercise_catalog.id`, entao ela nao se aplica; `users.id` e BIGINT signed
(conferido na 038), e por isso `execution_description_reviewed_by` e BIGINT
sem UNSIGNED.

Fluxo: `plan(db)` so LE `information_schema` e devolve os statements;
`up(db)` executa o plano.

- coluna ausente -> entra no unico `ALTER TABLE exercise_catalog ADD ...`;
- ja presente no formato esperado -> nada (estado convergido = 0 statements);
- tabela ausente, ENGINE != InnoDB, ou coluna presente com tipo/nulabilidade/
  default diferente -> `DescricaoExecucaoError` ANTES do primeiro statement.

Data API: sem PREPARE/SET/multi-statement; um statement por chamada; modulo
autocontido (nao importa codigo do app nem usa classes de dados).
"""

from __future__ import annotations

import re

CATALOG = "exercise_catalog"

# (coluna, DDL do ADD COLUMN, COLUMN_TYPE normalizado, IS_NULLABLE,
#  COLUMN_DEFAULT esperado)
COLUMNS = (
    ("execution_description", "TEXT NULL", "text", "YES", None),
    ("execution_description_model", "VARCHAR(80) NULL", "varchar(80)", "YES", None),
    ("execution_description_prompt_version", "VARCHAR(40) NULL", "varchar(40)", "YES", None),
    ("execution_description_generated_at", "DATETIME NULL", "datetime", "YES", None),
    ("execution_description_reviewed_by", "BIGINT NULL", "bigint", "YES", None),
    ("execution_description_reviewed_at", "DATETIME NULL", "datetime", "YES", None),
)

_INT_DISPLAY_WIDTH_RE = re.compile(r"^(tinyint|smallint|mediumint|int|bigint)\(\d+\)")


class DescricaoExecucaoError(Exception):
    """Estado incompativel; nenhum statement desta execucao foi aplicado."""


def _fail(message: str) -> None:
    raise DescricaoExecucaoError(f"039: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return _INT_DISPLAY_WIDTH_RE.sub(r"\1", _text(column_type).strip().lower())


def _normalize_default(value) -> str | None:
    if value is None:
        return None
    return _text(value).strip().strip("'")


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem."""
    engines = {
        _text(row["TABLE_NAME"]): _text(row["ENGINE"])
        for row in db.query(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
            (CATALOG,),
        )
    }
    if CATALOG not in engines:
        _fail(f"tabela {CATALOG} nao existe")
    if engines[CATALOG].lower() != "innodb":
        _fail(f"{CATALOG} usa ENGINE={engines[CATALOG]}, esperado InnoDB")

    existing = {
        _text(row["COLUMN_NAME"]): row
        for row in db.query(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)",
            (CATALOG,),
        )
    }

    missing: list[str] = []
    for column, ddl, expected_type, expected_nullable, expected_default in COLUMNS:
        info = existing.get(column)
        if info is None:
            missing.append(f"ADD COLUMN {column} {ddl}")
            continue
        label = f"{CATALOG}.{column}"
        if _normalize_type(info["COLUMN_TYPE"]) != expected_type:
            _fail(f"{label} e {_text(info['COLUMN_TYPE'])}, esperado {expected_type}")
        if _text(info["IS_NULLABLE"]) != expected_nullable:
            _fail(f"{label} tem IS_NULLABLE={_text(info['IS_NULLABLE'])}, esperado {expected_nullable}")
        if _normalize_default(info["COLUMN_DEFAULT"]) != _normalize_default(expected_default):
            _fail(f"{label} tem DEFAULT {_text(info['COLUMN_DEFAULT'])!r}, esperado {expected_default!r}")

    if not missing:
        return []
    return [(f"ALTER TABLE {CATALOG} " + ", ".join(missing), None)]


def up(db):
    for sql, params in plan(db):
        db.execute(sql, params)
