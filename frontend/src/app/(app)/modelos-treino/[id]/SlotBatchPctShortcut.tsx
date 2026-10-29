'use client';

import { Button, Group, NumberInput, Text, Tooltip } from '@mantine/core';
import { useState } from 'react';
import { EMPTY_BATCH_PCT_DRAFT, hasPendingBatchPct, type BatchPctDraft } from '@/lib/slot-batch-pct';
import { BRAND } from '@/theme';

// ---------------------------------------------------------------------------
// Atalho "Min %/Max % em lote" do header do slot (modo volume_mode=
// 'percent_budget') — puramente visual/local, NÃO é enviado ao backend nem
// persistido entre carregamentos.
//
// M1: a propagação deixou de acontecer ao digitar/sair do campo e passou a
// exigir o botão "Aplicar". Enquanto houver valor digitado e não aplicado o
// botão fica habilitado (filled) e uma legenda avisa da alteração pendente;
// os exercícios do slot permanecem intocados. Se o usuário digitar e sair da
// tela sem clicar, nada é aplicado — comportamento deliberado: o rascunho é
// local ao componente e morre junto com ele.
type SlotBatchPctShortcutProps = {
  onApply: (draft: BatchPctDraft) => void;
};

export function SlotBatchPctShortcut({ onApply }: SlotBatchPctShortcutProps) {
  const [draft, setDraft] = useState<BatchPctDraft>(EMPTY_BATCH_PCT_DRAFT);
  const [applied, setApplied] = useState<BatchPctDraft>(EMPTY_BATCH_PCT_DRAFT);
  const pending = hasPendingBatchPct(draft, applied);

  function handleApply() {
    if (!pending) return;
    onApply(draft);
    setApplied(draft);
  }

  return (
    // `wrap`: na grade de 4 colunas o slot tem ~280 px; o botão desce de linha
    // em vez de vazar para fora do card.
    <Group gap={4} wrap="wrap" align="flex-end" mb="xs">
      <Tooltip
        label="Preenchimento em lote: o % só é copiado para os grupos musculares deste slot quando você clicar em Aplicar. Não é salvo — só um atalho de preenchimento."
        multiline
        w={240}
      >
        <Group gap={4} wrap="nowrap" align="flex-end">
          <NumberInput
            size="xs"
            label="Min % (lote)"
            placeholder="—"
            value={draft.priority_min_pct}
            onChange={(v) => setDraft((prev) => ({ ...prev, priority_min_pct: v === '' ? '' : Number(v) }))}
            min={0}
            max={100}
            w={90}
          />
          <NumberInput
            size="xs"
            label="Max % (lote)"
            placeholder="—"
            value={draft.priority_max_pct}
            onChange={(v) => setDraft((prev) => ({ ...prev, priority_max_pct: v === '' ? '' : Number(v) }))}
            min={0}
            max={100}
            w={90}
          />
        </Group>
      </Tooltip>
      <Button size="xs" variant={pending ? 'filled' : 'light'} color="brand" disabled={!pending} onClick={handleApply}>
        Aplicar
      </Button>
      {pending && (
        <Text size="xs" c={BRAND.textMuted}>
          Alteração pendente
        </Text>
      )}
    </Group>
  );
}
