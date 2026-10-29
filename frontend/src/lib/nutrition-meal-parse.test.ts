import { describe, expect, it } from 'vitest';

import { AI_ESTIMATE_BASIS, MEAL_PARSE_STATUS_OPTIONS, nutritionLabel } from './nutrition-constants';
import {
  chooseCandidate,
  chooseEstimate,
  editorFormFromMeal,
  itemHasSource,
  itemsPayload,
  mealCreatePayload,
} from './nutrition-meal-form';
import { editorFormFromParse, editorItemFromParse } from './nutrition-meal-parse';
import type { Meal, MealParseItem, MealParseResult } from './nutrition-types';

// Resultado da leitura no formato do worker (`nutrition_meal_parse_worker`), com os códigos do motor.
const STATUS = MEAL_PARSE_STATUS_OPTIONS.map((opcao) => opcao.value);
const [SELECIONADO, ESCOLHA, ESTIMADO] = STATUS;

const ARROZ = { candidate_id: 3, food_id: 3, name: 'Arroz, tipo 1, cozido', score: 1, auto_accept: true, basis: 'g', source: 'taco', brand: null };
const FRANGO = { candidate_id: 410, food_id: 410, name: 'Frango, peito, sem pele, grelhado', score: 0.72, auto_accept: false, basis: 'g', source: 'taco', brand: null };
const WHEY = { name: 'whey protein', energy_kcal: 400, protein_g: 80, carbohydrates_g: 10, total_fat_g: 5, fiber_g: null };
const AVISO = { code: 'quantity_from_grams_estimate', label: 'Quantidade em gramas estimada pela IA.' };

function lido(extra: Partial<MealParseItem>): MealParseItem {
  return {
    index: 0,
    ai: { name: 'arroz branco', quantity: null, unit: 'g', grams_estimate: 120, preparation: 'cozido', confidence: 0.9 },
    status: SELECIONADO,
    candidates: [ARROZ],
    food_id: 3,
    match_confidence: 1,
    quantity: 120,
    unit: 'g',
    estimate: null,
    candidate_for_catalog: false,
    uses_estimate: false,
    limitations: [AVISO],
    ...extra,
  };
}

const RESULTADO: MealParseResult = {
  items: [
    lido({}),
    lido({ index: 1, ai: { ...lido({}).ai, name: 'peito de frango' }, status: ESCOLHA, candidates: [FRANGO], food_id: null,
      match_confidence: null, quantity: 150, estimate: { ...WHEY, name: 'peito de frango' } }),
    lido({ index: 2, ai: { ...lido({}).ai, name: 'whey protein' }, status: ESTIMADO, candidates: [], food_id: null,
      match_confidence: null, quantity: 30, estimate: WHEY, uses_estimate: true, candidate_for_catalog: true }),
  ],
  limitations: [],
  notes: 'Quantidade do arroz estimada.',
  job_id: 'job-1',
  user_id: 2,
  local_date: '2026-10-18',
  meal_slot: 'lunch',
  model: 'gpt-5.4-mini',
  prompt_version: 'nutrition-parse-v1',
  engine_version: 'nutrition-v1',
};

describe('nutrition-meal-parse: a proposta é do motor, a tela só transporta', () => {
  it('vocabulário de estados vem do dono', () => {
    expect(STATUS.length).toBe(4);
    expect(nutritionLabel(MEAL_PARSE_STATUS_OPTIONS, ESTIMADO)).toBeTruthy();
  });

  it('pré-selecionado, escolha e estimado viram itens do editor', () => {
    const [arroz, frango, whey] = editorFormFromParse(RESULTADO, { date: 'x', slot: null, text: 'arroz, frango e whey' }).items;
    expect(arroz).toMatchObject({ food_id: 3, label: ARROZ.name, quantity: 120, unit: 'g', matchConfidence: 1, usesEstimate: false });
    expect(itemHasSource(arroz)).toBe(true);
    expect(arroz.notices).toEqual([AVISO]);
    expect(frango).toMatchObject({ food_id: null, usesEstimate: false, label: 'peito de frango', choices: [FRANGO] });
    expect(itemHasSource(frango)).toBe(false); // a pessoa precisa escolher
    expect(whey).toMatchObject({ food_id: null, usesEstimate: true, label: 'whey protein', basis: AI_ESTIMATE_BASIS });
    expect(itemHasSource(whey)).toBe(true);
    expect(whey.originLabel).toBe(nutritionLabel(MEAL_PARSE_STATUS_OPTIONS, ESTIMADO));
  });

  it('sem escolha não há corpo; escolhida, o corpo leva o alimento ou a estimativa como a leitura entregou', () => {
    const form = editorFormFromParse(RESULTADO, { date: 'x', slot: null, text: 'arroz, frango e whey' });
    expect(mealCreatePayload(form, null)).toBeNull();
    const escolhido = { ...form, items: [form.items[0], chooseCandidate(form.items[1], FRANGO), form.items[2]] };
    expect(mealCreatePayload(escolhido, 2)).toEqual({
      local_date: '2026-10-18',
      meal_slot: 'lunch',
      eaten_at: null,
      notes: null,
      user_id: 2,
      ai_job_id: 'job-1',
      description_raw: 'arroz, frango e whey',
      items: [
        { food_id: 3, quantity: 120, unit: 'g', portion_id: null, match_confidence: 1 },
        { food_id: 410, quantity: 150, unit: 'g', portion_id: null, match_confidence: 0.72 },
        { food_id: null, estimate: WHEY, quantity: 30, unit: 'g', portion_id: null },
      ],
    });
    // Trocar pela estimativa da IA: sem alimento, com a estimativa, a base dela e a quantidade do motor.
    const mexido = { ...chooseCandidate(form.items[1], { ...FRANGO, basis: 'ml' }), quantity: 999, unit: 'xicara' };
    const estimado = chooseEstimate(mexido);
    expect(estimado).toMatchObject({
      food_id: null, usesEstimate: true, label: 'peito de frango', matchConfidence: null, basis: AI_ESTIMATE_BASIS,
      quantity: 150, unit: 'g',
    });
    expect(itemsPayload([estimado])).toEqual([
      { food_id: null, estimate: { ...WHEY, name: 'peito de frango' }, quantity: 150, unit: 'g', portion_id: null },
    ]);
  });

  it('item sem candidato nem estimativa fica sem origem', () => {
    const item = editorItemFromParse(lido({ status: STATUS[3], candidates: [], food_id: null, estimate: null }), 'k');
    expect(itemHasSource(item)).toBe(false);
    expect(chooseEstimate(item)).toBe(item);
  });

  it('o item estimado gravado volta ao editor e é reenviado só com o id', () => {
    const refeicao = {
      id: 1, user_id: 2, local_date: '2026-10-18', meal_slot: 'post_workout', eaten_at: null, source: 'ai_text', notes: null,
      description_raw: 'whey', ai_job_id: 'job-1',
      totals: { nutrients: {}, incomplete_fields: [], complete: true, nature: 'ESTIMATED', item_count: 1, limitations: [] },
      items: [{ id: 8, position: 0, food_id: null, portion_id: null, portion_label: null, label: 'whey protein', quantity: 30,
        unit: 'g', amount: 30, basis: 'g', nutrients: {}, nature: 'ESTIMATED', limitations: [], food_source: 'ai_estimate',
        citation: null, field_sources: [] }],
      engine_version: 'nutrition-v1', created_at: null, updated_at: null,
    } satisfies Meal;
    const form = editorFormFromMeal(refeicao);
    expect(form.items[0]).toMatchObject({ id: 8, food_id: null, usesEstimate: true, estimate: null });
    expect(form.items[0].originLabel).toBeTruthy();
    expect(itemsPayload(form.items)).toEqual([{ id: 8, food_id: null, quantity: 30, unit: 'g', portion_id: null }]);
  });
});
