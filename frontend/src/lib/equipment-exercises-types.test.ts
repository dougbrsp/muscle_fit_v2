// Convenção de carga do par equipamento/exercício (migração 045).
//
// O que estes testes protegem: o vocabulário e os rótulos desta borda são o
// espelho de `api/app/load_conventions.py`. Se um dos dois ganhar um valor
// novo sem o outro, a tela grava algo que o servidor rejeita com 400 — ou
// pior, mostra "—" para uma convenção que existe.

import { describe, expect, it } from 'vitest';

import {
  LOAD_CONVENTIONS,
  LOAD_CONVENTION_EMPTY_LABEL,
  LOAD_CONVENTION_LABELS,
  LOAD_CONVENTION_OPTIONS,
  loadConventionLabel,
  toLoadConventionPayload,
} from './equipment-exercises-types';

describe('vocabulário de convenção de carga', () => {
  it('é exatamente o ENUM da migração 045, na mesma ordem', () => {
    expect(LOAD_CONVENTIONS).toEqual([
      'total',
      'per_side',
      'per_dumbbell',
      'assisted',
      'bodyweight',
      'bodyweight_plus',
    ]);
  });

  it('tem rótulo pt-BR para todo valor, sem sobra nem falta', () => {
    expect(Object.keys(LOAD_CONVENTION_LABELS).sort()).toEqual([...LOAD_CONVENTIONS].sort());
    expect(LOAD_CONVENTION_OPTIONS).toHaveLength(LOAD_CONVENTIONS.length);
    for (const option of LOAD_CONVENTION_OPTIONS) {
      expect(option.label.trim()).not.toBe('');
    }
  });

  it('deixa explícito no rótulo que assistido inverte a direção do progresso', () => {
    expect(LOAD_CONVENTION_LABELS.assisted).toContain('menos kg');
  });
});

describe('loadConventionLabel', () => {
  it('traduz o valor cadastrado', () => {
    expect(loadConventionLabel('per_dumbbell')).toBe('Por halter');
    expect(loadConventionLabel('total')).toBe('Carga total');
  });

  it('diz o que acontece quando ninguém cadastrou (null = assume total)', () => {
    expect(loadConventionLabel(null)).toBe(LOAD_CONVENTION_EMPTY_LABEL);
    expect(loadConventionLabel(undefined)).toBe(LOAD_CONVENTION_EMPTY_LABEL);
    expect(LOAD_CONVENTION_EMPTY_LABEL).toContain('total');
  });
});

describe('toLoadConventionPayload', () => {
  it('converte o valor do Select no corpo do PUT', () => {
    expect(toLoadConventionPayload('assisted')).toBe('assisted');
  });

  it('limpa com null quando o Select é esvaziado', () => {
    // `clearable` devolve null; o Mantine também pode devolver string vazia.
    expect(toLoadConventionPayload(null)).toBeNull();
    expect(toLoadConventionPayload('')).toBeNull();
  });

  it('nunca envia valor fora do vocabulário (o servidor responderia 400)', () => {
    expect(toLoadConventionPayload('por_lado')).toBeNull();
  });
});
