'use client';

import { Button, Modal, Stack, Text } from '@mantine/core';
import { IconArrowRight, IconRepeat } from '@tabler/icons-react';
import { SWAP_REMAINING_NOTE } from '@/lib/equipment-change-confirm';
import { BRAND } from '@/theme';

type Props = {
  opened: boolean;
  onClose: () => void;
  remainingSets: number;
  /** Já há série lançada: a troca vale só para as restantes (sem troca retroativa). */
  hasLoggedSets?: boolean;
  onGoToNext: () => void;
  onSwapEquipment: () => void;
};

/**
 * Modal de "Encerrar exercício", oferecido pelo `SetLoggerCard` tanto antes
 * da primeira série quanto com séries já logadas. Duas opções: pular para o
 * próximo item pendente da ficha (reaproveita o fluxo de skip já existente,
 * `SkipExerciseModal`) ou trocar de equipamento mantendo o mesmo grupo
 * muscular, aplicando o alvo de séries restantes (`EquipmentSubstituteModal`,
 * Parte B).
 */
export function FinishExerciseModal({ opened, onClose, remainingSets, hasLoggedSets = false, onGoToNext, onSwapEquipment }: Props) {
  return (
    <Modal opened={opened} onClose={onClose} title="Encerrar exercício" radius="xl" centered>
      <Stack gap="md">
        <Text size="sm" c={BRAND.textMuted}>
          {remainingSets > 0
            ? `Ainda restam ${remainingSets} série(s) planejada(s) para este exercício. O que você quer fazer?`
            : 'O que você quer fazer?'}
        </Text>
        <Button size="lg" radius="xl" h={56} variant="light" leftSection={<IconArrowRight size={20} />} onClick={onGoToNext}>
          Ir para o próximo da lista
        </Button>
        <Button
          size="lg"
          radius="xl"
          h={56}
          variant="light"
          color="orange"
          leftSection={<IconRepeat size={20} />}
          onClick={onSwapEquipment}
        >
          Trocar de equipamento (séries restantes)
        </Button>
        {hasLoggedSets && (
          <Text size="xs" c={BRAND.textMuted} data-testid="swap-remaining-note">
            {SWAP_REMAINING_NOTE}
          </Text>
        )}
      </Stack>
    </Modal>
  );
}
