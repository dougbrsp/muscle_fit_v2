// Screen Wake Lock opcional para o Modo Treino. Só usa o recurso se houver
// suporte; qualquer falha (negado, bateria baixa, iOS sem suporte) é SILENCIOSA
// e vira um aviso de texto ("a tela pode apagar"). Não promete manter a tela
// ativa. O lock é liberado pelo navegador ao ocultar a página, então é
// readquirido quando ela volta a ficar visível.

export type WakeLockState = 'unsupported' | 'inactive' | 'active' | 'failed';

type SentinelLike = { release: () => Promise<void>; addEventListener?: (type: 'release', cb: () => void) => void };
type WakeLockApi = { request: (type: 'screen') => Promise<SentinelLike> };

export type WakeLockController = {
  /** Pede o lock (e passa a readquiri-lo ao voltar ao primeiro plano). */
  enable: () => Promise<void>;
  disable: () => Promise<void>;
  getState: () => WakeLockState;
  subscribe: (listener: () => void) => () => void;
};

export function createWakeLockController(env: {
  api: () => WakeLockApi | null;
  isVisible: () => boolean;
  addVisibilityListener: (cb: () => void) => () => void;
}): WakeLockController {
  let state: WakeLockState = env.api() ? 'inactive' : 'unsupported';
  let wanted = false;
  let sentinel: SentinelLike | null = null;
  let removeVisibility: (() => void) | null = null;
  const listeners = new Set<() => void>();

  const set = (next: WakeLockState) => {
    if (state === next) return;
    state = next;
    for (const listener of listeners) listener();
  };

  async function acquire(): Promise<void> {
    const api = env.api();
    if (!api) return set('unsupported');
    if (!wanted || !env.isVisible() || sentinel) return;
    try {
      const lock = await api.request('screen');
      if (!wanted) {
        await lock.release().catch(() => undefined);
        return;
      }
      sentinel = lock;
      lock.addEventListener?.('release', () => {
        if (sentinel === lock) sentinel = null;
        if (wanted) set('inactive');
      });
      set('active');
    } catch {
      sentinel = null;
      set('failed');
    }
  }

  return {
    async enable() {
      wanted = true;
      if (!removeVisibility) {
        removeVisibility = env.addVisibilityListener(() => {
          if (env.isVisible()) void acquire();
        });
      }
      await acquire();
    },
    async disable() {
      wanted = false;
      removeVisibility?.();
      removeVisibility = null;
      const lock = sentinel;
      sentinel = null;
      if (lock) await lock.release().catch(() => undefined);
      if (state !== 'unsupported') set('inactive');
    },
    getState: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

/** Controlador do navegador (singleton por página). */
let browserController: WakeLockController | null = null;

export function getBrowserWakeLock(): WakeLockController {
  if (!browserController) {
    browserController = createWakeLockController({
      api: () => {
        if (typeof navigator === 'undefined') return null;
        const wl = (navigator as unknown as { wakeLock?: WakeLockApi }).wakeLock;
        return wl ?? null;
      },
      isVisible: () => typeof document === 'undefined' || document.visibilityState === 'visible',
      addVisibilityListener: (cb) => {
        if (typeof document === 'undefined') return () => undefined;
        document.addEventListener('visibilitychange', cb);
        return () => document.removeEventListener('visibilitychange', cb);
      },
    });
  }
  return browserController;
}

/** Texto do aviso quando o lock não está ativo mas foi pedido; `null` = nada a avisar. */
export function wakeLockNotice(state: WakeLockState): string | null {
  if (state === 'unsupported' || state === 'failed') return 'A tela pode apagar durante o treino neste aparelho.';
  return null;
}
