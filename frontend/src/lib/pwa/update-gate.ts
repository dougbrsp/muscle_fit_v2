/**
 * Porta de segurança da atualização do SW (sem React, testável): aplicar a
 * nova versão recarrega a página, então só acontece com `hasActiveWork()`
 * false, conferido de novo NO MOMENTO de aplicar.
 */
export type UpdateGateDeps = {
  hasActiveWork: () => Promise<boolean>;
  markApplying: () => void;
  postSkipWaiting: () => void;
};

/** `true` se aplicou (SKIP_WAITING enviado); `false` se havia trabalho ativo e nada foi feito. */
export async function tryApplyUpdate(deps: UpdateGateDeps): Promise<boolean> {
  if (await deps.hasActiveWork()) return false;
  deps.markApplying();
  deps.postSkipWaiting();
  return true;
}
