'use client';

import { Modal } from '@mantine/core';
import { QuickEntryForm } from './QuickEntryForm';
import type { Meal } from '@/lib/nutrition-types';

/** Lançamento rápido no desktop (modal). No celular a mesma `QuickEntryForm` vai num `BottomSheet`. */
export function QuickEntryDialog({
  opened,
  onClose,
  date,
  today,
  onSaved,
}: {
  opened: boolean;
  onClose: () => void;
  date: string;
  today: string | null;
  onSaved: (meal: Meal) => void;
}) {
  return (
    <Modal opened={opened} onClose={onClose} title="Lançamento rápido" centered size="md">
      <QuickEntryForm date={date} today={today} onSaved={onSaved} onCancel={onClose} />
    </Modal>
  );
}
