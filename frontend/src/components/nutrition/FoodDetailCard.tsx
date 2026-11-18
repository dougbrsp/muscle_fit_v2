'use client';

import { Badge, Button, Group, Modal, Paper, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { IconEdit, IconTrash, IconWorld } from '@tabler/icons-react';
import { useState } from 'react';
import { FieldSourcesNote } from './FieldSourcesNote';
import { PortionsEditor } from './PortionsEditor';
import {
  FOOD_BASIS_OPTIONS,
  FOOD_PER_100_FIELDS,
  FOOD_SERVING_FIELDS,
  FOOD_SOURCE_OPTIONS,
  nutritionLabel,
} from '@/lib/nutrition-constants';
import { formatWithUnit } from '@/lib/nutrition-format';
import type { FoodDetail, FoodPortion } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';
import { FoodOriginBadge } from './FoodOriginBadge';

type Props = {
  food: FoodDetail;
  onEdit: () => void;
  onPromote: () => void;
  promoting: boolean;
  onPortionAdded: (portion: FoodPortion) => void;
  /** Exclusão confirmada; rejeita com o erro da API. Sem a prop, o botão não aparece. */
  onDelete?: () => Promise<unknown>;
  deleting?: boolean;
};

/** Alimento escolhido: tabela por 100 g/ml, porção do rótulo, citação, porções e ações permitidas pela API. */
export function FoodDetailCard({ food, onEdit, onPromote, promoting, onPortionAdded, onDelete, deleting = false }: Props) {
  const [confirming, setConfirming] = useState(false);
  // `can_delete` vem da API; payload sem o campo = sem botão.
  const canDelete = food.permissions.can_delete === true && !!onDelete;
  async function confirmar() {
    try {
      await onDelete?.();
    } catch {
      // o erro aparece na tela (Alert do pai); aqui só fecha a confirmação
    }
    setConfirming(false);
  }
  const base = nutritionLabel(FOOD_BASIS_OPTIONS, food.basis);
  const rotuloDaPorcao = FOOD_SERVING_FIELDS.find((campo) => campo.code === 'serving_size')?.label;
  const rotuloDaMedida = FOOD_SERVING_FIELDS.find((campo) => campo.code === 'household_measure')?.label;
  return (
    <Paper withBorder radius="md" p="md" bg={BRAND.bgElevated}>
      <Stack gap="md">
        <Group justify="space-between" align="flex-start" wrap="wrap">
          <div>
            <Title order={3} c={BRAND.textPrimary}>
              {food.name}
            </Title>
            <Group gap={6} mt={4}>
              <FoodOriginBadge origin={food.origin} fallback={nutritionLabel(FOOD_SOURCE_OPTIONS, food.source)} isPublic={food.is_public} />
              {!food.is_public && (
                <Badge variant="outline" color="gray">
                  {food.is_own ? 'Meu (privado)' : 'Privado de outro usuário'}
                </Badge>
              )}
              {[food.brand, food.category].filter(Boolean).map((texto) => (
                <Text key={texto} size="sm" c={BRAND.textMuted}>
                  {texto}
                </Text>
              ))}
            </Group>
          </div>
          <Group gap="xs">
            {food.permissions.can_edit && (
              <Button size="xs" variant="light" color="brand" leftSection={<IconEdit size={14} />} onClick={onEdit}>
                Editar
              </Button>
            )}
            {canDelete && (
              <Button size="xs" variant="light" color="red" leftSection={<IconTrash size={14} />} onClick={() => setConfirming(true)}>
                Excluir alimento
              </Button>
            )}
            {food.permissions.can_promote && (
              <Button size="xs" color="brand" leftSection={<IconWorld size={14} />} onClick={onPromote} loading={promoting}>
                Tornar público
              </Button>
            )}
          </Group>
        </Group>

        <div>
          <Text fw={600} c={BRAND.textPrimary}>
            Tabela nutricional {base ? `(${base})` : ''}
          </Text>
          <SimpleGrid cols={{ base: 2, sm: 3, md: 5 }} spacing="xs" mt={6}>
            {FOOD_PER_100_FIELDS.map((campo) => (
              <div key={campo.code}>
                <Text size="xs" c={BRAND.textMuted}>
                  {campo.label}
                </Text>
                <Text size="sm" fw={600}>
                  {formatWithUnit(food.per_100[campo.code], campo.unit)}
                </Text>
              </div>
            ))}
          </SimpleGrid>
          {food.serving.serving_size !== null && (
            <Text size="sm" mt={6}>
              {rotuloDaPorcao}: {formatWithUnit(food.serving.serving_size, food.basis)}
              {food.serving.household_measure ? ` · ${rotuloDaMedida}: ${food.serving.household_measure}` : ''}
            </Text>
          )}
          {food.field_sources?.length > 0 && (
            <div style={{ marginTop: 6 }}>
              <FieldSourcesNote sources={food.field_sources} />
            </div>
          )}
        </div>

        {food.citation && (
          <Text size="xs" c={BRAND.textMuted}>
            Fonte: {food.citation}
            {food.license ? ` Licença: ${food.license}` : ''}
          </Text>
        )}

        <PortionsEditor food={food} onAdded={onPortionAdded} />
      </Stack>
      <Modal opened={confirming} onClose={() => !deleting && setConfirming(false)} title="Excluir alimento" centered>
        <Stack gap="sm">
          <Text size="sm">
            Excluir <strong>{food.name}</strong>
            {food.barcode ? ` (código de barras ${food.barcode})` : ''}? O alimento deixa de aparecer na busca e nos favoritos.
          </Text>
          <Group justify="flex-end" gap="sm">
            <Button variant="default" onClick={() => setConfirming(false)} disabled={deleting}>
              Cancelar
            </Button>
            <Button color="red" onClick={confirmar} loading={deleting} disabled={deleting}>
              Excluir
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Paper>
  );
}
