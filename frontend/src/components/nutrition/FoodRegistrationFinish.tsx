'use client';

import { Alert, Button, Checkbox, Group, Stack, Text, Title } from '@mantine/core';
import { IconAlertTriangle, IconCircleCheck } from '@tabler/icons-react';
import { useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { addFavorite, favoriteKeys, nutritionErrorInfo, promoteFood } from '@/lib/nutrition-api';
import { canOfferPublic, runFinishActions, type FinishDone } from '@/lib/nutrition-food-registration';
import { favoriteErrorLines } from '@/lib/nutrition-favorites';
import type { FoodItem } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

type Props = {
  food: Pick<FoodItem, 'id' | 'name' | 'permissions'>;
  /** Título da etapa: Alimento salvo ou, se já estava no catálogo, Alimento encontrado. */
  title?: string;
  /** Tudo o que foi marcado deu certo (ou nada foi marcado): o fluxo entrega o alimento ao registro/à página. */
  onConclude: () => void;
};

/**
 * Última etapa depois de salvar/achar um alimento: adicionar aos favoritos e, só para quem pode promover
 * (`permissions.can_promote`, regra D-G: só admin), ao cadastro público. As ações só rodam em "Concluir".
 * Se uma falhar, a etapa fica aberta com o erro; o que já deu certo não é refeito.
 */
export function FoodRegistrationFinish({ food, title = 'Alimento salvo', onConclude }: Props) {
  const queryClient = useQueryClient();
  const [favorito, setFavorito] = useState(false);
  const [publico, setPublico] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [erro, setErro] = useState<string[] | null>(null);
  const feito = useRef<FinishDone>({ favorite: false, promote: false });
  const oferecePublico = canOfferPublic(food);

  async function concluir() {
    setEnviando(true);
    setErro(null);
    const resultado = await runFinishActions(
      food.id,
      { favorite: favorito, promote: oferecePublico && publico },
      feito.current,
      { addFavorite: (id) => addFavorite({ food_id: id }), promote: (id) => promoteFood(id) },
    );
    feito.current = resultado.done;
    if (resultado.done.favorite) void queryClient.invalidateQueries({ queryKey: favoriteKeys.all });
    if (resultado.done.favorite || resultado.done.promote) {
      void queryClient.invalidateQueries({ queryKey: ['nutrition-foods-search'] });
    }
    setEnviando(false);
    if (resultado.error !== null) {
      const falhouFavorito = favorito && !resultado.done.favorite;
      setErro(
        falhouFavorito
          ? favoriteErrorLines(resultado.error, 'Não foi possível adicionar aos favoritos.')
          : [nutritionErrorInfo(resultado.error, 'Não foi possível adicionar ao cadastro público.').message],
      );
      return;
    }
    onConclude();
  }

  return (
    <Stack gap="sm" data-testid="etapa-final">
      <Group gap="xs" wrap="nowrap">
        <IconCircleCheck size={20} color="var(--mantine-color-green-6)" />
        <Title order={5} c={BRAND.textPrimary}>
          {title}
        </Title>
      </Group>
      <Text fw={600} c={BRAND.textPrimary}>
        {food.name}
      </Text>
      <Checkbox
        label="Adicionar aos favoritos"
        checked={favorito}
        onChange={(e) => setFavorito(e.currentTarget.checked)}
        disabled={enviando || feito.current.favorite}
      />
      {oferecePublico && (
        <>
          <Checkbox
            label="Adicionar ao cadastro público"
            checked={publico}
            onChange={(e) => setPublico(e.currentTarget.checked)}
            disabled={enviando || feito.current.promote}
          />
          {publico && (
            <Text size="xs" c={BRAND.textMuted}>
              O alimento fica visível para todos e passa a ser editado só por administradores.
            </Text>
          )}
        </>
      )}
      {erro && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} role="alert">
          {erro.map((linha) => (
            <Text key={linha} size="sm">
              {linha}
            </Text>
          ))}
          <Text size="sm">O alimento já está salvo. Tente concluir de novo.</Text>
        </Alert>
      )}
      <Button color="brand" onClick={() => void concluir()} loading={enviando} style={{ minHeight: 44 }}>
        Concluir
      </Button>
    </Stack>
  );
}
