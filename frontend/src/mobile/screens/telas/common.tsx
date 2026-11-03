'use client';

import { Button, type ButtonProps } from '@mantine/core';
import { useEffect, useRef, type ComponentPropsWithoutRef, type ReactNode } from 'react';
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

/**
 * Visão de página inteira (formulário, editor): o voltar do navegador/gesto do
 * iOS fecha a visão em vez de sair da rota e descartar o rascunho. Abre uma
 * entrada de histórico (copiando o `state` do Next para ele não recarregar);
 * fechar por código desfaz a entrada; sair da rota (desmontar) não mexe no histórico.
 */
export function useBackToClose(open: boolean, onClose: () => void) {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const unmounting = useRef(false);
  // Declarado antes do efeito principal: no desmonte seu cleanup roda primeiro.
  useEffect(() => {
    unmounting.current = false;
    return () => {
      unmounting.current = true;
    };
  }, []);
  useEffect(() => {
    if (!open) return;
    let ownEntry = true;
    window.history.pushState(window.history.state, '');
    const onPop = () => {
      ownEntry = false;
      closeRef.current();
    };
    window.addEventListener('popstate', onPop);
    return () => {
      window.removeEventListener('popstate', onPop);
      if (ownEntry && !unmounting.current) window.history.back();
    };
  }, [open]);
}

/**
 * Tira o foco do campo ativo. Depois de escolher uma opção num Select, o primeiro
 * toque no botão fixo era engolido (só fechava o foco do campo): sem foco preso, o
 * toque seguinte já aciona a ação.
 */
export function blurActive() {
  const active = document.activeElement;
  if (active instanceof HTMLElement) active.blur();
}
