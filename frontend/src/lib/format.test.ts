import { describe, expect, it } from 'vitest';
import {
  formatDateOrDash,
  formatDateTimeOrDash,
  formatFixed,
  formatKgFixed,
  formatNumberOrDash,
  formatSetsCount,
} from '@/lib/format';

// Paridade do COD-09: cada função de `lib/format.ts` substituiu cópias locais
// das telas. As cópias abaixo são o código ORIGINAL, congelado aqui de
// propósito (não importar de lugar nenhum): se a função unificada divergir de
// qualquer uma delas para qualquer entrada, a tela mudou e o teste quebra.

const old = {
  // physical-indicators-types.ts, workout-log-types.ts e MuscleActivationTable.tsx (idênticas)
  formatNumberOrDash(value: number | string | null | undefined, digits = 1): string {
    if (value == null) return '-';
    const num = Number(value);
    if (!Number.isFinite(num)) return '-';
    return num.toLocaleString('pt-BR', { maximumFractionDigits: digits });
  },
  // exercicios/page.tsx
  exerciciosFormatNumber(value: number | null | undefined, digits = 1) {
    const n = Number(value);
    return Number.isFinite(n) ? n.toLocaleString('pt-BR', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '-';
  },
  exerciciosFormatKg(value: number | null | undefined, digits = 1) {
    const n = Number(value);
    return Number.isFinite(n) ? `${n.toLocaleString('pt-BR', { minimumFractionDigits: digits, maximumFractionDigits: digits })} kg` : '-';
  },
  // volume-grupamentos/VolumeGrupamentosClient.tsx
  volumeFmt(value: number | null | undefined, digits = 0) {
    const n = Number(value);
    return Number.isFinite(n)
      ? n.toLocaleString('pt-BR', { minimumFractionDigits: digits, maximumFractionDigits: digits })
      : '-';
  },
  volumeFormatKg(value: number | null | undefined, digits = 0) {
    const n = Number(value);
    return Number.isFinite(n) ? `${old.volumeFmt(n, digits)} kg` : '-';
  },
  volumeFormatRpe(value: number | null | undefined) {
    const n = Number(value);
    return Number.isFinite(n) ? n.toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : '-';
  },
  volumeFormatSets(value: number | null | undefined) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '-';
    return n.toLocaleString('pt-BR', { minimumFractionDigits: Number.isInteger(n) ? 0 : 1, maximumFractionDigits: Number.isInteger(n) ? 0 : 1 });
  },
  // planos-treino/PlanosTreinoClient.tsx, planos-treino/[id]/PlanDetailClient.tsx e agente-cardio/page.tsx (idênticas)
  formatDate(value?: string | null) {
    if (!value) return '-';
    const parsed = new Date(`${value}T00:00:00`);
    if (Number.isNaN(parsed.getTime())) return value;
    return parsed.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric' });
  },
  // agente-cardio/page.tsx
  formatDateTime(value?: string | null) {
    if (!value) return '-';
    const parsed = new Date(value.includes('T') || value.includes(' ') ? value : `${value}T00:00:00`);
    if (Number.isNaN(parsed.getTime())) return value;
    return parsed.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  },
};

const NUMBERS: any[] = [
  null,
  undefined,
  0,
  -0,
  1,
  7.5,
  12.345,
  1234.5,
  1234567.891,
  -3.25,
  0.05,
  NaN,
  Infinity,
  -Infinity,
  '12.5',
  '',
  'abc',
];
const DIGITS = [0, 1, 2, 3];
const DATES: any[] = [
  null,
  undefined,
  '',
  '2026-10-05',
  '2026-01-31',
  '2024-02-29',
  '2026-10-05T10:30:00',
  '2026-10-05 10:30:00',
  '2026-10-05T10:30:00Z',
  '05/10/2026',
  'não é data',
];

describe('lib/format: paridade com as cópias originais (COD-09)', () => {
  it('formatNumberOrDash = as três cópias de formatNumber/formatNumberOrDash', () => {
    for (const value of NUMBERS) {
      expect(formatNumberOrDash(value)).toBe(old.formatNumberOrDash(value));
      for (const digits of DIGITS) {
        expect(formatNumberOrDash(value, digits)).toBe(old.formatNumberOrDash(value, digits));
      }
    }
  });

  it('formatFixed = formatNumber de /exercicios (padrão 1) e fmt de /volume-grupamentos (padrão 0)', () => {
    for (const value of NUMBERS) {
      expect(formatFixed(value, 1)).toBe(old.exerciciosFormatNumber(value));
      expect(formatFixed(value, 0)).toBe(old.volumeFmt(value));
      for (const digits of DIGITS) {
        expect(formatFixed(value, digits)).toBe(old.exerciciosFormatNumber(value, digits));
        expect(formatFixed(value, digits)).toBe(old.volumeFmt(value, digits));
      }
    }
  });

  it('formatFixed(v, 1) = formatRpe de /volume-grupamentos', () => {
    for (const value of NUMBERS) {
      expect(formatFixed(value, 1)).toBe(old.volumeFormatRpe(value));
    }
  });

  it('formatKgFixed = formatKg de /exercicios (padrão 1) e de /volume-grupamentos (padrão 0)', () => {
    for (const value of NUMBERS) {
      expect(formatKgFixed(value, 1)).toBe(old.exerciciosFormatKg(value));
      expect(formatKgFixed(value, 0)).toBe(old.volumeFormatKg(value));
      for (const digits of DIGITS) {
        expect(formatKgFixed(value, digits)).toBe(old.exerciciosFormatKg(value, digits));
        expect(formatKgFixed(value, digits)).toBe(old.volumeFormatKg(value, digits));
      }
    }
  });

  it('formatSetsCount = formatSets de /volume-grupamentos', () => {
    for (const value of NUMBERS) {
      expect(formatSetsCount(value)).toBe(old.volumeFormatSets(value));
    }
    expect(formatSetsCount(7.5)).toBe('7,5');
    expect(formatSetsCount(12)).toBe('12');
  });

  it('formatDateOrDash = formatDate de /planos-treino, /planos-treino/[id] e /agente-cardio', () => {
    for (const value of DATES) {
      expect(formatDateOrDash(value)).toBe(old.formatDate(value));
    }
  });

  it('formatDateTimeOrDash = formatDateTime de /agente-cardio', () => {
    for (const value of DATES) {
      expect(formatDateTimeOrDash(value)).toBe(old.formatDateTime(value));
    }
  });
});

describe('lib/format: o que a tela mostra', () => {
  it('ausente é hífen, não travessão (família fora do hub)', () => {
    expect(formatNumberOrDash(null)).toBe('-');
    expect(formatDateOrDash(null)).toBe('-');
    expect(formatDateTimeOrDash('')).toBe('-');
    expect(formatFixed(undefined, 1)).toBe('-');
  });

  it('null em formatFixed/formatKgFixed vira zero (comportamento herdado das telas)', () => {
    expect(formatFixed(null, 1)).toBe('0,0');
    expect(formatKgFixed(null, 0)).toBe('0 kg');
  });

  it('casas: "no máximo" em formatNumberOrDash, "exatamente" em formatFixed', () => {
    expect(formatNumberOrDash(12, 1)).toBe('12');
    expect(formatFixed(12, 1)).toBe('12,0');
    expect(formatKgFixed(1234.5, 1)).toBe('1.234,5 kg');
  });

  it('data: dia vira dd/mm/aaaa; DATETIME em formatDateOrDash volta como veio', () => {
    expect(formatDateOrDash('2026-10-05')).toBe('05/10/2026');
    expect(formatDateOrDash('2026-10-05 10:30:00')).toBe('2026-10-05 10:30:00');
  });
});
