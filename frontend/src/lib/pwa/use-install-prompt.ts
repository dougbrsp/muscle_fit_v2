'use client';

import { useCallback, useEffect, useState } from 'react';

import { INSTALL_DISMISS_STORAGE_KEY } from './constants';
import { isInstallDismissed } from './install-banner-policy';
import { manualInstallMode, readInstallEnv, type InstallMode } from './platform';

type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
};

export type InstallPromptState = {
  mode: InstallMode | 'prompt';
  dismissed: boolean;
  /** Só existe em `mode === 'prompt'` (Android/Chromium). */
  promptInstall: () => Promise<void>;
  dismiss: () => void;
};

function readDismissed(): boolean {
  try {
    return isInstallDismissed(window.localStorage.getItem(INSTALL_DISMISS_STORAGE_KEY), Date.now());
  } catch {
    return false;
  }
}

/**
 * Estado do convite de instalação. iOS: instrução manual (não existe prompt
 * automático no Safari). Android/Chromium: usa `beforeinstallprompt` apenas se
 * o navegador disparar o evento; senão não mostra nada.
 */
export function useInstallPrompt(): InstallPromptState {
  const [mode, setMode] = useState<InstallMode | 'prompt'>('none');
  const [dismissed, setDismissed] = useState(true);
  const [deferred, setDeferred] = useState<BeforeInstallPromptEvent | null>(null);

  useEffect(() => {
    setDismissed(readDismissed());
    setMode(manualInstallMode(readInstallEnv()));

    const onBefore = (e: Event) => {
      e.preventDefault();
      setDeferred(e as BeforeInstallPromptEvent);
      setMode('prompt');
    };
    const onInstalled = () => {
      setDeferred(null);
      setMode('none');
    };
    window.addEventListener('beforeinstallprompt', onBefore);
    window.addEventListener('appinstalled', onInstalled);
    return () => {
      window.removeEventListener('beforeinstallprompt', onBefore);
      window.removeEventListener('appinstalled', onInstalled);
    };
  }, []);

  const dismiss = useCallback(() => {
    try {
      window.localStorage.setItem(INSTALL_DISMISS_STORAGE_KEY, String(Date.now()));
    } catch {
      // storage indisponível: dispensa vale só nesta sessão
    }
    setDismissed(true);
  }, []);

  const promptInstall = useCallback(async () => {
    if (!deferred) return;
    try {
      await deferred.prompt();
      await deferred.userChoice;
    } finally {
      setDeferred(null);
      setMode('none');
    }
  }, [deferred]);

  return { mode, dismissed, promptInstall, dismiss };
}
