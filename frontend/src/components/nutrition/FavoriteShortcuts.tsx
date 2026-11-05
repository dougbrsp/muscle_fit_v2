'use client';

import { Button, Group, ScrollArea, Stack, Text } from '@mantine/core';
import { IconStarFilled } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { favoriteKeys, fetchFavorites } from '@/lib/nutrition-api';
import { favoriteDefaultSummary } from '@/lib/nutrition-favorites';
import type { FavoriteItem } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

type Props = {
  /** Adiciona o favorito à refeição (com a porção padrão, se houver). */
  onPick: (favorite: FavoriteItem) => void;
  /** Refeição cheia: os atalhos ficam visíveis, mas desligados. */
  disabled?: boolean;
};

/**
 * Atalho dos preferidos no editor de refeição (N3): um toque adiciona o alimento já com a porção
 * padrão. A tela não calcula nada: o preview do editor pede os nutrientes à API (`meal_math`).
 * Sem preferidos, ou sem a migração 102 (503), não ocupa espaço.
 */
export function FavoriteShortcuts({ onPick, disabled = false }: Props) {
  const favoritos = useQuery({ queryKey: favoriteKeys.all, queryFn: fetchFavorites, retry: false, staleTime: 30_000 });
  const itens = favoritos.data?.items ?? [];
  if (itens.length === 0) return null;
  return (
    <Stack gap={4} aria-label="Preferidos">
      <Group gap={6}>
        <IconStarFilled size={14} color="var(--mantine-color-yellow-5)" />
        <Text size="sm" fw={600} c={BRAND.textPrimary}>
          Preferidos
        </Text>
      </Group>
      <ScrollArea type="auto" offsetScrollbars scrollbarSize={6}>
        <Group gap="xs" wrap="nowrap" pb={6}>
          {itens.map((favorito) => {
            const resumo = favoriteDefaultSummary(favorito);
            return (
              <Button
                key={favorito.food.id}
                variant="light"
                color="brand"
                radius="xl"
                size="sm"
                disabled={disabled}
                onClick={() => onPick(favorito)}
                style={{ flex: '0 0 auto', minHeight: 44 }}
                aria-label={`Adicionar ${favorito.food.name}${resumo ? ` (${resumo})` : ''}`}
              >
                {favorito.food.name}
                {resumo ? ` · ${resumo}` : ''}
                {favorito.food.origin ? (
                  <span style={{ marginLeft: 6, opacity: 0.7, fontSize: '0.75em' }}>({favorito.food.origin.label})</span>
                ) : null}
              </Button>
            );
          })}
        </Group>
      </ScrollArea>
    </Stack>
  );
}
