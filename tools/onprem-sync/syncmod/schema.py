"""Modelo de schema, ordem topologica de FK e forma canonica de valor.

Tudo aqui e puro: recebe linhas cruas de `information_schema` (lidas do
onpremise, que e a fonte de verdade tambem do formato) e devolve estrutura.

## Forma canonica

O calo desta ferramenta e comparar duas linhas vindas de dois motores
diferentes (MySQL 8.4 onprem x Aurora MySQL 8.0) sem falsos positivos. A
solucao adotada: os DOIS lados sao lidos com a MESMA lista de expressoes SQL,
que converte todo valor para texto (`CAST(col AS CHAR)`), e a comparacao e
feita em Python sobre strings. Isso elimina de uma vez:

- diferenca de renderizacao de DECIMAL (10.5 x 10.50);
- diferenca de tipagem devolvida pelo driver (Data API x cliente mysql);
- diferenca de escala/fracao de segundo em DATETIME/TIMESTAMP.

Duas armadilhas sobraram e sao tratadas explicitamente:

1. TIMESTAMP e convertido para o `time_zone` da SESSAO na leitura. Sem fixar
   o mesmo time_zone nos dois lados, o mesmo instante vira string diferente e
   toda coluna TIMESTAMP aparece como divergencia. Ver `session.time_zone`.
2. JSON e re-canonicalizado pelo servidor ao gravar, e a forma canonica pode
   variar entre versoes de engine. Por isso colunas JSON passam por
   `normalize_value`, que faz parse + serializacao com chaves ordenadas em
   Python antes de comparar. Sem isso, a segunda execucao do sync continuaria
   vendo UPDATEs (quebra do criterio de idempotencia).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable


INTEGER_TYPES = {"tinyint", "smallint", "mediumint", "int", "integer", "bigint"}
STRING_TYPES = {"char", "varchar", "text", "tinytext", "mediumtext", "longtext", "enum", "set"}
TEMPORAL_TYPES = {"date", "datetime", "timestamp", "time", "year"}


@dataclass(frozen=True)
class Column:
    name: str
    data_type: str
    is_nullable: bool
    extra: str
    ordinal: int

    @property
    def is_auto_increment(self) -> bool:
        return "auto_increment" in self.extra.lower()

    @property
    def is_integer(self) -> bool:
        return self.data_type in INTEGER_TYPES

    @property
    def is_string(self) -> bool:
        return self.data_type in STRING_TYPES

    @property
    def is_json(self) -> bool:
        return self.data_type == "json"

    def select_expr(self, alias: str | None = None) -> str:
        """Expressao SQL que le a coluna ja na forma canonica (texto)."""
        ref = f"`{alias}`.`{self.name}`" if alias else f"`{self.name}`"
        if self.is_string:
            # Ja e texto; CAST seria no-op mas forcaria conversao de collation
            # a toa nas 3 tabelas utf8mb4_unicode_ci.
            return ref
        return f"CAST({ref} AS CHAR)"

    def data_api_param(self, name: str, value: str | None) -> dict[str, Any]:
        """Converte o valor canonico (texto) de volta para parametro do Data API."""
        if value is None:
            return {"name": name, "value": {"isNull": True}}
        if self.is_integer:
            # tinyint(1) tambem chega como '0'/'1'; longValue serve aos dois.
            return {"name": name, "value": {"longValue": int(value)}}
        if self.data_type == "decimal":
            return {"name": name, "value": {"stringValue": value}, "typeHint": "DECIMAL"}
        if self.data_type in ("datetime", "timestamp"):
            return {"name": name, "value": {"stringValue": value}, "typeHint": "TIMESTAMP"}
        if self.data_type == "date":
            return {"name": name, "value": {"stringValue": value}, "typeHint": "DATE"}
        if self.data_type == "time":
            return {"name": name, "value": {"stringValue": value}, "typeHint": "TIME"}
        if self.is_json:
            return {"name": name, "value": {"stringValue": value}, "typeHint": "JSON"}
        return {"name": name, "value": {"stringValue": value}}


@dataclass(frozen=True)
class ForeignKey:
    name: str
    table: str
    columns: tuple[str, ...]
    ref_table: str
    ref_columns: tuple[str, ...]

    @property
    def is_self_reference(self) -> bool:
        return self.table == self.ref_table


@dataclass
class Table:
    name: str
    collation: str | None
    auto_increment: int | None
    columns: list[Column] = field(default_factory=list)
    pk_columns: tuple[str, ...] = ()
    foreign_keys: list[ForeignKey] = field(default_factory=list)

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> Column:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(f"Coluna {self.name}.{name} nao existe no schema")

    @property
    def has_pk(self) -> bool:
        return bool(self.pk_columns)

    @property
    def auto_increment_column(self) -> Column | None:
        for c in self.columns:
            if c.is_auto_increment:
                return c
        return None

    def select_list(self) -> str:
        return ", ".join(f"{c.select_expr()} AS `{c.name}`" for c in self.columns)

    def order_by(self) -> str:
        return ", ".join(f"`{c}`" for c in self.pk_columns)

    def pk_of(self, row: dict[str, str | None]) -> tuple[str | None, ...]:
        return tuple(row[c] for c in self.pk_columns)


@dataclass
class Schema:
    tables: dict[str, Table]

    def __getitem__(self, name: str) -> Table:
        return self.tables[name]

    def __contains__(self, name: str) -> bool:
        return name in self.tables

    @property
    def names(self) -> list[str]:
        return sorted(self.tables)

    def all_foreign_keys(self) -> list[ForeignKey]:
        out: list[ForeignKey] = []
        for t in self.tables.values():
            out.extend(t.foreign_keys)
        return out


# ---------------------------------------------------------------------------
# Construcao a partir de information_schema
# ---------------------------------------------------------------------------

def build_schema(
    table_rows: Iterable[dict[str, str]],
    column_rows: Iterable[dict[str, str]],
    pk_rows: Iterable[dict[str, str]],
    fk_rows: Iterable[dict[str, str]],
) -> Schema:
    tables: dict[str, Table] = {}

    for row in table_rows:
        auto = row.get("auto_increment")
        tables[row["table_name"]] = Table(
            name=row["table_name"],
            collation=row.get("table_collation") or None,
            auto_increment=int(auto) if auto not in (None, "", "NULL") else None,
        )

    for row in sorted(column_rows, key=lambda r: (r["table_name"], int(r["ordinal_position"]))):
        table = tables.get(row["table_name"])
        if table is None:
            continue  # view ou tabela fora do recorte de BASE TABLE
        table.columns.append(
            Column(
                name=row["column_name"],
                data_type=row["data_type"].lower(),
                is_nullable=row["is_nullable"].upper() == "YES",
                extra=row.get("extra") or "",
                ordinal=int(row["ordinal_position"]),
            )
        )

    pk_acc: dict[str, list[tuple[int, str]]] = {}
    for row in pk_rows:
        pk_acc.setdefault(row["table_name"], []).append((int(row["seq_in_index"]), row["column_name"]))
    for table_name, cols in pk_acc.items():
        if table_name in tables:
            tables[table_name].pk_columns = tuple(c for _, c in sorted(cols))

    fk_acc: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in fk_rows:
        fk_acc.setdefault((row["table_name"], row["constraint_name"]), []).append(row)
    for (table_name, constraint_name), rows in fk_acc.items():
        if table_name not in tables:
            continue
        rows.sort(key=lambda r: int(r["ordinal_position"]))
        tables[table_name].foreign_keys.append(
            ForeignKey(
                name=constraint_name,
                table=table_name,
                columns=tuple(r["column_name"] for r in rows),
                ref_table=rows[0]["referenced_table_name"],
                ref_columns=tuple(r["referenced_column_name"] for r in rows),
            )
        )

    return Schema(tables=tables)


# ---------------------------------------------------------------------------
# Ordem topologica
# ---------------------------------------------------------------------------

class TopologyError(RuntimeError):
    pass


def topological_order(schema: Schema, tables: Iterable[str]) -> list[str]:
    """Ordem DIRETA: pais antes de filhos (ordem segura de INSERT).

    Derivada do grafo de FK real, nao de lista fixa - se o schema evoluir, a
    ordem acompanha. Auto-referencia e ignorada como aresta (uma tabela nao
    pode esperar por si mesma); FK apontando para tabela fora do recorte
    tambem e ignorada, porque a dependencia nao participa deste sync.
    """
    scope = set(tables)
    deps: dict[str, set[str]] = {t: set() for t in scope}

    for table_name in scope:
        for fk in schema[table_name].foreign_keys:
            if fk.is_self_reference:
                continue
            if fk.ref_table in scope:
                deps[table_name].add(fk.ref_table)

    ordered: list[str] = []
    remaining = dict(deps)
    while remaining:
        # Desempate alfabetico deixa a ordem estavel entre execucoes.
        ready = sorted(t for t, d in remaining.items() if not d)
        if not ready:
            raise TopologyError(
                "Ciclo de foreign keys detectado entre: " + ", ".join(sorted(remaining))
            )
        for t in ready:
            ordered.append(t)
            del remaining[t]
        for d in remaining.values():
            d.difference_update(ready)

    return ordered


def reverse_topological_order(schema: Schema, tables: Iterable[str]) -> list[str]:
    """Ordem INVERSA: filhos antes de pais (ordem segura de DELETE)."""
    return list(reversed(topological_order(schema, tables)))


# ---------------------------------------------------------------------------
# Normalizacao de valor para comparacao
# ---------------------------------------------------------------------------

def normalize_value(column: Column, value: str | None) -> str | None:
    if value is None:
        return None
    if column.is_json:
        try:
            return json.dumps(json.loads(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except (ValueError, TypeError):
            # JSON invalido armazenado como texto: compara literalmente.
            return value
    return value


def normalize_row(table: Table, row: dict[str, str | None]) -> dict[str, str | None]:
    return {c.name: normalize_value(c, row.get(c.name)) for c in table.columns}
