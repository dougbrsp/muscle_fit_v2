// M4 - encadeamento do backfill das descrições de execução.
//
// Nenhuma chamada real: `runBatch` é injetado e devolve respostas montadas
// aqui. O que precisa ser provado é o que o admin sente na tela — o lote anda
// até acabar, mostra progresso, para quando mandam parar, não trava num laço
// e não perde a lista do que falhou.

import { describe, expect, it, vi } from 'vitest';
import {
  MAX_ROUNDS,
  progressLabel,
  runExecutionDescriptionBackfill,
} from './execution-description-batch';
import type { ExecutionDescriptionBatchResponse } from './exercise-catalog-admin-types';

function resposta(
  partial: Partial<ExecutionDescriptionBatchResponse> = {},
): ExecutionDescriptionBatchResponse {
  return {
    status: 'ok',
    requested: 10,
    generated: 10,
    skipped: 0,
    failed: 0,
    remaining: 0,
    prompt_version: 'exec-desc-v1',
    items: [],
    ...partial,
  };
}

function filaDeRespostas(respostas: ExecutionDescriptionBatchResponse[]) {
  let i = 0;
  return vi.fn(async () => respostas[Math.min(i++, respostas.length - 1)]);
}

describe('runExecutionDescriptionBackfill', () => {
  it('encadeia chamadas até remaining chegar a zero', async () => {
    const runBatch = filaDeRespostas([
      resposta({ remaining: 15 }),
      resposta({ remaining: 5 }),
      resposta({ requested: 5, generated: 5, remaining: 0 }),
    ]);

    const outcome = await runExecutionDescriptionBackfill({ runBatch, initialPending: 25 });

    expect(runBatch).toHaveBeenCalledTimes(3);
    expect(outcome.reason).toBe('done');
    expect(outcome.generated).toBe(25);
    expect(outcome.processed).toBe(25);
    expect(outcome.remaining).toBe(0);
  });

  it('nada pendente não chama a rota nenhuma vez', async () => {
    const runBatch = filaDeRespostas([resposta()]);
    const outcome = await runExecutionDescriptionBackfill({ runBatch, initialPending: 0 });
    expect(runBatch).not.toHaveBeenCalled();
    expect(outcome.reason).toBe('done');
  });

  it('reporta progresso a cada rodada, com o total ajustado pelo servidor', async () => {
    const vistos: string[] = [];
    const runBatch = filaDeRespostas([
      resposta({ remaining: 10 }),
      resposta({ remaining: 0 }),
    ]);

    await runExecutionDescriptionBackfill({
      runBatch,
      // O admin viu "20" na tela, mas o servidor sabe mais: depois da 1ª
      // rodada sobraram 10, então o total real é 10 + 10 = 20; depois da 2ª,
      // 20 + 0 = 20.
      initialPending: 20,
      onProgress: (p) => vistos.push(progressLabel(p)),
    });

    expect(vistos).toEqual(['10 de 20', '20 de 20']);
  });

  it('parar no meio interrompe antes da próxima chamada e preserva o que já foi gerado', async () => {
    let parar = false;
    const runBatch = vi.fn(async () => {
      parar = true; // o admin aperta "Parar" durante a 1ª rodada
      return resposta({ remaining: 40 });
    });

    const outcome = await runExecutionDescriptionBackfill({
      runBatch,
      initialPending: 50,
      isCancelled: () => parar,
    });

    // A rodada em voo SEMPRE termina: cancelar não pode descartar um texto que
    // o backend já gravou.
    expect(runBatch).toHaveBeenCalledTimes(1);
    expect(outcome.reason).toBe('cancelled');
    expect(outcome.generated).toBe(10);
    expect(outcome.remaining).toBe(40);
  });

  it('cancelado antes de começar não chama a rota', async () => {
    const runBatch = filaDeRespostas([resposta()]);
    const outcome = await runExecutionDescriptionBackfill({
      runBatch,
      initialPending: 30,
      isCancelled: () => true,
    });
    expect(runBatch).not.toHaveBeenCalled();
    expect(outcome.reason).toBe('cancelled');
  });

  it('item que falha não derruba o lote e é reportado no fim', async () => {
    const runBatch = filaDeRespostas([
      resposta({
        requested: 2,
        generated: 1,
        failed: 1,
        remaining: 1,
        items: [
          { exercise_id: 1, status: 'generated' },
          { exercise_id: 2, status: 'failed' },
        ],
      }),
      resposta({
        requested: 1,
        generated: 1,
        remaining: 0,
        items: [{ exercise_id: 3, status: 'generated' }],
      }),
    ]);

    const outcome = await runExecutionDescriptionBackfill({ runBatch, initialPending: 3 });

    expect(outcome.reason).toBe('done');
    expect(outcome.failed).toBe(1);
    expect(outcome.failures.map((item) => item.exercise_id)).toEqual([2]);
    expect(outcome.generated).toBe(2);
  });

  it('erro de rede para o encadeamento sem perder o progresso parcial', async () => {
    let chamada = 0;
    const runBatch = vi.fn(async () => {
      chamada += 1;
      if (chamada === 2) throw new Error('502');
      return resposta({ remaining: 10 });
    });

    const outcome = await runExecutionDescriptionBackfill({ runBatch, initialPending: 20 });

    expect(outcome.reason).toBe('error');
    expect((outcome.error as Error).message).toBe('502');
    expect(outcome.generated).toBe(10);
  });

  it('rodada parcial por orçamento de tempo do servidor é progresso normal, não erro', async () => {
    // Produção: 10 pedidos, mas o servidor fechou o lote em 8 (20 s) e
    // adiou 2. A tela precisa somar só os 8 tentados e chamar de novo.
    const runBatch = filaDeRespostas([
      resposta({ requested: 8, generated: 8, remaining: 12, time_budget_exhausted: true, deferred: 2 }),
      resposta({ requested: 7, generated: 7, remaining: 5, time_budget_exhausted: true, deferred: 3 }),
      resposta({ requested: 5, generated: 5, remaining: 0 }),
    ]);
    const vistos: string[] = [];

    const outcome = await runExecutionDescriptionBackfill({
      runBatch,
      initialPending: 20,
      onProgress: (p) => vistos.push(progressLabel(p)),
    });

    expect(runBatch).toHaveBeenCalledTimes(3);
    expect(outcome.reason).toBe('done');
    expect(outcome.generated).toBe(20);
    expect(outcome.processed).toBe(20);
    expect(outcome.rounds).toBe(3);
    expect(outcome.partialRounds).toBe(2);
    expect(vistos).toEqual(['8 de 20', '15 de 20', '20 de 20']);
  });

  it('fetch rejeitado (status 0) vira server_timeout, preservando o progresso', async () => {
    // O API Gateway cortou o request em 29 s: 504 sem CORS, o `fetch` rejeita
    // e o `apiFetch` lança um erro com status 0. O que o backend gerou até
    // ali está gravado; a tela precisa dizer isso, não "verifique a conexão".
    class ErroDeApi extends Error {
      constructor(public status: number) {
        super('Não foi possível conectar à API.');
      }
    }
    let chamada = 0;
    const runBatch = vi.fn(async () => {
      chamada += 1;
      if (chamada === 2) throw new ErroDeApi(0);
      return resposta({ remaining: 30 });
    });

    const outcome = await runExecutionDescriptionBackfill({
      runBatch,
      initialPending: 40,
      isServerTimeout: (error) => error instanceof ErroDeApi && error.status === 0,
    });

    expect(outcome.reason).toBe('server_timeout');
    expect(outcome.generated).toBe(10);
    expect(outcome.remaining).toBe(30);
  });

  it('erro com status HTTP real continua sendo error, não server_timeout', async () => {
    class ErroDeApi extends Error {
      constructor(public status: number) {
        super('Acesso restrito.');
      }
    }
    const runBatch = vi.fn(async () => {
      throw new ErroDeApi(403);
    });

    const outcome = await runExecutionDescriptionBackfill({
      runBatch,
      initialPending: 5,
      isServerTimeout: (error) => error instanceof ErroDeApi && error.status === 0,
    });

    expect(outcome.reason).toBe('error');
  });

  it('rodada que não processa nada para em vez de virar laço infinito', async () => {
    // O que sobrou está reservado por uma geração automática em andamento:
    // o servidor devolve `requested: 0` e `remaining` teimosamente > 0.
    const runBatch = filaDeRespostas([
      resposta({ requested: 0, generated: 0, remaining: 3 }),
    ]);

    const outcome = await runExecutionDescriptionBackfill({ runBatch, initialPending: 3 });

    expect(runBatch).toHaveBeenCalledTimes(1);
    expect(outcome.reason).toBe('stalled');
    expect(outcome.remaining).toBe(3);
  });

  it('servidor que nunca zera remaining esbarra no teto de rodadas', async () => {
    const runBatch = filaDeRespostas([resposta({ requested: 1, generated: 1, remaining: 99 })]);
    const outcome = await runExecutionDescriptionBackfill({
      runBatch,
      initialPending: 99,
      maxRounds: 4,
    });
    expect(runBatch).toHaveBeenCalledTimes(4);
    expect(outcome.reason).toBe('stalled');
    expect(MAX_ROUNDS).toBeGreaterThan(4);
  });
});
