import { describe, expect, it, vi } from 'vitest';

vi.mock('./api', () => ({ apiFetch: vi.fn() }));

import { apiFetch } from './api';
import {
  bodyRows,
  fetchNutritionDashboard,
  fetchNutritionToday,
  hasSeries,
  hasValues,
  macroRows,
  MACRO_ORDER,
  ringParts,
  shareRows,
  shortDateLabel,
  todayKeys,
  weekAdherenceRows,
  weekBarRows,
  type DashboardDay,
  type DashboardWeek,
} from './nutrition-dashboard';
import { diaryKeys } from './nutrition-api';
import type { BalanceRow, NutritionWeek } from './nutrition-types';

function linha(field: string, extra: Partial<BalanceRow> = {}): BalanceRow {
  return {
    field,
    target: 2000,
    intake: 1500,
    intake_complete: true,
    remaining: 500,
    share: 0.75,
    within_target: 1500,
    to_consume: 500,
    excess: 0,
    ...extra,
  };
}

describe('macroRows', () => {
  it('ordena kcal, proteína, carboidrato e gordura e ignora o restante (fibra)', () => {
    const rows = [linha('fiber_g'), linha('total_fat_g'), linha('energy_kcal'), linha('carbohydrates_g'), linha('protein_g')];
    expect(macroRows(rows).map((r) => r.field)).toEqual([...MACRO_ORDER]);
  });

  it('omite macro ausente da resposta', () => {
    expect(macroRows([linha('protein_g')]).map((r) => r.field)).toEqual(['protein_g']);
  });
});

describe('ringParts', () => {
  it('usa as partes prontas do motor, sem fatia de excesso quando não passou da meta', () => {
    const partes = ringParts(linha('energy_kcal'));
    expect(partes?.map((p) => [p.key, p.value])).toEqual([
      ['within', 1500],
      ['to_consume', 500],
    ]);
  });

  it('mostra o excesso quando passou da meta', () => {
    const partes = ringParts(linha('energy_kcal', { within_target: 2000, to_consume: 0, excess: 300, remaining: -300, intake: 2300, share: 1.15 }));
    expect(partes?.map((p) => p.key)).toEqual(['within', 'to_consume', 'excess']);
    expect(partes?.[2].value).toBe(300);
  });

  it('com meta e sem refeição a rosca é a meta toda a consumir', () => {
    const partes = ringParts(linha('energy_kcal', { intake: null, share: null, remaining: null, within_target: 0, to_consume: 2000, excess: 0 }));
    expect(partes?.map((p) => [p.key, p.value])).toEqual([
      ['within', 0],
      ['to_consume', 2000],
    ]);
  });

  it('sem meta ou com ingestão incompleta não há rosca', () => {
    expect(ringParts(linha('energy_kcal', { within_target: null, to_consume: null, excess: null }))).toBeNull();
  });
});

describe('weekBarRows', () => {
  const semana = {
    days: [
      { local_date: '2026-10-12', target: { kcal: 2000 }, intake_kcal: 1800, intake_incomplete: false },
      { local_date: '2026-10-13', target: { kcal: 2100 }, intake_kcal: null, intake_incomplete: true },
      { local_date: '2026-10-14', target: null, intake_kcal: null, intake_incomplete: false },
    ],
  } as unknown as NutritionWeek;

  it('usa o kcal que a API já decidiu (nulo quando incompleto ou sem refeição)', () => {
    const rows = weekBarRows(semana, (d) => d.slice(8));
    expect(rows).toEqual([
      { date: '2026-10-12', label: '12', consumido: 1800, meta: 2000, incompleto: false },
      { date: '2026-10-13', label: '13', consumido: null, meta: 2100, incompleto: true },
      { date: '2026-10-14', label: '14', consumido: null, meta: null, incompleto: false },
    ]);
  });

  it('semana ainda não carregada = sem barras', () => {
    expect(weekBarRows(undefined, shortDateLabel)).toEqual([]);
  });
});

describe('hasValues', () => {
  it('só há dado quando algum valor existe (zero conta)', () => {
    expect(hasValues([{ v: null }, { v: 0 }], (r) => r.v)).toBe(true);
    expect(hasValues([{ v: null }], (r) => r.v)).toBe(false);
    expect(hasValues([], (r: { v: number | null }) => r.v)).toBe(false);
  });
});

describe('shareRows', () => {
  it('converte só a unidade da fração do motor (0,75 -> 75%) e preserva os marcadores do dia', () => {
    const dias = [
      { local_date: '2026-10-12', logged: true, intake_incomplete: false, macros: { protein_g: linha('protein_g', { share: 0.9 }) } },
      { local_date: '2026-10-13', logged: false, intake_incomplete: true, macros: {} },
    ] as unknown as DashboardDay[];
    const rows = shareRows(dias, 'protein_g', shortDateLabel);
    expect(rows[0]).toMatchObject({ label: '12/10', percent: 90, registrado: true });
    expect(rows[1]).toMatchObject({ percent: null, incompleto: true });
  });
});

describe('weekAdherenceRows', () => {
  it('usa a aderência de cada semana e marca a parcial e a incompleta', () => {
    const semanas = [
      {
        start: '2026-10-05',
        partial: true,
        report: { adherence: { kcal_share: 1.02, protein_share: 0.95, logged_days: 3, valid_week: false } },
      },
      {
        start: '2026-10-12',
        partial: false,
        report: { adherence: { kcal_share: 0.9, protein_share: 1, logged_days: 6, valid_week: true } },
      },
    ] as unknown as DashboardWeek[];
    expect(weekAdherenceRows(semanas, shortDateLabel)).toEqual([
      { start: '2026-10-05', label: '05/10', kcal: 102, proteina: 95, parcial: true, valida: false, diasRegistrados: 3 },
      { start: '2026-10-12', label: '12/10', kcal: 90, proteina: 100, parcial: false, valida: true, diasRegistrados: 6 },
    ]);
  });
});

describe('bodyRows e hasSeries', () => {
  const pontos = [
    { local_date: '2026-10-01', weight_kg: 80, weight_trend_kg: 80, body_fat_percent: null, muscle_mass_kg: null },
    { local_date: '2026-10-08', weight_kg: 79.5, weight_trend_kg: 79.7, body_fat_percent: 18, muscle_mass_kg: 36 },
  ];

  it('remonta os pontos com a média móvel da API', () => {
    const rows = bodyRows(pontos, shortDateLabel);
    expect(rows[1]).toEqual({ date: '2026-10-08', label: '08/10', peso: 79.5, media: 79.7, gordura: 18, musculo: 36 });
  });

  it('só há gráfico quando a série tem algum valor', () => {
    const rows = bodyRows([pontos[0]], shortDateLabel);
    expect(hasSeries(rows, 'peso')).toBe(true);
    expect(hasSeries(rows, 'gordura')).toBe(false);
    expect(hasSeries([], 'peso')).toBe(false);
  });
});

describe('chamadas', () => {
  it('monta a URL do painel com a janela, nunca com user_id (sem janela = padrão da API)', async () => {
    vi.mocked(apiFetch).mockClear();
    vi.mocked(apiFetch).mockResolvedValue({} as never);
    await fetchNutritionDashboard(null);
    await fetchNutritionDashboard(28);
    expect(vi.mocked(apiFetch).mock.calls.map((c) => c[0])).toEqual([
      '/nutrition/dashboard',
      '/nutrition/dashboard?days=28',
    ]);
  });

  it('o card de hoje lê /nutrition/today e sua chave está sob a do diário (salvar refeição o invalida)', async () => {
    vi.mocked(apiFetch).mockClear();
    vi.mocked(apiFetch).mockResolvedValue({} as never);
    await fetchNutritionToday();
    expect(vi.mocked(apiFetch).mock.calls.map((c) => c[0])).toEqual(['/nutrition/today']);
    expect(todayKeys.one().slice(0, diaryKeys.all.length)).toEqual([...diaryKeys.all]);
  });
});
