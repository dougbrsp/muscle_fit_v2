'use client';

import { Alert, Button, Group, Loader, Paper, Stack, Text, Title, UnstyledButton } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { keepPreviousData, useInfiniteQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { foodKeys, listFoodsByLetter, nutritionErrorInfo } from '@/lib/nutrition-api';
import type { FoodItem } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

/** Seção "Alimentos cadastrados" (#195): navegação por letra e lista paginada; ordem, abas e página são da API. */
export function FoodsAlphabet({
  selectedId,
  onSelect,
}: {
  selectedId: number | null;
  onSelect: (item: FoodItem) => void;
}) {
  // undefined = primeira carga: a API escolhe o balde e o devolve em `letter`.
  const [letter, setLetter] = useState<string | undefined>(undefined);
  const query = useInfiniteQuery({
    queryKey: foodKeys.byLetterKey(letter),
    queryFn: ({ pageParam }) => listFoodsByLetter(letter, pageParam),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.has_more && last.limit > 0 ? (last.offset ?? 0) + last.limit : undefined), // resposta malformada: sem NaN nem laço
    placeholderData: keepPreviousData, // mantém as abas enquanto a outra letra carrega
    retry: false,
  });

  const pages = query.data?.pages ?? [];
  // Últimas abas e rótulos recebidos da API: um erro ao trocar de letra não pode apagar a navegação.
  const [nav, setNav] = useState<{ letters: string[]; labels: Record<string, string>; active?: string }>({
    letters: [],
    labels: {},
  });
  const first = pages[0];
  useEffect(() => {
    if (first?.letters?.length) {
      setNav({ letters: first.letters, labels: first.letter_labels ?? {}, active: first.letter });
    }
  }, [first]);
  const letters = nav.letters;
  const ativa = letter ?? nav.active;
  // Dedup por id (alimento criado/excluído entre páginas pode repetir); mantém a ordem da API.
  const items = pages.flatMap((p) => p.items ?? []).filter((it, i, all) => all.findIndex((o) => o.id === it.id) === i);
  const trocando = query.isPlaceholderData;

  return (
    <Paper withBorder radius="md" p="md" bg={BRAND.bgElevated}>
      <Stack gap="sm">
        <Title order={3} c={BRAND.textPrimary}>
          Alimentos cadastrados
        </Title>
        {letters.length > 0 && (
          <Group gap={6} role="group" aria-label="Filtrar por letra">
            {letters.map((l) => (
              <Button
                key={l}
                size="compact-sm"
                variant={l === ativa ? 'filled' : 'light'}
                aria-pressed={l === ativa}
                miw={36}
                onClick={() => l !== ativa && setLetter(l)}
              >
                {nav.labels[l] ?? l}
              </Button>
            ))}
          </Group>
        )}

        {query.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            <Group gap="sm">
              <Text size="sm">{nutritionErrorInfo(query.error, 'Não foi possível carregar os alimentos.').message}</Text>
              <Button size="compact-sm" variant="default" onClick={() => query.refetch()}>
                Tentar de novo
              </Button>
            </Group>
          </Alert>
        )}
        {(query.isPending || trocando) && (
          <Group gap="xs" role="status">
            <Loader size="xs" />
            <Text size="sm" c={BRAND.textMuted}>
              Carregando alimentos...
            </Text>
          </Group>
        )}
        {!query.isPending && !trocando && !query.isError && items.length === 0 && (
          <Text size="sm" c={BRAND.textMuted}>
            Nenhum alimento cadastrado nesta letra.
          </Text>
        )}

        {!trocando && items.length > 0 && (
          <Stack gap={4} component="ul" m={0} p={0} style={{ listStyle: 'none' }}>
            {items.map((item) => (
              <li key={item.id}>
                <UnstyledButton
                  w="100%"
                  p="xs"
                  aria-label={`Ver ${item.name}`}
                  onClick={() => onSelect(item)}
                  bg={item.id === selectedId ? 'var(--mantine-color-brand-light)' : undefined}
                  style={{ borderRadius: 6 }}
                >
                  <Text size="sm" fw={600} c={BRAND.textPrimary}>
                    {item.name}
                  </Text>
                  <Text size="xs" c={BRAND.textMuted}>
                    {[item.brand, item.category].filter(Boolean).join(' · ') || ' '}
                  </Text>
                </UnstyledButton>
              </li>
            ))}
          </Stack>
        )}

        {!trocando && query.hasNextPage && (
          <Button variant="default" onClick={() => query.fetchNextPage()} loading={query.isFetchingNextPage}>
            Carregar mais
          </Button>
        )}
      </Stack>
    </Paper>
  );
}
