'use client';

import { Alert, Badge, Button, Group, Loader, Paper, SegmentedControl, Stack, Text, Title } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { NutritionSubnav } from '@/components/nutrition/NutritionSubnav';
import { RecipeEditor } from '@/components/nutrition/RecipeEditor';
import { deleteFood, duplicateRecipe, fetchRecipe, fetchRecipes, nutritionErrorInfo, recipeKeys } from '@/lib/nutrition-api';
import type { RecipeScope } from '@/lib/nutrition-types';
import { usePresentationSlot } from '@/lib/presentation-slots';
import { fetchCurrentUser } from '@/lib/use-is-admin';

const SCOPES: { value: RecipeScope; label: string }[] = [
  { value: 'mine', label: 'Minhas' },
  { value: 'public', label: 'Públicas' },
  { value: 'favorites', label: 'Favoritas' },
];
const VAZIO: Record<RecipeScope, string> = {
  mine: 'Você ainda não tem receitas. Crie uma para registrar pratos preparados em casa, com os valores por 100 g calculados dos ingredientes.',
  public: 'Ainda não há receitas públicas no catálogo.',
  favorites: 'Você ainda não favoritou receitas.',
};
import { BRAND } from '@/theme';

const DESCRICAO =
  'Receita é um alimento composto: você lista os ingredientes crus e o peso final pronto, e o app calcula os valores por 100 g. Depois ela entra no diário como qualquer alimento.';

/** `/nutricao/receitas` — minhas receitas (#286). Todo cálculo é da API (`nutrition_recipes`); a tela só exibe. */
export function ReceitasClient() {
  const qc = useQueryClient();
  const Header = usePresentationSlot('nutritionHeader');
  // `null` = lista; `'new'` = receita nova; número = receita aberta para editar.
  const [editing, setEditing] = useState<'new' | number | null>(null);
  const [archiving, setArchiving] = useState<number | null>(null);

  const [scope, setScope] = useState<RecipeScope>('mine');
  // Sessão para a chave do rascunho no aparelho (mesma query de `usePermissions`).
  const me = useQuery({ queryKey: ['current-user'], queryFn: fetchCurrentUser, staleTime: 60_000, retry: false });

  const list = useQuery({ queryKey: recipeKeys.list(scope), queryFn: () => fetchRecipes(scope), retry: false });
  const detail = useQuery({
    queryKey: recipeKeys.detail(typeof editing === 'number' ? editing : 0),
    queryFn: () => fetchRecipe(editing as number),
    enabled: typeof editing === 'number',
    retry: false,
    // Sempre a versão corrente do servidor: o editor parte dela.
    gcTime: 0,
    // Refetch automático que falhasse trocaria o editor pelo alerta e perderia edições.
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

  async function refresh() {
    await qc.invalidateQueries({ queryKey: recipeKeys.all });
    await qc.invalidateQueries({ queryKey: ['nutrition-foods-search'] });
  }

  const archive = useMutation({
    mutationFn: (id: number) => deleteFood(id),
    onSuccess: async () => {
      setArchiving(null);
      await refresh();
    },
  });

  const duplicate = useMutation({
    mutationFn: (id: number) => duplicateRecipe(id),
    onSuccess: async (copia) => {
      await refresh();
      if (copia?.id != null) setEditing(copia.id);
    },
  });

  function open(target: 'new' | number) {
    archive.reset();
    duplicate.reset();
    setEditing(target);
  }

  return (
    <Stack gap="xl">
      {Header ? (
        <Header active="/nutricao/receitas" title="Receitas" description={DESCRICAO} />
      ) : (
        <Stack gap="xs">
          <NutritionSubnav active="/nutricao/receitas" />
          <Title order={1} c={BRAND.textPrimary}>
            Receitas
          </Title>
          <Text c={BRAND.textMuted} size="md">
            {DESCRICAO}
          </Text>
        </Stack>
      )}

      {editing !== null ? (
        detail.isError && !detail.data ? (
          <Stack>
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {nutritionErrorInfo(detail.error, 'Não foi possível carregar a receita.').message}
            </Alert>
            <Group>
              <Button variant="default" onClick={() => setEditing(null)}>
                Voltar
              </Button>
            </Group>
          </Stack>
        ) : typeof editing === 'number' && !detail.data ? (
          <Loader size="sm" aria-label="Carregando a receita" />
        ) : (
          <RecipeEditor
            key={editing}
            recipe={typeof editing === 'number' ? (detail.data ?? null) : null}
            userId={me.data?.id}
            onDuplicate={typeof editing === 'number' ? () => duplicate.mutate(editing) : undefined}
            duplicating={duplicate.isPending}
            onSaved={async () => {
              setEditing(null);
              await refresh();
            }}
            onPublished={async () => {
              setEditing(null);
              await refresh();
            }}
            onCancel={() => setEditing(null)}
          />
        )
      ) : (
        <Stack gap="md">
          <Group justify="space-between">
            <SegmentedControl
              aria-label="Escopo das receitas"
              value={scope}
              onChange={(v) => {
                archive.reset();
                setArchiving(null);
                setScope(v as RecipeScope);
              }}
              data={SCOPES}
            />
            <Button onClick={() => open('new')}>Nova receita</Button>
          </Group>

          {archive.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {nutritionErrorInfo(archive.error, 'Não foi possível arquivar a receita.').message}
            </Alert>
          )}
          {duplicate.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {nutritionErrorInfo(duplicate.error, 'Não foi possível duplicar a receita.').message}
            </Alert>
          )}
          {list.isLoading && <Loader size="sm" aria-label="Carregando as receitas" />}
          {list.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {nutritionErrorInfo(list.error, 'Não foi possível carregar as receitas.').message}
            </Alert>
          )}
          {list.data && list.data.items.length === 0 && <Text c={BRAND.textMuted}>{VAZIO[scope]}</Text>}
          {list.data?.items.map((r) => (
            <Paper key={r.id} withBorder radius="md" p="md" bg={BRAND.bgElevated} data-testid="receita">
              <Group justify="space-between" wrap="wrap">
                <Stack gap={2}>
                  <Group gap="xs">
                    <Text fw={600} c={BRAND.textPrimary}>
                      {r.name}
                    </Text>
                    {r.is_public && (
                      <Badge variant="light" data-testid="selo-publica">
                        Pública
                      </Badge>
                    )}
                    <Badge variant="outline" color="gray" data-testid="autoria">
                      {r.is_own ? 'Sua' : 'Catálogo'}
                    </Badge>
                  </Group>
                  <Text size="sm" c={BRAND.textMuted}>
                    {r.per_100.energy_kcal != null ? `${r.per_100.energy_kcal} kcal por 100 g` : 'Energia não calculada'}
                    {r.version != null && ` · versão ${r.version}`}
                  </Text>
                </Stack>
                {archiving === r.id ? (
                  <Group gap="xs">
                    <Text size="sm">Arquivar {r.name}?</Text>
                    <Button color="red" size="xs" loading={archive.isPending} onClick={() => archive.mutate(r.id)}>
                      Confirmar arquivamento
                    </Button>
                    <Button variant="default" size="xs" onClick={() => {
                        archive.reset();
                        setArchiving(null);
                      }}>
                      Manter
                    </Button>
                  </Group>
                ) : (
                  <Group gap="xs">
                    <Button variant="light" size="xs" onClick={() => open(r.id)} aria-label={`${r.is_own ? 'Editar' : 'Abrir'} ${r.name}`}>
                      {r.is_own ? 'Editar' : 'Abrir'}
                    </Button>
                    <Button
                      variant="default"
                      size="xs"
                      loading={duplicate.isPending && duplicate.variables === r.id}
                      disabled={duplicate.isPending}
                      onClick={() => duplicate.mutate(r.id)}
                      aria-label={`Duplicar ${r.name}`}
                    >
                      Duplicar
                    </Button>
                    {r.is_own && (
                      <Button variant="subtle" color="red" size="xs" onClick={() => {
                          archive.reset();
                          setArchiving(r.id);
                        }} aria-label={`Arquivar ${r.name}`}>
                        Arquivar
                      </Button>
                    )}
                  </Group>
                )}
              </Group>
            </Paper>
          ))}
        </Stack>
      )}
    </Stack>
  );
}
