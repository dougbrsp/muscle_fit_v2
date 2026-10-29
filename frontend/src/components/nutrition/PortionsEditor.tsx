'use client';

import { Badge, Button, Group, NumberInput, Stack, Table, Text, TextInput } from '@mantine/core';
import { IconPlus } from '@tabler/icons-react';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { NutritionLimitations } from './NutritionLimitations';
import { addFoodPortion, nutritionErrorInfo } from '@/lib/nutrition-api';
import { formatWithUnit, inputNumberOrNull } from '@/lib/nutrition-format';
import type { FoodDetail, FoodPortion } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

type Props = {
  food: FoodDetail;
  onAdded: (portion: FoodPortion) => void;
};

/**
 * Porções do alimento ("1 unidade média = 86 g"), na base dele (g ou ml). A
 * API decide quem cria e de quem é a porção (pública só do admin num alimento
 * público) e valida a quantidade com a faixa do motor.
 */
export function PortionsEditor({ food, onAdded }: Props) {
  const [rotulo, setRotulo] = useState('');
  const [quantidade, setQuantidade] = useState<number | string>('');

  const addMutation = useMutation({
    mutationFn: () => addFoodPortion(food.id, { label: rotulo.trim(), amount: inputNumberOrNull(quantidade) ?? 0 }),
    onSuccess: (porcao) => {
      setRotulo('');
      setQuantidade('');
      onAdded(porcao);
    },
  });
  const erro = addMutation.isError ? nutritionErrorInfo(addMutation.error, 'Não foi possível salvar a porção.') : null;

  return (
    <Stack gap="xs">
      <Text fw={600} c={BRAND.textPrimary}>
        Porções
      </Text>
      {food.portions.length === 0 ? (
        <Text size="sm" c={BRAND.textMuted}>
          Nenhuma porção cadastrada.
        </Text>
      ) : (
        <Table verticalSpacing={4}>
          <Table.Tbody>
            {food.portions.map((porcao) => (
              <Table.Tr key={porcao.id}>
                <Table.Td>
                  <Text size="sm">{porcao.label}</Text>
                </Table.Td>
                <Table.Td ta="right">
                  <Text size="sm">{formatWithUnit(porcao.amount, food.basis)}</Text>
                </Table.Td>
                <Table.Td w={90}>
                  <Badge size="xs" variant="light" color={porcao.is_public ? 'brand' : 'gray'}>
                    {porcao.is_public ? 'Pública' : 'Minha'}
                  </Badge>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      {food.permissions.can_add_portion && (
        <Group gap="sm" align="flex-end" wrap="wrap">
          <TextInput
            label="Nova porção"
            placeholder="Ex.: 1 unidade média"
            value={rotulo}
            onChange={(event) => setRotulo(event.currentTarget.value)}
            maxLength={120}
            style={{ flex: '1 1 200px' }}
          />
          <NumberInput
            label={`Quantidade (${food.basis})`}
            value={quantidade}
            onChange={setQuantidade}
            min={0}
            decimalScale={2}
            decimalSeparator=","
            hideControls
            w={140}
          />
          <Button
            leftSection={<IconPlus size={14} />}
            variant="light"
            color="brand"
            onClick={() => addMutation.mutate()}
            loading={addMutation.isPending}
            disabled={!rotulo.trim() || inputNumberOrNull(quantidade) === null}
          >
            Adicionar
          </Button>
        </Group>
      )}
      {erro && <NutritionLimitations items={erro.items} title={erro.message} tone="error" />}
    </Stack>
  );
}
