import { describe, expect, it, vi } from 'vitest';

import { MEAL_ITEMS_LIMIT } from './nutrition-constants';
import {
  addRegisteredFood,
  canOfferPublic,
  runFinishActions,
  manualFormWithName,
  registrationAfterBarcodeNotFound,
  registrationName,
} from './nutrition-food-registration';
import { editorItemFromFood, type MealEditorForm } from './nutrition-meal-form';

const RASCUNHO: MealEditorForm = {
  local_date: '2026-11-04',
  meal_slot: 'lunch',
  eaten_at: '12:30',
  notes: 'sem sal',
  items: [{ ...editorItemFromFood({ id: 1, name: 'Arroz', basis: 'g' }, 'novo-1'), quantity: 150 }],
  ai: null,
};

describe('nutrition-food-registration: cadastro dentro do registro da refeicao', () => {
  it('o termo buscado vira o nome (aparado) e o resto do formulario fica vazio', () => {
    expect(registrationName('  iogurte   proteico ')).toBe('iogurte proteico');
    expect(manualFormWithName('  iogurte ').name).toBe('iogurte');
    expect(manualFormWithName(undefined).name).toBe('');
  });

  it('o alimento salvo entra como item e o rascunho fica intacto (refeicao, hora, dia, anotacao, quantidades)', () => {
    const novo = addRegisteredFood(RASCUNHO, { id: 41, name: 'Iogurte proteico', basis: 'g' }, 'novo-2', MEAL_ITEMS_LIMIT);
    expect(novo.items).toHaveLength(2);
    expect(novo.items[1]).toMatchObject({ food_id: 41, label: 'Iogurte proteico', unit: 'g', quantity: '' });
    expect(novo.items[0]).toBe(RASCUNHO.items[0]);
    expect(novo.items[0].quantity).toBe(150);
    expect({ ...novo, items: [] }).toEqual({ ...RASCUNHO, items: [] });
    // Imutavel: o rascunho original (o que vale se o usuario fechar sem salvar) nao foi tocado.
    expect(RASCUNHO.items).toHaveLength(1);
  });

  it('no teto de itens devolve o mesmo rascunho', () => {
    const cheio = {
      ...RASCUNHO,
      items: Array.from({ length: MEAL_ITEMS_LIMIT }, (_, i) =>
        editorItemFromFood({ id: i + 1, name: 'A', basis: 'g' }, `k-${i}`),
      ),
    };
    expect(addRegisteredFood(cheio, { id: 99, name: 'B', basis: 'g' }, 'k-x', MEAL_ITEMS_LIMIT)).toBe(cheio);
  });

  it('codigo de barras sem resultado cai na digitacao com o codigo preenchido', () => {
    expect(registrationAfterBarcodeNotFound(' 7891000100103 ')).toEqual({ tab: 'manual', barcode: '7891000100103' });
    expect(registrationAfterBarcodeNotFound('   ')).toBeNull();
  });
});

describe('cadastro: alimento recém-cadastrado entra com a porção padrão', () => {
  it('serving_size 30 g vem preenchido e editável; sem porção fica vazio', () => {
    const com = addRegisteredFood(RASCUNHO, { id: 41, name: 'Aveia', basis: 'g', default_portion: { quantity: 30, unit: 'g' } }, 'novo-2', MEAL_ITEMS_LIMIT);
    expect(com.items[1]).toMatchObject({ quantity: 30, unit: 'g' });
    const sem = addRegisteredFood(RASCUNHO, { id: 42, name: 'Sem porção', basis: 'g', default_portion: null }, 'novo-3', MEAL_ITEMS_LIMIT);
    expect(sem.items[1]).toMatchObject({ quantity: '', unit: 'g' });
  });
});

describe('etapa final do cadastro', () => {
  const admin = { permissions: { can_promote: true } } as Parameters<typeof canOfferPublic>[0];
  const comum = { permissions: { can_promote: false } } as Parameters<typeof canOfferPublic>[0];

  it('a opção pública só existe com can_promote (admin, alimento privado)', () => {
    expect(canOfferPublic(admin)).toBe(true);
    expect(canOfferPublic(comum)).toBe(false);
  });

  it('nada marcado não chama ação nenhuma', async () => {
    const acoes = { addFavorite: vi.fn(), promote: vi.fn() };
    const r = await runFinishActions(5, { favorite: false, promote: false }, { favorite: false, promote: false }, acoes);
    expect(r.error).toBeNull();
    expect(acoes.addFavorite).not.toHaveBeenCalled();
    expect(acoes.promote).not.toHaveBeenCalled();
  });

  it('favorito e público funcionam isolados e juntos', async () => {
    const so = async (favorite: boolean, promote: boolean) => {
      const acoes = { addFavorite: vi.fn().mockResolvedValue({}), promote: vi.fn().mockResolvedValue({}) };
      const r = await runFinishActions(5, { favorite, promote }, { favorite: false, promote: false }, acoes);
      return { r, acoes };
    };
    const a = await so(true, false);
    expect(a.acoes.addFavorite).toHaveBeenCalledWith(5);
    expect(a.acoes.promote).not.toHaveBeenCalled();
    const b = await so(false, true);
    expect(b.acoes.promote).toHaveBeenCalledWith(5);
    expect(b.acoes.addFavorite).not.toHaveBeenCalled();
    const c = await so(true, true);
    expect(c.r.done).toEqual({ favorite: true, promote: true });
    expect(c.r.error).toBeNull();
  });

  it('falha parcial: o que deu certo não se refaz e a nova tentativa completa o resto', async () => {
    const acoes = {
      addFavorite: vi.fn().mockResolvedValue({}),
      promote: vi.fn().mockRejectedValueOnce(new Error('403')).mockResolvedValue({}),
    };
    const primeira = await runFinishActions(5, { favorite: true, promote: true }, { favorite: false, promote: false }, acoes);
    expect(primeira.error).toBeInstanceOf(Error);
    expect(primeira.done).toEqual({ favorite: true, promote: false });
    const segunda = await runFinishActions(5, { favorite: true, promote: true }, primeira.done, acoes);
    expect(segunda.error).toBeNull();
    expect(segunda.done).toEqual({ favorite: true, promote: true });
    expect(acoes.addFavorite).toHaveBeenCalledTimes(1);
    expect(acoes.promote).toHaveBeenCalledTimes(2);
  });

  it('falha no favorito não tenta o público e mantém o estado para nova tentativa', async () => {
    const acoes = { addFavorite: vi.fn().mockRejectedValue(new Error('409')), promote: vi.fn() };
    const r = await runFinishActions(5, { favorite: true, promote: true }, { favorite: false, promote: false }, acoes);
    expect(r.error).not.toBeNull();
    expect(r.done).toEqual({ favorite: false, promote: false });
    expect(acoes.promote).not.toHaveBeenCalled();
  });
});
