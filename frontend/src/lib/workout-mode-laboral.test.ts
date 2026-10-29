import { describe, expect, it } from 'vitest';
import {
  isLaboralItemDone,
  isLaboralPlanExercise,
  laboralPrescriptionOf,
  laboralSetRecords,
  laboralSummaryLabel,
} from './workout-mode-laboral';

const PADRAO = { sets: 3, reps: 15, load_kg: 2 };

describe('exercício laboral no Modo Treino (M100)', () => {
  it('só é laboral com a flag E o padrão pronto do backend', () => {
    expect(isLaboralPlanExercise({ is_laboral: true, laboral: PADRAO })).toBe(true);
    expect(laboralPrescriptionOf({ is_laboral: true, laboral: PADRAO })).toEqual(PADRAO);
    expect(isLaboralPlanExercise({ is_laboral: true, laboral: null })).toBe(false);
    expect(isLaboralPlanExercise({ is_laboral: false, laboral: PADRAO })).toBe(false);
    expect(isLaboralPlanExercise(undefined)).toBe(false);
  });

  it('concluído quando o servidor tem série laboral', () => {
    expect(isLaboralItemDone([{ set_type: 'laboral' }])).toBe(true);
    expect(isLaboralItemDone([{ set_type: 'warmup' }, { set_type: 'working' }])).toBe(false);
    expect(isLaboralItemDone(null)).toBe(false);
  });

  it('grava as N séries prescritas a partir do próximo set_number físico', () => {
    expect(laboralSetRecords(PADRAO, 4)).toEqual([
      { set_number: 4, set_type: 'laboral', reps: 15, weight_kg: 2 },
      { set_number: 5, set_type: 'laboral', reps: 15, weight_kg: 2 },
      { set_number: 6, set_type: 'laboral', reps: 15, weight_kg: 2 },
    ]);
    expect(laboralSetRecords({ sets: 1, reps: 10, load_kg: null }, 1)).toEqual([
      { set_number: 1, set_type: 'laboral', reps: 10, weight_kg: null },
    ]);
  });

  it('resumo só leitura', () => {
    expect(laboralSummaryLabel({ sets: 2, reps: 12, load_kg: null }, 'kg')).toBe('2 × 12');
    expect(laboralSummaryLabel(PADRAO, 'kg')).toMatch(/^3 × 15 · 2\s?kg$/);
  });
});
