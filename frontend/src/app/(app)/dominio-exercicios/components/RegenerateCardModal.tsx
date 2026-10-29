'use client';

import { Alert, Button, Group, Modal, Stack, Text, Textarea } from '@mantine/core';
import { IconAlertTriangle, IconRefresh } from '@tabler/icons-react';
import { useState } from 'react';
import { EXERCISE_CARD_INSTRUCTION_MAX_CHARS, exerciseCardErrorText } from '@/lib/exercise-cards';
import { BRAND } from '@/theme';

type RegenerateCardFormProps = {
  description?: React.ReactNode;
  confirmLabel: string;
  onCancel: () => void;
  onConfirm: (instruction: string) => void;
  loading: boolean;
  error: unknown;
};

/**
 * Corpo do modal (separado para o teste por `renderToStaticMarkup`, que não
 * renderiza o portal do `Modal`). O texto nasce vazio a cada abertura: o
 * `Modal` desmonta o conteúdo ao fechar.
 */
export function RegenerateCardForm({ description, confirmLabel, onCancel, onConfirm, loading, error }: RegenerateCardFormProps) {
  const [instruction, setInstruction] = useState('');
  const length = instruction.trim().length;
  const tooLong = length > EXERCISE_CARD_INSTRUCTION_MAX_CHARS;

  return (
    <Stack gap="sm">
      {description && (
        <Text size="sm" c={BRAND.textMuted}>
          {description}
        </Text>
      )}
      <Textarea
        label="O que gostaria de alterar?"
        description="Opcional. Vai junto para a IA nesta geração — descreva a posição, o equipamento ou o movimento."
        placeholder="Ex.: na posição inicial, antebraços apoiados nas almofadas e pés nos degraus."
        autosize
        minRows={3}
        maxRows={8}
        maxLength={EXERCISE_CARD_INSTRUCTION_MAX_CHARS}
        value={instruction}
        onChange={(event) => setInstruction(event.currentTarget.value)}
        disabled={loading}
        data-autofocus
      />
      <Text size="xs" ta="right" c={tooLong ? 'red' : BRAND.textMuted} data-instruction-counter>
        {length}/{EXERCISE_CARD_INSTRUCTION_MAX_CHARS}
      </Text>
      {Boolean(error) && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {exerciseCardErrorText(error, 'Não foi possível pedir a nova geração.')}
        </Alert>
      )}
      <Group justify="flex-end">
        <Button variant="default" onClick={onCancel} disabled={loading}>
          Cancelar
        </Button>
        <Button
          color="brand"
          leftSection={<IconRefresh size={16} />}
          loading={loading}
          disabled={tooLong}
          onClick={() => onConfirm(instruction.trim())}
        >
          {confirmLabel}
        </Button>
      </Group>
    </Stack>
  );
}

/**
 * "Gerar novamente" / "Gerar nova versão" do card ilustrado: pede (opcional)
 * o que mudar e devolve a instrução (≤ 300 caracteres, mesmo teto do backend).
 */
export function RegenerateCardModal({
  opened,
  title,
  onClose,
  ...form
}: Omit<RegenerateCardFormProps, 'onCancel'> & { opened: boolean; title: string; onClose: () => void }) {
  return (
    <Modal opened={opened} onClose={onClose} title={title} size="md" centered>
      <RegenerateCardForm {...form} onCancel={onClose} />
    </Modal>
  );
}
