import { describe, expect, it } from 'vitest';
import { EXERCISE_PROGRESSION_LABELS } from './dashboard-status';
import type { ExerciseProgressionCounts } from './dashboard-v2-types';
import {
  groupCompositionText,
  scopeDescription,
  scopeFootnote,
  scopeLabel,
  scopeWindowText,
  type ProgressionScope,
} from './progression-scope';

const SCOPE: ProgressionScope = {
  code: 'exercise_last_vs_previous_exposure',
  entity: 'exercise',
  metric: 'exposure',
  comparator: 'last_vs_previous',
  label: 'Última exposição vs. anterior',
  description: 'Compara a última exposição com a anterior.',
};
const WINDOW = { kind: '30d', date_from: '2026-10-15', date_to: '2026-11-14', timezone: 'America/Sao_Paulo', days: 30 };
const ZERO: ExerciseProgressionCounts = {
  progressing: 0,
  stable: 0,
  plateau_suspected: 0,
  regressing: 0,
  comparable_total: 0,
  not_comparable: 0,
  insufficient_data: 0,
};

describe('progression-scope', () => {
  it('rótulo e descrição vêm do descritor; sem ele, null', () => {
    expect(scopeLabel(SCOPE)).toBe(SCOPE.label);
    expect(scopeDescription(SCOPE)).toBe(SCOPE.description);
    expect(scopeLabel(undefined)).toBeNull();
    expect(scopeLabel(null)).toBeNull();
    expect(scopeDescription(undefined)).toBeNull();
  });

  it('formata a janela em datas pt-BR, com singular e sem janela', () => {
    expect(scopeWindowText(WINDOW)).toBe('15/10/2026 a 14/11/2026 (30 dias)');
    expect(scopeWindowText({ ...WINDOW, days: 1 })).toContain('(1 dia)');
    expect(scopeWindowText(undefined)).toBeNull();
  });

  it('rodapé combina janela e regra; cada parte é opcional', () => {
    expect(scopeFootnote(WINDOW, 'progression-status-v2')).toBe(
      'Janela 15/10/2026 a 14/11/2026 (30 dias) · regra progression-status-v2',
    );
    expect(scopeFootnote(undefined, 'progression-status-v2')).toBe('regra progression-status-v2');
    expect(scopeFootnote(WINDOW, null)).toBe('Janela 15/10/2026 a 14/11/2026 (30 dias)');
    expect(scopeFootnote(undefined, undefined)).toBeNull();
  });

  it('composição do grupo: só contagens positivas, com os rótulos do espelho', () => {
    const L = EXERCISE_PROGRESSION_LABELS;
    expect(groupCompositionText({ ...ZERO, progressing: 2, stable: 1, regressing: 3, comparable_total: 6 })).toBe(
      `${L.PROGRESSING} 2 · ${L.STABLE} 1 · ${L.REGRESSING} 3`,
    );
    expect(groupCompositionText({ ...ZERO, not_comparable: 1 })).toBe(`${L.NOT_COMPARABLE} 1`);
  });

  it('composição do grupo: tudo zerado ou ausente não mostra nada', () => {
    expect(groupCompositionText(ZERO)).toBeNull();
    expect(groupCompositionText(undefined)).toBeNull();
    expect(groupCompositionText(null)).toBeNull();
  });
});
