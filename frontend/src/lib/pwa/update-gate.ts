/**
 * Porta de segurança da atualização do SW (sem React, testável): aplicar a
 * nova versão recarrega a página, então só acontece com `hasActiveWork()`
 * false, conferido de novo NO MOMENTO de aplicar.
 */
export type UpdateGateDeps = {
  /** Já deve incluir a regra "sem provedor registrado = ocupado" (ver `isBusyForUpdate`). */
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

/**
 * "Ocupado" para fins de atualização: com `hasActiveWork()` o contrato devolve
 * false quando NINGUÉM registrou provedor (a frente B ainda não ligou a fila).
 * Para recarregar a página isso é desconhecido, não ocioso: sem provedor,
 * trata como ocupado e nunca oferece nem aplica a atualização.
 */
export async function isBusyForUpdate(deps: { hasProviders: () => boolean; hasActiveWork: () => Promise<boolean> }): Promise<boolean> {
  if (!deps.hasProviders()) return true;
  return deps.hasActiveWork();
}
