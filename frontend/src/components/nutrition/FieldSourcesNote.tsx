'use client';

import { Stack, Text, Tooltip } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import type { FoodFieldSource } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

// `sources` ausente (resposta de uma API anterior ao M85, ex.: rollback só do backend) = nenhuma origem.
/** Nota discreta com a origem dos valores que vieram de outra fonte (M85); nota e detalhe vêm prontos da API. */
export function FieldSourcesNote({ sources }: { sources?: FoodFieldSource[] }) {
  if (!sources?.length) return null;
  return (
    <Stack gap={2}>
      {sources.map((origem) => (
        <Tooltip key={`${origem.source}-${origem.source_id}`} label={origem.detail} multiline w={360} withArrow>
          <Text size="xs" c={BRAND.textMuted} style={{ cursor: 'help' }}>
            {origem.note}
          </Text>
        </Tooltip>
      ))}
    </Stack>
  );
}

/** Ícone com a mesma nota no tooltip, para linhas de tabela (itens da refeição). */
export function FieldSourcesIcon({ sources }: { sources?: FoodFieldSource[] }) {
  if (!sources?.length) return null;
  return (
    <Tooltip label={sources.map((origem) => origem.note).join(' ')} multiline w={340} withArrow>
      <IconInfoCircle size={14} color="var(--mantine-color-gray-5)" aria-label="Origem dos valores" />
    </Tooltip>
  );
}
