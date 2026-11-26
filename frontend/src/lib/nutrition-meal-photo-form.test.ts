import { describe, expect, it } from 'vitest';
import {
  CATALOG_ITEM_NATURE,
  ESTIMATE_ITEM_NATURE,
  MEAL_NATURE_OPTIONS,
  MEAL_PARSE_STATUS_OPTIONS,
  MEAL_PHOTO,
  PORTION_UNITS,
  nutritionLabel,
} from './nutrition-constants';
import { chooseCandidate, chooseCatalogFood, chooseEstimate, itemsPayload, mealCreatePayload, mealPreviewPayload, photoMetaView } from './nutrition-meal-form';
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

  it('evidência, faixa, incerteza e principal incerteza vêm do motor; leitura v1 não tem metadado', () => {
    const form = editorFormFromPhoto(
      {
        ...resultado([
          item({ index: 0, evidence: 'observed', grams_low: 120, grams_high: 180, uncertainty: 'hidden_portion' }),
          item({ index: 1 }),
        ]),
        main_uncertainty: 'preparation',
      },
      { date: 'x', slot: null },
    );
    expect(form.items[0].photoMeta).toEqual({ evidence: 'observed', gramsLow: 120, gramsHigh: 180, uncertainty: 'hidden_portion' });
    expect(form.items[1].photoMeta).toBeUndefined();
    expect(form.ai?.mainUncertainty).toBe('preparation');
    expect(photoMetaView(form.items[0])).toMatchObject({ evidence: 'observed', gramsLow: 120 });
    // Gramas trocadas pela pessoa: "informado por você", sem a faixa da IA.
    const editado = MEAL_PHOTO.edited_item_keeps_range ? { gramsLow: 120, gramsHigh: 180 } : { gramsLow: null, gramsHigh: null };
    expect(photoMetaView({ ...form.items[0], quantity: 200 })).toMatchObject({ evidence: MEAL_PHOTO.edited_item_evidence, ...editado });
    expect(photoMetaView({ ...form.items[0], unit: 'ml' })).toMatchObject({ evidence: MEAL_PHOTO.edited_item_evidence });
    // O metadado nunca vai à API.
    expect(JSON.stringify(mealPreviewPayload(form))).not.toMatch(/evidence|grams_low|uncertainty/);
  });

  it('trocar o alimento conta como edição; voltar ao original desfaz', () => {
    const [estimado] = editorFormFromPhoto(
      resultado([item({ evidence: 'observed', grams_low: 120, grams_high: 180, candidates: [CANDIDATO] })]),
      { date: 'x', slot: null },
    ).items;
    expect(photoMetaView(estimado)).toMatchObject({ evidence: 'observed', gramsLow: 120 });
    const trocado = chooseCandidate(estimado, CANDIDATO);
    const mantem = MEAL_PHOTO.edited_item_keeps_range ? { gramsLow: 120, gramsHigh: 180 } : { gramsLow: null, gramsHigh: null };
    expect(photoMetaView(trocado)).toMatchObject({ evidence: MEAL_PHOTO.edited_item_evidence, ...mantem });
    expect(photoMetaView(chooseEstimate(trocado))).toMatchObject({ evidence: 'observed', gramsLow: 120 });
  });

  it('buscar outro alimento no catálogo troca a origem, preserva chave e quantidade e conta como edição', () => {
    const form = editorFormFromPhoto(
      resultado([item({ index: 0, evidence: 'observed', grams_low: 120, grams_high: 180, candidates: [CANDIDATO] }), item({ index: 1 })]),
      { date: 'x', slot: null },
    );
    const [estimado, outro] = form.items;
    const AVEIA = { id: 77, name: 'Aveia', basis: 'g', default_portion: { quantity: 30, unit: 'g' }, brand: 'X' } as const;
    const trocado = chooseCatalogFood(estimado, AVEIA);
    expect(trocado).toMatchObject({
      key: estimado.key,
      food_id: 77,
      label: 'Aveia',
      basis: 'g',
      quantity: 120,
      unit: 'g',
      usesEstimate: false,
      lowConfidence: false,
      matchConfidence: null,
      originLabel: SELO_CATALOGO,
      originalFoodId: null,
    });
    expect(trocado.searchedFoods?.map((f) => f.id)).toEqual([77]);
    expect(chooseCatalogFood(trocado, AVEIA).searchedFoods).toHaveLength(1); // não duplica a opção
    const mantem = MEAL_PHOTO.edited_item_keeps_range ? { gramsLow: 120, gramsHigh: 180 } : { gramsLow: null, gramsHigh: null };
    expect(photoMetaView(trocado)).toMatchObject({ evidence: MEAL_PHOTO.edited_item_evidence, ...mantem });
    // Os demais itens não mudam; o corpo leva o alimento novo, sem estimativa.
    expect(itemsPayload([trocado, outro])?.[0]).toEqual({ quantity: 120, unit: 'g', food_id: 77, portion_id: null });
    // Unidade com porção não se aplica ao alimento novo: volta ao padrão do alimento.
    const comPorcao = chooseCatalogFood({ ...estimado, unit: PORTION_UNITS[0], portion_id: 5 }, AVEIA);
    expect(comPorcao).toMatchObject({ quantity: 30, unit: 'g', portion_id: null });
    // Base diferente (g × ml): usa a unidade e a quantidade padrão do alimento novo.
    const LEITE = { id: 78, name: 'Leite', basis: 'ml', default_portion: { quantity: 200, unit: 'ml' }, brand: null } as const;
    expect(chooseCatalogFood(estimado, LEITE)).toMatchObject({ quantity: 200, unit: 'ml' });
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

describe('paridade dos códigos fixos da foto com o espelho do motor', () => {
  it('uncertainty_none e a evidência do item editado existem nos enums do dono', () => {
    expect(MEAL_PHOTO.uncertainties).toContain(MEAL_PHOTO.uncertainty_none);
    expect(MEAL_PHOTO.evidences).toContain(MEAL_PHOTO.edited_item_evidence);
  });
});
