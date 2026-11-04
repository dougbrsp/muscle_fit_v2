import { describe, expect, it } from 'vitest';
import type { Metric } from '@/lib/dashboard-v3-types';
import { metricLimitations, metricText, mobileSafeHref } from './pure';

const base: Metric = {
  value: 12,
  unit: 'séries',
  nature: 'OBSERVED',
  metric_version: 'v1',
  data_quality: 'HIGH',
  limitations: [],
};

describe('metricText', () => {
  it('null nunca vira zero', () => {
    expect(metricText({ ...base, value: null })).toBe('—');
    expect(metricText(null)).toBe('—');
    expect(metricText(undefined)).toBe('—');
  });

  it('zero observado continua sendo zero', () => {
    expect(metricText({ ...base, value: 0 }, 0)).toBe('0 séries');
  });

  it('anexa a unidade vinda do backend', () => {
    expect(metricText({ ...base, value: 3.5, unit: 'kg' })).toContain('kg');
    expect(metricText({ ...base, value: 3, unit: null }, 0)).toBe('3');
  });
});

describe('metricLimitations', () => {
  it('junta e deduplica o texto do backend', () => {
    const a = { ...base, limitations: ['x', 'y'] };
    const b = { ...base, limitations: ['y', 'z'] };
    expect(metricLimitations([a, b, null, undefined])).toEqual(['x', 'y', 'z']);
  });
});

describe('mobileSafeHref', () => {
  it('descarta rota só do computador e mantém as demais', () => {
    expect(mobileSafeHref('/modelos-treino/3')).toBeNull();
    expect(mobileSafeHref('/metas-ativacao?x=1')).toBeNull();
    expect(mobileSafeHref('/dashboard/recovery?window=7d')).toBe('/dashboard/recovery?window=7d');
    expect(mobileSafeHref(null)).toBeNull();
  });
});
