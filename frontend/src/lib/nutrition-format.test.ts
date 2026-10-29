import { describe, expect, it } from 'vitest';

import {
  formatDayLong,
  formatDurationMinutes,
  formatGrams,
  formatKcal,
  formatShareAsPercent,
  formatSignedKcal,
  formatTotal,
  formatWithUnit,
  inputNumberOrNull,
  percentToShare,
  shareToPercent,
  shiftIsoDate,
  weekdayShort,
} from './nutrition-format';
import { NUTRITION_FORM_RANGES } from './nutrition-constants';

describe('nutrition-format: só apresentação, ausente nunca vira 0', () => {
  it('fração <-> % ida e volta com as faixas do dono', () => {
    const { cutting } = NUTRITION_FORM_RANGES;
    for (const fracao of [...cutting.weekly_rate_share, cutting.weekly_rate_share_default, ...cutting.fat_kcal_share]) {
      const percentual = shareToPercent(fracao);
      expect(percentual).not.toBeNull();
      expect(percentToShare(percentual)).toBeCloseTo(fracao, 6);
    }
    expect(shareToPercent(null)).toBeNull();
    expect(percentToShare('')).toBeNull();
    expect(percentToShare(null)).toBeNull();
  });

  it('campo vazio vira null e número vira número', () => {
    expect(inputNumberOrNull('')).toBeNull();
    expect(inputNumberOrNull(undefined)).toBeNull();
    expect(inputNumberOrNull('2.6')).toBe(2.6);
    expect(inputNumberOrNull(4)).toBe(4);
  });

  it('formata kcal, gramas e % com traço para ausente', () => {
    expect(formatKcal(null)).toBe('-');
    expect(formatGrams(undefined)).toBe('-');
    expect(formatShareAsPercent(null)).toBe('-');
    expect(formatKcal(2491.6)).toMatch(/kcal$/);
    expect(formatGrams(160)).toBe('160 g');
    expect(formatShareAsPercent(NUTRITION_FORM_RANGES.cutting.fat_kcal_share_default)).toMatch(/%$/);
  });

  it('valor de tabela de alimento leva a unidade do campo e traço quando a fonte não informa', () => {
    expect(formatWithUnit(2.52, 'g')).toBe('2,52 g');
    expect(formatWithUnit(0, 'mg')).toBe('0 mg');
    expect(formatWithUnit(null, 'g')).toBe('-');
    expect(formatWithUnit(128.256, 'kcal', 0)).toBe('128 kcal');
  });

  it('dia da semana da data ISO não depende do fuso do navegador', () => {
    expect(weekdayShort('2026-10-12')).toMatch(/^seg/);
    expect(weekdayShort('2026-10-18')).toMatch(/^dom/);
    expect(weekdayShort('x')).toBe('x');
  });

  it('diário: total incompleto aparece como mínimo e ausente como traço (nunca 0)', () => {
    expect(formatTotal(1234.4, 'kcal', true)).toBe('1.234 kcal');
    expect(formatTotal(1234.4, 'kcal', false)).toBe('≥ 1.234 kcal');
    expect(formatTotal(null, 'g', false)).toBe('-');
    expect(formatSignedKcal(120)).toBe('+120 kcal');
    expect(formatSignedKcal(-350)).toBe('-350 kcal');
    expect(formatSignedKcal(null)).toBe('-');
    expect(formatDurationMinutes(2700)).toBe('45 min');
    expect(formatDurationMinutes(null)).toBe('-');
  });

  it('navegação de datas sem escorregar de fuso', () => {
    expect(shiftIsoDate('2026-10-17', 1)).toBe('2026-10-18');
    expect(shiftIsoDate('2026-10-12', -1)).toBe('2026-10-11');
    expect(shiftIsoDate('2026-12-31', 1)).toBe('2027-01-01');
    expect(shiftIsoDate('x', 1)).toBe('x');
    expect(formatDayLong('2026-10-17')).toMatch(/^sábado, 17\/10\/2026$/);
  });
});
