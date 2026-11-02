'use client';

import {
  ActionIcon,
  Alert,
  Button,
  Group,
  Modal,
  NumberInput,
  Paper,
  Select,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { IconAlertTriangle, IconArrowDown, IconArrowUp, IconPencil, IconStarFilled, IconTrash } from '@tabler/icons-react';
import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { FAVORITE_TOGGLE_KEY, FoodSearch } from './FoodSearch';
import { NutritionLimitations } from './NutritionLimitations';
import {
  addFavorite,
  favoriteKeys,
  fetchFavorites,
  fetchFood,
  foodKeys,
  nutritionErrorInfo,
  removeFavorite,
  reorderFavorites,
  updateFavoriteDefault,
} from '@/lib/nutrition-api';
import { UNIT_OPTIONS } from '@/lib/nutrition-constants';
import {
  favoriteErrorLines,
  favoriteDefaultFormFrom,
  favoriteDefaultPayload,
  favoriteDefaultSummary,
  moveFavorite,
  type FavoriteDefaultForm,
} from '@/lib/nutrition-favorites';
import type { FavoriteItem, FavoritesResponse } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

const TOUCH_TARGET_PX = 44;
const MOBILE_QUERY = '(max-width: 48em)';

function DefaultPortionModal({ favorite, onClose }: { favorite: FavoriteItem; onClose: () => void }) {
  const queryClient = useQueryClient();
  const fullScreen = useMediaQuery(MOBILE_QUERY) ?? false;
  const [form, setForm] = useState<FavoriteDefaultForm>(() => favoriteDefaultFormFrom(favorite));
  const detalhe = useQuery({
    queryKey: foodKeys.detail(favorite.food.id),
    queryFn: () => fetchFood(favorite.food.id),
    retry: false,
    staleTime: 60_000,
  });
  const porcoes = (detalhe.data?.portions ?? []).map((porcao) => ({
    value: String(porcao.id),
    label: `${porcao.label} (${porcao.amount} ${detalhe.data?.basis ?? favorite.food.basis})`,
  }));
  const salvar = useMutation({
    mutationFn: () => updateFavoriteDefault(favorite.food.id, favoriteDefaultPayload(form)),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: favoriteKeys.all });
      onClose();
    },
  });
  const erro = salvar.isError ? nutritionErrorInfo(salvar.error, 'Não foi possível salvar a porção padrão.') : null;

  return (
    <Modal
      opened
      onClose={onClose}
      title={`Porção padrão: ${favorite.food.name}`}
      fullScreen={fullScreen}
      centered={!fullScreen}
      styles={{
        content: fullScreen
          ? { paddingTop: 'env(safe-area-inset-top)', paddingBottom: 'env(safe-area-inset-bottom)' }
          : undefined,
      }}
    >
      <Stack gap="sm">
        <Text size="sm" c={BRAND.textMuted}>
          É o que entra na refeição quando você toca neste preferido. Deixe a quantidade vazia para não ter porção
          padrão. Os nutrientes são calculados pelo diário, como em qualquer item.
        </Text>
        <NumberInput
          label="Quantidade"
          value={form.quantity}
          onChange={(valor) => setForm((atual) => ({ ...atual, quantity: valor }))}
          min={0}
          decimalScale={3}
          inputMode="decimal"
        />
        <Select
          label="Unidade"
          data={[...UNIT_OPTIONS]}
          value={form.unit}
          onChange={(valor) => setForm((atual) => ({ ...atual, unit: valor, portionId: valor ? atual.portionId : null }))}
          clearable
          placeholder="Escolha a unidade"
        />
        <Select
          label="Porção cadastrada (opcional)"
          description="Vale com a unidade porção ou unidade."
          data={porcoes}
          value={form.portionId != null ? String(form.portionId) : null}
          onChange={(valor) => setForm((atual) => ({ ...atual, portionId: valor ? Number(valor) : null }))}
          clearable
          placeholder="Nenhuma"
          nothingFoundMessage="Alimento sem porção cadastrada"
        />
        {erro && <NutritionLimitations items={erro.items} title={erro.message} tone="error" />}
        {erro && erro.items.length === 0 && (
          <Text size="sm" c="red">
            {erro.message}
          </Text>
        )}
        <Group justify="flex-end" gap="sm">
          <Button variant="default" onClick={onClose} disabled={salvar.isPending} style={{ minHeight: TOUCH_TARGET_PX }}>
            Cancelar
          </Button>
          <Button color="brand" onClick={() => salvar.mutate()} loading={salvar.isPending} style={{ minHeight: TOUCH_TARGET_PX }}>
            Salvar
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

/**
 * Edição da lista de preferidos (N3), em `/nutricao/favoritos`: adicionar pela busca (estrela),
 * reordenar (subir/descer, com alvo de toque de 44 px), editar a porção padrão e remover.
 * Ordem, teto e validação da porção são do motor (`domain/nutrition/favorites`); a tela só
 * mostra o que a API devolve.
 */
export function FavoritesManager() {
  const queryClient = useQueryClient();
  const [editando, setEditando] = useState<FavoriteItem | null>(null);
  const favoritos = useQuery({ queryKey: favoriteKeys.all, queryFn: fetchFavorites, retry: false });
  const itens = favoritos.data?.items ?? [];

  // Toda alteração da lista: para uma leitura em voo (ela devolveria a ordem velha por cima), e ao
  // terminar, com sucesso ou erro, relê a lista (um erro de ordem velha 400/409 se corrige sozinho).
  const preparar = () => queryClient.cancelQueries({ queryKey: favoriteKeys.all });
  const relerLista = () => queryClient.invalidateQueries({ queryKey: favoriteKeys.all });
  const guardar = (resposta: FavoritesResponse) => queryClient.setQueryData(favoriteKeys.all, resposta);
  const reordenar = useMutation({
    mutationFn: reorderFavorites,
    onMutate: preparar,
    onSuccess: guardar,
    onSettled: relerLista,
  });
  const remover = useMutation({
    mutationFn: (foodId: number) => removeFavorite(foodId),
    onMutate: preparar,
    onSettled: relerLista,
  });
  const adicionar = useMutation({
    mutationFn: (foodId: number) => addFavorite({ food_id: foodId }),
    onMutate: preparar,
    onSettled: relerLista,
  });
  // A estrela da busca (dentro do FoodSearch) também altera a lista: trava os botões enquanto grava.
  const alternando = useIsMutating({ mutationKey: FAVORITE_TOGGLE_KEY }) > 0;
  const ocupado = reordenar.isPending || remover.isPending || adicionar.isPending || alternando || favoritos.isFetching;
  const erro = [favoritos, reordenar, remover, adicionar].find((m) => m.isError);

  function limparErros() {
    reordenar.reset();
    remover.reset();
    adicionar.reset();
  }

  function mover(foodId: number, delta: -1 | 1) {
    limparErros();
    reordenar.mutate(moveFavorite(itens.map((i) => i.food.id), foodId, delta));
  }

  return (
    <Stack gap="lg">
      <Paper withBorder radius="md" p="md" bg={BRAND.bgElevated}>
        <Stack gap="sm">
          <Title order={4} c={BRAND.textPrimary}>
            Adicionar à lista
          </Title>
          <Text size="sm" c={BRAND.textMuted}>
            Busque um alimento e toque na estrela, ou toque na linha para adicioná-lo agora.
          </Text>
          <FoodSearch
            isAdmin={false}
            selectedId={null}
            onSelect={(alimento) => {
              limparErros();
              adicionar.mutate(alimento.id);
            }}
          />
        </Stack>
      </Paper>

      {erro && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} withCloseButton onClose={limparErros}>
          {favoriteErrorLines(erro.error, 'Não foi possível atualizar os preferidos.').map((linha) => (
            <Text key={linha} size="sm">
              {linha}
            </Text>
          ))}
        </Alert>
      )}

      <Paper withBorder radius="md" p="md" bg={BRAND.bgElevated}>
        <Stack gap="sm">
          <Group gap={6}>
            <IconStarFilled size={16} color="var(--mantine-color-yellow-5)" />
            <Title order={4} c={BRAND.textPrimary}>
              Meus preferidos
            </Title>
            {favoritos.data && (
              <Text size="xs" c={BRAND.textMuted}>
                {itens.length} de {favoritos.data.max_items}
              </Text>
            )}
          </Group>
          {favoritos.isSuccess && itens.length === 0 && (
            <Text size="sm" c={BRAND.textMuted}>
              Nenhum preferido ainda. Eles aparecem primeiro na busca do diário e como atalho ao montar a refeição.
            </Text>
          )}
          {itens.map((favorito, indice) => {
            const resumo = favoriteDefaultSummary(favorito);
            return (
              <Group
                key={favorito.food.id}
                justify="space-between"
                wrap="nowrap"
                gap="xs"
                style={{ borderTop: `1px solid ${BRAND.border}`, paddingTop: 8 }}
              >
                <Stack gap={0} style={{ minWidth: 0, flex: 1 }}>
                  <Text size="sm" fw={600} c={BRAND.textPrimary} truncate>
                    {favorito.food.name}
                  </Text>
                  <Text size="xs" c={BRAND.textMuted} truncate>
                    {[favorito.food.brand, resumo ? `Porção padrão: ${resumo}` : 'Sem porção padrão']
                      .filter(Boolean)
                      .join(' · ')}
                  </Text>
                </Stack>
                <Group gap={2} wrap="nowrap">
                  <ActionIcon
                    variant="subtle"
                    color="gray"
                    aria-label={`Subir ${favorito.food.name}`}
                    disabled={indice === 0 || ocupado}
                    onClick={() => mover(favorito.food.id, -1)}
                    style={{ minWidth: TOUCH_TARGET_PX, minHeight: TOUCH_TARGET_PX }}
                  >
                    <IconArrowUp size={18} />
                  </ActionIcon>
                  <ActionIcon
                    variant="subtle"
                    color="gray"
                    aria-label={`Descer ${favorito.food.name}`}
                    disabled={indice === itens.length - 1 || ocupado}
                    onClick={() => mover(favorito.food.id, 1)}
                    style={{ minWidth: TOUCH_TARGET_PX, minHeight: TOUCH_TARGET_PX }}
                  >
                    <IconArrowDown size={18} />
                  </ActionIcon>
                  <ActionIcon
                    variant="subtle"
                    color="brand"
                    aria-label={`Editar a porção padrão de ${favorito.food.name}`}
                    onClick={() => setEditando(favorito)}
                    style={{ minWidth: TOUCH_TARGET_PX, minHeight: TOUCH_TARGET_PX }}
                  >
                    <IconPencil size={18} />
                  </ActionIcon>
                  <ActionIcon
                    variant="subtle"
                    color="red"
                    aria-label={`Remover ${favorito.food.name} dos preferidos`}
                    disabled={ocupado}
                    onClick={() => {
                      limparErros();
                      remover.mutate(favorito.food.id);
                    }}
                    style={{ minWidth: TOUCH_TARGET_PX, minHeight: TOUCH_TARGET_PX }}
                  >
                    <IconTrash size={18} />
                  </ActionIcon>
                </Group>
              </Group>
            );
          })}
        </Stack>
      </Paper>

      {editando && <DefaultPortionModal key={editando.food.id} favorite={editando} onClose={() => setEditando(null)} />}
    </Stack>
  );
}
