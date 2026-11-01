'use client';

import { Text } from '@mantine/core';
import { useEffect, useSyncExternalStore } from 'react';
import { getBrowserWakeLock, wakeLockNotice } from '@/lib/wake-lock';
import { BRAND } from '@/theme';

/**
 * Mantém a tela acesa durante o treino quando o navegador permite (Screen Wake
 * Lock) e avisa em texto quando não dá ("a tela pode apagar"). Falha é
 * silenciosa e nunca bloqueia o treino; não promete nada no iOS.
 */
export function WakeLockNotice() {
  const controller = getBrowserWakeLock();
  const state = useSyncExternalStore(controller.subscribe, controller.getState, () => 'inactive' as const);

  useEffect(() => {
    void controller.enable();
    return () => {
      void controller.disable();
    };
  }, [controller]);

  const notice = wakeLockNotice(state);
  if (!notice) return null;
  return (
    <Text size="xs" c={BRAND.textMuted} role="status" data-testid="wake-lock-notice">
      {notice}
    </Text>
  );
}
