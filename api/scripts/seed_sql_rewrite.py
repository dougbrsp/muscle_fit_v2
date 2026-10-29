"""Reescrita PURA (sem banco) de statements do dump de producao para o schema local.

Usado por `api/scripts/bootstrap_local_db.py` (seed opcional do MySQL local).

Problema: o dump em `tools/onprem-sync/backups/<timestamp>/restore.sql` e
gerado a partir da Aurora de producao e insere com LISTA EXPLICITA de colunas
(`INSERT INTO `t` (`a`, `b`) VALUES (...)`). Quando o schema local diverge
(coluna so existe na Aurora, ou foi removida localmente por um guard
`_ensure_schema()`), o MySQL rejeita o statement inteiro com "Unknown column"
e a linha nunca e importada - deixando tabelas-pai vazias e filhas orfas.

Solucao: antes de executar, descartar do statement as colunas que nao existem
no schema alvo, junto com os valores correspondentes em TODAS as tuplas.

Nada aqui loga ou devolve conteudo de linha: so nomes de tabela/coluna.
Modulo sem dependencias externas (so stdlib), testado em
`api/tests/unit/test_seed_sql_rewrite.py`.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass

# Tokenizer SQL no nivel lexico que interessa aqui. Ordem das alternativas
# importa. Quantificadores possessivos (*+, ++) evitam backtracking
# catastrofico em strings longas (JSON inteiro dentro de um literal) e fazem
# uma string sem aspa de fechamento simplesmente NAO casar (tratado como erro).
#   - strings '...' e "...": escapes com barra (\', \\, \n...) e aspa dobrada ('');
#   - identificadores `...` (crase dobrada escapa crase);
#   - comentarios `-- ` (exige espaco/fim de linha, como no MySQL), `#` e /* */;
#     /*! ... */ (comentario executavel do MySQL) NAO e tratado como comentario;
#   - pontuacao estrutural ( ) , ;
#   - qualquer outro trecho (palavras-chave, NULL, numeros, 0x.., _binary, X, ...).
_TOKEN_RE = re.compile(
    r"""
      (?P<sq>'(?:[^'\\]++|\\.|'')*+')
    | (?P<dq>"(?:[^"\\]++|\\.|"")*+")
    | (?P<bq>`(?:[^`]++|``)*+`)
    | (?P<comment>--(?=[ \t\r\n]|$)[^\n]*+|\#[^\n]*+|/\*(?!!).*?\*/)
    | (?P<punct>[(),;])
    | (?P<other>[^'"`(),;\-#/]++|[-/])
    """,
    re.VERBOSE | re.DOTALL,
)

_INSERT_HEADER_RE = re.compile(
    r"""
    \A\s*INSERT\s+(?P<ignore>IGNORE\s+)?INTO\s+
    (?:(?:`(?:[^`]|``)+`|\w+)\s*\.\s*)?          # schema opcional (ignorado)
    (?P<table>`(?:[^`]|``)+`|\w+)\s*
    \((?P<columns>[^)]*)\)\s*
    VALUES\s*
    """,
    re.VERBOSE | re.IGNORECASE,
)

_IDENTIFIER_RE = re.compile(r"\s*(`(?:[^`]|``)+`|\w+)\s*")


class SqlTokenizeError(ValueError):
    """Texto SQL que o tokenizer nao consegue interpretar (ex.: string sem
    aspa de fechamento). A mensagem cita so a posicao, nunca o conteudo."""


def _tokens(sql: str, start: int = 0):
    """Gera (tipo, texto, inicio, fim) a partir de `start`."""
    pos = start
    length = len(sql)
    while pos < length:
        match = _TOKEN_RE.match(sql, pos)
        if match is None:
            raise SqlTokenizeError(f"Token SQL invalido/nao terminado na posicao {pos}")
        yield match.lastgroup, match.group(), pos, match.end()
        pos = match.end()


def split_statements(sql: str) -> list[str]:
    """Quebra um script SQL em statements pelo `;` de nivel superior.

    Ao contrario de `split(";\\n")`, respeita `;` e quebras de linha dentro de
    strings/identificadores. Comentarios (fora de strings) sao removidos e
    statements vazios descartados. Nao suporta `DELIMITER` (diretiva do
    cliente `mysql`, que o dump nao usa)."""
    statements: list[str] = []
    parts: list[str] = []
    for kind, text, _start, _end in _tokens(sql):
        if kind == "comment":
            parts.append(" ")
        elif kind == "punct" and text == ";":
            statement = "".join(parts).strip()
            if statement:
                statements.append(statement)
            parts = []
        else:
            parts.append(text)
    tail = "".join(parts).strip()
    if tail:
        statements.append(tail)
    return statements


def _unquote_identifier(identifier: str) -> str:
    identifier = identifier.strip()
    if identifier.startswith("`") and identifier.endswith("`"):
        return identifier[1:-1].replace("``", "`")
    return identifier


def _quote_identifier(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


@dataclass(frozen=True)
class InsertStatement:
    """`INSERT [IGNORE] INTO tabela (colunas) VALUES (tupla)[, (tupla)...]`.
    Cada valor e mantido como texto SQL cru (ex.: `'a,b'`, `NULL`, `0x1F`)."""

    table: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    ignore: bool = False


def insert_table_name(statement: str) -> str | None:
    """Nome da tabela de um INSERT com lista de colunas (so o cabecalho), ou None."""
    match = _INSERT_HEADER_RE.match(statement)
    return _unquote_identifier(match.group("table")) if match else None


def _parse_columns(raw: str) -> tuple[str, ...] | None:
    columns: list[str] = []
    for chunk in raw.split(","):
        match = _IDENTIFIER_RE.fullmatch(chunk)
        if match is None:
            return None
        columns.append(_unquote_identifier(match.group(1)))
    return tuple(columns)


def parse_insert(statement: str) -> InsertStatement | None:
    """Interpreta um INSERT multi-tupla com lista explicita de colunas.

    Devolve None para qualquer coisa fora desse formato (INSERT sem lista de
    colunas, INSERT ... SELECT, ON DUPLICATE KEY UPDATE, outros statements):
    o chamador executa esses statements como estao. Levanta ValueError se o
    numero de valores de alguma tupla nao bate com o numero de colunas."""
    header = _INSERT_HEADER_RE.match(statement)
    if header is None:
        return None
    columns = _parse_columns(header.group("columns"))
    if not columns:
        return None

    rows: list[tuple[str, ...]] = []
    depth = 0
    values: list[str] = []
    current: list[str] = []
    expecting_row = True  # antes da 1a tupla ou logo apos a virgula entre tuplas

    for kind, text, _start, _end in _tokens(statement, header.end()):
        if depth == 0:
            if kind == "comment" or (kind == "other" and not text.strip()):
                continue
            if kind == "punct" and text == "(" and expecting_row:
                depth = 1
                values, current = [], []
                expecting_row = False
                continue
            if kind == "punct" and text == "," and not expecting_row and rows:
                expecting_row = True
                continue
            return None  # sufixo nao suportado (ex.: ON DUPLICATE KEY UPDATE)

        if kind == "punct" and text == "(":
            depth += 1
        elif kind == "punct" and text == ")":
            depth -= 1
            if depth == 0:
                values.append("".join(current).strip())
                if len(values) != len(columns):
                    raise ValueError(
                        f"INSERT em {header.group('table')}: tupla {len(rows) + 1} "
                        f"tem {len(values)} valores para {len(columns)} colunas"
                    )
                rows.append(tuple(values))
                continue
        elif kind == "punct" and text == "," and depth == 1:
            values.append("".join(current).strip())
            current = []
            continue
        current.append(text)

    if depth != 0 or expecting_row or not rows:
        return None  # tupla nao fechada / VALUES vazio / virgula sobrando
    return InsertStatement(
        table=_unquote_identifier(header.group("table")),
        columns=columns,
        rows=tuple(rows),
        ignore=bool(header.group("ignore")),
    )


def render_insert(insert: InsertStatement) -> str:
    keyword = "INSERT IGNORE INTO" if insert.ignore else "INSERT INTO"
    columns = ", ".join(_quote_identifier(c) for c in insert.columns)
    rows = ", ".join("(" + ", ".join(row) + ")" for row in insert.rows)
    return f"{keyword} {_quote_identifier(insert.table)} ({columns}) VALUES {rows}"


@dataclass(frozen=True)
class RewriteResult:
    """Resultado de `rewrite_insert_for_schema`.

    - `statement`: SQL a executar, ou None quando nao ha nada a executar
      (tabela inexistente, ou nenhuma coluna do INSERT existe localmente);
    - `table`: tabela do INSERT (None para statements que nao sao INSERT);
    - `dropped_columns`: colunas do dump descartadas por nao existirem local;
    - `table_missing`: a tabela nao existe no schema alvo."""

    statement: str | None
    table: str | None = None
    dropped_columns: tuple[str, ...] = ()
    table_missing: bool = False


def rewrite_insert_for_schema(
    statement: str,
    table_columns: Mapping[str, Collection[str]],
    *,
    insert_ignore: bool = False,
) -> RewriteResult:
    """Adapta um INSERT do dump ao schema alvo.

    `table_columns` mapeia nome de tabela -> colunas existentes, AMBOS em
    minusculas (nomes de coluna no MySQL sao case-insensitive; tabelas sao
    comparadas em minusculas por simplicidade - o schema local nao tem nomes
    que so diferem por caixa).

    - statement que nao e INSERT com lista de colunas: devolvido inalterado;
    - tabela ausente em `table_columns`: statement None, `table_missing=True`;
    - colunas inexistentes: removidas junto com os valores de todas as tuplas;
    - nada a descartar e `insert_ignore=False`: devolve o texto ORIGINAL
      (byte a byte) - zero risco de regressao para as tabelas sem drift;
    - `insert_ignore=True`: INSERT vira INSERT IGNORE."""
    table = insert_table_name(statement)
    if table is None:
        return RewriteResult(statement=statement)

    existing = table_columns.get(table.lower())
    if existing is None:
        return RewriteResult(statement=None, table=table, table_missing=True)

    insert = parse_insert(statement)
    if insert is None:
        return RewriteResult(statement=statement, table=table)

    keep = [i for i, column in enumerate(insert.columns) if column.lower() in existing]
    dropped = tuple(c for c in insert.columns if c.lower() not in existing)

    if not keep:
        return RewriteResult(statement=None, table=insert.table, dropped_columns=dropped)
    if not dropped and (not insert_ignore or insert.ignore):
        return RewriteResult(statement=statement, table=insert.table)

    rewritten = InsertStatement(
        table=insert.table,
        columns=tuple(insert.columns[i] for i in keep),
        rows=tuple(tuple(row[i] for i in keep) for row in insert.rows),
        ignore=insert.ignore or insert_ignore,
    )
    return RewriteResult(
        statement=render_insert(rewritten), table=insert.table, dropped_columns=dropped
    )
