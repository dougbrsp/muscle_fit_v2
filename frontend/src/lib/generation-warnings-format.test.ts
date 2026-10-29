import { describe, expect, it } from 'vitest';
import { formatInsufficientVolumeGroup, formatSets } from '@/lib/generation-warnings-format';
import type { InsufficientTargetVolumeGroup } from '@/lib/workout-plans-types';

// Regressao do bug de producao: `g.equivalent_sets_achieved.toFixed(1)` com o
// motor deterministico (mode_a), que por um periodo devolveu so
// `deficit_equivalent_sets` — `undefined.toFixed` derrubava a tela inteira
// dentro do `.map`. Fichas geradas nesse periodo continuam no banco, entao a
// blindagem tem de valer para sempre, nao so ate o backend voltar ao normal.

describe('formatSets', () => {
  it('formata numero finito', () => {
    expect(formatSets(6)).toBe('6.0');
    expect(formatSets(6.25, 2)).toBe('6.25');
    expect(formatSets(0)).toBe('0.0');
  });

  it('devolve null (e nunca lanca) para nao-numero', () => {
    for (const value of [undefined, null, NaN, Infinity, -Infinity, '6', {}, []]) {
      expect(() => formatSets(value)).not.toThrow();
      expect(formatSets(value)).toBeNull();
    }
  });
});

describe('formatInsufficientVolumeGroup', () => {
  it('formato legado completo: achieved/target', () => {
    const legacy: InsufficientTargetVolumeGroup = {
      muscle_primary: 'Peitoral',
      equivalent_sets_achieved: 6,
      equivalent_sets_target: 20,
      exercises_added: 2,
      reason: 'catalogo_esgotado',
    };
    expect(formatInsufficientVolumeGroup(legacy)).toBe(
      'Peitoral: 6.0/20.0 séries equivalentes — catalogo_esgotado',
    );
  });

  it('formato do motor deterministico atual (achieved/target + deficit)', () => {
    expect(
      formatInsufficientVolumeGroup({
        muscle_primary: 'Ombros',
        muscle_group_id: 3,
        equivalent_sets_achieved: 1.5,
        equivalent_sets_target: 8,
        exercises_added: 1,
        reason: 'deficit_ativacao',
        deficit_equivalent_sets: 6.5,
      } as InsufficientTargetVolumeGroup),
    ).toBe('Ombros: 1.5/8.0 séries equivalentes — deficit_ativacao');
  });

  it('SEM equivalent_sets_achieved/target: cai no deficit, nao lanca', () => {
    const broken = {
      muscle_primary: 'Peitoral',
      muscle_group_id: 1,
      reason: 'deficit_ativacao',
      deficit_equivalent_sets: 14,
    } as InsufficientTargetVolumeGroup;
    expect(() => formatInsufficientVolumeGroup(broken)).not.toThrow();
    expect(formatInsufficientVolumeGroup(broken)).toBe(
      'Peitoral: déficit de 14.0 séries equivalentes — deficit_ativacao',
    );
  });

  it('sem NENHUM numero: sobra o nome do grupo, sem lancar', () => {
    expect(formatInsufficientVolumeGroup({ muscle_primary: 'Dorsal' })).toBe('Dorsal');
    expect(formatInsufficientVolumeGroup({ muscle_primary: 'Dorsal', reason: 'x' })).toBe('Dorsal — x');
  });

  it('item vazio / nulo / sem muscle_primary nao derruba a linha', () => {
    for (const value of [undefined, null, {}, { muscle_primary: null }, { muscle_primary: '  ' }]) {
      expect(() => formatInsufficientVolumeGroup(value as InsufficientTargetVolumeGroup)).not.toThrow();
      expect(formatInsufficientVolumeGroup(value as InsufficientTargetVolumeGroup)).toBe('Grupo muscular');
    }
  });

  it('so achieved, sem target', () => {
    expect(
      formatInsufficientVolumeGroup({ muscle_primary: 'Biceps', equivalent_sets_achieved: 2 }),
    ).toBe('Biceps: 2.0 séries equivalentes');
  });

  it('lista com formatos MISTURADOS (legado + novo + quebrado) renderiza inteira', () => {
    // Exatamente o `.map` do GenerationWarnings.tsx: um item ruim nao pode
    // derrubar os outros nem a tela.
    const mixed = [
      { muscle_primary: 'Peitoral', equivalent_sets_achieved: 6, equivalent_sets_target: 20, exercises_added: 2, reason: 'catalogo_esgotado' },
      { muscle_primary: 'Ombros', muscle_group_id: 3, reason: 'deficit_ativacao', deficit_equivalent_sets: 6.5 },
      { muscle_primary: 'Dorsal' },
      {},
    ] as InsufficientTargetVolumeGroup[];

    expect(() => mixed.map((g) => formatInsufficientVolumeGroup(g))).not.toThrow();
    expect(mixed.map((g) => formatInsufficientVolumeGroup(g))).toEqual([
      'Peitoral: 6.0/20.0 séries equivalentes — catalogo_esgotado',
      'Ombros: déficit de 6.5 séries equivalentes — deficit_ativacao',
      'Dorsal',
      'Grupo muscular',
    ]);
  });
});
