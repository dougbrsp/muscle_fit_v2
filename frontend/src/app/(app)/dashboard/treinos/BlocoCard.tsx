'use client';

import { Group, Paper, Text, Title } from '@mantine/core';
import type { ReactNode } from 'react';
import { BRAND } from '@/theme';

export const CARD_STYLE = { background: BRAND.bgElevated, borderColor: BRAND.border };
export const TILE_STYLE = { background: BRAND.bgSurface, borderColor: BRAND.border };

/** Moldura comum dos blocos de `/dashboard/treinos` (título, legenda, ação). */
export function BlocoCard({
  titulo,
  descricao,
  acao,
  children,
}: {
  titulo: string;
  descricao?: ReactNode;
  acao?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <Paper withBorder radius="lg" p="md" style={CARD_STYLE}>
      <Group justify="space-between" align="flex-start" wrap="wrap" mb="sm" gap="sm">
        <div>
          <Title order={4} c={BRAND.textPrimary}>
            {titulo}
          </Title>
          {descricao && (
            <Text size="sm" c={BRAND.textMuted}>
              {descricao}
            </Text>
          )}
        </div>
        {acao}
      </Group>
      {children}
    </Paper>
  );
}
