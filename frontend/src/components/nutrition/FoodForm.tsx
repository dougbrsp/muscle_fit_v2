'use client';

import { Button, Group, NumberInput, Select, SimpleGrid, Stack, Text, TextInput } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { NutritionLimitations } from './NutritionLimitations';
import { createFood, nutritionErrorInfo, updateFood } from '@/lib/nutrition-api';
import {
  FOOD_BASIS_OPTIONS,
  FOOD_PER_100_FIELDS,
  FOOD_REQUIRED_FIELDS,
  FOOD_SERVING_FIELDS,
  nutritionLabel,
  type NutritionCode,
  type Rdc429Field,
} from '@/lib/nutrition-constants';
import { emptyFoodForm, foodFormFromItem, foodPayloadFromForm, type FoodFormState } from '@/lib/nutrition-food-form';
import type { FoodItem, FoodSaveResponse } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

type Props = {
  food: FoodItem | null;
  /** Cadastro novo já preenchido (ex.: leitura da foto do rótulo, L6). */
  initial?: FoodFormState;
  /** Fonte do cadastro novo (`USER_ENTRY_SOURCES` do motor; ausente = manual). */
  createSource?: NutritionCode;
  onSaved: (food: FoodSaveResponse) => void;
  onCancel: () => void;
};

function servingField(code: string): Rdc429Field | undefined {
  return FOOD_SERVING_FIELDS.find((campo) => campo.code === code);
}

/**
 * Cadastro/edição de alimento (RDC 429). Campos, rótulos, unidades e
 * obrigatórios vêm do motor (JSON gerado, `nutrition-constants.ts`); a API
 * valida (400 com o código do motor) e devolve os avisos (Atwater, subtotal),
 * que não bloqueiam. O alimento novo é privado do usuário (D-G).
 */
export function FoodForm({ food, initial, createSource, onSaved, onCancel }: Props) {
  const [form, setForm] = useState<FoodFormState>(() => (food ? foodFormFromItem(food) : (initial ?? emptyFoodForm())));
  const base = nutritionLabel(FOOD_BASIS_OPTIONS, form.basis);
  const porcao = servingField('serving_size');
  const medida = servingField('household_measure');
  const embalagem = servingField('servings_per_package');

  function update(patch: Partial<FoodFormState>) {
    setForm((atual) => ({ ...atual, ...patch }));
  }

  function updateValue(code: string, value: number | string) {
    setForm((atual) => ({ ...atual, values: { ...atual.values, [code]: value } }));
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = foodPayloadFromForm(form);
      if (food) return updateFood(food.id, payload);
      return createFood(createSource ? { ...payload, source: createSource } : payload);
    },
    onSuccess: onSaved,
  });
  const erro = saveMutation.isError ? nutritionErrorInfo(saveMutation.error, 'Não foi possível salvar o alimento.') : null;

  return (
    <Stack gap="md">
      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
        <TextInput
          label="Nome"
          value={form.name}
          onChange={(event) => update({ name: event.currentTarget.value })}
          required
          maxLength={255}
        />
        <Select
          label="Valores da tabela"
          data={[...FOOD_BASIS_OPTIONS]}
          value={form.basis}
          onChange={(basis) => basis && update({ basis })}
          allowDeselect={false}
        />
        <TextInput
          label="Marca"
          value={form.brand}
          onChange={(event) => update({ brand: event.currentTarget.value })}
          maxLength={120}
        />
        <TextInput
          label="Categoria"
          value={form.category}
          onChange={(event) => update({ category: event.currentTarget.value })}
          maxLength={120}
        />
      </SimpleGrid>

      <div>
        <Text fw={600} c={BRAND.textPrimary}>
          Tabela nutricional {base ? `(${base})` : ''}
        </Text>
        <Text size="xs" c={BRAND.textMuted}>
          Campos da RDC 429/2020. Os marcados com * são obrigatórios; vazio = não informado.
        </Text>
      </div>
      <SimpleGrid cols={{ base: 1, xs: 2, md: 3 }} spacing="md">
        {FOOD_PER_100_FIELDS.map((campo) => (
          <NumberInput
            key={campo.code}
            label={`${campo.label} (${campo.unit})`}
            value={form.values[campo.code] ?? ''}
            onChange={(valor) => updateValue(campo.code, valor)}
            required={FOOD_REQUIRED_FIELDS.includes(campo.code)}
            min={0}
            decimalScale={2}
            decimalSeparator=","
            hideControls
          />
        ))}
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, xs: 2, md: 3 }} spacing="md">
        {porcao && (
          <NumberInput
            label={`${porcao.label} (${form.basis})`}
            value={form.servingSize}
            onChange={(servingSize) => update({ servingSize })}
            min={0}
            decimalScale={2}
            decimalSeparator=","
            hideControls
          />
        )}
        {medida && (
          <TextInput
            label={medida.label}
            placeholder="Ex.: 1 xícara"
            value={form.householdMeasure}
            onChange={(event) => update({ householdMeasure: event.currentTarget.value })}
            maxLength={120}
          />
        )}
        {embalagem && (
          <NumberInput
            label={embalagem.label}
            value={form.servingsPerPackage}
            onChange={(servingsPerPackage) => update({ servingsPerPackage })}
            min={0}
            decimalScale={2}
            decimalSeparator=","
            hideControls
          />
        )}
      </SimpleGrid>

      {erro && <NutritionLimitations items={erro.items} title={erro.message} tone="error" />}
      {erro && erro.items.length === 0 && (
        <Text size="sm" c="red">
          {erro.message}
        </Text>
      )}

      <Group justify="flex-end" gap="sm">
        <Button variant="default" onClick={onCancel} disabled={saveMutation.isPending}>
          Cancelar
        </Button>
        <Button color="brand" onClick={() => saveMutation.mutate()} loading={saveMutation.isPending}>
          {food ? 'Salvar alterações' : 'Cadastrar alimento'}
        </Button>
      </Group>
    </Stack>
  );
}
