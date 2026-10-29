/**
 * Trava síncrona contra envio duplicado (toque duplo no "Finalizar série").
 * O `isPending` do React Query só muda no PRÓXIMO render; dois toques no
 * mesmo quadro passariam os dois. A trava fecha na hora, no próprio clique,
 * e só reabre quando a gravação termina (sucesso ou erro).
 */
export type SubmitLatch = {
  /** `true` = pode enviar (e a trava fecha); `false` = já há envio em curso. */
  tryEnter: () => boolean;
  release: () => void;
  isBusy: () => boolean;
};

export function createSubmitLatch(): SubmitLatch {
  let busy = false;
  return {
    tryEnter: () => {
      if (busy) return false;
      busy = true;
      return true;
    },
    release: () => {
      busy = false;
    },
    isBusy: () => busy,
  };
}
