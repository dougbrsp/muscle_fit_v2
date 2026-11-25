'use client';

import { Button, Fieldset, Group, NumberInput, Select, SimpleGrid, Stack, Switch, Text, Textarea } from '@mantine/core';
import { useMutation, useQuery } from '@tanstack/react-query';
import { formatNumberOrDash } from '@/lib/format';
import { useState } from 'react';
import { NutritionLimitations } from './NutritionLimitations';
import motorConstants from '@/lib/motor-constants.generated.json';
import { PlanCycleDays, flatCycleDays, withoutDayWarnings, type CycleDaysValue } from './PlanCycleDays';
import { PlanSuggestionPanel } from './PlanSuggestionPanel';
import { createNutritionPlan, nutritionErrorInfo, planPreviewQuery, updateNutritionPlan } from '@/lib/nutrition-api';
import {
  ACTIVITY_LEVEL_OPTIONS,
  AUTO_TDEE_MODE,
  DEFAULT_ROUTINE_LEVEL,
  ROUTINE_LEVEL_OPTIONS,
  TDEE_MODE_HINTS,
  BMR_FORMULA_OPTIONS,
  DEFAULT_DAY_KIND,
  DEFAULT_BMR_FORMULA,
  DEFAULT_MACRO_PRIORITY,
  DEFAULT_MANUAL_TRAINING_MODE,
  MANUAL_TRAINING_MODES_BY_TDEE_MODE,
  MANUAL_TRAINING_MODE_HINTS,
  MANUAL_TRAINING_MODE_OPTIONS,
  DEFAULT_TDEE_MODE,
  DEFAULT_CARB_CYCLE_POLICY,
  MACRO_PRIORITY_OPTIONS,
  NUTRITION_FORM_RANGES,
  PROTEIN_BASIS_OPTIONS,
  SEX_OPTIONS,
  STRATEGY_OPTIONS,
  TDEE_MODE_OPTIONS,
  WATCH_CALORIE_MODE_OPTIONS,
  fieldAppliesToStrategy,
  nutritionLabel,
  strategyFormRanges,
  type NutritionCode,
  type StrategyFormRanges,
} from '@/lib/nutrition-constants';
import { cyclePolicyPayload, initialCyclePolicy } from '@/lib/nutrition-cycle-policy';
import { asField, formatKcal, inputNumberOrNull, percentToShare, shareToPercent } from '@/lib/nutrition-format';
import {
  cycleDaysPayload,
  macroPriorityPayload,
  manualTargetsPayload,
  maintenanceAdjustPayload,
  manualTrainingModePayload,
} from '@/lib/nutrition-plan-form';
import {
  applySuggestion,
  keepSuggestion,
  suggestionCurrentPayload,
  undoSuggestion,
  type PlanSuggestionFields,
  type SuggestionFormSlice,
} from '@/lib/nutrition-plan-suggestion';
import { ADAPTIVE_TDEE_MODE, DEFAULT_WATCH_CALORIE_MODE, adaptiveFieldHint } from '@/lib/nutrition-dashboard';
import {
  type NutritionPlan,
  type NutritionPlanSaved,
  type NutritionPlanSettingsPayload,
  type NutritionWatchCalorieMode,
} from '@/lib/nutrition-types';
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
  watchCalorieMode: NutritionWatchCalorieMode;
  activityLevel: NutritionCode | null;
  meals: NumberField;
  goalBodyFat: NumberField;
  goalWeight: NumberField;
  macroPriority: NutritionCode;
  manualOn: boolean;
  manualMode: NutritionCode;
  manualProtein: NumberField;
  manualCarb: NumberField;
  manualFat: NumberField;
  cycleOn: boolean;
  cycleDays: CycleDaysValue;
  cyclePolicy: NutritionCode;
  notes: string;
};

const SALVE_PARA_VER = 'Salve para ver as % atualizadas';

/** Tipo "normal" do dia (código do motor em `DAY_KIND_OPTIONS`). */
const NORMAL_DAY_KIND: NutritionCode = DEFAULT_DAY_KIND;

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

export function initialState(plan: NutritionPlan | null): FormState {
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
      watchCalorieMode: plan.energy_params?.watch_calorie_mode ?? DEFAULT_WATCH_CALORIE_MODE,
      activityLevel: plan.activity_level,
      meals: asField(plan.meals_per_day),
      goalBodyFat: asField(plan.goal_body_fat_percent),
      goalWeight: asField(plan.goal_weight_kg),
      macroPriority: plan.macro_priority ?? DEFAULT_MACRO_PRIORITY,
      manualOn: plan.manual_targets != null,
      manualMode: plan.manual_targets?.training_mode ?? DEFAULT_MANUAL_TRAINING_MODE,
      manualProtein: asField(plan.manual_targets?.protein_g),
      manualCarb: asField(plan.manual_targets?.carb_g),
      manualFat: asField(plan.manual_targets?.fat_g),
      cycleOn: plan.cycle_days != null,
      cycleDays: { ...flatCycleDays(NORMAL_DAY_KIND), ...(plan.cycle_days ?? {}) },
      cyclePolicy: initialCyclePolicy(plan.carb_cycle_policy),
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
    watchCalorieMode: DEFAULT_WATCH_CALORIE_MODE,
    activityLevel: null,
    meals: '',
    goalBodyFat: '',
    goalWeight: '',
    macroPriority: DEFAULT_MACRO_PRIORITY,
    manualOn: false,
    manualMode: DEFAULT_MANUAL_TRAINING_MODE,
    manualProtein: '',
    manualCarb: '',
    manualFat: '',
    cycleOn: false,
    cycleDays: flatCycleDays(NORMAL_DAY_KIND),
    cyclePolicy: DEFAULT_CARB_CYCLE_POLICY,
    notes: '',
  };
}

/** Limites do campo em % a partir da faixa em fração do motor. */
/** Rótulo do aviso "gramas manuais ignoram a compensação" vindo da prévia (aviso geral ou de algum dia), se houver. */
function ignoredManualLabel(preview: { warnings: { code: string; label: string }[]; week: { limitations: { code: string; label: string }[] }[] } | undefined): string | null {
  const code = motorConstants.nutrition.fat_compensation_ignored_manual;
  const itens = [...(preview?.warnings ?? []), ...(preview?.week.flatMap((dia) => dia.limitations) ?? [])];
  return itens.find((item) => item.code === code)?.label ?? null;
}

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
  /** Dono do plano (admin/PT); `null` = o próprio usuário (D2). */
  userId: number | null;
  plan: NutritionPlan | null;
  /** Ajuste da manutenção do plano em vigor: um rascunho NOVO de manutenção o herda (o check-in o moveu). */
  inheritedMaintenanceAdjust?: number | null;
  onSaved: (plan: NutritionPlanSaved) => void;
  onCancel: () => void;
};

/**
 * Formulário do rascunho (o próprio usuário, ou admin/PT para outro usuário). Opções, faixas e defaults vêm do motor
 * (`motor-constants.generated.json` via `nutrition-constants.ts`); a API valida
 * de novo e responde 400 com o código do motor. Frações (gordura, ritmo,
 * superávit) aparecem em % só para a tela.
 */
export function PlanForm({ userId, plan, inheritedMaintenanceAdjust = null, onSaved, onCancel }: Props) {
  const [form, setForm] = useState<FormState>(() => initialState(plan));
  // Os campos que a sugestão da IA preencheu, como estavam, para o "Desfazer".
  const [antesDaSugestao, setAntesDaSugestao] = useState<SuggestionFormSlice | null>(null);
  const [sugestaoPendente, setSugestaoPendente] = useState(false);
  const ranges = strategyFormRanges(form.strategy);
  const surplusRange = form.advanced ? ranges?.surplus_share_advanced : ranges?.surplus_share;
  // Quais campos a estratégia usa: `STRATEGY_ONLY_FIELDS` do motor (via JSON gerado).
  const usaRitmo = fieldAppliesToStrategy('weekly_rate_share', form.strategy);
  const usaSuperavit = fieldAppliesToStrategy('surplus_share', form.strategy);
  const usaCarboMinimo = fieldAppliesToStrategy('carb_min_g_per_kg', form.strategy);
  const usaAjusteManutencao = fieldAppliesToStrategy('maintenance_kcal_adjust', form.strategy);
  const ajusteManutencao = maintenanceAdjustPayload(usaAjusteManutencao, plan, inheritedMaintenanceAdjust);

  // Modos das gramas manuais que valem para o gasto do treino escolhido (`add_training` só com o treino à parte).
  const automatico = form.tdeeMode === AUTO_TDEE_MODE;
  const adaptativo = form.tdeeMode === ADAPTIVE_TDEE_MODE;
  const modosManuais = MANUAL_TRAINING_MODES_BY_TDEE_MODE[form.tdeeMode] ?? [DEFAULT_MANUAL_TRAINING_MODE];

  // Prévia do rascunho salvo (mesma chave da prévia da tela): kcal e % por macro vêm da API, sem conta aqui.
  const previewQuery = useQuery({ ...planPreviewQuery(plan ?? { id: 0, updated_at: null, state: '' }), enabled: plan != null && (form.manualOn || form.cycleOn) });
  const preview = previewQuery.data;
  // A prévia é do rascunho SALVO: com edição não salva, os números dela não valem para o que está na tela.
  const semSalvar = plan != null && JSON.stringify(form) !== JSON.stringify(initialState(plan));
  const shares = preview?.target.macro_kcal_shares;
  const shareText = (share: number | undefined) => {
    if (semSalvar && preview) return SALVE_PARA_VER;
    const pct = shareToPercent(share);
    return pct == null ? undefined : `${formatNumberOrDash(pct, 0)}% das kcal`;
  };

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

  const payload: NutritionPlanSettingsPayload = {
    strategy: form.strategy ?? '',
    advanced: usaSuperavit ? form.advanced : false,
    // Com gramas manuais os campos só ficam ocultos: o rascunho mantém os valores (o motor os ignora nas gramas).
    protein_g_per_kg: inputNumberOrNull(form.protein),
    fat_kcal_share: percentToShare(form.fatPercent),
    weekly_rate_share: usaRitmo ? percentToShare(form.ratePercent) : null,
    surplus_share: usaSuperavit ? percentToShare(form.surplusPercent) : null,
    carb_min_g_per_kg: usaCarboMinimo ? inputNumberOrNull(form.carbMin) : null,
    bmr_formula: form.bmrFormula,
    tdee_mode: form.tdeeMode,
    activity_level: automatico ? (form.activityLevel ?? DEFAULT_ROUTINE_LEVEL) : form.activityLevel,
    meals_per_day: inputNumberOrNull(form.meals),
    goal_body_fat_percent: inputNumberOrNull(form.goalBodyFat),
    goal_weight_kg: inputNumberOrNull(form.goalWeight),
    macro_priority: macroPriorityPayload(form.manualOn, form.macroPriority),
    manual_targets: manualTargetsPayload(form.manualOn, form.manualProtein, form.manualCarb, form.manualFat),
    manual_training_mode: manualTrainingModePayload(
      form.manualOn,
      modosManuais.includes(form.manualMode) ? form.manualMode : DEFAULT_MANUAL_TRAINING_MODE,
    ),
    cycle_days: cycleDaysPayload(form.cycleOn, form.cycleDays),
    carb_cycle_policy: cyclePolicyPayload(form.cyclePolicy),
    maintenance_kcal_adjust: ajusteManutencao,
    // Só o modo adaptativo envia `energy_params`: o payload dos planos legados não muda.
    ...(adaptativo ? { energy_params: { watch_calorie_mode: form.watchCalorieMode } } : {}),
    notes: form.notes.trim() ? form.notes.trim() : null,
  };

  function setSuggestionState(state: { form: FormState; undo: SuggestionFormSlice | null }) {
    setForm(state.form);
    setAntesDaSugestao(state.undo);
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      return plan
        ? updateNutritionPlan(plan.id, payload)
        : createNutritionPlan(userId == null ? payload : { ...payload, user_id: userId });
    },
    onSuccess: onSaved,
  });

  const erro = saveMutation.isError ? nutritionErrorInfo(saveMutation.error, 'Não foi possível salvar o rascunho.') : null;
  const proteinBasis = nutritionLabel(PROTEIN_BASIS_OPTIONS, ranges?.protein_basis);

  return (
    <Stack gap="md">
      <PlanSuggestionPanel
        userId={userId}
        current={suggestionCurrentPayload(payload, form.manualOn)}
        draft={form.strategy ? (userId == null ? payload : { ...payload, user_id: userId }) : null}
        onApply={(fields: PlanSuggestionFields) => setSuggestionState(applySuggestion({ form, undo: antesDaSugestao }, fields))}
        onUndo={() => setSuggestionState(undoSuggestion({ form, undo: antesDaSugestao }))}
        onKeep={() => setSuggestionState(keepSuggestion({ form, undo: antesDaSugestao }))}
        onPendingChange={setSugestaoPendente}
      />

      <Fieldset variant="unstyled" p={0} disabled={sugestaoPendente}>
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
                label="Nível avançado (superávit menor)"
                checked={form.advanced}
                onChange={(event) => changeAdvanced(event.currentTarget.checked)}
              />
            )}
          </SimpleGrid>

          {ranges && (
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="md">
              {!form.manualOn && (
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
              )}
              {!form.manualOn && (
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
              )}
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
              {usaAjusteManutencao && !!ajusteManutencao && (
                <Text size="sm" c={BRAND.textMuted}>
                  Ajuste da manutenção em vigor: {ajusteManutencao > 0 ? '+' : ''}
                  {ajusteManutencao} kcal por dia sobre o gasto. Quem o muda é o check-in semanal; salvar mantém o valor.
                </Text>
              )}
            </SimpleGrid>
          )}

          <Select
            label="Prioridade de macros"
            description="Como o motor reparte as calorias que sobram depois da proteína."
            data={[...MACRO_PRIORITY_OPTIONS]}
            value={form.macroPriority}
            onChange={(macroPriority) => update({ macroPriority: macroPriority ?? DEFAULT_MACRO_PRIORITY })}
            disabled={form.manualOn}
            allowDeselect={false}
          />

          <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
            <Select
              label="Fórmula da TMB"
              description={adaptativo ? adaptiveFieldHint('bmr_formula') : undefined}
              data={[...BMR_FORMULA_OPTIONS]}
              value={form.bmrFormula}
              onChange={(bmrFormula) => update({ bmrFormula: bmrFormula ?? DEFAULT_BMR_FORMULA })}
              allowDeselect={false}
            />
            <Select
              label="Gasto do treino"
              description={TDEE_MODE_HINTS[form.tdeeMode]}
              data={[...TDEE_MODE_OPTIONS]}
              value={form.tdeeMode}
              onChange={(tdeeMode) => {
                const novo = tdeeMode ?? DEFAULT_TDEE_MODE;
                // O nível guardado muda de sentido entre o automático (rotina) e os outros (nível de atividade): recomeça.
                update({ tdeeMode: novo, activityLevel: (novo === AUTO_TDEE_MODE) === automatico ? form.activityLevel : null });
              }}
              allowDeselect={false}
            />
            {automatico ? (
              <Select
                label="Rotina fora do exercício"
                description="Como é o seu dia sem contar o treino. O treino e o cardio medidos pelo Intervals entram à parte."
                data={[...ROUTINE_LEVEL_OPTIONS]}
                value={form.activityLevel ?? DEFAULT_ROUTINE_LEVEL}
                onChange={(activityLevel) => update({ activityLevel: activityLevel ?? DEFAULT_ROUTINE_LEVEL })}
                allowDeselect={false}
              />
            ) : (
              <Select
                label="Nível de atividade"
                description={
                  adaptativo
                    ? adaptiveFieldHint('activity_level')
                    : 'Usado na conta do gasto do dia (o fator já considera se o exercício está incluído).'
                }
                placeholder="Usar o do perfil"
                data={[...ACTIVITY_LEVEL_OPTIONS]}
                value={form.activityLevel}
                onChange={(activityLevel) => update({ activityLevel })}
                clearable
              />
            )}
          </SimpleGrid>

          {adaptativo && (
            <Select
              label="Calorias do relógio"
              description={adaptiveFieldHint('watch_calorie_mode')}
              data={[...WATCH_CALORIE_MODE_OPTIONS]}
              value={form.watchCalorieMode}
              onChange={(modo) => update({ watchCalorieMode: (modo as NutritionWatchCalorieMode | null) ?? DEFAULT_WATCH_CALORIE_MODE })}
              allowDeselect={false}
              data-testid="plano-calorias-relogio"
            />
          )}

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

          <Stack gap="xs">
            <Switch
              label="Definir as gramas manualmente"
              description="Você fixa proteína, carboidrato e gordura em gramas; proteína (g/kg) e gordura (%) da estratégia não valem. As kcal e a % de cada macro vêm do motor, do rascunho já salvo. O motor confere as faixas, avisa e precisa do seu peso registrado."
              checked={form.manualOn}
              onChange={(event) => update({ manualOn: event.currentTarget.checked })}
            />
            {form.manualOn && (
              <Select
                label="Gramas manuais e treino"
                description={MANUAL_TRAINING_MODE_HINTS[modosManuais.includes(form.manualMode) ? form.manualMode : DEFAULT_MANUAL_TRAINING_MODE]}
                data={MANUAL_TRAINING_MODE_OPTIONS.filter((opcao) => modosManuais.includes(opcao.value))}
                value={modosManuais.includes(form.manualMode) ? form.manualMode : DEFAULT_MANUAL_TRAINING_MODE}
                onChange={(manualMode) => update({ manualMode: manualMode ?? DEFAULT_MANUAL_TRAINING_MODE })}
                allowDeselect={false}
              />
            )}
            {form.manualOn && (
              <SimpleGrid cols={{ base: 1, xs: 3 }} spacing="md">
                <NumberInput
                  label="Proteína (g)"
                  description={shareText(shares?.protein)}
                  value={form.manualProtein}
                  onChange={(manualProtein) => update({ manualProtein })}
                  min={0}
                  decimalScale={0}
                  inputMode="numeric"
                />
                <NumberInput
                  label="Carboidrato (g)"
                  description={shareText(shares?.carb)}
                  value={form.manualCarb}
                  onChange={(manualCarb) => update({ manualCarb })}
                  min={0}
                  decimalScale={0}
                  inputMode="numeric"
                />
                <NumberInput
                  label="Gordura (g)"
                  description={shareText(shares?.fat)}
                  value={form.manualFat}
                  onChange={(manualFat) => update({ manualFat })}
                  min={0}
                  decimalScale={0}
                  inputMode="numeric"
                />
                {preview && !semSalvar && (
                  <Text size="xs" c={BRAND.textMuted} data-testid="manual-energia">
                    Energia do rascunho salvo: {formatKcal(preview.target.kcal)}
                  </Text>
                )}
              </SimpleGrid>
            )}
          </Stack>

          <PlanCycleDays
            enabled={form.cycleOn}
            // Ao limpar o ciclo a política volta ao padrão (o motor recusa compensação sem ciclo).
            onEnabledChange={(cycleOn) => update({ cycleOn })}
            policy={form.cyclePolicy}
            onPolicyChange={(cyclePolicy) => update({ cyclePolicy })}
            value={form.cycleDays}
            onChange={(cycleDays) => update({ cycleDays })}
            userId={userId}
            week={semSalvar ? undefined : preview?.week}
            manualOn={form.manualOn}
            ignoredManualLabel={semSalvar ? null : ignoredManualLabel(preview)}
          />
          {form.cycleOn && preview && !semSalvar && <NutritionLimitations items={withoutDayWarnings(preview.warnings, preview.week)} title="Avisos do rascunho salvo" />}

          <Textarea
            label="Observações"
            value={form.notes}
            onChange={(event) => update({ notes: event.currentTarget.value })}
            maxLength={1000}
            autosize
            minRows={2}
          />
        </Stack>
      </Fieldset>

      {erro && <NutritionLimitations items={erro.items} title={erro.message} tone="error" />}
      {erro && erro.items.length === 0 && (
        <Text c="red" size="sm">
          {erro.message}
        </Text>
      )}

      {/* Barra de ações presa ao rodapé do shell mobile, acima da safe-area do aparelho. */}
      <Group
        justify="flex-end"
        style={{
          position: 'sticky',
          bottom: 'calc(var(--m-bottom-nav, 0px) + env(safe-area-inset-bottom))',
          zIndex: 5,
          background: BRAND.bgElevated,
          paddingTop: 'var(--mantine-spacing-xs)',
          paddingBottom: 'var(--mantine-spacing-xs)', // a safe-area já está no `bottom`
        }}
      >
        <Button variant="default" onClick={onCancel}>
          Cancelar
        </Button>
        <Button
          color="brand"
          onClick={() => saveMutation.mutate()}
          loading={saveMutation.isPending}
          disabled={!form.strategy || sugestaoPendente}
        >
          Salvar rascunho
        </Button>
      </Group>
      <Text size="xs" c={BRAND.textMuted}>
        O rascunho não muda nenhuma meta até ser publicado.
      </Text>
    </Stack>
  );
}
