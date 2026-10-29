import { describe, expect, it } from 'vitest';
import { recentExerciseVolumePath, volumeGrupamentosKeys } from './volume-grupamentos-queries';

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

  it('o objetivo entra nas chaves de cache', () => {
    expect(volumeGrupamentosKeys.recentVolume(400)).toEqual(['muscle-groups-recent-exercise-volume', 400, null]);
    expect(volumeGrupamentosKeys.recentVolume(400, 'g-outro')).not.toEqual(volumeGrupamentosKeys.recentVolume(400));
    expect(volumeGrupamentosKeys.recentVolumeRange('a', 'b', 'g-outro').at(-1)).toBe('g-outro');
    expect(volumeGrupamentosKeys.slotComparison(400, 'g-outro')).toEqual([
      'muscle-groups-slot-comparison',
      400,
      'g-outro',
    ]);
  });
});
