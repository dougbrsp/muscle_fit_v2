'use client';

import { Button, Loader, Stack, Text, ThemeIcon } from '@mantine/core';
import {
  IconAlertTriangle,
  IconInbox,
  IconRefresh,
  IconWifiOff,
  type Icon as TablerIcon,
} from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { MOBILE_TOKENS } from '@/theme';
import { useOnlineStatus } from './use-online-status';

/**
 * Estados padronizados da UI mobile (plano mobile, 3.3): carregando, vazio,
 * erro e offline. Cor nunca é o único sinal: todo estado leva ícone e texto.
 * Botões com altura mínima de 44 px. Só apresentação.
 */

const CENTER_STYLE = { minHeight: 200, textAlign: 'center' } as const;
const ACTION_STYLE = { minHeight: MOBILE_TOKENS.touchTarget } as const;

export function LoadingState({ label = 'Carregando…' }: { label?: string }) {
  return (
    <Stack align="center" justify="center" gap="sm" py="xl" role="status" aria-live="polite" style={CENTER_STYLE}>
      <Loader color="brand" />
      <Text size="sm" c={MOBILE_TOKENS.textMuted}>
        {label}
      </Text>
    </Stack>
  );
}

function StateBase({
  icon: Icon,
  color,
  title,
  description,
  action,
  role,
}: {
  icon: TablerIcon;
  color: string;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  role?: 'alert' | 'status';
}) {
  return (
    <Stack align="center" justify="center" gap="sm" py="xl" px="md" role={role} style={CENTER_STYLE}>
      <ThemeIcon size={48} radius="xl" variant="light" color={color}>
        <Icon size={26} aria-hidden />
      </ThemeIcon>
      <Text fw={700} c={MOBILE_TOKENS.text}>
        {title}
      </Text>
      {description ? (
        <Text size="sm" c={MOBILE_TOKENS.textMuted} maw={420}>
          {description}
        </Text>
      ) : null}
      {action}
    </Stack>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return <StateBase icon={IconInbox} color="gray" title={title} description={description} action={action} />;
}

export function ErrorState({
  title = 'Algo deu errado',
  description,
  onRetry,
}: {
  title?: string;
  description?: ReactNode;
  onRetry?: () => void;
}) {
  return (
    <StateBase
      role="alert"
      icon={IconAlertTriangle}
      color="red"
      title={title}
      description={description}
      action={
        onRetry ? (
          <Button color="brand" style={ACTION_STYLE} leftSection={<IconRefresh size={16} />} onClick={onRetry}>
            Tentar de novo
          </Button>
        ) : undefined
      }
    />
  );
}

export function OfflineState({ onRetry }: { onRetry?: () => void }) {
  return (
    <StateBase
      role="status"
      icon={IconWifiOff}
      color="yellow"
      title="Sem conexão"
      description="Você continua conectado. Assim que a rede voltar, tente de novo."
      action={
        onRetry ? (
          <Button variant="light" color="brand" style={ACTION_STYLE} onClick={onRetry}>
            Tentar de novo
          </Button>
        ) : undefined
      }
    />
  );
}

/** Faixa fina no topo do conteúdo; some sozinha quando a rede volta. */
export function OfflineBanner() {
  const online = useOnlineStatus();
  if (online) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '8px 16px',
        background: MOBILE_TOKENS.surfaceRaised,
        borderBottom: `1px solid ${MOBILE_TOKENS.warning}`,
        color: MOBILE_TOKENS.text,
        fontSize: 13,
      }}
    >
      <IconWifiOff size={16} color={MOBILE_TOKENS.warning} aria-hidden />
      <span>Sem conexão. Alguns dados podem estar desatualizados.</span>
    </div>
  );
}
