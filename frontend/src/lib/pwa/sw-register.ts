import { SW_CACHE_PREFIX, SW_SCOPE, SW_URL } from './constants';

export type SwRegisterInput = {
  isProduction: boolean;
  mobileUiEnabled: boolean;
  supported: boolean;
};

/** Pura: registrar só em produção, com a flag mobile ligada e suporte do navegador. */
export function shouldRegisterServiceWorker(i: SwRegisterInput): boolean {
  return i.isProduction && i.mobileUiEnabled && i.supported;
}

/**
 * Pura: desfazer um SW anterior quando a flag foi desligada (rollback pelo cliente).
 * Atenção: override `on` em localStorage (`musclefit.flag.mobile-ui`) ou `?mobile=1` mantém a
 * flag LIGADA no aparelho mesmo com a flag de build OFF; limpar o override faz parte do rollback.
 */
export function shouldUnregisterServiceWorker(i: SwRegisterInput): boolean {
  return i.isProduction && !i.mobileUiEnabled && i.supported;
}

export function isServiceWorkerSupported(): boolean {
  return typeof navigator !== 'undefined' && 'serviceWorker' in navigator && typeof caches !== 'undefined';
}

export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  try {
    return await navigator.serviceWorker.register(SW_URL, { scope: SW_SCOPE });
  } catch {
    return null; // sem SW o app continua funcionando online
  }
}

/** Desregistra os SWs do app e apaga só os caches `musclefit-*`. Falha silenciosa. */
export async function unregisterServiceWorkerAndCaches(): Promise<void> {
  try {
    const regs = await navigator.serviceWorker.getRegistrations();
    await Promise.all(regs.map((r) => r.unregister()));
    const names = await caches.keys();
    await Promise.all(names.filter((n) => n.startsWith(SW_CACHE_PREFIX)).map((n) => caches.delete(n)));
  } catch {
    // ignorado
  }
}
