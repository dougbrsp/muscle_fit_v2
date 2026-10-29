import { describe, expect, it } from 'vitest';
import {
  alignedDateDomain,
  checkVisual,
  dateToTime,
  sharedTimeDomain,
  collectBodyLimitations,
  emptyMeasurementForm,
  isMeasurementSite,
  measurementChartRows,
  MEASUREMENT_DATE_MIN,
  MEASUREMENT_SITE_OPTIONS,
  measurementDateMax,
  milestonesInRange,
  performanceChartRows,
  RECOMPOSITION_VISUAL,
  toMeasurementInput,
  validateMeasurementForm,
  weightChartRows,
  weightSourceLabel,
} from './dashboard-body';
import type { BodyResponse, MeasurementPoint, WeightPoint } from './dashboard-body-types';

describe('gráfico de peso', () => {
  it('um ponto por dia COM pesagem, fonte rotulada, sem inventar zero', () => {
    const points: WeightPoint[] = [
      { local_date: '2026-09-01', weight_kg: 80, source: 'body_composition', ma7_kg: null, ma7_points: 1 },
      { local_date: '2026-09-04', weight_kg: 80.4, source: 'daily_biometrics', ma7_kg: 80.2, ma7_points: 2 },
    ];
    const rows = weightChartRows(points);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({ date: '2026-09-01', peso: 80, media7: null, fonte: 'Bioimpedância (balança)' });
    expect(rows[1]).toMatchObject({ peso: 80.4, media7: 80.2, fonte: 'Registro diário', pontos: 2 });
    expect(weightSourceLabel('intervals_wellness')).toBe('Intervals.icu');
    expect(weightSourceLabel('outra')).toBe('outra');
  });
});

describe('gráfico de medidas', () => {
  it('separa protocolo (linha) de fora do protocolo (ponto isolado)', () => {
    const points: MeasurementPoint[] = [
      { id: 1, local_date: '2026-09-01', value_cm: 90, protocol_ok: true, data_quality: 'HIGH', notes: null },
      { id: 2, local_date: '2026-09-10', value_cm: 86, protocol_ok: false, data_quality: 'LOW', notes: 'pós-almoço' },
    ];
    const rows = measurementChartRows(points);
    expect(rows[0]).toMatchObject({ protocolo: 90, foraProtocolo: null });
    expect(rows[1]).toMatchObject({ protocolo: null, foraProtocolo: 86, notas: 'pós-almoço' });
  });

  it('série de desempenho e domínio comum de datas', () => {
    const desempenho = performanceChartRows([{ local_date: '2026-09-03', e1rm_avg_kg: 92.9, exercises: 3 }]);
    expect(desempenho[0]).toMatchObject({ e1rm: 92.9, exercicios: 3 });
    const dominio = alignedDateDomain(
      [{ date: '2026-09-04' }, { date: '2026-09-01' }],
      desempenho,
      [{ date: '2026-09-01' }],
    );
    expect(dominio).toEqual(['2026-09-01', '2026-09-03', '2026-09-04']);
    const [inicio, fim] = sharedTimeDomain('2026-09-01', '2026-09-04');
    expect(inicio).toBe(dateToTime('2026-09-01'));
    expect(fim - inicio).toBe(3 * 24 * 3600 * 1000);
    expect(desempenho[0].t).toBe(dateToTime('2026-09-03'));
  });
});

describe('sinal de recomposição', () => {
  it('tem rótulo para os três estados e nunca diz "confirmada"', () => {
    for (const visual of Object.values(RECOMPOSITION_VISUAL)) {
      expect(visual.label).toBeTruthy();
      expect(visual.description).toBeTruthy();
      // Nem "confirmada" (sinal nao e prova) nem "descartada" (o veredito
      // negativo e "nao compativel no periodo", nao uma refutacao).
      for (const texto of [visual.label, visual.description]) {
        expect(texto.toLowerCase()).not.toContain('confirmad');
      }
      expect(visual.label.toLowerCase()).not.toContain('descartad');
    }
    expect(RECOMPOSITION_VISUAL.not_compatible.description).toContain('veja quais abaixo');
    expect(RECOMPOSITION_VISUAL.insufficient_data.description).toContain('o que registrar');
    expect(RECOMPOSITION_VISUAL.compatible.label).toBe('Compatível com recomposição');
    expect(checkVisual(true).label).toBe('Sim');
    expect(checkVisual(false).color).toBe('red');
    expect(checkVisual(null).label).toBe('Sem dado');
  });
});

describe('limitações e marcos', () => {
  it('deduplica envelope + cintura + peso', () => {
    const payload = {
      limitations: ['a'],
      waist: { limitations: ['b', 'a'] },
      weight: { limitations: ['c', 'b'] },
    } as unknown as BodyResponse;
    expect(collectBodyLimitations(payload)).toEqual(['a', 'b', 'c']);
  });

  it('filtra marcos por intervalo inclusivo', () => {
    const marcos = [{ local_date: '2026-08-31' }, { local_date: '2026-09-01' }, { local_date: '2026-09-30' }];
    expect(milestonesInRange(marcos, '2026-09-01', '2026-09-30')).toHaveLength(2);
  });
});

describe('formulário de medida', () => {
  it('nasce com cintura e a data de hoje', () => {
    const form = emptyMeasurementForm('2026-09-22');
    expect(form).toEqual({ local_date: '2026-09-22', site: 'waist', value_cm: '', protocol_ok: true, notes: '' });
    expect(MEASUREMENT_SITE_OPTIONS[0]).toEqual({ value: 'waist', label: 'Cintura' });
    expect(isMeasurementSite('calf_r')).toBe(true);
    expect(isMeasurementSite('belly')).toBe(false);
  });

  it('valida data, local, faixa de 10 a 300 cm e tamanho da nota', () => {
    const base = emptyMeasurementForm('2026-09-22');
    expect(validateMeasurementForm({ ...base, value_cm: 88.5 })).toEqual({});
    expect(validateMeasurementForm({ ...base, value_cm: '' }).value_cm).toMatch(/Informe/);
    expect(validateMeasurementForm({ ...base, value_cm: 5 }).value_cm).toMatch(/entre 10 e 300/);
    expect(validateMeasurementForm({ ...base, value_cm: 301 }).value_cm).toMatch(/entre 10 e 300/);
    expect(validateMeasurementForm({ ...base, value_cm: 80, local_date: '22/09/2026' }).local_date).toBeTruthy();
    expect(validateMeasurementForm({ ...base, value_cm: 80, notes: 'x'.repeat(256) }).notes).toBeTruthy();
    expect(
      validateMeasurementForm({ ...base, value_cm: 80, site: 'nariz' as MeasurementFormSite }).site,
    ).toBeTruthy();
  });

  it('limita o seletor de data à janela do backend (2000-01-01 a amanhã)', () => {
    expect(MEASUREMENT_DATE_MIN).toBe('2000-01-01');
    expect(measurementDateMax('2026-10-10')).toBe('2026-10-11');
    expect(measurementDateMax('2026-12-31')).toBe('2027-01-01');
    expect(measurementDateMax('2028-02-28')).toBe('2028-02-29');
  });

  it('monta o payload do POST arredondando a 0,1 cm e limpando a nota', () => {
    const form = { ...emptyMeasurementForm('2026-09-22'), value_cm: 84.56, notes: '  jejum ' };
    expect(toMeasurementInput(form)).toEqual({
      local_date: '2026-09-22',
      site: 'waist',
      value_cm: 84.6,
      protocol_ok: true,
      notes: 'jejum',
    });
    expect(toMeasurementInput({ ...form, notes: '   ' }).notes).toBeNull();
  });
});

type MeasurementFormSite = ReturnType<typeof emptyMeasurementForm>['site'];
