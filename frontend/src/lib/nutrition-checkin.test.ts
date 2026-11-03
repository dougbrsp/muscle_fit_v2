import { describe, expect, it, vi } from 'vitest';

vi.mock('./api', () => ({ apiFetch: vi.fn() }));

import { apiFetch } from './api';
import {
  acceptCheckin,
  checkinKeys,
  declineCheckin,
  fetchCheckin,
  fetchCheckinHistory,
  fetchCheckinSettings,
  formatAdjustment,
  formatChangeValue,
  saveCheckinWeekday,
  shortDay,
  verdictTone,
  weekRangeLabel,
} from './nutrition-checkin';
import { diaryKeys, nutritionKeys } from './nutrition-api';
import { dashboardKeys } from './nutrition-dashboard';
import { targetsChangedKeys } from './nutrition-plan-form';

const mock = vi.mocked(apiFetch);

describe('chamadas do check-in', () => {
  it('lê o check-in, o histórico e o dia escolhido do próprio usuário ou de um aluno', async () => {
    mock.mockResolvedValue({});
    await fetchCheckin(null);
    await fetchCheckin(7);
    await fetchCheckinHistory(null);
    await fetchCheckinHistory(7);
    await fetchCheckinSettings(7);
    expect(mock.mock.calls.map((c) => c[0])).toEqual([
      '/nutrition/checkin',
      '/nutrition/checkin?user_id=7',
      '/nutrition/checkins',
      '/nutrition/checkins?user_id=7',
      '/nutrition/checkin/settings?user_id=7',
    ]);
  });

  it('aceitar e recusar são POST por id; o dia vai em PUT com o aluno só quando há', async () => {
    mock.mockClear();
    mock.mockResolvedValue({});
    await acceptCheckin(5);
    await declineCheckin(5);
    await saveCheckinWeekday(4, null);
    await saveCheckinWeekday(2, 9);
    expect(mock.mock.calls[0]).toEqual(['/nutrition/checkins/5/accept', { method: 'POST' }]);
    expect(mock.mock.calls[1]).toEqual(['/nutrition/checkins/5/decline', { method: 'POST' }]);
    expect(mock.mock.calls[2]).toEqual(['/nutrition/checkin/settings', { method: 'PUT', body: JSON.stringify({ weekday: 4 }) }]);
    expect(mock.mock.calls[3]?.[1]).toEqual({ method: 'PUT', body: JSON.stringify({ weekday: 2, user_id: 9 }) });
  });
});

describe('chaves e invalidação', () => {
  it('as chaves ficam sob uma raiz própria, por usuário', () => {
    expect(checkinKeys.all).toEqual(['nutrition-checkin']);
    expect(checkinKeys.current(null)).toEqual(['nutrition-checkin', 'current', 'me']);
    expect(checkinKeys.history(3)).toEqual(['nutrition-checkin', 'history', 3]);
    expect(checkinKeys.settings(null)[2]).toBe('me');
  });

  it('aceitar invalida o que as metas novas tornam velho: plano, diário e painel', () => {
    expect(targetsChangedKeys()).toEqual([nutritionKeys.myPlan, diaryKeys.all, dashboardKeys.all]);
  });
});

describe('apresentação', () => {
  it('a etiqueta do veredito: verde no ritmo, laranja fora dele, cinza inconclusivo, azul alvo atingido', () => {
    expect(verdictTone('on_track')).toBe('green');
    for (const v of ['slow', 'fast', 'above_band', 'below_band'] as const) expect(verdictTone(v)).toBe('orange');
    expect(verdictTone('inconclusive')).toBe('gray');
    expect(verdictTone('goal_reached')).toBe('blue');
  });

  it('fração vira % e gramas ganham unidade; ausente é traço', () => {
    expect(formatChangeValue({ unit: 'share' }, 0.0075)).toMatch(/^0,75%$/);
    expect(formatChangeValue({ unit: 'share' }, 0.1)).toBe('10%');
    expect(formatChangeValue({ unit: 'g' }, 239.6)).toBe('240 g');
    expect(formatChangeValue({ unit: 'g' }, null)).toBe('-');
  });

  it('o ajuste de kcal sai com sinal e arredondado; sem proposta é null', () => {
    expect(formatAdjustment(-122.7)).toMatch(/^-123 kcal$/);
    expect(formatAdjustment(80.2)).toMatch(/^\+80 kcal$/);
    expect(formatAdjustment(0.2)).toBe('0 kcal');
    expect(formatAdjustment(null)).toBeNull();
  });

  it('datas curtas e a semana', () => {
    expect(shortDay('2026-11-01')).toBe('01/11');
    expect(shortDay(null)).toBe('-');
    expect(weekRangeLabel({ week_start: '2026-10-26', week_end: '2026-11-01' })).toBe('26/10 a 01/11');
  });
});
