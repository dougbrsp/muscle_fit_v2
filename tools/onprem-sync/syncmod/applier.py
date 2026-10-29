"""Aplicacao do plano no Aurora, em transacao unica com rollback em erro.

Ordem de operacoes (exigencia do grafo de FK):

1. DELETEs em ordem topologica INVERSA (filhos antes de pais);
2. UPDATEs e INSERTs em ordem topologica DIRETA (pais antes de filhos),
   com UPDATE antes de INSERT dentro de cada tabela para liberar chaves
   unicas que tenham "andado" de linha;
3. `ALTER TABLE ... AUTO_INCREMENT` FORA da transacao.

O passo 3 e separado de proposito: no MySQL, `ALTER TABLE` e DDL e provoca
COMMIT IMPLICITO. Executa-lo dentro da transacao destruiria a atomicidade do
rollback sem nenhum aviso.

`FOREIGN_KEY_CHECKS = 0` fica ativo durante a transacao. Nao e atalho: com
espelho exato, estados intermediarios legitimos violam FK (ex.: um pai ja
deletado enquanto o filho ainda nao foi). A checagem e devolvida ao normal
antes do commit e a integridade referencial e provada depois, explicitamente,
por `verify.check_referential_integrity`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from .aurora import AuroraClient
from .planner import PkTuple, Row, SyncPlan
from .schema import Schema, Table


Progress = Callable[[str], None]


@dataclass
class ApplyStats:
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    auto_increment_fixed: list[str] = field(default_factory=list)
    statements: int = 0


def _param_bytes(params: Sequence[dict[str, Any]]) -> int:
    total = 0
    for p in params:
        value = p.get("value", {})
        sv = value.get("stringValue")
        total += len(sv.encode("utf-8")) if isinstance(sv, str) else 16
        total += len(p.get("name", "")) + 40
    return total


def _chunk_by_size(
    parameter_sets: list[Sequence[dict[str, Any]]],
    max_rows: int,
    max_bytes: int,
) -> Iterable[list[Sequence[dict[str, Any]]]]:
    """Lotes dimensionados por BYTES reais, nao por contagem fixa.

    O limite do Data API e ~1 MB por chamada. Como `reports.context_snapshot`
    chega a 316 KB numa unica linha, um lote de contagem fixa estouraria ali e
    desperdicaria capacidade em `exercise_sets`.
    """
    batch: list[Sequence[dict[str, Any]]] = []
    size = 0
    for ps in parameter_sets:
        ps_size = _param_bytes(ps)
        if batch and (len(batch) >= max_rows or size + ps_size > max_bytes):
            yield batch
            batch, size = [], 0
        batch.append(ps)
        size += ps_size
    if batch:
        yield batch


class Applier:
    def __init__(
        self,
        client: AuroraClient,
        schema: Schema,
        progress: Progress = lambda _msg: None,
    ) -> None:
        self.client = client
        self.schema = schema
        self.progress = progress
        self.stats = ApplyStats()

    # -- construcao de statements -----------------------------------------

    def _insert_statement(self, table: Table) -> str:
        cols = ", ".join(f"`{c}`" for c in table.column_names)
        placeholders = ", ".join(f":{c}" for c in table.column_names)
        return f"INSERT INTO `{table.name}` ({cols}) VALUES ({placeholders})"

    def _insert_params(self, table: Table, row: Row) -> list[dict[str, Any]]:
        return [table.column(c).data_api_param(c, row.get(c)) for c in table.column_names]

    def _update_statement(self, table: Table) -> str:
        assignable = [c for c in table.column_names if c not in table.pk_columns]
        sets = ", ".join(f"`{c}` = :{c}" for c in assignable)
        where = " AND ".join(f"`{c}` = :pk__{c}" for c in table.pk_columns)
        return f"UPDATE `{table.name}` SET {sets} WHERE {where}"

    def _update_params(self, table: Table, row: Row) -> list[dict[str, Any]]:
        params = [
            table.column(c).data_api_param(c, row.get(c))
            for c in table.column_names
            if c not in table.pk_columns
        ]
        params += [
            table.column(c).data_api_param(f"pk__{c}", row.get(c)) for c in table.pk_columns
        ]
        return params

    def _delete_statement(self, table: Table) -> str:
        where = " AND ".join(f"`{c}` = :pk__{c}" for c in table.pk_columns)
        return f"DELETE FROM `{table.name}` WHERE {where}"

    def _delete_params(self, table: Table, pk: PkTuple) -> list[dict[str, Any]]:
        return [
            table.column(col).data_api_param(f"pk__{col}", value)
            for col, value in zip(table.pk_columns, pk)
        ]

    # -- execucao ----------------------------------------------------------

    def _run_batches(self, sql: str, parameter_sets: list[Sequence[dict[str, Any]]]) -> int:
        cfg = self.client.batch_cfg
        applied = 0
        for batch in _chunk_by_size(parameter_sets, cfg.max_rows_per_batch, cfg.max_bytes_per_batch):
            self.client.batch(sql, batch)
            self.stats.statements += 1
            applied += len(batch)
        return applied

    def apply(self, plan: SyncPlan) -> ApplyStats:
        """Executa o plano inteiro. Deve ser chamado com transacao ja aberta."""
        if not self.client.transaction_id:
            raise RuntimeError("apply() exige uma transacao aberta no Aurora.")

        # 1. DELETEs, filhos antes de pais.
        for table_name in plan.delete_order:
            tp = plan.tables.get(table_name)
            if not tp or not tp.deletes:
                continue
            table = self.schema[table_name]
            self.progress(f"  DELETE {table_name}: {len(tp.deletes)} linha(s)")
            sets = [self._delete_params(table, pk) for pk in tp.deletes]
            self.stats.deleted += self._run_batches(self._delete_statement(table), sets)

        # 2. UPDATEs e INSERTs, pais antes de filhos.
        for table_name in plan.insert_order:
            tp = plan.tables.get(table_name)
            if not tp:
                continue
            table = self.schema[table_name]

            if tp.updates:
                self.progress(f"  UPDATE {table_name}: {len(tp.updates)} linha(s)")
                sets = [self._update_params(table, row) for row in tp.updates]
                self.stats.updated += self._run_batches(self._update_statement(table), sets)

            if tp.inserts:
                self.progress(f"  INSERT {table_name}: {len(tp.inserts)} linha(s)")
                sets = [self._insert_params(table, row) for row in tp.inserts]
                self.stats.inserted += self._run_batches(self._insert_statement(table), sets)

        return self.stats

    def fix_auto_increment(self, plan: SyncPlan) -> list[str]:
        """Alinha o proximo AUTO_INCREMENT de cada tabela com o do onprem.

        Sem isso, insercoes futuras feitas pelo app na AWS comecariam de um
        contador diferente e poderiam colidir com ids que o onprem ainda vai
        gerar. DDL: roda FORA da transacao (ALTER TABLE faz commit implicito).
        """
        if self.client.transaction_id:
            raise RuntimeError("fix_auto_increment() deve rodar FORA da transacao (ALTER = DDL).")

        fixed: list[str] = []
        for table_name in plan.insert_order:
            tp = plan.tables.get(table_name)
            if not tp or tp.onprem_auto_increment is None:
                continue
            table = self.schema[table_name]
            if table.auto_increment_column is None:
                continue
            self.client.execute(
                f"ALTER TABLE `{table_name}` AUTO_INCREMENT = {int(tp.onprem_auto_increment)}",
                include_metadata=False,
            )
            fixed.append(f"{table_name} -> {tp.onprem_auto_increment}")
        self.stats.auto_increment_fixed = fixed
        return fixed
