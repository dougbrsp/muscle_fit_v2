import { describe, expect, it } from 'vitest';
import { statusVisual } from './dashboard-status';
import { FIXTURE_EVALUATION } from './methodology-volume-fixtures';
import type { MethodologyAxisEvaluation, MethodologyGroupEvaluation } from './methodology-volume-types';
import { buildWeeklySetsBarRow, buildWeeklySetsBars, WEEKLY_SETS_BAR_COLORS } from './weekly-sets-bar';

const baseGroup = FIXTURE_EVALUATION.groups[0];

function weekly(overrides: Partial<MethodologyAxisEvaluation>): MethodologyAxisEvaluation {
  return { ...baseGroup.weekly_sets_axis, reason: null, value_weekly: null, range_min: null, range_max: null, ...overrides };
}

function group(overrides: Partial<MethodologyGroupEvaluation>): MethodologyGroupEvaluation {
  return { ...baseGroup, ...overrides };
}

describe('buildWeeklySetsBarRow', () => {
  it('escala até 1,5 × máx: trechos e marcador', () => {
    const row = buildWeeklySetsBarRow(
      group({
        muscle_group_name: 'Costas',
        weekly_sets_axis: weekly({ status: 'BELOW_RANGE', value_weekly: 12, range_min: 18, range_max: 32 }),
      }),
    );
    expect(row.text).toBe('12 / 18–32');
    expect(row.segments).toEqual({ belowPct: 37.5, withinPct: 29.2, abovePct: 33.3 });
    expect(row.markerPct).toBe(25);
    expect(row.overflow).toBe(false);
    expect(row.status).toBe(statusVisual('methodologyVolume', 'BELOW_RANGE'));
  });

  it('valor além da escala prende o marcador em 100% e marca overflow', () => {
    const row = buildWeeklySetsBarRow(
      group({ weekly_sets_axis: weekly({ status: 'ABOVE_RANGE', value_weekly: 40, range_min: 10, range_max: 20 }) }),
    );
    expect(row.markerPct).toBe(100);
    expect(row.overflow).toBe(true);
  });

  it('sem meta: barra neutra, sem marcador; não treinado vale 0', () => {
    const row = buildWeeklySetsBarRow(
      group({ weekly_sets_axis: weekly({ status: 'NOT_DIRECTLY_TRAINED', value_weekly: null }) }),
    );
    expect(row.hasRange).toBe(false);
    expect(row.segments).toBeNull();
    expect(row.markerPct).toBeNull();
    expect(row.text).toBe('0 · sem meta');
  });

  it('laranja abaixo e verde dentro (domínio); vermelho acima só na barra', () => {
    expect(WEEKLY_SETS_BAR_COLORS.below).toBe(statusVisual('methodologyVolume', 'BELOW_RANGE').hex);
    expect(WEEKLY_SETS_BAR_COLORS.within).toBe(statusVisual('methodologyVolume', 'WITHIN_RANGE').hex);
    expect(WEEKLY_SETS_BAR_COLORS.above).toBe('#e03131');
  });
});

describe('buildWeeklySetsBars', () => {
  it('só grupos com macro-grupo, ordenados pelo id do macro-grupo e pelo nome', () => {
    const evaluation = {
      ...FIXTURE_EVALUATION,
      groups: [
        group({ muscle_group_id: 1, muscle_group_name: 'Tríceps', volume_class_id: 2, volume_class_name: 'Pequenos' }),
        group({ muscle_group_id: 6, muscle_group_name: 'Abdômen', volume_class_id: 3, volume_class_name: 'Outros' }),
        group({ muscle_group_id: 2, muscle_group_name: 'Sem classe', volume_class_id: null, volume_class_name: null }),
        group({ muscle_group_id: 3, muscle_group_name: 'Peitoral', volume_class_id: 1, volume_class_name: 'Grandes' }),
        group({ muscle_group_id: 4, muscle_group_name: 'Bíceps', volume_class_id: 2, volume_class_name: 'Pequenos' }),
        group({ muscle_group_id: 5, muscle_group_name: 'Costas', volume_class_id: 1, volume_class_name: 'Grandes' }),
      ],
    };
    expect(buildWeeklySetsBars(evaluation).map((row) => row.groupName)).toEqual([
      'Costas',
      'Peitoral',
      'Bíceps',
      'Tríceps',
      'Abdômen',
    ]);
    expect(buildWeeklySetsBars(null)).toEqual([]);
  });
});
