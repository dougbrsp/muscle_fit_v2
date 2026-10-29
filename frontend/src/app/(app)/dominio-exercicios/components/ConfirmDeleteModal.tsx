'use client';

import { Alert, Button, Group, Modal, Stack, Text } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { ApiError } from '@/lib/api';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

/**
 * Modal de confirmação genérico para ações destrutivas (delete de alias,
 * mapeamento externo, tipo de equipamento, descarte de card ilustrado) —
 * nunca `confirm()` nativo.
 *
 * Opcionais (sem efeito nos usos antigos): `confirmLabel` troca o "Excluir"
 * do botão; `confirmDisabled` trava o botão (ex.: campo inválido);
 * `children` entra entre a mensagem e o erro (ex.: um campo de motivo).
 */
export function ConfirmDeleteModal({
  opened,
  title,
  message,
  onClose,
  onConfirm,
  loading,
  error,
  fallbackErrorMessage,
  confirmLabel = 'Excluir',
  confirmDisabled = false,
  children,
}: {
  opened: boolean;
  title: string;
  message: React.ReactNode;
  onClose: () => void;
  onConfirm: () => void;
  loading: boolean;
  error: unknown;
  fallbackErrorMessage: string;
  confirmLabel?: string;
  confirmDisabled?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <Modal opened={opened} onClose={onClose} title={title} size="sm" centered>
      <Stack gap="md">
        <Text>{message}</Text>
        {children}
        {Boolean(error) && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            {isForbidden(error) ? 'Ação restrita a administradores.' : errorMessage(error, fallbackErrorMessage)}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancelar
          </Button>
          <Button color="red" loading={loading} disabled={confirmDisabled} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
