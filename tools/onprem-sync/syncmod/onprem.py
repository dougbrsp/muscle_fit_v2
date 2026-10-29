"""Leitura SOMENTE-LEITURA do MySQL onpremise via `docker exec`.

Regras inegociaveis implementadas aqui:

- O repositorio e o banco onpremise sao PRODUCAO e nunca podem ser alterados
  por esta ferramenta. `_run_sql` recusa qualquer statement que nao seja
  SELECT/SET/SHOW/USE (ver `_assert_read_only`). Isso e um cinto de seguranca
  contra alguem evoluir o codigo e acidentalmente escrever na origem.
- NUNCA usar `docker compose` - isso recriaria os containers de producao. Só
  `docker exec` no container ja em execucao.
- O cliente mysql roda com `--default-character-set=utf8mb4` e a saida e
  decodificada explicitamente como UTF-8, para nao depender do code page do
  console do Windows (origem historica de double-encoding neste projeto).
"""

from __future__ import annotations

import json
import shlex
import subprocess
from typing import Any, Iterable

from .config import OnpremConfig
from .schema import Schema, Table, build_schema


_ALLOWED_PREFIXES = ("select", "set", "show", "use", "--")


class OnpremError(RuntimeError):
    pass


def _assert_read_only(sql: str) -> None:
    for raw_stmt in sql.split(";"):
        stmt = raw_stmt.strip()
        if not stmt:
            continue
        head = stmt.split(None, 1)[0].lower()
        if not any(head.startswith(p) for p in _ALLOWED_PREFIXES):
            raise OnpremError(
                "Bloqueado: o onpremise e somente-leitura para esta ferramenta. "
                f"Statement recusado: {stmt[:120]!r}"
            )


class OnpremReader:
    def __init__(self, cfg: OnpremConfig, time_zone: str = "+00:00") -> None:
        self.cfg = cfg
        self.time_zone = time_zone

    # -- infraestrutura ----------------------------------------------------

    def _mysql_command(self) -> list[str]:
        inner = (
            f"exec mysql --default-character-set=utf8mb4 --raw -N -B "
            f"-u{shlex.quote(self.cfg.mysql_user)} "
            f'-p"${self.cfg.password_env}" {shlex.quote(self.cfg.database)}'
        )
        return ["docker", "exec", "-i", self.cfg.container, "sh", "-c", inner]

    def _run_sql(self, sql: str) -> list[str]:
        _assert_read_only(sql)
        prelude = f"SET SESSION time_zone = '{self.time_zone}';\n"
        payload = (prelude + sql).encode("utf-8")

        try:
            proc = subprocess.run(
                self._mysql_command(),
                input=payload,
                capture_output=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise OnpremError("`docker` nao encontrado no PATH.") from exc

        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace")
            raise OnpremError(
                f"mysql retornou {proc.returncode} no container {self.cfg.container}: {stderr.strip()}"
            )

        stdout = proc.stdout.decode("utf-8")
        return [line for line in stdout.split("\n") if line.strip()]

    def _run_json_rows(self, sql: str) -> list[dict[str, Any]]:
        """Executa um SELECT que devolve UMA coluna JSON por linha.

        JSON_OBJECT garante uma linha de saida por registro mesmo quando o
        conteudo tem quebra de linha ou TAB (ambos viram escape dentro da
        string JSON), o que torna o parsing robusto - ao contrario do formato
        tabular cru do cliente mysql.
        """
        return [json.loads(line) for line in self._run_sql(sql)]

    def ping(self) -> str:
        rows = self._run_sql("SELECT CONCAT(VERSION(), '|', @@SESSION.time_zone, '|', DATABASE());")
        if not rows:
            raise OnpremError("Sem resposta do MySQL onpremise.")
        return rows[0]

    # -- introspeccao ------------------------------------------------------

    def load_schema(self) -> Schema:
        db = self.cfg.database.replace("'", "''")

        table_rows = self._run_json_rows(
            "SELECT JSON_OBJECT("
            "'table_name', TABLE_NAME,"
            "'table_collation', TABLE_COLLATION,"
            "'auto_increment', CAST(AUTO_INCREMENT AS CHAR)"
            ") FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = '{db}' AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME;"
        )

        column_rows = self._run_json_rows(
            "SELECT JSON_OBJECT("
            "'table_name', TABLE_NAME,"
            "'column_name', COLUMN_NAME,"
            "'data_type', DATA_TYPE,"
            "'is_nullable', IS_NULLABLE,"
            "'extra', EXTRA,"
            "'ordinal_position', CAST(ORDINAL_POSITION AS CHAR)"
            ") FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA = '{db}' ORDER BY TABLE_NAME, ORDINAL_POSITION;"
        )

        pk_rows = self._run_json_rows(
            "SELECT JSON_OBJECT("
            "'table_name', TABLE_NAME,"
            "'column_name', COLUMN_NAME,"
            "'seq_in_index', CAST(SEQ_IN_INDEX AS CHAR)"
            ") FROM information_schema.STATISTICS "
            f"WHERE TABLE_SCHEMA = '{db}' AND INDEX_NAME = 'PRIMARY' ORDER BY TABLE_NAME, SEQ_IN_INDEX;"
        )

        fk_rows = self._run_json_rows(
            "SELECT JSON_OBJECT("
            "'table_name', TABLE_NAME,"
            "'constraint_name', CONSTRAINT_NAME,"
            "'column_name', COLUMN_NAME,"
            "'ordinal_position', CAST(ORDINAL_POSITION AS CHAR),"
            "'referenced_table_name', REFERENCED_TABLE_NAME,"
            "'referenced_column_name', REFERENCED_COLUMN_NAME"
            ") FROM information_schema.KEY_COLUMN_USAGE "
            f"WHERE TABLE_SCHEMA = '{db}' AND REFERENCED_TABLE_NAME IS NOT NULL "
            "ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION;"
        )

        return build_schema(table_rows, column_rows, pk_rows, fk_rows)

    # -- dados -------------------------------------------------------------

    def fetch_rows(self, table: Table) -> list[dict[str, str | None]]:
        """Le a tabela inteira ja na forma canonica (todo valor como texto)."""
        pairs = ", ".join(
            f"'{c.name}', {c.select_expr()}" for c in table.columns
        )
        # Sem ORDER BY de proposito: a ordenacao nao e usada (as linhas sao
        # indexadas por PK em Python) e ordenar uma lista de selecao cheia de
        # CAST forca filesort carregando colunas de centenas de KB no sort
        # buffer - vide "Out of sort memory" em `reports`.
        sql = f"SELECT JSON_OBJECT({pairs}) FROM `{table.name}`;"
        rows = self._run_json_rows(sql)
        # JSON_OBJECT devolve numeros como numero quando a expressao e
        # numerica; como todas as expressoes sao CHAR/texto, o que chega e
        # str ou None. A normalizacao abaixo e defensiva.
        return [
            {k: (v if v is None else str(v)) for k, v in row.items()}
            for row in rows
        ]

    def count_rows(self, tables: Iterable[str]) -> dict[str, int]:
        tables = list(tables)
        if not tables:
            return {}
        union = " UNION ALL ".join(
            f"SELECT '{t}' AS t, COUNT(*) AS n FROM `{t}`" for t in tables
        )
        rows = self._run_json_rows(
            f"SELECT JSON_OBJECT('t', t, 'n', CAST(n AS CHAR)) FROM ({union}) x;"
        )
        return {r["t"]: int(r["n"]) for r in rows}

    def text_byte_stats(self, table: Table) -> dict[str, dict[str, int]]:
        """Soma de OCTET_LENGTH e CHAR_LENGTH por coluna textual.

        Prova de encoding: se o sync introduzisse double-encoding, o numero de
        BYTES cresceria enquanto o numero de CARACTERES ficaria igual (ou os
        dois mudariam de forma incoerente). Comparar os dois agregados entre
        onprem e Aurora detecta corrupcao em qualquer linha da tabela, nao so
        na linha de sonda.
        """
        text_cols = [c for c in table.columns if c.is_string or c.is_json]
        if not text_cols:
            return {}
        pairs = ", ".join(
            f"'{c.name}_bytes', CAST(IFNULL(SUM(OCTET_LENGTH(`{c.name}`)), 0) AS CHAR), "
            f"'{c.name}_chars', CAST(IFNULL(SUM(CHAR_LENGTH(`{c.name}`)), 0) AS CHAR)"
            for c in text_cols
        )
        rows = self._run_json_rows(f"SELECT JSON_OBJECT({pairs}) FROM `{table.name}`;")
        flat = rows[0] if rows else {}
        return {
            c.name: {
                "bytes": int(flat.get(f"{c.name}_bytes", 0) or 0),
                "chars": int(flat.get(f"{c.name}_chars", 0) or 0),
            }
            for c in text_cols
        }

    def probe_hex(self, table: Table, pk: dict[str, str], column: str) -> dict[str, Any] | None:
        where = " AND ".join(
            f"`{k}` = '{str(v).replace(chr(39), chr(39) * 2)}'" for k, v in pk.items()
        )
        rows = self._run_json_rows(
            "SELECT JSON_OBJECT("
            f"'hex', HEX(`{column}`),"
            f"'bytes', CAST(OCTET_LENGTH(`{column}`) AS CHAR),"
            f"'chars', CAST(CHAR_LENGTH(`{column}`) AS CHAR)"
            f") FROM `{table.name}` WHERE {where} LIMIT 1;"
        )
        return rows[0] if rows else None
