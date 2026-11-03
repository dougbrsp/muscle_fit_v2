'use client';

import { Stack, Text, Title } from '@mantine/core';
import { FavoritesManager } from '@/components/nutrition/FavoritesManager';
import { NutritionSubnav } from '@/components/nutrition/NutritionSubnav';
import { BRAND } from '@/theme';

/**
 * `/nutricao/favoritos` — edição da lista de alimentos preferidos (N3). A lista é pessoal: os
 * preferidos aparecem primeiro na busca do diário e como atalho no editor de refeição, com a
 * porção padrão de cada um. Ordem, teto e validação são da API (`domain/nutrition/favorites`).
 */
export function FavoritosClient() {
  return (
    <Stack gap="xl">
      <Stack gap="xs">
        <NutritionSubnav active="/nutricao/favoritos" />
        <Title order={1} c={BRAND.textPrimary}>
          Preferidos
        </Title>
        <Text c={BRAND.textMuted} size="md">
          Os alimentos que você mais usa. Eles ganham um atalho ao montar a refeição, já com a porção que você costuma
          comer.
        </Text>
      </Stack>
      <FavoritesManager />
    </Stack>
  );
}
