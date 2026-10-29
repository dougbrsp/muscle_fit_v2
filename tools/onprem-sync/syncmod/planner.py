"""Calculo do plano de mudancas (diff onprem -> Aurora).

Semantica de espelho exato, conforme decidido pelo usuario:

- linha no onprem e nao no Aurora  -> INSERT
- linha nos dois, conteudo diferente -> UPDATE (onprem sempre vence)
- linha so no Aurora                -> DELETE

Os IDs do onprem sao preservados: o id do onprem E o id do Aurora. Nao existe
tabela de correlacao. E isso que mantem todas as FKs validas por construcao.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schema import Schema, Table, normalize_row


PkTuple = tuple[str | None, ...]
Row = dict[str, str | None]


@dataclass
class TablePlan:
    table: str
    inserts: list[Row] = field(default_factory=list)
    updates: list[Row] = field(default_factory=list)
    deletes: list[PkTuple] = field(default_factory=list)
    onprem_count: int = 0
    aurora_count: int = 0
    onprem_auto_increment: int | None = None
    aurora_auto_increment: int | None = None
    changed_columns: dict[str, int] = field(default_factory=dict)

    @property
    def total_changes(self) -> int:
        return len(self.inserts) + len(self.updates) + len(self.deletes)

    @property
    def is_empty(self) -> bool:
        return self.total_changes == 0 and not self.auto_increment_needs_fix

    @property
    def auto_increment_needs_fix(self) -> bool:
        return (
            self.onprem_auto_increment is not None
            and self.aurora_auto_increment is not None
            and self.onprem_auto_increment != self.aurora_auto_increment
        )


@dataclass
class SyncPlan:
    tables: dict[str, TablePlan] = field(default_factory=dict)
    insert_order: list[str] = field(default_factory=list)
    delete_order: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return all(tp.is_empty for tp in self.tables.values())

    def totals(self) -> dict[str, int]:
        return {
            "inserts": sum(len(t.inserts) for t in self.tables.values()),
            "updates": sum(len(t.updates) for t in self.tables.values()),
            "deletes": sum(len(t.deletes) for t in self.tables.values()),
        }


def diff_table(
    table: Table,
    onprem_rows: list[Row],
    aurora_rows: list[Row],
) -> TablePlan:
    plan = TablePlan(
        table=table.name,
        onprem_count=len(onprem_rows),
        aurora_count=len(aurora_rows),
    )

    src: dict[PkTuple, Row] = {}
    for row in onprem_rows:
        normalized = normalize_row(table, row)
        src[table.pk_of(normalized)] = normalized

    dst: dict[PkTuple, Row] = {}
    for row in aurora_rows:
        normalized = normalize_row(table, row)
        dst[table.pk_of(normalized)] = normalized

    for pk, row in src.items():
        existing = dst.get(pk)
        if existing is None:
            plan.inserts.append(row)
        elif existing != row:
            plan.updates.append(row)
            for col in table.column_names:
                if existing.get(col) != row.get(col):
                    plan.changed_columns[col] = plan.changed_columns.get(col, 0) + 1

    for pk in dst:
        if pk not in src:
            plan.deletes.append(pk)

    return plan


def effective_auto_increment(table: Table, rows: list[Row]) -> int | None:
    """Proximo AUTO_INCREMENT que o MySQL de fato vai assumir.

    `information_schema.TABLES.AUTO_INCREMENT` do onprem NAO e confiavel como
    alvo: varias tabelas deste banco foram populadas com ids EXPLICITOS (por
    import), o que nao faz o contador interno avancar. Resultado medido no
    onprem: `manual_effort_metrics` tem MAX(id)=40 e AUTO_INCREMENT=40 - um
    valor impossivel de honrar, porque o InnoDB nunca aceita um contador
    menor que MAX(id)+1 e silenciosamente eleva o `ALTER TABLE`.

    Sem esta correcao, toda tabela nessa situacao apareceria eternamente como
    "AUTO_INCREMENT divergente" no relatorio, quebrando a idempotencia
    aparente do plano mesmo com os dados perfeitamente espelhados.
    """
    column = table.auto_increment_column
    if column is None:
        return None
    reported = table.auto_increment
    max_id = 0
    for row in rows:
        value = row.get(column.name)
        if value is not None:
            try:
                max_id = max(max_id, int(value))
            except ValueError:
                continue
    return max(reported or 1, max_id + 1)


def suggest_page_size(rows: list[Row], max_bytes: int) -> int:
    """Tamanho de pagina/lote derivado do tamanho REAL medido das linhas.

    A maior linha do onprem tem ~316 KB (`reports.context_snapshot`), entao
    contagem fixa nao serve: 100 linhas de `reports` estourariam o limite de
    1 MB do Data API enquanto 100 linhas de `exercise_sets` nem chegam perto.
    """
    if not rows:
        return 200
    max_row_bytes = max(
        sum(len(v.encode("utf-8")) + len(k) + 24 for k, v in row.items() if v is not None)
        for row in rows
    )
    max_row_bytes = max(max_row_bytes, 1)
    return max(1, min(200, max_bytes // max_row_bytes))


def build_plan(
    schema: Schema,
    table_plans: dict[str, TablePlan],
    insert_order: list[str],
) -> SyncPlan:
    return SyncPlan(
        tables=table_plans,
        insert_order=insert_order,
        delete_order=list(reversed(insert_order)),
    )
