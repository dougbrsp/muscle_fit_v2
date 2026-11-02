/**
 * Pura: o aviso "Atualização disponível" só aparece com uma versão nova em
 * `waiting`, sem trabalho ativo (treino em andamento/ops pendentes, via
 * `hasActiveWork()`) e fora das rotas silenciosas.
 */
export function shouldOfferUpdate(i: { waitingWorker: boolean; activeWork: boolean; silentPath: boolean }): boolean {
  return i.waitingWorker && !i.activeWork && !i.silentPath;
}
