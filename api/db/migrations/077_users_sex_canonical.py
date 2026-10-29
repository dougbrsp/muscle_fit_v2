"""077 - sexo canônico em `users` (plano de nutrição, Lote L2a, 2026-10-17).

    users.sex   'M' -> 'male'   'F' -> 'female'   (e as outras grafias que
                `normalize_sex` aceita: 'masculino', 'Mulher', 'não binário'...)

Por que: o vocabulário de sexo tem dono no motor de nutrição
(`app/domain/nutrition/anthropometrics.py`: `Sex`, `normalize_sex`). As telas de
perfil e de administração gravavam 'M'/'F' e o L0 deixou a conversão para este
lote, junto com as telas que passam a ler as opções do motor (`nutrition-constants.ts`).
Medido em produção em 2026-10-15: `users.sex` é `varchar(20)` com exatamente
'M' (1 linha), 'F' (1 linha) e NULL (2 linhas) -> 2 statements, 2 linhas.

--- Decisoes ---------------------------------------------------------------

1. **Aliases embutidos, sem importar o motor.** `SEX_ALIASES` é uma cópia
   congelada do vocabulário de `normalize_sex` (já dobrado: minúsculas, sem
   acento, sem hífen/sublinhado, espaços únicos). Migração é autocontida e o
   checksum não pode mudar quando o motor mudar. `test_migration_077_plano.py`
   confere que cada alias daqui dá, em `normalize_sex`, o mesmo código, e que
   todo alias do motor está aqui.
2. **Comparação exata.** A coluna é `utf8mb4_0900_ai_ci` (ignora caixa e
   acento): lida e atualizada com `COLLATE utf8mb4_0900_bin` (binária, sem
   PAD SPACE), para 'MALE' e 'male ' não se esconderem atrás de 'male' no
   `GROUP BY` e o plano dizer exatamente o que falta. Um UPDATE por código
   canônico, com os valores encontrados no `IN`.
3. **NULL continua NULL; valor desconhecido não muda.** O `plan` registra no log
   o que sobrou fora do vocabulário (valor e nº de linhas, sem id de usuário);
   a leitura da API devolve `null` para ele (`sex_code`).
4. **Sem `down` de dados.** `plan_down`/`down` são no-op (0 statements): o
   vocabulário canônico continua válido para o código anterior (lê e grava
   `sex` como texto livre; a tela antiga mostra o campo vazio e, ao salvar sem
   escolher, o `COALESCE` preserva o valor). Voltar a 'M'/'F' só reabriria a
   divergência.
5. **Ordem do deploy.** Com `RUN_DB_MIGRATIONS=true` o deploy aplica a 077
   antes das Lambdas; no intervalo o código anterior lê `male`/`female` e a tela
   antiga mostra o campo vazio (aceitável e curto). Não é migração que remodela
   forma (nenhuma anterior confere `users.sex` por igualdade): fica fora de
   `RESHAPING_MIGRATIONS`.

--- Fluxo -----------------------------------------------------------------

`plan(db)` só LÊ (`information_schema` e os valores distintos de `users.sex`);
`up(db)` executa o plano e loga o `rowcount`.

- coluna com o tipo/nulabilidade/charset de `EXPECTED_COLUMNS`/`EXPECTED_CHARSET`
  -> um UPDATE por código canônico que tem valor a converter (0 a 3);
- reexecução (tudo canônico, NULL ou desconhecido) -> 0 statements;
- coluna ausente ou com outra forma -> `UsersSexCanonicalError` antes do
  primeiro statement;
- `users` ausente: erro numa execução real; em DRY-RUN, 0 statements.

Data API: sem PREPARE/SET/USE/multi-statement; um statement por chamada;
módulo autocontido (não importa código do app).
"""

from __future__ import annotations

import logging
import unicodedata

logger = logging.getLogger("fitness.migrate")

TABLE = "users"
COLUMN = "sex"

# coluna -> (tipo normalizado, IS_NULLABLE) - forma criada pela 001.
EXPECTED_COLUMNS = {COLUMN: ("varchar(20)", "YES")}
# `COLLATE utf8mb4_0900_bin` exige a coluna em utf8mb4 (produção: utf8mb4_0900_ai_ci).
EXPECTED_CHARSET = "utf8mb4"
BINARY_COLLATION = "utf8mb4_0900_bin"

# Vocabulário de `anthropometrics.Sex`, na ordem do enum.
CANONICAL_CODES = ("male", "female", "other")

# Cópia congelada de `anthropometrics._SEX_ALIASES` + os próprios códigos
# (que `normalize_sex` aceita por `Sex(chave)`), já dobrados por `_fold`.
SEX_ALIASES = {
    "male": "male",
    "m": "male",
    "h": "male",
    "man": "male",
    "masc": "male",
    "masculino": "male",
    "homem": "male",
    "female": "female",
    "f": "female",
    "woman": "female",
    "fem": "female",
    "feminino": "female",
    "feminina": "female",
    "mulher": "female",
    "other": "other",
    "o": "other",
    "x": "other",
    "outro": "other",
    "outra": "other",
    "outros": "other",
    "nao binario": "other",
    "nao binaria": "other",
    "non binary": "other",
    "nonbinary": "other",
    "nb": "other",
}

DISTINCT_SQL = (
    f"SELECT {COLUMN} COLLATE {BINARY_COLLATION} AS raw_value, COUNT(*) AS n FROM {TABLE} "
    f"WHERE {COLUMN} IS NOT NULL GROUP BY raw_value ORDER BY raw_value"
)


def update_sql(count: int) -> str:
    """UPDATE de um código canônico para `count` valores exatos encontrados."""
    placeholders = ", ".join(["%s"] * count)
    return f"UPDATE {TABLE} SET {COLUMN} = %s WHERE {COLUMN} COLLATE {BINARY_COLLATION} IN ({placeholders})"


class UsersSexCanonicalError(Exception):
    """Estado incompatível; nenhum statement desta execução foi aplicado."""


def _fail(message: str) -> None:
    raise UsersSexCanonicalError(f"077: {message}. Nenhum statement executado.")


def _text(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8")
    return "" if value is None else str(value)


def _normalize_type(column_type) -> str:
    return "".join(_text(column_type).split()).lower()


def _fold(raw) -> str:
    """Mesma dobra de `anthropometrics.fold_text` (cópia congelada): minúsculas,
    sem acento, hífen/sublinhado viram espaço, espaços únicos."""
    texto = unicodedata.normalize("NFKD", _text(raw))
    sem_acento = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return " ".join(sem_acento.lower().replace("-", " ").replace("_", " ").split())


def canonical_code(raw) -> str | None:
    """Código canônico do valor gravado, ou None se fora do vocabulário."""
    return SEX_ALIASES.get(_fold(raw))


def _read_column(db) -> tuple[bool, dict | None]:
    tables = db.query(
        "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (TABLE,),
    )
    exists = any(_text(row["TABLE_NAME"]) == TABLE for row in tables)
    if not exists:
        return False, None
    rows = db.query(
        "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, CHARACTER_SET_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (TABLE, COLUMN),
    )
    return True, (rows[0] if rows else None)


def _check_shape(column: dict | None) -> None:
    if column is None:
        _fail(f"{TABLE}.{COLUMN} nao existe (a 001 precisa estar aplicada)")
    expected_type, expected_nullable = EXPECTED_COLUMNS[COLUMN]
    if _normalize_type(column["COLUMN_TYPE"]) != expected_type:
        _fail(f"{TABLE}.{COLUMN} e {_text(column['COLUMN_TYPE'])}, esperado {expected_type}")
    if _text(column["IS_NULLABLE"]) != expected_nullable:
        _fail(f"{TABLE}.{COLUMN} tem IS_NULLABLE={_text(column['IS_NULLABLE'])}, esperado {expected_nullable}")
    if _text(column.get("CHARACTER_SET_NAME")).lower() != EXPECTED_CHARSET:
        _fail(
            f"{TABLE}.{COLUMN} usa CHARACTER SET {_text(column.get('CHARACTER_SET_NAME'))}, "
            f"esperado {EXPECTED_CHARSET} (o UPDATE compara com COLLATE {BINARY_COLLATION})"
        )


def _distinct_values(db) -> list[tuple[str, int]]:
    """(valor exato, nº de linhas) de `users.sex` não nulo."""
    resultado = []
    for row in db.query(DISTINCT_SQL):
        n = row["n"] if isinstance(row, dict) else row[1]
        raw = row["raw_value"] if isinstance(row, dict) else row[0]
        resultado.append((_text(raw), int(n or 0)))
    return resultado


def plan(db) -> list[tuple[str, tuple | None]]:
    """Somente leitura. Devolve [(sql, params)] a executar, na ordem de `CANONICAL_CODES`."""
    exists, column = _read_column(db)
    if not exists:
        if bool(getattr(db, "dry_run", False)):
            return []
        _fail(f"tabela {TABLE} nao existe (a 001 precisa estar aplicada)")
    _check_shape(column)

    a_converter: dict[str, list[str]] = {code: [] for code in CANONICAL_CODES}
    desconhecidos: list[tuple[str, int]] = []
    for raw, n in _distinct_values(db):
        code = canonical_code(raw)
        if code is None:
            desconhecidos.append((raw, n))
        elif raw != code:
            a_converter[code].append(raw)

    if desconhecidos:
        logger.warning(
            "077: %d valor(es) de %s.%s fora do vocabulario ficam como estao (a API devolve null): %s",
            len(desconhecidos),
            TABLE,
            COLUMN,
            ", ".join(f"{raw[:20]!r} ({n} linha(s))" for raw, n in desconhecidos),
        )
    return [(update_sql(len(raws)), (code, *raws)) for code, raws in a_converter.items() if raws]


def plan_down(db) -> list[tuple[str, tuple | None]]:
    """NO-OP deliberado: o vocabulário canônico continua válido para o código
    anterior (ver Decisão 4 na docstring do módulo)."""
    return []


def up(db):
    for sql, params in plan(db):
        afetadas = db.execute(sql, params)
        logger.info("077: %s linha(s) de %s.%s convertidas para '%s'.", afetadas, TABLE, COLUMN, params[0])


def down(db):
    # Intencionalmente vazio: ver `plan_down`.
    for sql, params in plan_down(db):
        db.execute(sql, params)
