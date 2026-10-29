'use client';

import { Badge, Group, Modal, Stack, Text, UnstyledButton } from '@mantine/core';
import { IconPhoto } from '@tabler/icons-react';
import { BRAND } from '@/theme';

export type SkipExerciseModalItem = {
  workoutPlanExerciseId: number;
  exerciseName: string;
  muscleGroup?: string | null;
  equipmentName?: string | null;
  equipmentPhotoUrl?: string | null;
};

type Props = {
  opened: boolean;
  onClose: () => void;
  items: SkipExerciseModalItem[];
  onSelect: (workoutPlanExerciseId: number) => void;
};

/**
 * Modal de escolha de próximo exercício, aberto pelos pontos de disparo de
 * "Pular" (`SetLoggerCard` e `NextExercisePreviewCard`, Parte B). Lista os
 * itens da ficha ainda pendentes (o exercício sendo pulado já vem excluído
 * por quem chama) — a escolha define a prioridade do próximo item, com
 * precedência sobre a ordem/sequence original da ficha.
 */
export function SkipExerciseModal({ opened, onClose, items, onSelect }: Props) {
  return (
    <Modal opened={opened} onClose={onClose} title="Fazer qual exercício agora?" radius="xl" centered>
      <Stack gap="xs">
        {items.length === 0 && (
          <Text size="sm" c={BRAND.textMuted}>
            Não há outro exercício pendente nesta ficha.
          </Text>
        )}
        {items.map((item) => (
          <UnstyledButton
            key={item.workoutPlanExerciseId}
            onClick={() => onSelect(item.workoutPlanExerciseId)}
            p="sm"
            style={{ borderRadius: 'var(--mantine-radius-xl)', border: `1px solid ${BRAND.border}` }}
          >
            <Group gap="sm" wrap="nowrap" align="center">
              {item.equipmentPhotoUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={item.equipmentPhotoUrl}
                  alt={item.equipmentName || item.exerciseName}
                  width={64}
                  height={64}
                  style={{ objectFit: 'cover', borderRadius: 12, flexShrink: 0 }}
                />
              ) : (
                <IconPhoto size={64} color={BRAND.textMuted} style={{ flexShrink: 0 }} />
              )}
              <div>
                <Text fw={800} fz="md" lh={1.2} c={BRAND.textPrimary}>
                  {item.exerciseName}
                </Text>
                <Group gap={6} mt={4}>
                  {item.muscleGroup && (
                    <Badge size="sm" variant="light" color="brand">
                      {item.muscleGroup}
                    </Badge>
                  )}
                  {item.equipmentName && (
                    <Badge size="sm" variant="light" color="grape">
                      {item.equipmentName}
                    </Badge>
                  )}
                </Group>
              </div>
            </Group>
          </UnstyledButton>
        ))}
      </Stack>
    </Modal>
  );
}
