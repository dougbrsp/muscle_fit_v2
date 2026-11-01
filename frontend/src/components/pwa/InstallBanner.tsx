'use client';

import { ActionIcon, Button, Group, Paper, Text } from '@mantine/core';
import { IconX } from '@tabler/icons-react';
import { usePathname } from 'next/navigation';

import { useMobileUi } from '@/lib/mobile/flags';
import { useLayoutMode } from '@/lib/mobile/layout-mode';
import { shouldShowInstallBanner } from '@/lib/pwa/install-banner-policy';
import { useInstallPrompt } from '@/lib/pwa/use-install-prompt';

/**
 * Convite discreto para instalar o app. Só aparece com a flag mobile ligada e
 * viewport mobile; iOS Safari fora do modo standalone recebe a instrução manual
 * (o Safari não tem prompt automático); Android só se o navegador oferecer
 * `beforeinstallprompt`. Dispensável, reaparece após `INSTALL_REAPPEAR_DAYS`.
 */
export function InstallBanner() {
  const pathname = usePathname();
  const layoutMode = useLayoutMode();
  const { mode, dismissed, promptInstall, dismiss } = useInstallPrompt();
  const flagOn = useMobileUi() === true;

  const show = shouldShowInstallBanner({
    mobileUiEnabled: flagOn,
    isMobileViewport: layoutMode === 'mobile',
    mode,
    dismissed,
    pathname,
  });
  if (!show) return null;

  return (
    <Paper
      role="region"
      aria-label="Instalar o aplicativo"
      data-testid="pwa-install-banner"
      withBorder
      shadow="md"
      radius="md"
      p="sm"
      style={{
        position: 'fixed',
        left: 12,
        right: 12,
        bottom: 'calc(12px + env(safe-area-inset-bottom, 0px))',
        zIndex: 150,
      }}
    >
      <Group justify="space-between" align="flex-start" wrap="nowrap" gap="xs">
        <div style={{ flex: 1 }}>
          <Text size="sm" fw={600}>
            Instale o MuscleFit
          </Text>
          {mode === 'ios-safari' ? (
            <Text size="xs" c="dimmed" data-testid="pwa-install-ios-text">
              Safari → Compartilhar → Adicionar à Tela de Início
            </Text>
          ) : (
            <Text size="xs" c="dimmed">
              Abra o app direto da tela inicial, em tela cheia.
            </Text>
          )}
          {mode === 'prompt' ? (
            <Button size="compact-sm" mt={6} onClick={() => void promptInstall()}>
              Instalar
            </Button>
          ) : null}
        </div>
        <ActionIcon variant="subtle" color="gray" size={44} aria-label="Dispensar convite de instalação" onClick={dismiss}>
          <IconX size={18} />
        </ActionIcon>
      </Group>
    </Paper>
  );
}
