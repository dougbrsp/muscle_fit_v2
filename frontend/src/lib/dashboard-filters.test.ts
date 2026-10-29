import { describe, expect, it } from 'vitest';
import {
  customRangeError,
  DASHBOARD_WINDOW_LABELS,
  DASHBOARD_WINDOW_OPTIONS,
  DEFAULT_DASHBOARD_WINDOW,
  EMPTY_DASHBOARD_FILTERS,
  MAX_CUSTOM_WINDOW_DAYS,
  WINDOW_TO_DAYS,
  dashboardDayRange,
  dashboardHref,
  dashboardWindowDays,
  dashboardWindowQuery,
  isIsoDate,
  parseDashboardFilters,
  serializeDashboardFilters,
  type DashboardFilters,
} from './dashboard-filters';

const parse = (search: string) => parseDashboardFilters(new URLSearchParams(search));

describe('parseDashboardFilters', () => {
  it('usa o default quando a query string está vazia', () => {
    expect(parse('')).toEqual(EMPTY_DASHBOARD_FILTERS);
  });

  it('aceita todas as janelas oferecidas no seletor', () => {
    for (const window of DASHBOARD_WINDOW_OPTIONS) {
      expect(parse(`window=${window}`).window).toBe(window);
    }
  });

  it('ignora janela desconhecida e cai no default', () => {
    expect(parse('window=42d').window).toBe(DEFAULT_DASHBOARD_WINDOW);
  });

  it('aceita window=cycle (liberada na F1.1)', () => {
    expect(parse('window=cycle').window).toBe('cycle');
  });

  it('converte o ?days= legado apenas em equivalência exata', () => {
    expect(parse('days=7').window).toBe('7d');
    expect(parse('days=180').window).toBe('180d');
    // 14 e 30 não são janela nomeada: cai no default em vez de "arredondar".
    expect(parse('days=14').window).toBe(DEFAULT_DASHBOARD_WINDOW);
    expect(parse('days=30').window).toBe(DEFAULT_DASHBOARD_WINDOW);
  });

  it('window explícita tem prioridade sobre o days legado', () => {
    expect(parse('window=90d&days=7').window).toBe('90d');
  });

  it('lê os ids e o nome do exercício', () => {
    const filters = parse('muscleGroupId=6&exerciseMappingId=12&exerciseName=Supino&planId=3');
    expect(filters).toMatchObject({
      muscleGroupId: 6,
      exerciseMappingId: 12,
      exerciseName: 'Supino',
      planId: 3,
    });
  });

  it('descarta ids não inteiros, negativos ou zero', () => {
    expect(parse('planId=abc').planId).toBeNull();
    expect(parse('planId=-1').planId).toBeNull();
    expect(parse('planId=0').planId).toBeNull();
    expect(parse('planId=1.5').planId).toBeNull();
  });

  it('descarta datas fora do formato ISO ou inexistentes no calendário', () => {
    expect(parse('window=custom&date_from=01/02/2026').dateFrom).toBeNull();
    expect(parse('window=custom&date_from=2026-02-31').dateFrom).toBeNull();
    expect(parse('window=custom&date_from=2026-02-28').dateFrom).toBe('2026-02-28');
  });
});

describe('serializeDashboardFilters', () => {
  const base: DashboardFilters = { ...EMPTY_DASHBOARD_FILTERS };

  it('não escreve a janela padrão na URL', () => {
    expect(serializeDashboardFilters(base).toString()).toBe('');
  });

  it('faz ida e volta com os filtros preenchidos', () => {
    const filters: DashboardFilters = {
      window: '90d',
      dateFrom: null,
      dateTo: null,
      muscleGroupId: 7,
      exerciseMappingId: 21,
      exerciseName: 'Remada curvada',
      planId: 9,
    };
    expect(parseDashboardFilters(serializeDashboardFilters(filters))).toEqual(filters);
  });

  it('só escreve as datas na janela custom', () => {
    const custom = { ...base, window: 'custom' as const, dateFrom: '2026-01-01', dateTo: '2026-01-31' };
    expect(serializeDashboardFilters(custom).get('date_from')).toBe('2026-01-01');

    const rolling = { ...custom, window: '7d' as const };
    expect(serializeDashboardFilters(rolling).get('date_from')).toBeNull();
  });

  it('remove o parâmetro days legado e preserva parâmetros de fora do hub', () => {
    const params = new URLSearchParams('days=30&ref=email');
    const result = serializeDashboardFilters(base, params);
    expect(result.get('days')).toBeNull();
    expect(result.get('ref')).toBe('email');
  });

  it('remove filtros que voltaram a ser nulos', () => {
    const params = new URLSearchParams('planId=3&exerciseName=Supino');
    const result = serializeDashboardFilters(base, params);
    expect(result.get('planId')).toBeNull();
    expect(result.get('exerciseName')).toBeNull();
  });
});

describe('mapa window <-> days', () => {
  it('é literal: 28d são 28 dias, nunca 30', () => {
    expect(WINDOW_TO_DAYS).toEqual({ '7d': 7, '28d': 28, '90d': 90, '180d': 180 });
    expect(DASHBOARD_WINDOW_LABELS['28d']).toContain('28');
  });

  it('deriva days de cada janela rolling', () => {
    for (const [window, days] of Object.entries(WINDOW_TO_DAYS)) {
      expect(dashboardWindowDays({ ...EMPTY_DASHBOARD_FILTERS, window: window as '7d' })).toBe(days);
    }
  });

  it('conta a janela custom de forma inclusiva', () => {
    const filters = {
      ...EMPTY_DASHBOARD_FILTERS,
      window: 'custom' as const,
      dateFrom: '2026-01-01',
      dateTo: '2026-01-07',
    };
    expect(dashboardWindowDays(filters)).toBe(7);
  });

  it('um único dia em custom vale 1 dia', () => {
    const filters = {
      ...EMPTY_DASHBOARD_FILTERS,
      window: 'custom' as const,
      dateFrom: '2026-03-10',
      dateTo: '2026-03-10',
    };
    expect(dashboardWindowDays(filters)).toBe(1);
  });

  it('custom inválida cai no default em vez de mandar número sem sentido', () => {
    const invertida = {
      ...EMPTY_DASHBOARD_FILTERS,
      window: 'custom' as const,
      dateFrom: '2026-05-10',
      dateTo: '2026-05-01',
    };
    expect(dashboardWindowDays(invertida)).toBe(WINDOW_TO_DAYS[DEFAULT_DASHBOARD_WINDOW]);

    const semDatas = { ...EMPTY_DASHBOARD_FILTERS, window: 'custom' as const };
    expect(dashboardWindowDays(semDatas)).toBe(WINDOW_TO_DAYS[DEFAULT_DASHBOARD_WINDOW]);
  });

  it('cycle cai no default nas rotas v2, que só entendem dias', () => {
    expect(dashboardWindowDays({ ...EMPTY_DASHBOARD_FILTERS, window: 'cycle' })).toBe(
      WINDOW_TO_DAYS[DEFAULT_DASHBOARD_WINDOW],
    );
  });
});

describe('validação da janela custom', () => {
  it('exige as duas datas', () => {
    expect(customRangeError(null, '2026-01-10')).not.toBeNull();
    expect(customRangeError('2026-01-10', null)).not.toBeNull();
  });

  it('exige fim >= início', () => {
    expect(customRangeError('2026-01-10', '2026-01-09')).not.toBeNull();
    expect(customRangeError('2026-01-10', '2026-01-10')).toBeNull();
  });

  it('aceita exatamente 365 dias e recusa 366', () => {
    // 2026 não é bissexto: 2026-01-01..2026-12-31 são 365 dias inclusivos.
    expect(customRangeError('2026-01-01', '2026-12-31')).toBeNull();
    expect(customRangeError('2026-01-01', '2027-01-01')).toContain(String(MAX_CUSTOM_WINDOW_DAYS));
  });

  it('recusa datas malformadas', () => {
    expect(customRangeError('10/01/2026', '2026-01-20')).not.toBeNull();
  });
});

describe('isIsoDate', () => {
  it('aceita só datas ISO existentes', () => {
    expect(isIsoDate('2026-09-22')).toBe(true);
    expect(isIsoDate('2024-02-29')).toBe(true); // bissexto
    expect(isIsoDate('2026-02-29')).toBe(false);
    expect(isIsoDate('2026-13-01')).toBe(false);
    expect(isIsoDate('')).toBe(false);
    expect(isIsoDate(null)).toBe(false);
  });
});

describe('dashboardWindowQuery (rotas v3)', () => {
  it('manda a janela nomeada sem datas', () => {
    expect(dashboardWindowQuery({ ...EMPTY_DASHBOARD_FILTERS, window: '90d' })).toEqual({ window: '90d' });
  });

  it('manda as datas na janela custom válida', () => {
    expect(
      dashboardWindowQuery({
        ...EMPTY_DASHBOARD_FILTERS,
        window: 'custom',
        dateFrom: '2026-01-01',
        dateTo: '2026-01-31',
      }),
    ).toEqual({ window: 'custom', date_from: '2026-01-01', date_to: '2026-01-31' });
  });

  it('não manda window=custom incompleta (o backend devolveria 400)', () => {
    expect(dashboardWindowQuery({ ...EMPTY_DASHBOARD_FILTERS, window: 'custom' })).toEqual({
      window: DEFAULT_DASHBOARD_WINDOW,
    });
  });

  it('manda window=cycle para as rotas v3 (o backend resolve pelo Modelo)', () => {
    expect(dashboardWindowQuery({ ...EMPTY_DASHBOARD_FILTERS, window: 'cycle' })).toEqual({
      window: 'cycle',
    });
  });
});

describe('dashboardDayRange (rotas v2 de /dashboard/progressao, M61)', () => {
  it('janela pré-definida manda só days, como sempre', () => {
    expect(dashboardDayRange({ ...EMPTY_DASHBOARD_FILTERS, window: '90d' })).toEqual({ days: 90 });
  });

  it('custom válida manda as duas datas junto com o tamanho da faixa', () => {
    expect(
      dashboardDayRange({
        ...EMPTY_DASHBOARD_FILTERS,
        window: 'custom',
        dateFrom: '2026-08-01',
        dateTo: '2026-08-10',
      }),
    ).toEqual({ days: 10, date_from: '2026-08-01', date_to: '2026-08-10' });
  });

  it('custom inválida cai no default sem datas (o backend devolveria 400)', () => {
    expect(
      dashboardDayRange({
        ...EMPTY_DASHBOARD_FILTERS,
        window: 'custom',
        dateFrom: '2026-08-10',
        dateTo: '2026-08-01',
      }),
    ).toEqual({ days: WINDOW_TO_DAYS[DEFAULT_DASHBOARD_WINDOW] });
  });
});

describe('dashboardHref', () => {
  it('propaga o contexto para outra sub-rota do hub', () => {
    const filters = { ...EMPTY_DASHBOARD_FILTERS, window: '90d' as const, muscleGroupId: 6 };
    expect(dashboardHref('/dashboard/musculos', filters)).toBe(
      '/dashboard/musculos?window=90d&muscleGroupId=6',
    );
  });

  it('não acrescenta "?" quando não há nada a propagar', () => {
    expect(dashboardHref('/dashboard', EMPTY_DASHBOARD_FILTERS)).toBe('/dashboard');
  });
});
