'use client';

import { Alert, Button, Group, Modal, Stack, Text } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconPencil, IconTrash } from '@tabler/icons-react';
import { DISCARD_DRAFT_LABEL, draftBannerText } from '@/lib/template-drafts';
import { BRAND } from '@/theme';

type Props = {
  updatedAt: string | null;
  currentVersionNumber: number | null | undefined;
  discarding: boolean;
  onDiscard: () => void;
};

/**
 * Aviso do topo do builder quando o editor abriu o RASCUNHO do Modelo
 * (migração 067), não a versão vigente. "Descartar rascunho" pede confirmação
 * e o builder recarrega a vigente.
 *
 * Nome inequívoco de propósito: o rascunho da periodização por IA é outra
 * coisa (`PeriodizationDraftPanel.tsx`).
 */
export function TemplateDraftBanner({ updatedAt, currentVersionNumber, discarding, onDiscard }: Props) {
  const [confirmOpened, { open, close }] = useDisclosure(false);
  return (
    <>
      <Alert color="yellow" icon={<IconPencil size={16} />} data-testid="template-draft-banner">
        <Group justify="space-between" align="center" wrap="wrap" gap="sm">
          <Text size="sm" c={BRAND.textPrimary} style={{ overflowWrap: 'anywhere', flex: '1 1 260px' }}>
            {draftBannerText(updatedAt, currentVersionNumber)}
          </Text>
          <Button size="xs" variant="light" color="red" leftSection={<IconTrash size={14} />} onClick={open}>
            {DISCARD_DRAFT_LABEL}
          </Button>
        </Group>
      </Alert>
      <Modal opened={confirmOpened} onClose={close} title={DISCARD_DRAFT_LABEL}>
        <Stack gap="sm">
          <Text size="sm">
            As alterações do rascunho serão perdidas e o editor voltará para a versão publicada. Esta ação não pode ser
            desfeita.
          </Text>
          <Group justify="flex-end" gap="xs" wrap="wrap">
            <Button variant="default" onClick={close}>
              Cancelar
            </Button>
            <Button
              color="red"
              loading={discarding}
              onClick={() => {
                onDiscard();
                close();
              }}
            >
              {DISCARD_DRAFT_LABEL}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
