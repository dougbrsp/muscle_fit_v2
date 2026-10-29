import { describe, expect, it } from 'vitest';
import { DEFAULT_TIMEZONE, isKnownTimezone, normalizeTimezone, TIMEZONE_OPTIONS } from './timezones';

describe('timezones', () => {
  it('lista curta: fusos do Brasil + UTC, sem duplicatas, default presente', () => {
    const values = TIMEZONE_OPTIONS.map((o) => o.value);
    expect(new Set(values).size).toBe(values.length);
    expect(values).toContain(DEFAULT_TIMEZONE);
    expect(values).toContain('UTC');
    expect(values).toEqual([
      'America/Sao_Paulo',
      'America/Manaus',
      'America/Belem',
      'America/Fortaleza',
      'America/Recife',
      'America/Bahia',
      'America/Cuiaba',
      'America/Rio_Branco',
      'America/Noronha',
      'UTC',
    ]);
  });

  it('isKnownTimezone aceita apenas a lista', () => {
    expect(isKnownTimezone('America/Manaus')).toBe(true);
    expect(isKnownTimezone('Europe/Lisbon')).toBe(false);
    expect(isKnownTimezone(null)).toBe(false);
    expect(isKnownTimezone(42)).toBe(false);
  });

  it('normalizeTimezone: valor da lista e preservado; null/vazio/desconhecido caem no default', () => {
    expect(normalizeTimezone('America/Recife')).toBe('America/Recife');
    expect(normalizeTimezone(null)).toBe(DEFAULT_TIMEZONE);
    expect(normalizeTimezone(undefined)).toBe(DEFAULT_TIMEZONE);
    expect(normalizeTimezone('')).toBe(DEFAULT_TIMEZONE);
    expect(normalizeTimezone('Europe/Lisbon')).toBe(DEFAULT_TIMEZONE);
  });

  it('normalizeTimezone com keepUnknown preserva um fuso fora da lista', () => {
    expect(normalizeTimezone('Europe/Lisbon', true)).toBe('Europe/Lisbon');
    expect(normalizeTimezone('  ', true)).toBe(DEFAULT_TIMEZONE);
  });
});
