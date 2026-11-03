import { describe, expect, it } from 'vitest';
import {
  CATALOG_ITEM_NATURE,
  ESTIMATE_ITEM_NATURE,
  MEAL_NATURE_OPTIONS,
  MEAL_PARSE_STATUS_OPTIONS,
  MEAL_PHOTO,
  nutritionLabel,
} from './nutrition-constants';
import { chooseCandidate, chooseEstimate, itemsPayload, mealCreatePayload, mealPreviewPayload } from './nutrition-meal-form';
import { editorFormFromPhoto } from './nutrition-meal-parse';
import type { MealParseCandidate, MealPhotoItem, MealPhotoResult } from './nutrition-types';

// Formulário da foto do prato (L10): selo pela natureza do motor, troca de origem, edição
// de gramas e corpo do preview. A tela só transporta: nenhum macro é calculado aqui.
const ESTIMATIVA = { name: 'moqueca', energy_kcal: 150, protein_g: 10, carbohydrates_g: 8, total_fat_g: 7, fiber_g: 1 };
const CANDIDATO: MealParseCandidate = {
  candidate_id: 9,
  food_id: 9,
  name: 'Arroz branco',
  score: 0.95,
  auto_accept: true,
  basis: 'g',
  source: 'taco',
  brand: null,
};

function item(extra: Partial<MealPhotoItem>): MealPhotoItem {
  return {
    index: 0,
    ai: { name: 'x', quantity: null, unit: 'g', grams_estimate: 120, preparation: null, confidence: 0.8 },
    status: 'estimated',
    candidates: [],
    food_id: null,
    match_confidence: null,
    quantity: 120,
    unit: 'g',
    estimate: ESTIMATIVA,
    candidate_for_catalog: true,
    uses_estimate: true,
    limitations: [],
    nature: ESTIMATE_ITEM_NATURE,
    ...extra,
  };
}

function resultado(itens: MealPhotoItem[]): MealPhotoResult {
  return {
    items: itens,
    limitations: [],
    notes: null,
    job_id: 'j1',
    user_id: 12,
    local_date: '2026-11-02',
    meal_slot: null,
    model: 'm',
    prompt_version: 'p',
    engine_version: 'e',
    plate_status: 'recognized',
  };
}

const SELO_ESTIMADO = nutritionLabel(MEAL_NATURE_OPTIONS, ESTIMATE_ITEM_NATURE);
const SELO_CATALOGO = nutritionLabel(MEAL_NATURE_OPTIONS, CATALOG_ITEM_NATURE);

describe('foto do prato: formulário', () => {
  it('o selo vem da natureza; sem ela, do estado do casamento', () => {
    const form = editorFormFromPhoto(
      resultado([
        item({ index: 0 }),
        item({ index: 1, status: 'selected', food_id: 9, candidates: [CANDIDATO], uses_estimate: false, nature: CATALOG_ITEM_NATURE }),
        item({ index: 2, status: 'choice', candidates: [CANDIDATO], uses_estimate: false, nature: null }),
      ]),
      { date: 'x', slot: null },
    );
    expect(form.items.map((i) => i.originLabel)).toEqual([
      SELO_ESTIMADO,
      SELO_CATALOGO,
      nutritionLabel(MEAL_PARSE_STATUS_OPTIONS, 'choice'),
    ]);
    expect(form.items[0].usesEstimate).toBe(true);
    expect(form.ai?.ai_source).toBe(MEAL_PHOTO.meal_source);
  });

  it('trocar a origem atualiza o selo e o destaque, nos dois sentidos', () => {
    const [estimado] = editorFormFromPhoto(resultado([item({ candidates: [CANDIDATO] })]), { date: 'x', slot: null }).items;
    const noCatalogo = chooseCandidate(estimado, CANDIDATO);
    expect(noCatalogo).toMatchObject({ food_id: 9, usesEstimate: false, originLabel: SELO_CATALOGO });
    const deVolta = chooseEstimate(noCatalogo);
    expect(deVolta).toMatchObject({ food_id: null, usesEstimate: true, originLabel: SELO_ESTIMADO, quantity: 120 });
  });

  it('item estimado vai com a estimativa, e os gramas editados seguem no corpo', () => {
    const [estimado] = editorFormFromPhoto(resultado([item({})]), { date: 'x', slot: null }).items;
    expect(itemsPayload([{ ...estimado, quantity: 200 }])).toEqual([
      { quantity: 200, unit: 'g', food_id: null, estimate: ESTIMATIVA, portion_id: null },
    ]);
    expect(itemsPayload([{ ...estimado, quantity: '' }])).toBeNull();
  });

  it('sem refeição há preview, mas não há gravação', () => {
    const form = editorFormFromPhoto(resultado([item({})]), { date: 'x', slot: null });
    expect(mealCreatePayload(form)).toBeNull();
    expect(mealPreviewPayload(form)).toMatchObject({
      meal_slot: null,
      ai_job_id: 'j1',
      ai_source: MEAL_PHOTO.meal_source,
    });
    expect(mealPreviewPayload({ ...form, items: [] })).toBeNull();
    expect(mealPreviewPayload({ ...form, meal_slot: 'lunch' }, 7)).toMatchObject({ meal_slot: 'lunch', meal_id: 7 });
  });
});
