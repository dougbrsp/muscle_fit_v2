import { describe, expect, it } from 'vitest';
import { dashboardKeys, overviewPath, progressionByMuscleGroupPath } from './dashboard-api';
import { EMPTY_DASHBOARD_FILTERS, dashboardWindowQuery, type DashboardFilters } from './dashboard-filters';

function params(path: string): Record<string, string> {
  return Object.fromEntries(new URLSearchParams(path.split('?')[1] ?? ''));
}

function filtros(over: Partial<DashboardFilters>): DashboardFilters {
  return { ...EMPTY_DASHBOARD_FILTERS, ...over };
}

describe('rota de grupos usa a mesma janela do overview', () => {
  it('window=cycle vai como `window`, sem converter para days', () => {
    const win = dashboardWindowQuery(filtros({ window: 'cycle' }));
    const grupos = params(progressionByMuscleGroupPath(win));
    expect(grupos.window).toBe('cycle');
    expect(grupos.days).toBeUndefined();
    expect(grupos).toEqual(params(overviewPath(win)));
  });

  it('período personalizado manda window=custom e as duas datas, iguais às do overview', () => {
    const win = dashboardWindowQuery(
      filtros({ window: 'custom', dateFrom: '2026-10-01', dateTo: '2026-10-20' }),
    );
    const grupos = params(progressionByMuscleGroupPath(win));
    expect(grupos).toEqual({ window: 'custom', date_from: '2026-10-01', date_to: '2026-10-20' });
    expect(grupos).toEqual(params(overviewPath(win)));
  });

  it('a chave de cache muda com a janela (cycle não reaproveita 28 dias)', () => {
    const cycle = dashboardKeys.progressionByMuscleGroup(dashboardWindowQuery(filtros({ window: 'cycle' })));
    const d28 = dashboardKeys.progressionByMuscleGroup(dashboardWindowQuery(filtros({ window: '28d' })));
    expect(cycle).not.toEqual(d28);
  });
});
