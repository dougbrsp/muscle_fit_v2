import { describe, expect, it } from 'vitest';
import {
  EM_DASH,
  fmtDate,
  fmtDecimal,
  fmtDuration,
  fmtInt,
  fmtKg,
  fmtKgTotal,
  fmtPercent,
  fmtRpe,
  fmtSets,
  fmtSignedPercent,
  fmtWeekdayDateTime,
} from './dashboard-format';

/** Node pode usar espaço estreito não separável em alguns formatos. */
const norm = (value: string) => value.replace(/ | /g, ' ');

const ABSENT = [null, undefined] as const;

describe('ausência de dado', () => {
  it('toda função devolve "—" para null/undefined (nunca 0)', () => {
    for (const value of ABSENT) {
      expect(fmtDecimal(value)).toBe(EM_DASH);
      expect(fmtInt(value)).toBe(EM_DASH);
      expect(fmtKg(value)).toBe(EM_DASH);
      expect(fmtKgTotal(value)).toBe(EM_DASH);
      expect(fmtRpe(value)).toBe(EM_DASH);
      expect(fmtSets(value)).toBe(EM_DASH);
      expect(fmtPercent(value)).toBe(EM_DASH);
      expect(fmtSignedPercent(value)).toBe(EM_DASH);
      expect(fmtDuration(value)).toBe(EM_DASH);
      expect(fmtDate(value)).toBe(EM_DASH);
    }
  });

  it('zero observado continua sendo zero', () => {
    expect(fmtInt(0)).toBe('0');
    expect(fmtSets(0)).toBe('0');
    expect(fmtKgTotal(0)).toBe('0 kg');
  });

  it('NaN/Infinity contam como ausência, não como número', () => {
    expect(fmtDecimal(Number.NaN)).toBe(EM_DASH);
    expect(fmtInt(Number.POSITIVE_INFINITY)).toBe(EM_DASH);
  });
});

describe('fmtDate', () => {
  it('formata ISO como dd/mm/aaaa sem deslocar por fuso', () => {
    expect(fmtDate('2026-09-22')).toBe('22/09/2026');
    expect(fmtDate('2026-01-01T23:30:00')).toBe('01/01/2026');
  });

  it('devolve o próprio dia quando não é data reconhecível', () => {
    expect(fmtDate('sem-data')).toBe('sem-data');
  });
});

describe('fmtWeekdayDateTime', () => {
  it('instante com fuso → dia da semana curto, dd/mm e hh:mm no fuso pedido', () => {
    expect(fmtWeekdayDateTime('2026-10-17T14:05:00-03:00', 'America/Sao_Paulo')).toBe('sáb 17/10 14:05');
    expect(fmtWeekdayDateTime('2026-10-17T14:05:00-03:00', 'America/Manaus')).toBe('sáb 17/10 13:05');
  });

  it('meia-noite sai como 00, nunca 24', () => {
    expect(fmtWeekdayDateTime('2026-10-19T00:00:00-03:00', 'America/Sao_Paulo')).toBe('seg 19/10 00:00');
  });

  it('ausente ou irreconhecível vira "—"; fuso inválido não derruba', () => {
    expect(fmtWeekdayDateTime(null, 'America/Sao_Paulo')).toBe(EM_DASH);
    expect(fmtWeekdayDateTime(undefined)).toBe(EM_DASH);
    expect(fmtWeekdayDateTime('sem-data', 'America/Sao_Paulo')).toBe(EM_DASH);
    // Fuso inválido cai no do navegador (o relógio varia com a máquina do teste).
    expect(fmtWeekdayDateTime('2026-10-17T14:05:00-03:00', 'Nao/Existe')).not.toBe(EM_DASH);
  });
});

describe('números', () => {
  it('fmtDecimal respeita o número de casas', () => {
    expect(norm(fmtDecimal(7.456, 2))).toBe('7,46');
    expect(norm(fmtDecimal(7.456, 0))).toBe('7');
  });

  it('fmtInt arredonda e agrupa milhares', () => {
    expect(norm(fmtInt(12480.6))).toBe('12.481');
  });

  it('fmtKg usa 1 casa e fmtKgTotal arredonda', () => {
    expect(norm(fmtKg(60.55))).toBe('60,6 kg');
    expect(norm(fmtKgTotal(12480.6))).toBe('12.481 kg');
  });

  it('fmtRpe usa 1 casa', () => {
    expect(norm(fmtRpe(8.25))).toBe('8,3');
  });

  it('fmtSets usa 2 casas por padrão', () => {
    expect(norm(fmtSets(4.6))).toBe('4,6');
    expect(norm(fmtSets(4.567))).toBe('4,57');
    expect(norm(fmtSets(4.567, 1))).toBe('4,6');
  });

  it('fmtPercent e fmtSignedPercent', () => {
    expect(norm(fmtPercent(12.34))).toBe('12,3%');
    expect(norm(fmtPercent(99.9, 0))).toBe('100%');
    expect(norm(fmtSignedPercent(3.21))).toBe('+3,2%');
    expect(norm(fmtSignedPercent(-1.4))).toBe('-1,4%');
    expect(norm(fmtSignedPercent(0))).toBe('0%');
  });
});

describe('fmtDuration', () => {
  it('converte segundos em minutos inteiros', () => {
    expect(fmtDuration(3600)).toBe('60 min');
    expect(fmtDuration(90)).toBe('2 min');
    expect(fmtDuration(0)).toBe('0 min');
  });
});
