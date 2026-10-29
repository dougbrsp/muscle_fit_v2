import { describe, expect, it } from 'vitest';
import { formatLoggedSets, setFormFromRow, setUpdatePayload } from './exercise-set-edit';
import { hasSides, setSidesPayload, setTypeTakesSides, sidesSuffix, withSavedCanonical } from './set-sides';

describe('M102: lados da série unilateral (transporte, sem regra numérica)', () => {
  it('sem lado, payload vazio (relógio e série bilateral iguais a antes)', () => {
    expect(setSidesPayload({})).toEqual({});
    expect(setSidesPayload({ reps_left: null, rpe_right: null })).toEqual({});
    expect(hasSides(null)).toBe(false);
  });

  it('com lado, os quatro campos vão explícitos e o DECIMAL vira número', () => {
    expect(setSidesPayload({ reps_left: 10, reps_right: 8 })).toEqual({
      reps_left: 10,
      reps_right: 8,
      rpe_left: null,
      rpe_right: null,
    });
    expect(hasSides({ rpe_left: '8.50' } as never)).toBe(true);
  });

  it('o canônico vem da resposta do servidor, nunca de um cálculo da tela', () => {
    const values = { reps: null, rpe: null, reps_left: 10, reps_right: 8 };
    expect(withSavedCanonical(values, { reps: 8, rpe: 9 })).toMatchObject({ reps: 8, rpe: 9 });
    // Sem lados, os valores digitados seguem como estão.
    expect(withSavedCanonical({ reps: 10, rpe: 8 }, { reps: 1, rpe: 1 })).toEqual({ reps: 10, rpe: 8 });
  });

  it('só trabalho e backoff pedem os lados (v1)', () => {
    expect(setTypeTakesSides(undefined)).toBe(true);
    expect(setTypeTakesSides('working')).toBe(true);
    expect(setTypeTakesSides('backoff')).toBe(true);
    expect(setTypeTakesSides('warmup')).toBe(false);
    expect(setTypeTakesSides('drop_set')).toBe(false);
  });

  it('texto pronto da API vira sufixo; sem texto, nada', () => {
    expect(sidesSuffix({ sides_text: 'reps E 10 · D 8' })).toBe(' (reps E 10 · D 8)');
    expect(sidesSuffix({ sides_text: null })).toBe('');
  });

  it('edição do registro: carrega e reenvia os lados', () => {
    const form = setFormFromRow(
      { set_number: 1, reps: 8, weight_kg: '20.00', rpe: '9.00', reps_left: 10, reps_right: 8, rpe_left: '8.00', rpe_right: '9.00' },
      0,
    );
    expect(form).toMatchObject({ reps_left: 10, reps_right: 8, rpe_left: 8, rpe_right: 9 });
    expect(setUpdatePayload({ ...form, reps_right: 11 }, 0)).toMatchObject({
      reps_left: 10,
      reps_right: 11,
      rpe_left: 8,
      rpe_right: 9,
    });
    const plain = setUpdatePayload(setFormFromRow({ set_number: 1, reps: 10, weight_kg: 20, rpe: 8 }, 0), 0);
    expect(plain).toMatchObject({ reps_left: null, reps_right: null, rpe_left: null, rpe_right: null });
  });

  it('lista de exercícios lançados mostra os lados do servidor', () => {
    expect(
      formatLoggedSets([{ set_number: 1, reps: 8, weight_kg: 20, rpe: 9, sides_text: 'reps E 10 · D 8; RPE E 8 · D 9' }]),
    ).toBe('Série 1: 8x 20kg @RPE 9 (reps E 10 · D 8; RPE E 8 · D 9)');
  });
});
