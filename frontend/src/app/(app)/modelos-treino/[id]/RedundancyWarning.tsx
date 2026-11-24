'use client';

import { Alert, Badge, Button, Group, List, Stack, Text } from '@mantine/core';
import { IconCopy } from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';
import {
  previewRedundancy,
  type RedundancyPair,
  type RedundancyPreviewItem,
} from '@/lib/exercise-redundancy-api';
import type { ItemState } from '@/lib/workout-plan-template-form';
import { BRAND } from '@/theme';

type Entry = RedundancyPreviewItem & { localId: string };
export type FetchPreview = typeof previewRedundancy;

const keyOf = (i: Pick<RedundancyPreviewItem, 'mapping_id' | 'equipment_id'>) => `${i.mapping_id}:${i.equipment_id ?? ''}`;

/** Itens com exercício escolhido + índice do primeiro cujo exercício/equipamento mudou desde `prev` (null = nada novo). */
export function findChangedTarget(
  prev: Map<string, string>,
  items: Pick<ItemState, 'localId' | 'mapping_id' | 'equipment_id'>[],
): { entries: Entry[]; targetIndex: number | null } {
  const entries: Entry[] = items
    .filter((i) => i.mapping_id != null)
    .map((i) => ({ localId: i.localId, mapping_id: i.mapping_id as number, equipment_id: i.equipment_id }));
  const idx = entries.findIndex((e) => prev.get(e.localId) !== keyOf(e));
  return { entries, targetIndex: idx < 0 ? null : idx };
}

/** Pior par do alvo (a API já ordena do mais redundante); erro da prévia = sem aviso; guarda a identidade do outro item. */
export async function loadRedundancy(
  fetchPreview: FetchPreview,
  entries: Entry[],
  targetIndex: number,
  signal?: AbortSignal,
): Promise<{ pair: RedundancyPair; otherLocalId: string } | null> {
  if (entries.length < 2) return null;
  try {
    const res = await fetchPreview(entries, targetIndex, signal);
    const pair = res.items?.[0];
    if (!pair) return null;
    const other = entries[pair.a_index === targetIndex ? pair.b_index : pair.a_index];
    return other ? { pair, otherLocalId: other.localId } : null;
  } catch (e) {
    if (!(e instanceof DOMException && e.name === 'AbortError')) console.warn('Prévia de redundância indisponível.');
    return null;
  }
}

export function RedundancyAlert({
  pair,
  otherPosition,
  onDismiss,
}: {
  pair: RedundancyPair;
  otherPosition: number;
  onDismiss: () => void;
}) {
  return (
    <Alert color="yellow" variant="light" p="xs" icon={<IconCopy size={16} />} title="Exercício parecido neste dia" role="status">
      <Stack gap={6}>
        <Group gap="xs">
          <Badge size="sm" variant="light" color="yellow">
            {pair.level_label}
          </Badge>
          <Text size="xs" c={BRAND.textPrimary}>
            Parecido com o exercício {otherPosition} deste dia.
          </Text>
        </Group>
        {pair.reasons.length > 0 && (
          <Text size="xs" c={BRAND.textMuted}>
            {pair.reasons.map((r) => r.label).join(' · ')}
          </Text>
        )}
        {(pair.matching.length > 0 || pair.diverging.length > 0) && (
          <Group gap="lg" align="flex-start" wrap="wrap">
            {pair.matching.length > 0 && (
              <div>
                <Text size="xs" fw={600}>
                  Igual
                </Text>
                <List size="xs" spacing={0}>
                  {pair.matching.map((m) => (
                    <List.Item key={m}>{m}</List.Item>
                  ))}
                </List>
              </div>
            )}
            {pair.diverging.length > 0 && (
              <div>
                <Text size="xs" fw={600}>
                  Muda
                </Text>
                <List size="xs" spacing={0}>
                  {pair.diverging.map((m) => (
                    <List.Item key={m}>{m}</List.Item>
                  ))}
                </List>
              </div>
            )}
          </Group>
        )}
        <Group>
          <Button size="compact-xs" variant="subtle" color="gray" onClick={onDismiss}>
            Manter assim
          </Button>
        </Group>
      </Stack>
    </Alert>
  );
}

/** Aviso NÃO bloqueante: após trocar exercício/equipamento de um item do dia, consulta a prévia (debounce) e avisa. */
export function RedundancyWarning({
  items,
  fetchPreview = previewRedundancy,
  debounceMs = 600,
}: {
  items: ItemState[];
  fetchPreview?: FetchPreview;
  debounceMs?: number;
}) {
  const known = useRef<Map<string, string> | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abort = useRef<AbortController | null>(null);
  const [result, setResult] = useState<{ pair: RedundancyPair; otherLocalId: string } | null>(null);

  useEffect(() => {
    const first = known.current === null;
    const { entries, targetIndex } = findChangedTarget(known.current ?? new Map(), items);
    known.current = new Map(entries.map((e) => [e.localId, keyOf(e)]));
    if (first || targetIndex === null) return; // carga inicial e edição de outros campos não consultam
    if (timer.current) clearTimeout(timer.current);
    abort.current?.abort();
    setResult(null);
    timer.current = setTimeout(() => {
      const ctrl = new AbortController();
      abort.current = ctrl;
      void loadRedundancy(fetchPreview, entries, targetIndex, ctrl.signal).then((r) => {
        if (!ctrl.signal.aborted) setResult(r);
      });
    }, debounceMs);
  }, [items, fetchPreview, debounceMs]);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
      abort.current?.abort();
    },
    [],
  );

  // Posição calculada aqui, na lista atual: reordenar corrige o número; item removido some com o aviso.
  const otherIndex = result ? items.findIndex((i) => i.localId === result.otherLocalId) : -1;
  if (!result || otherIndex < 0) return null;
  return <RedundancyAlert pair={result.pair} otherPosition={otherIndex + 1} onDismiss={() => setResult(null)} />;
}
