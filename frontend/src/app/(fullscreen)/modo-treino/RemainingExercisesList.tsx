'use client';

import { Button, Card, Group, Stack, Text } from '@mantine/core';
import { IconChevronDown, IconChevronUp, IconListNumbers } from '@tabler/icons-react';
import { useState } from 'react';
import { BRAND } from '@/theme';

export type RemainingExerciseRow = {
  key: string;
  /** Ordinal de execução ("Exercício N de T") que o item terá. */
  ordinal: number;
  name: string;
  equipmentName?: string | null;
  setsCount?: number | null;
};

/** Quantas linhas aparecem antes do "Ver todos" — mantém o card curto no iPhone. */
export const REMAINING_COLLAPSED_LIMIT = 3;

type Props = {
  rows: ReadonlyArray<RemainingExerciseRow>;
};

/**
 * M64: lista compacta dos exercícios RESTANTES (depois do próximo, que já
 * aparece no `NextExercisePreviewCard`), exibida na pausa de transição entre
 * exercícios. A ordem vem de `findRemainingAfterUpcoming` — a mesma fila que
 * define o próximo. Sem restantes, não renderiza nada.
 *
 * Colapsada em `REMAINING_COLLAPSED_LIMIT` linhas; expandida, a altura é
 * limitada com rolagem interna, para uma ficha longa não esticar a tela.
 */
export function RemainingExercisesList({ rows }: Props) {
  const [expanded, setExpanded] = useState(false);
  if (rows.length === 0) return null;

  const hasMore = rows.length > REMAINING_COLLAPSED_LIMIT;
  const visible = expanded || !hasMore ? rows : rows.slice(0, REMAINING_COLLAPSED_LIMIT);

  return (
    <Card withBorder radius="xl" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Stack gap="xs">
        <Group justify="space-between" align="center" wrap="nowrap">
          <Text size="xs" c={BRAND.textMuted} fw={700} tt="uppercase">
            Depois ({rows.length})
          </Text>
          <IconListNumbers size={16} color={BRAND.textMuted} />
        </Group>

        <ol
          aria-label="Exercícios restantes"
          style={{
            listStyle: 'none',
            margin: 0,
            padding: 0,
            display: 'flex',
            flexDirection: 'column',
            gap: 6,
            maxHeight: expanded ? 280 : undefined,
            overflowY: expanded ? 'auto' : undefined,
          }}
        >
          {visible.map((row) => {
            const details = [
              row.equipmentName || null,
              row.setsCount ? `${row.setsCount} ${row.setsCount === 1 ? 'série' : 'séries'}` : null,
            ].filter(Boolean);
            return (
              <li key={row.key} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', minWidth: 0 }}>
                <Text size="sm" fw={700} c={BRAND.orange} w={24} ta="right" style={{ flexShrink: 0 }}>
                  {row.ordinal}.
                </Text>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <Text size="sm" fw={600} c={BRAND.textPrimary} lh={1.3} truncate="end">
                    {row.name}
                  </Text>
                  {details.length > 0 && (
                    <Text size="xs" c={BRAND.textMuted} lh={1.3} truncate="end">
                      {details.join(' · ')}
                    </Text>
                  )}
                </div>
              </li>
            );
          })}
        </ol>

        {hasMore && (
          <Button
            variant="subtle"
            size="xs"
            color="gray"
            onClick={() => setExpanded((value) => !value)}
            rightSection={expanded ? <IconChevronUp size={14} /> : <IconChevronDown size={14} />}
          >
            {expanded ? 'Ver menos' : `Ver todos (${rows.length})`}
          </Button>
        )}
      </Stack>
    </Card>
  );
}
