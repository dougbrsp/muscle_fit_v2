'use client';

import { Button, type ButtonProps } from '@mantine/core';
import type { ComponentPropsWithoutRef, ReactNode } from 'react';
import { ErrorState, LoadingState, OfflineState, useOnlineStatus } from '../../ui';

/**
 * Porteiro de estados de uma query (carregando, offline, erro). Só apresenta:
 * a tela decide o que mostrar quando pronto. Erro sem rede vira "offline".
 */
export function QueryGate({
  isLoading,
  isError,
  error,
  onRetry,
  loadingLabel,
  errorTitle,
  children,
}: {
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  onRetry: () => void;
  loadingLabel?: string;
  errorTitle?: string;
  children: ReactNode;
}) {
  const online = useOnlineStatus();
  if (isLoading) return <LoadingState label={loadingLabel} />;
  if (isError) {
    if (!online) return <OfflineState onRetry={onRetry} />;
    return (
      <ErrorState
        title={errorTitle ?? 'Não foi possível carregar'}
        description={error instanceof Error ? error.message : undefined}
        onRetry={onRetry}
      />
    );
  }
  return <>{children}</>;
}

/** Botão de largura total de 48 pt para ações dentro de folhas e rodapés. */
export function SheetButton(props: ButtonProps & Omit<ComponentPropsWithoutRef<'button'>, keyof ButtonProps>) {
  return <Button fullWidth size="md" style={{ minHeight: 48 }} {...props} />;
}
