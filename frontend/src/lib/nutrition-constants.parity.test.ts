import { describe, expect, it } from 'vitest';

import motorConstants from './motor-constants.generated.json';
import {
  ACTIVITY_LEVEL_OPTIONS,
  AI_ESTIMATE_BASIS,
  AI_ESTIMATE_SOURCE,
  BMR_FORMULA_OPTIONS,
  BODY_FAT_METHOD_OPTIONS,
  BREAK_PROTOCOL_OPTIONS,
  DAY_KIND_OPTIONS,
  DEFAULT_BMR_FORMULA,
  DEFAULT_FOOD_BASIS,
  DEFAULT_TDEE_MODE,
  ENERGY_NUTRIENT,
  FOOD_BASIS_OPTIONS,
  FOOD_CITATIONS,
  FOOD_LICENSES,
  FOOD_PER_100_FIELDS,
  FOOD_REQUIRED_FIELDS,
  FOOD_SERVING_FIELDS,
  FOOD_SOURCE_OPTIONS,
  GENERIC_PORTIONS,
  HUNGER_OPTIONS,
  ITEM_NUTRIENT_FIELDS,
  LABEL_LEGIBILITY_OPTIONS,
  LABEL_PHOTO,
  MEAL_ITEMS_LIMIT,
  MEAL_NATURE_OPTIONS,
  MEAL_PARSE_QUOTA_EXCEEDED_LABEL,
  MEAL_PARSE_STATUS_OPTIONS,
  MEAL_PARSE_TEXT_MAX_CHARS,
  MEAL_SLOT_OPTIONS,
  MEAL_UNITS,
  NUTRIENT_FIELDS,
  NUTRITION_ENGINE_VERSION,
  NUTRITION_FORM_RANGES,
  PHASE_KIND_OPTIONS,
  PLAN_STATE_OPTIONS,
  PORTION_UNITS,
  PROTEIN_BASIS_OPTIONS,
  RDC429_FIELDS,
  SESSION_KIND_OPTIONS,
  SEX_OPTIONS,
  STRATEGY_OPTIONS,
  TDEE_MODE_OPTIONS,
  TRAINING_KCAL_NATURE_OPTIONS,
  USDA_DATA_TYPE_OPTIONS,
  NUTRITION_NOTICES,
  STRATEGY_ONLY_FIELDS,
  UNIT_OPTIONS,
  WATER_ML_LIMITS,
  fieldAppliesToStrategy,
  nutrientField,
  nutritionLabel,
  nutritionNotice,
  strategyFormRanges,
  type NutritionOption,
} from './nutrition-constants';

// Nutrição, Lote L1: o dono é `api/app/domain/nutrition/` (`export_for_frontend()`),
// gravado no bloco `nutrition` de motor-constants.generated.json por
// `api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`. O espelho só lê.
const N = motorConstants.nutrition;

type Par = [ReadonlyArray<NutritionOption>, ReadonlyArray<string>, Readonly<Record<string, string>>];

const PARES: ReadonlyArray<Par> = [
  [SEX_OPTIONS, N.sexes, N.sex_labels],
  [ACTIVITY_LEVEL_OPTIONS, N.activity_levels, N.activity_level_labels],
  [STRATEGY_OPTIONS, N.strategies, N.strategy_labels],
  [PROTEIN_BASIS_OPTIONS, N.protein_bases, N.protein_basis_labels],
  [DAY_KIND_OPTIONS, N.day_kinds, N.day_kind_labels],
  [MEAL_SLOT_OPTIONS, N.meal_slots, N.meal_slot_labels],
  [BMR_FORMULA_OPTIONS, N.bmr_formulas, N.bmr_formula_labels],
  [TDEE_MODE_OPTIONS, N.tdee_modes, N.tdee_mode_labels],
  [SESSION_KIND_OPTIONS, N.session_kinds, N.session_kind_labels],
  [TRAINING_KCAL_NATURE_OPTIONS, N.training_kcal_natures, N.training_kcal_nature_labels],
  [MEAL_NATURE_OPTIONS, N.meal_natures, N.meal_nature_labels],
  [BODY_FAT_METHOD_OPTIONS, N.body_fat_methods, N.body_fat_method_labels],
  [PHASE_KIND_OPTIONS, N.phase_kinds, N.phase_kind_labels],
  [BREAK_PROTOCOL_OPTIONS, N.break_protocols, N.break_protocol_labels],
  [PLAN_STATE_OPTIONS, N.plan_states, N.plan_state_labels],
  [FOOD_SOURCE_OPTIONS, N.food_sources, N.food_source_labels],
  [FOOD_BASIS_OPTIONS, N.food_bases, N.food_basis_labels],
  [USDA_DATA_TYPE_OPTIONS, N.usda_data_types, N.usda_data_type_labels],
  // Diário (L4): unidades do item e escala de fome (níveis numéricos viram texto no Select).
  [UNIT_OPTIONS, N.units, N.unit_labels],
  [HUNGER_OPTIONS, N.hunger_levels.map(String), N.hunger_labels],
  // Foto do rótulo (L6): legibilidade da leitura.
  [LABEL_LEGIBILITY_OPTIONS, N.label_legibilities, N.label_legibility_labels],
];

function dentroDaFaixa(faixa: ReadonlyArray<number>, valor: number): void {
  expect(faixa).toHaveLength(2);
  const [minimo, maximo] = faixa;
  expect(minimo).toBeLessThanOrEqual(maximo);
  expect(valor).toBeGreaterThanOrEqual(minimo);
  expect(valor).toBeLessThanOrEqual(maximo);
}

describe('nutrition-constants: paridade com o bloco `nutrition` do dono', () => {
  it('cada lista de opções tem os códigos do dono, na mesma ordem, com o rótulo do dono', () => {
    for (const [opcoes, codigos, rotulos] of PARES) {
      expect(opcoes.map((opcao) => opcao.value)).toEqual([...codigos]);
      expect(Object.keys(rotulos).sort()).toEqual([...codigos].sort());
      for (const opcao of opcoes) {
        expect(opcao.label).toBe(rotulos[opcao.value]);
        expect(opcao.label.length).toBeGreaterThan(0);
      }
    }
  });

  it('todo bloco de rótulos exportado pelo dono tem lista de opções aqui', () => {
    const chavesDeRotulo = Object.keys(N).filter((chave) => chave.endsWith('_labels'));
    expect(chavesDeRotulo).toHaveLength(PARES.length);
  });

  it('versão, unidades, porções, RDC 429 e faixas vêm do JSON gerado', () => {
    expect(NUTRITION_ENGINE_VERSION).toBe(N.engine_version);
    expect(DEFAULT_TDEE_MODE).toBe(N.default_tdee_mode);
    expect(N.tdee_modes).toContain(DEFAULT_TDEE_MODE);
    expect(MEAL_UNITS).toEqual(N.units);
    expect(GENERIC_PORTIONS).toEqual(N.generic_portions);
    expect(GENERIC_PORTIONS.every((porcao) => MEAL_UNITS.includes(porcao.code))).toBe(true);
    expect(RDC429_FIELDS).toEqual(N.rdc429_fields);
    expect(NUTRITION_FORM_RANGES).toBe(N.form_ranges);
  });

  it('a natureza da kcal do treino não fala de proveniência (manual × Intervals é do L0)', () => {
    const textos = TRAINING_KCAL_NATURE_OPTIONS.map((opcao) => opcao.label.toLowerCase()).join(' ');
    expect(textos).not.toContain('intervals');
    expect(textos).not.toContain('manual');
    expect(Object.keys(N).some((chave) => chave.includes('kcal_source'))).toBe(false);
  });

  it('natureza e qualidade comuns não são do bloco de nutrição (dono: domain/data_nature.py)', () => {
    expect(Object.keys(N).filter((chave) => /^(natures|nature_labels|qualities|quality_labels)$/.test(chave))).toEqual([]);
    expect(N.meal_natures).toEqual(['DERIVED', 'ESTIMATED', 'MIXED']);
  });

  it('faixas de formulário são [mín, máx] e o default de cada estratégia fica dentro', () => {
    const { cutting, bulking, maintenance } = NUTRITION_FORM_RANGES;
    dentroDaFaixa(cutting.weekly_rate_share, cutting.weekly_rate_share_default);
    dentroDaFaixa(cutting.protein_g_per_kg, cutting.protein_g_per_kg_default);
    dentroDaFaixa(cutting.fat_kcal_share, cutting.fat_kcal_share_default);
    dentroDaFaixa(bulking.surplus_share, bulking.surplus_share_default);
    dentroDaFaixa(bulking.surplus_share_advanced, bulking.surplus_share_default_advanced);
    dentroDaFaixa(bulking.protein_g_per_kg, bulking.protein_g_per_kg_default);
    dentroDaFaixa(bulking.fat_kcal_share, bulking.fat_kcal_share_default);
    dentroDaFaixa(bulking.carb_min_g_per_kg, bulking.carb_min_g_per_kg_default);
    dentroDaFaixa(maintenance.protein_g_per_kg, maintenance.protein_g_per_kg_default);
    dentroDaFaixa(maintenance.fat_kcal_share, maintenance.fat_kcal_share_default);
    for (const bloco of [cutting, bulking, maintenance]) {
      expect(N.protein_bases).toContain(bloco.protein_basis);
    }
    for (const faixa of [
      NUTRITION_FORM_RANGES.height_cm,
      NUTRITION_FORM_RANGES.weight_kg,
      NUTRITION_FORM_RANGES.body_fat_percent,
      NUTRITION_FORM_RANGES.meals_per_day,
    ]) {
      expect(faixa).toHaveLength(2);
      expect(faixa[0]).toBeLessThan(faixa[1]);
    }
  });

  it('campos de uma estratégia só e avisos vêm do dono', () => {
    expect(STRATEGY_ONLY_FIELDS).toBe(N.strategy_only_fields);
    for (const [campo, dona] of Object.entries(STRATEGY_ONLY_FIELDS)) {
      expect(N.strategies).toContain(dona);
      expect(Object.keys(strategyFormRanges(dona) ?? {})).toContain(campo);
      for (const outra of N.strategies.filter((codigo) => codigo !== dona)) {
        expect(fieldAppliesToStrategy(campo, outra)).toBe(false);
        expect(Object.keys(strategyFormRanges(outra) ?? {})).not.toContain(campo);
      }
      expect(fieldAppliesToStrategy(campo, dona)).toBe(true);
    }
    expect(fieldAppliesToStrategy('protein_g_per_kg', N.strategies[0])).toBe(true);
    expect(NUTRITION_NOTICES).toBe(N.notices);
    for (const codigo of [
      'carb_cycling_unavailable',
      'scale_bmr_deviation',
      'phase_should_stop',
      // Diário (L4): selos e dicas com o texto das limitações do motor.
      'target_frozen',
      'target_backfilled',
      'intake_incomplete_days',
      'training_unknown_future_day',
    ]) {
      expect(nutritionNotice(codigo).length).toBeGreaterThan(0);
      expect(nutritionNotice(codigo)).not.toBe(codigo);
    }
  });

  it('strategyFormRanges lê o bloco de cada estratégia do dono e recusa código desconhecido', () => {
    expect(DEFAULT_BMR_FORMULA).toBe(N.default_bmr_formula);
    expect(N.bmr_formulas).toContain(DEFAULT_BMR_FORMULA);
    for (const codigo of N.strategies) {
      const bloco = strategyFormRanges(codigo);
      expect(bloco).not.toBeNull();
      expect(bloco).toBe((NUTRITION_FORM_RANGES as unknown as Record<string, unknown>)[codigo]);
      expect(N.protein_bases).toContain(bloco?.protein_basis);
    }
    expect(strategyFormRanges('height_cm')).toBeNull();
    expect(strategyFormRanges('keto')).toBeNull();
    expect(strategyFormRanges(null)).toBeNull();
  });

  it('catálogo (L3a): campos da RDC 429, obrigatórios, base e citação vêm do dono', () => {
    expect(FOOD_PER_100_FIELDS.map((campo) => campo.code)).toEqual(N.food_per_100_fields);
    expect(FOOD_SERVING_FIELDS.map((campo) => campo.code)).toEqual(N.food_serving_fields);
    for (const campo of [...FOOD_PER_100_FIELDS, ...FOOD_SERVING_FIELDS]) {
      expect(RDC429_FIELDS).toContainEqual(campo);
    }
    expect(FOOD_REQUIRED_FIELDS).toEqual(N.food_required_fields);
    expect(FOOD_REQUIRED_FIELDS.every((codigo) => N.food_per_100_fields.includes(codigo))).toBe(true);
    expect(DEFAULT_FOOD_BASIS).toBe(N.default_food_basis);
    expect(N.food_bases).toContain(DEFAULT_FOOD_BASIS);
    expect(FOOD_CITATIONS).toBe(N.food_citations);
    expect(FOOD_CITATIONS.taco).toContain('TACO');
    expect(N.food_sources).toContain('taco');
  });

  it('fontes externas (L3b): citação e licença de cada fonte e tipos de dado do USDA vêm do dono', () => {
    expect(FOOD_LICENSES).toBe(N.food_licenses);
    expect(Object.keys(FOOD_LICENSES).sort()).toEqual(Object.keys(FOOD_CITATIONS).sort());
    for (const fonte of ['usda', 'off']) {
      expect(N.food_sources).toContain(fonte);
      expect(FOOD_CITATIONS[fonte].length).toBeGreaterThan(0);
    }
    expect(FOOD_LICENSES.off).toContain('ODbL');
    expect(USDA_DATA_TYPE_OPTIONS.map((opcao) => opcao.value)).toEqual(N.usda_data_types);
    for (const opcao of USDA_DATA_TYPE_OPTIONS) {
      expect(opcao.label).toBe(N.usda_data_type_labels[opcao.value as keyof typeof N.usda_data_type_labels]);
    }
  });

  it('diário (L4): nutrientes, faixas e rótulos das medidas caseiras vêm do dono', () => {
    expect(NUTRIENT_FIELDS.map((campo) => campo.code)).toEqual(N.nutrient_fields);
    for (const campo of NUTRIENT_FIELDS) {
      expect(RDC429_FIELDS).toContainEqual(campo);
      expect(nutrientField(campo.code)).toEqual(campo);
    }
    expect(nutrientField('codigo_novo')).toBeNull();
    expect(ENERGY_NUTRIENT).toBe(N.energy_field);
    expect(nutrientField(ENERGY_NUTRIENT)?.unit).toBe('kcal');
    expect(ITEM_NUTRIENT_FIELDS.map((campo) => campo.code)).toEqual(N.item_nutrient_fields);
    expect(ITEM_NUTRIENT_FIELDS[0].code).toBe(ENERGY_NUTRIENT);
    expect(WATER_ML_LIMITS).toEqual(N.water_ml_range);
    expect(PORTION_UNITS).toEqual(N.portion_units);
    expect(PORTION_UNITS.every((codigo) => MEAL_UNITS.includes(codigo))).toBe(true);
    expect(MEAL_ITEMS_LIMIT).toBe(N.meal_max_items);
    for (const porcao of GENERIC_PORTIONS) {
      expect(nutritionLabel(UNIT_OPTIONS, porcao.code)).toBe(porcao.label);
    }
  });

  it('leitura por IA (L5b): teto do texto, estados do casamento, fonte do estimado e texto da cota vêm do dono', () => {
    const P = N.meal_parse;
    expect(MEAL_PARSE_TEXT_MAX_CHARS).toBe(P.text_max_chars);
    expect(MEAL_PARSE_STATUS_OPTIONS.map((opcao) => opcao.value)).toEqual(P.match_statuses);
    for (const opcao of MEAL_PARSE_STATUS_OPTIONS) {
      expect(opcao.label).toBe(P.match_status_labels[opcao.value as keyof typeof P.match_status_labels]);
    }
    expect(AI_ESTIMATE_SOURCE).toBe(P.estimate_source);
    expect(AI_ESTIMATE_BASIS).toBe(P.estimate_basis);
    expect(N.food_bases).toContain(AI_ESTIMATE_BASIS);
    expect(N.food_sources).toContain(AI_ESTIMATE_SOURCE);
    expect(MEAL_PARSE_QUOTA_EXCEEDED_LABEL).toBe(P.quota_exceeded_label);
  });

  it('foto do rótulo (L6): redução, upload, retenção e a fonte do cadastro vêm do dono', () => {
    expect(LABEL_PHOTO).toBe(N.label_photo);
    expect(LABEL_PHOTO.resize_max_edge_px).toBeGreaterThan(0);
    expect(LABEL_PHOTO.resize_quality).toBeGreaterThan(0);
    expect(LABEL_PHOTO.resize_quality).toBeLessThanOrEqual(1);
    expect(LABEL_PHOTO.max_bytes).toBeGreaterThan(0);
    expect(LABEL_PHOTO.content_type).toMatch(/^image\//);
    expect(LABEL_PHOTO.retention_days).toBeGreaterThan(0);
    expect(N.food_sources).toContain(LABEL_PHOTO.food_source);
    expect(N.label_legibilities).toHaveLength(LABEL_LEGIBILITY_OPTIONS.length);
  });

  it('nutritionLabel devolve o rótulo do dono, o próprio código se for desconhecido e null se ausente', () => {
    const [primeira] = STRATEGY_OPTIONS;
    expect(nutritionLabel(STRATEGY_OPTIONS, primeira.value)).toBe(N.strategy_labels[primeira.value as keyof typeof N.strategy_labels]);
    expect(nutritionLabel(STRATEGY_OPTIONS, 'codigo_novo')).toBe('codigo_novo');
    expect(nutritionLabel(STRATEGY_OPTIONS, null)).toBeNull();
    expect(nutritionLabel(STRATEGY_OPTIONS, undefined)).toBeNull();
  });
});
