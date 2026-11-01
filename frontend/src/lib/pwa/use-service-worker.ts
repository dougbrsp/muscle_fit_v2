'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { hasActiveWork } from '@/lib/mobile/active-work';
import { isMobileUiEnabled } from '@/lib/mobile/flags';

import { UPDATE_RECHECK_INTERVAL_MS } from './constants';
import {
  isServiceWorkerSupported,
  registerServiceWorker,
  shouldRegisterServiceWorker,
  shouldUnregisterServiceWorker,
  unregisterServiceWorkerAndCaches,
} from './sw-register';
import { tryApplyUpdate } from './update-gate';

export type ServiceWorkerUpdateState = {
  /** Há versão nova em `waiting` E o aplicativo está ocioso (sem treino/ops pendentes). */
  updateReady: boolean;
  applyUpdate: () => Promise<void>;
};

/**
 * Registra o SW (produção + flag mobile) e expõe a atualização segura: a nova
 * versão fica em `waiting`; só é oferecida quando `hasActiveWork()` é false e
 * só é aplicada (SKIP_WAITING + reload) depois de reconferir `hasActiveWork()`.
 */
export function useServiceWorker(): ServiceWorkerUpdateState {
  const [waiting, setWaiting] = useState<ServiceWorker | null>(null);
  const [idle, setIdle] = useState(false);
  const applying = useRef(false);

  useEffect(() => {
    const input = {
      isProduction: process.env.NODE_ENV === 'production',
      mobileUiEnabled: isMobileUiEnabled(),
      supported: isServiceWorkerSupported(),
    };
    if (shouldUnregisterServiceWorker(input)) {
      void unregisterServiceWorkerAndCaches();
      return undefined;
    }
    if (!shouldRegisterServiceWorker(input)) return undefined;

    let cancelled = false;
    let registration: ServiceWorkerRegistration | null = null;

    const watch = (reg: ServiceWorkerRegistration) => {
      const check = () => {
        // `controller` nulo = primeira instalação: não há "atualização" a oferecer.
        if (reg.waiting && navigator.serviceWorker.controller && !cancelled) setWaiting(reg.waiting);
      };
      check();
      reg.addEventListener('updatefound', () => {
        const installing = reg.installing;
        installing?.addEventListener('statechange', () => {
          if (installing.state === 'installed') check();
        });
      });
    };

    void registerServiceWorker().then((reg) => {
      if (!reg || cancelled) return;
      registration = reg;
      watch(reg);
    });

    const onVisible = () => {
      if (document.visibilityState === 'visible') void registration?.update().catch(() => undefined);
    };
    document.addEventListener('visibilitychange', onVisible);

    const onControllerChange = () => {
      // Só recarrega quando fomos nós que pedimos (evita reload na 1ª instalação/claim).
      if (applying.current) window.location.reload();
    };
    navigator.serviceWorker.addEventListener('controllerchange', onControllerChange);

    return () => {
      cancelled = true;
      document.removeEventListener('visibilitychange', onVisible);
      navigator.serviceWorker.removeEventListener('controllerchange', onControllerChange);
    };
  }, []);

  // Reavalia o trabalho ativo enquanto há atualização aguardando.
  useEffect(() => {
    if (!waiting) {
      setIdle(false);
      return undefined;
    }
    let alive = true;
    const tick = async () => {
      const busy = await hasActiveWork();
      if (alive) setIdle(!busy);
    };
    void tick();
    const id = window.setInterval(() => void tick(), UPDATE_RECHECK_INTERVAL_MS);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [waiting]);

  const applyUpdate = useCallback(async () => {
    if (!waiting) return;
    const applied = await tryApplyUpdate({
      hasActiveWork,
      markApplying: () => {
        applying.current = true;
      },
      postSkipWaiting: () => waiting.postMessage({ type: 'SKIP_WAITING' }),
    });
    if (!applied) setIdle(false); // apareceu trabalho entre o aviso e o toque: não recarrega
  }, [waiting]);

  return { updateReady: waiting !== null && idle, applyUpdate };
}
