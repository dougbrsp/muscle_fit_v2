"""Blocos de LEITURA independentes de uma requisicao, em paralelo (PERF-05).

Problema medido em producao (CloudWatch, `musclefit-engines`, 14 dias):
`GET /analytics/dashboard/overview` leva p50 5,3 s porque os blocos
(aderencia, musculos, recuperacao, corpo, qualidade...) rodam um depois do
outro, e na RDS Data API cada statement e uma chamada HTTP de ~50 ms. Os
blocos nao dependem uns dos outros: rodando juntos, o tempo da rota passa a
ser o do bloco mais lento, nao a soma. Idem para o agregador de
`GET /analytics/training-evolution`.

Contrato (o que impede este modulo de mudar resultado):

- So LEITURA. Quem escreve (ex.: o cooldown de alertas) continua fora, depois
  dos blocos, na conexao da rota.
- Nenhuma conexao e compartilhada entre threads: a `Connection` da Data API
  carrega uma `transactionId` e a do PyMySQL um socket. `run_blocks` da a cada
  bloco a PROPRIA conexao (`conn_factory`); em `run_parallel` cada tarefa ja
  abre as suas (servicos com `fetch_all`/`get_conn`). O client `rds-data` e
  compartilhado e e thread-safe (`db_dataapi._shared_client`).
- Nenhuma instancia de servico com cache proprio e usada por duas tarefas ao
  mesmo tempo: o chamador agrupa numa MESMA tarefa as leituras do mesmo objeto.
- O contexto da requisicao (`request_memo`) e copiado para cada thread com
  `contextvars.copy_context()`: todas enxergam o MESMO memo.
- Resultado na ordem declarada; a excecao que sobe e a da PRIMEIRA tarefa que
  falhou (nessa ordem), e so depois de todas terminarem.
- Sem `conn_factory` (testes unitarios com conexao falsa, scripts), os blocos
  de `run_blocks` rodam em sequencia, na ordem declarada, com a conexao
  recebida - o mesmo caminho de antes.

Nenhuma regra mora aqui (docs/motores/FONTES_DA_VERDADE.md): so a decisao de
"quando" cada leitura roda, nunca "o que" ela calcula.
"""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, ContextManager, Optional, Sequence

# Teto de threads por requisicao (o maior uso hoje sao as 6 tarefas de
# `training-evolution`). O client boto3 tem pool de 10 conexoes HTTP (default
# do botocore), entao nunca disputam conexao. Limite de concorrencia de I/O,
# nao regra de motor.
MAX_PARALLEL_TASKS = 6

Block = Callable[[Any], Any]
ConnFactory = Callable[[], ContextManager[Any]]


def run_parallel(tasks: Sequence[Callable[[], Any]], *, max_workers: int = MAX_PARALLEL_TASKS) -> list[Any]:
    """Executa `tasks` (sem argumentos) em threads e devolve os resultados na
    mesma ordem. Uma tarefa so: roda na thread atual."""
    if len(tasks) <= 1:
        return [task() for task in tasks]
    workers = max(1, min(int(max_workers), len(tasks)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="parallel-reads") as pool:
        futures = [pool.submit(contextvars.copy_context().run, task) for task in tasks]
        # O `with` espera TODAS terminarem; depois, `result()` na ordem
        # declarada faz subir a primeira excecao (nessa ordem).
    return [future.result() for future in futures]


def run_blocks(
    blocks: Sequence[Block],
    *,
    conn: Any,
    conn_factory: Optional[ConnFactory] = None,
    max_workers: int = MAX_PARALLEL_TASKS,
) -> list[Any]:
    """Executa `blocks` (cada um recebe uma conexao) e devolve os resultados na
    mesma ordem. Com `conn_factory`, em paralelo, cada bloco com
    `with conn_factory() as c`; sem, em sequencia com `conn`."""
    if conn_factory is None:
        return [block(conn) for block in blocks]

    def _with_own_conn(block: Block) -> Callable[[], Any]:
        def run() -> Any:
            with conn_factory() as own_conn:
                return block(own_conn)

        return run

    return run_parallel([_with_own_conn(block) for block in blocks], max_workers=max_workers)


__all__ = ["MAX_PARALLEL_TASKS", "ConnFactory", "run_blocks", "run_parallel"]
