import { describe, expect, it } from 'vitest';
import { recentExerciseVolumePath, trainingVolumePath, volumeGrupamentosKeys } from './volume-grupamentos-queries';

describe('leituras de /volume-grupamentos com filtro de objetivo (M97 F4)', () => {
  it('sem objetivo, as URLs de sempre', () => {
    expect(recentExerciseVolumePath(400)).toBe('/analytics/muscle-groups-recent-exercise-volume?limit=400');
    expect(recentExerciseVolumePath(400, { dateFrom: '2026-10-01', dateTo: '2026-10-07' })).toBe(
      '/analytics/muscle-groups-recent-exercise-volume?date_from=2026-10-01&date_to=2026-10-07',
    );
    expect(recentExerciseVolumePath(400, null, null)).toBe('/analytics/muscle-groups-recent-exercise-volume?limit=400');
  });

  it('com objetivo, o `goal` vai na query', () => {
    expect(recentExerciseVolumePath(400, null, 'g-outro')).toBe(
      '/analytics/muscle-groups-recent-exercise-volume?limit=400&goal=g-outro',
    );
    expect(recentExerciseVolumePath(400, { dateFrom: '2026-10-01', dateTo: '2026-10-31' }, 'g-outro')).toBe(
      '/analytics/muscle-groups-recent-exercise-volume?date_from=2026-10-01&date_to=2026-10-31&goal=g-outro',
    );
  });

  it('`include` e `activity_id` pedem os blocos prontos', () => {
    expect(recentExerciseVolumePath(400, null, null, { include: ['weekly', 'equipment_weekly'] })).toBe(
      '/analytics/muscle-groups-recent-exercise-volume?limit=400&include=weekly%2Cequipment_weekly',
    );
    expect(recentExerciseVolumePath(400, null, 'g', { include: ['heatmap'], activityId: 7 })).toBe(
      '/analytics/muscle-groups-recent-exercise-volume?limit=400&goal=g&include=heatmap&activity_id=7',
    );
    expect(
      recentExerciseVolumePath(400, { dateFrom: '2026-10-01', dateTo: '2026-10-31' }, null, { include: ['heatmap'] }),
    ).toBe('/analytics/muscle-groups-recent-exercise-volume?date_from=2026-10-01&date_to=2026-10-31&include=heatmap');
    expect(trainingVolumePath(21, 'week')).toBe('/analytics/training-volume?days=21&granularity=week');
  });

  it('cada combinação de parâmetros tem chave de cache própria', () => {
    expect(volumeGrupamentosKeys.recentVolume(400)).toEqual(['muscle-groups-recent-exercise-volume', 400, null, '']);
    expect(volumeGrupamentosKeys.recentVolume(400, 'g-outro')).not.toEqual(volumeGrupamentosKeys.recentVolume(400));
    expect(volumeGrupamentosKeys.recentVolume(400, null, ['weekly'])).not.toEqual(volumeGrupamentosKeys.recentVolume(400));
    const range = { dateFrom: 'a', dateTo: 'b' };
    expect(volumeGrupamentosKeys.heatmap(range, null, 'g-outro').at(-1)).toBe('g-outro');
    expect(volumeGrupamentosKeys.heatmap(null, 7)).not.toEqual(volumeGrupamentosKeys.heatmap(null, 8));
    expect(volumeGrupamentosKeys.heatmap(range, null)).not.toEqual(volumeGrupamentosKeys.heatmap(null, null));
    expect(volumeGrupamentosKeys.trainingVolume(14, 'd7')).not.toEqual(volumeGrupamentosKeys.trainingVolume(14, 'week'));
    expect(volumeGrupamentosKeys.slotComparison(400, 'g-outro')).toEqual([
      'muscle-groups-slot-comparison',
      400,
      'g-outro',
    ]);
  });
});
