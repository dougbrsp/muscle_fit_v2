'use client';

import { Text } from '@mantine/core';
import { BRAND } from '@/theme';

/** Texto da fórmula da regra, escrito só pelo servidor (`text` / `POST /describe`); sem ele, "gerando". */
export function FormulaPreview({ text }: { text: string | null | undefined }) {
  return (
    <Text size="xs" c={text ? undefined : BRAND.textMuted} data-rule-text style={{ overflowWrap: 'anywhere' }}>
      {text ?? 'Gerando o texto da regra…'}
    </Text>
  );
}
