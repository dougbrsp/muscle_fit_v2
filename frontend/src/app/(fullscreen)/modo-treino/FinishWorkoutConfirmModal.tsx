'use client';

import { Button, Modal, Text } from '@mantine/core';
import styles from './intensive.module.css';
import { WORKOUT_MODE_CSS_VARS } from './ui/workout-mode-css-vars';

type Props = {
  opened: boolean;
  finishing: boolean;
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
  /** Rótulo do botão que desiste; o padrão é o do Modo Treino. */
  cancelLabel?: string;
};

/** Confirmação antes de encerrar o treino (toque em "Encerrar treino"). */
export function FinishWorkoutConfirmModal({ opened, finishing, error, onConfirm, onCancel, cancelLabel = 'Continuar treino' }: Props) {
  return (
    <Modal
      opened={opened}
      onClose={onCancel}
      title="Encerrar treino"
      centered
      radius="lg"
      closeOnClickOutside={!finishing}
      closeOnEscape={!finishing}
      withCloseButton={!finishing}
      closeButtonProps={{ 'aria-label': 'Fechar' }}
    >
      <div style={WORKOUT_MODE_CSS_VARS} className={styles.inlineRow} data-testid="finish-confirm">
        <Text size="sm" w="100%">
          O treino será finalizado. As séries e os exercícios já registrados ficam salvos.
        </Text>
        {error && (
          <span className={styles.errorBox} role="alert">
            {error}
          </span>
        )}
        <Button
          fullWidth
          size="lg"
          h={56}
          color="green"
          radius="xl"
          loading={finishing}
          disabled={finishing}
          onClick={onConfirm}
        >
          Confirmar encerramento
        </Button>
        <Button fullWidth size="lg" h={56} variant="light" radius="xl" disabled={finishing} onClick={onCancel}>
          {cancelLabel}
        </Button>
      </div>
    </Modal>
  );
}
