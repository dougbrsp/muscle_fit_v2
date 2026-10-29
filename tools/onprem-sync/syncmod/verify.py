"""Verificacao pos-sync: contagem, checksum de conteudo, FK orfa e encoding.

Nada aqui confia no texto exibido no terminal. A prova de encoding e feita com
`OCTET_LENGTH`/`CHAR_LENGTH`/`HEX` comparados entre os dois bancos, porque
double-encoding ("Estacao" -> "EstaAsAo") mantem a aparencia plausivel na tela
mas muda a contagem de bytes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .aurora import AuroraClient
from .config import EncodingProbe
from .onprem import OnpremReader
from .schema import ForeignKey, Schema, Table, normalize_row


Row = dict[str, str | None]


@dataclass
class TableVerification:
    table: str
    onprem_count: int
    aurora_count: int
    onprem_checksum: str
    aurora_checksum: str

    @property
    def counts_match(self) -> bool:
        return self.onprem_count == self.aurora_count

    @property
    def content_match(self) -> bool:
        return self.onprem_checksum == self.aurora_checksum

    @property
    def status(self) -> str:
        return "OK" if self.counts_match and self.content_match else "DIVERGENTE"


@dataclass
class OrphanCheck:
    constraint: str
    child: str
    parent: str
    columns: str
    orphans: int

    @property
    def status(self) -> str:
        return "OK" if self.orphans == 0 else "QUEBRADO"


@dataclass
class EncodingCheck:
    label: str
    onprem: dict[str, object]
    aurora: dict[str, object]

    @property
    def status(self) -> str:
        return "OK" if self.onprem == self.aurora else "DIVERGENTE"


@dataclass
class VerificationReport:
    tables: list[TableVerification] = field(default_factory=list)
    orphans: list[OrphanCheck] = field(default_factory=list)
    encoding: list[EncodingCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            all(t.status == "OK" for t in self.tables)
            and all(o.status == "OK" for o in self.orphans)
            and all(e.status == "OK" for e in self.encoding)
        )


def table_checksum(table: Table, rows: list[Row]) -> str:
    """SHA-256 estavel sobre o conteudo canonico da tabela.

    Calculado em Python (e nao com CHECKSUM TABLE) de proposito: CHECKSUM
    TABLE depende do formato fisico do storage engine e nao e comparavel entre
    MySQL 8.4 onprem e Aurora MySQL 8.0. O hash aqui e sobre o mesmo texto
    canonico que alimenta o diff, entao "checksum igual" significa exatamente
    "o diff da proxima execucao sera vazio".
    """
    digest = hashlib.sha256()
    normalized = [normalize_row(table, row) for row in rows]
    normalized.sort(key=lambda r: tuple("" if v is None else v for v in table.pk_of(r)))
    for row in normalized:
        digest.update(
            json.dumps(
                [row.get(c) for c in table.column_names],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\x1e")
    return digest.hexdigest()


def verify_tables(
    schema: Schema,
    onprem_rows: dict[str, list[Row]],
    aurora_rows: dict[str, list[Row]],
) -> list[TableVerification]:
    out: list[TableVerification] = []
    for table_name in sorted(onprem_rows):
        table = schema[table_name]
        src = onprem_rows[table_name]
        dst = aurora_rows.get(table_name, [])
        out.append(
            TableVerification(
                table=table_name,
                onprem_count=len(src),
                aurora_count=len(dst),
                onprem_checksum=table_checksum(table, src),
                aurora_checksum=table_checksum(table, dst),
            )
        )
    return out


def check_referential_integrity(
    client: AuroraClient,
    schema: Schema,
    tables: set[str],
) -> list[OrphanCheck]:
    """Procura FK orfa em cada relacao do schema, dentro do Aurora.

    O join e sempre por coluna de id (numerica), o que o torna imune a
    divergencia de collation das tabelas `intervals_*` ("Illegal mix of
    collations").
    """
    checks: list[OrphanCheck] = []
    for table_name in sorted(tables):
        table = schema[table_name]
        for fk in table.foreign_keys:
            if fk.ref_table not in schema:
                continue
            on_clause = " AND ".join(
                f"c.`{col}` = p.`{ref}`" for col, ref in zip(fk.columns, fk.ref_columns)
            )
            not_null = " AND ".join(f"c.`{col}` IS NOT NULL" for col in fk.columns)
            missing = " AND ".join(f"p.`{ref}` IS NULL" for ref in fk.ref_columns)
            sql = (
                f"SELECT CAST(COUNT(*) AS CHAR) AS n FROM `{table_name}` c "
                f"LEFT JOIN `{fk.ref_table}` p ON {on_clause} "
                f"WHERE {not_null} AND {missing}"
            )
            rows = client.fetch_all(sql)
            checks.append(
                OrphanCheck(
                    constraint=fk.name,
                    child=table_name,
                    parent=fk.ref_table,
                    columns=",".join(fk.columns),
                    orphans=int(rows[0]["n"] or 0) if rows else 0,
                )
            )
    return checks


def check_encoding(
    reader: OnpremReader,
    client: AuroraClient,
    schema: Schema,
    tables: set[str],
    probes: list[EncodingProbe],
) -> list[EncodingCheck]:
    checks: list[EncodingCheck] = []

    # 1. Agregado por tabela: soma de bytes e de caracteres de toda coluna
    #    textual. Detecta corrupcao em QUALQUER linha, nao so nas sondas.
    for table_name in sorted(tables):
        table = schema[table_name]
        src = reader.text_byte_stats(table)
        if not src:
            continue
        dst = client.text_byte_stats(table)
        for column, stats in sorted(src.items()):
            checks.append(
                EncodingCheck(
                    label=f"{table_name}.{column} (soma bytes/chars)",
                    onprem=dict(stats),
                    aurora=dict(dst.get(column, {})),
                )
            )

    # 2. Sondas nomeadas, comparando HEX byte a byte.
    for probe in probes:
        if probe.table not in schema or probe.table not in tables:
            continue
        table = schema[probe.table]
        src_probe = reader.probe_hex(table, probe.pk, probe.column)
        dst_probe = client.probe_hex(table, probe.pk, probe.column)
        checks.append(
            EncodingCheck(
                label=f"sonda {probe.table}.{probe.column} pk={probe.pk} ({probe.note})",
                onprem=dict(src_probe or {}),
                aurora=dict(dst_probe or {}),
            )
        )

    return checks
