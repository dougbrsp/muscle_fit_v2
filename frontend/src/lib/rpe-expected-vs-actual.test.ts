import { describe, expect, it } from 'vitest';

import { formatLoggedSets, setFormFromRow, setUpdatePayload } from './exercise-set-edit';
import { rpeExpectedCountsText, rpeExpectedSuffix } from './rpe-expected-vs-actual';

const below = { status: 'below', label: 'Abaixo do esperado', delta: -1 } as const;

describe('rpeExpectedSuffix', () => {
  it('esperado com comparação', () => {
    expect(rpeExpectedSuffix({ rpe_target_planned: 9, rpe_vs_expected: below })).toBe(
      ' (esperado 9 · Abaixo do esperado)',
    );
  });
  it('sem esperado: vazio (série antiga)', () => {
    expect(rpeExpectedSuffix({})).toBe('');
    expect(rpeExpectedSuffix({ rpe_target_planned: null, rpe_vs_expected: null })).toBe('');
  });
  it('comparação nula ou ausente: vazio (a decisão é do servidor)', () => {
    expect(rpeExpectedSuffix({ rpe_target_planned: 8.5, rpe_vs_expected: null })).toBe('');
    expect(rpeExpectedSuffix({ rpe_target_planned: 8.5 })).toBe('');
  });
  it('rótulo vazio da API cai na reserva do módulo', () => {
    expect(rpeExpectedSuffix({ rpe_target_planned: 9, rpe_vs_expected: { ...below, label: '' } })).toBe(
      ' (esperado 9 · abaixo)',
    );
  });
});

describe('rpeExpectedCountsText', () => {
  it('com rótulos da API (minúsculas na frase)', () => {
    const labels = { below: 'Abaixo do esperado', on_target: 'No esperado', above: 'Acima do esperado' };
    expect(rpeExpectedCountsText({ below: 1, on_target: 2, above: 0, compared: 3 }, labels)).toBe(
      '3 séries comparadas: 1 abaixo do esperado, 2 no esperado',
    );
  });
  it('rótulo vazio ou só espaços cai na reserva', () => {
    const labels = { below: '', on_target: '   ', above: 'Acima do esperado' };
    expect(rpeExpectedCountsText({ below: 1, on_target: 1, above: 1, compared: 3 }, labels)).toBe(
      '3 séries comparadas: 1 abaixo, 1 no esperado, 1 acima do esperado',
    );
  });
  it('contagens (reserva sem rótulos)', () => {
    expect(rpeExpectedCountsText({ below: 0, on_target: 1, above: 1, compared: 2 })).toBe(
      '2 séries comparadas: 1 no esperado, 1 acima',
    );
    expect(rpeExpectedCountsText({ below: 1, on_target: 0, above: 0, compared: 1 })).toBe(
      '1 série comparada: 1 abaixo',
    );
  });
  it('nulas ou sem comparação', () => {
    expect(rpeExpectedCountsText(null)).toBeNull();
    expect(rpeExpectedCountsText(undefined)).toBeNull();
    expect(rpeExpectedCountsText({ below: 0, on_target: 0, above: 0, compared: 0 })).toBeNull();
  });
});

describe('integração com o resumo e o PUT', () => {
  const base = { set_number: 1, reps: 8, weight_kg: 20, rpe: 8 };
  it('resumo com e sem esperado', () => {
    expect(formatLoggedSets([base])).toBe('Série 1: 8x 20kg @RPE 8');
    expect(formatLoggedSets([{ ...base, rpe_target_planned: 9, rpe_vs_expected: below }])).toBe(
      'Série 1: 8x 20kg @RPE 8 (esperado 9 · Abaixo do esperado)',
    );
  });
  it('o PUT nunca envia os campos do esperado', () => {
    const form = setFormFromRow({ ...base, rpe_target_planned: 9, rpe_vs_expected: below } as never, 0);
    const payload = setUpdatePayload(form, 0);
    expect(payload).not.toHaveProperty('rpe_target_planned');
    expect(payload).not.toHaveProperty('rpe_vs_expected');
  });
});
