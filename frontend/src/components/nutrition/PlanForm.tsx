'use client';

import { Button, Group, NumberInput, Select, SimpleGrid, Stack, Switch, Text, Textarea } from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { NutritionLimitations } from './NutritionLimitations';
import { createNutritionPlan, nutritionErrorInfo, updateNutritionPlan } from '@/lib/nutrition-api';
import {
  ACTIVITY_LEVEL_OPTIONS,
  BMR_FORMULA_OPTIONS,
  DEFAULT_BMR_FORMULA,
  DEFAULT_TDEE_MODE,
  NUTRITION_FORM_RANGES,
  PROTEIN_BASIS_OPTIONS,
  SEX_OPTIONS,
  STRATEGY_OPTIONS,
  TDEE_MODE_OPTIONS,
  fieldAppliesToStrategy,
  nutritionLabel,
  nutritionNotice,
  strategyFormRanges,
  type NutritionCode,
  type StrategyFormRanges,
} from '@/lib/nutrition-constants';
import { inputNumberOrNull, percentToShare, shareToPercent } from '@/lib/nutrition-format';
import type { NutritionPlan, NutritionPlanSettingsPayload } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

type NumberField = number | string;

type FormState = {
  strategy: NutritionCode | null;
  advanced: boolean;
  protein: NumberField;
  fatPercent: NumberField;
  ratePercent: NumberField;
  surplusPercent: NumberField;
  carbMin: NumberField;
  bmrFormula: NutritionCode;
  tdeeMode: NutritionCode;
  activityLevel: NutritionCode | null;
  meals: NumberField;
  goalBodyFat: NumberField;
  goalWeight: NumberField;
  notes: string;
};

function asField(value: number | null | undefined): NumberField {
  return value == null ? '' : value;
}

/** Defaults da estratégia exatamente como o motor exporta (`form_ranges`). */
function strategyDefaults(ranges: StrategyFormRanges | null, advanced: boolean): Partial<FormState> {
  if (!ranges) return {};
  const superavit = advanced ? ranges.surplus_share_default_advanced : ranges.surplus_share_default;
  return {
    protein: asField(ranges.protein_g_per_kg_default),
    fatPercent: asField(shareToPercent(ranges.fat_kcal_share_default)),
    ratePercent: asField(shareToPercent(ranges.weekly_rate_share_default ?? null)),
    surplusPercent: asField(shareToPercent(superavit ?? null)),
    carbMin: asField(ranges.carb_min_g_per_kg_default ?? null),
  };
}

function initialState(plan: NutritionPlan | null): FormState {
  if (plan) {
    return {
      strategy: plan.strategy,
      advanced: plan.advanced,
      protein: asField(plan.protein_g_per_kg),
      fatPercent: asField(shareToPercent(plan.fat_kcal_share)),
      ratePercent: asField(shareToPercent(plan.weekly_rate_share)),
      surplusPercent: asField(shareToPercent(plan.surplus_share)),
      carbMin: asField(plan.carb_min_g_per_kg),
      bmrFormula: plan.bmr_formula,
      tdeeMode: plan.tdee_mode,
      activityLevel: plan.activity_level,
      meals: asField(plan.meals_per_day),
      goalBodyFat: asField(plan.goal_body_fat_percent),
      goalWeight: asField(plan.goal_weight_kg),
      notes: plan.notes ?? '',
    };
  }
  return {
    strategy: null,
    advanced: false,
    protein: '',
    fatPercent: '',
    ratePercent: '',
    surplusPercent: '',
    carbMin: '',
    bmrFormula: DEFAULT_BMR_FORMULA,
    tdeeMode: DEFAULT_TDEE_MODE,
    activityLevel: null,
    meals: '',
    goalBodyFat: '',
    goalWeight: '',
    notes: '',
  };
}

/** Limites do campo em % a partir da faixa em fração do motor. */
function percentBounds(faixa: ReadonlyArray<number> | undefined): { min?: number; max?: number } {
  if (!faixa) return {};
  return { min: shareToPercent(faixa[0]) ?? undefined, max: shareToPercent(faixa[1]) ?? undefined };
}

function bounds(faixa: ReadonlyArray<number> | undefined): { min?: number; max?: number } {
  return faixa ? { min: faixa[0], max: faixa[1] } : {};
}

function rangeHint(faixa: ReadonlyArray<number> | undefined, suffix: string, asPercent = false): string | undefined {
  if (!faixa) return undefined;
  const limites = asPercent ? percentBounds(faixa) : bounds(faixa);
  return `Faixa da fonte: ${limites.min?.toLocaleString('pt-BR')}–${limites.max?.toLocaleString('pt-BR')}${suffix}`;
}

function stopBodyFatHint(strategy: NutritionCode | null): string | undefined {
  const tabela = (NUTRITION_FORM_RANGES.stop_body_fat_percent as Record<string, Record<string, number> | undefined>)[
    strategy ?? ''
  ];
  if (!tabela) return 'Vazio: sem meta de % de gordura.';
  const partes = Object.entries(tabela).map(([sexo, valor]) => `${nutritionLabel(SEX_OPTIONS, sexo)} ${valor}%`);
  return `Vazio: padrão do motor (${partes.join(' / ')}).`;
}

type Props = {
  userId: number;
  plan: NutritionPlan | null;
  onSaved: (plan: NutritionPlan) => void;
  onCancel: () => void;
};

/**
 * Formulário do rascunho (admin/PT). Opções, faixas e defaults vêm do motor
 * (`motor-constants.generated.json` via `nutrition-constants.ts`); a API valida
 * de novo e responde 400 com o código do motor. Frações (gordura, ritmo,
 * superávit) aparecem em % só para a tela.
 */
export function PlanForm({ userId, plan, onSaved, onCancel }: Props) {
  const [form, setForm] = useState<FormState>(() => initialState(plan));
  const ranges = strategyFormRanges(form.strategy);
  const surplusRange = form.advanced ? ranges?.surplus_share_advanced : ranges?.surplus_share;
  // Quais campos a estratégia usa: `STRATEGY_ONLY_FIELDS` do motor (via JSON gerado).
  const usaRitmo = fieldAppliesToStrategy('weekly_rate_share', form.strategy);
  const usaSuperavit = fieldAppliesToStrategy('surplus_share', form.strategy);
  const usaCarboMinimo = fieldAppliesToStrategy('carb_min_g_per_kg', form.strategy);

  function update(patch: Partial<FormState>) {
    setForm((atual) => ({ ...atual, ...patch }));
  }

  function changeStrategy(strategy: NutritionCode | null) {
    update({ strategy, advanced: false, ...strategyDefaults(strategyFormRanges(strategy), false) });
  }

  function changeAdvanced(advanced: boolean) {
    const superavit = advanced ? ranges?.surplus_share_default_advanced : ranges?.surplus_share_default;
    update({ advanced, surplusPercent: asField(shareToPercent(superavit ?? null)) });
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload: NutritionPlanSettingsPayload = {
        strategy: form.strategy ?? '',
        advanced: usaSuperavit ? form.advanced : false,
        protein_g_per_kg: inputNumberOrNull(form.protein),
        fat_kcal_share: percentToShare(form.fatPercent),
        weekly_rate_share: usaRitmo ? percentToShare(form.ratePercent) : null,
        surplus_share: usaSuperavit ? percentToShare(form.surplusPercent) : null,
        carb_min_g_per_kg: usaCarboMinimo ? inputNumberOrNull(form.carbMin) : null,
        bmr_formula: form.bmrFormula,
        tdee_mode: form.tdeeMode,
        activity_level: form.activityLevel,
        meals_per_day: inputNumberOrNull(form.meals),
        goal_body_fat_percent: inputNumberOrNull(form.goalBodyFat),
        goal_weight_kg: inputNumberOrNull(form.goalWeight),
        carb_cycling_enabled: false,
        notes: form.notes.trim() ? form.notes.trim() : null,
      };
      return plan ? updateNutritionPlan(plan.id, payload) : createNutritionPlan({ ...payload, user_id: userId });
    },
    onSuccess: onSaved,
  });

  const erro = saveMutation.isError ? nutritionErrorInfo(saveMutation.error, 'Não foi possível salvar o rascunho.') : null;
  const proteinBasis = nutritionLabel(PROTEIN_BASIS_OPTIONS, ranges?.protein_basis);

  return (
    <Stack gap="md">
      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
        <Select
          label="Estratégia"
          placeholder="Escolha a estratégia"
          data={[...STRATEGY_OPTIONS]}
          value={form.strategy}
          onChange={changeStrategy}
          required
          allowDeselect={false}
        />
        {ranges && usaSuperavit && (
          <Switch
            mt="xl"
            label="Aluno avançado (superávit menor)"
            checked={form.advanced}
            onChange={(event) => changeAdvanced(event.currentTarget.checked)}
          />
        )}
      </SimpleGrid>

      {ranges && (
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
          <NumberInput
            label="Proteína (g/kg)"
            description={[proteinBasis, rangeHint(ranges.protein_g_per_kg, ' g/kg')].filter(Boolean).join(' · ')}
            value={form.protein}
            onChange={(protein) => update({ protein })}
            {...bounds(ranges.protein_g_per_kg)}
            step={0.1}
            decimalScale={2}
            decimalSeparator=","
          />
          <NumberInput
            label="Gordura (% das kcal)"
            description={rangeHint(ranges.fat_kcal_share, '%', true)}
            value={form.fatPercent}
            onChange={(fatPercent) => update({ fatPercent })}
            {...percentBounds(ranges.fat_kcal_share)}
            step={1}
            decimalScale={1}
            decimalSeparator=","
            suffix="%"
          />
          {usaRitmo && (
            <NumberInput
              label="Ritmo de perda (% do peso por semana)"
              description={rangeHint(ranges.weekly_rate_share, '%', true)}
              value={form.ratePercent}
              onChange={(ratePercent) => update({ ratePercent })}
              {...percentBounds(ranges.weekly_rate_share)}
              step={0.1}
              decimalScale={2}
              decimalSeparator=","
              suffix="%"
            />
          )}
          {usaSuperavit && (
            <NumberInput
              label="Superávit (% do gasto)"
              description={rangeHint(surplusRange, '%', true)}
              value={form.surplusPercent}
              onChange={(surplusPercent) => update({ surplusPercent })}
              {...percentBounds(surplusRange)}
              step={1}
              decimalScale={1}
              decimalSeparator=","
              suffix="%"
            />
          )}
          {usaCarboMinimo && (
            <NumberInput
              label="Carboidrato mínimo (g/kg)"
              description={rangeHint(ranges.carb_min_g_per_kg, ' g/kg')}
              value={form.carbMin}
              onChange={(carbMin) => update({ carbMin })}
              {...bounds(ranges.carb_min_g_per_kg)}
              step={0.5}
              decimalScale={2}
              decimalSeparator=","
            />
          )}
        </SimpleGrid>
      )}

      <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
        <Select
          label="Fórmula da TMB"
          data={[...BMR_FORMULA_OPTIONS]}
          value={form.bmrFormula}
          onChange={(bmrFormula) => update({ bmrFormula: bmrFormula ?? DEFAULT_BMR_FORMULA })}
          allowDeselect={false}
        />
        <Select
          label="Gasto do treino"
          data={[...TDEE_MODE_OPTIONS]}
          value={form.tdeeMode}
          onChange={(tdeeMode) => update({ tdeeMode: tdeeMode ?? DEFAULT_TDEE_MODE })}
          allowDeselect={false}
        />
        <Select
          label="Nível de atividade"
          placeholder="Usar o do perfil"
          data={[...ACTIVITY_LEVEL_OPTIONS]}
          value={form.activityLevel}
          onChange={(activityLevel) => update({ activityLevel })}
          clearable
        />
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
        <NumberInput
          label="Refeições por dia"
          description={rangeHint(NUTRITION_FORM_RANGES.meals_per_day, '')}
          value={form.meals}
          onChange={(meals) => update({ meals })}
          {...bounds(NUTRITION_FORM_RANGES.meals_per_day)}
          allowDecimal={false}
        />
        <NumberInput
          label="% de gordura alvo"
          description={stopBodyFatHint(form.strategy)}
          value={form.goalBodyFat}
          onChange={(goalBodyFat) => update({ goalBodyFat })}
          {...bounds(NUTRITION_FORM_RANGES.body_fat_percent)}
          decimalScale={1}
          decimalSeparator=","
          suffix="%"
        />
        <NumberInput
          label="Peso-alvo (kg)"
          value={form.goalWeight}
          onChange={(goalWeight) => update({ goalWeight })}
          {...bounds(NUTRITION_FORM_RANGES.weight_kg)}
          decimalScale={1}
          decimalSeparator=","
        />
      </SimpleGrid>

      <Switch
        label="Ciclo de carboidrato"
        description={nutritionNotice('carb_cycling_unavailable')}
        checked={false}
        disabled
        readOnly
      />

      <Textarea
        label="Observações"
        value={form.notes}
        onChange={(event) => update({ notes: event.currentTarget.value })}
        maxLength={1000}
        autosize
        minRows={2}
      />

      {erro && <NutritionLimitations items={erro.items} title={erro.message} tone="error" />}
      {erro && erro.items.length === 0 && (
        <Text c="red" size="sm">
          {erro.message}
        </Text>
      )}

      <Group justify="flex-end">
        <Button variant="default" onClick={onCancel}>
          Cancelar
        </Button>
        <Button
          color="brand"
          onClick={() => saveMutation.mutate()}
          loading={saveMutation.isPending}
          disabled={!form.strategy}
        >
          Salvar rascunho
        </Button>
      </Group>
      <Text size="xs" c={BRAND.textMuted}>
        O rascunho não muda nada para o aluno até ser publicado.
      </Text>
    </Stack>
  );
}
