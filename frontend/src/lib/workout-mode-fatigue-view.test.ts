import { describe, expect, it } from 'vitest';
import { BRAND, WORKOUT_MODE_TOKENS } from '@/theme';
import { FATIGUE_UNAVAILABLE_LABEL, fatigueChipView } from './workout-mode-fatigue-view';
import type { GroupFatigueBand, GroupFatigueReading } from './workout-mode-types';

const reading = (band: GroupFatigueBand | string, band_label: string): GroupFatigueReading => ({
  muscle_group_id: 3,
  muscle_group_name: 'Peito',
  prior_sets: 0,
  level: 0,
  band: band as GroupFatigueBand,
  band_label,
});

describe('fatigueChipView (mapa de faixas de fadiga)', () => {
  it('cobre as quatro faixas do backend com cor e ícone próprios e o rótulo do backend', () => {
    const cases: [GroupFatigueBand, string, string, string][] = [
      ['rested', 'Descansado', WORKOUT_MODE_TOKENS.success, 'battery-4'],
      ['pre_fatigued', 'Pré-fatigado', WORKOUT_MODE_TOKENS.orangeSoft, 'battery-3'],
      ['fatigued', 'Fatigado', BRAND.orange, 'battery-2'],
      ['heavily_fatigued', 'Muito fatigado', WORKOUT_MODE_TOKENS.danger, 'battery-1'],
    ];
    for (const [band, label, color, icon] of cases) {
      const view = fatigueChipView(reading(band, label));
      expect(view.available).toBe(true);
      expect(view.band).toBe(band);
      expect(view.label).toBe(label);
      expect(view.color).toBe(color);
      expect(view.icon).toBe(icon);
      expect(view.ariaLabel).toBe(`Fadiga atual do grupo Peito: ${label}`);
    }
  });

  it('nunca reescreve o rótulo do backend', () => {
    expect(fatigueChipView(reading('pre_fatigued', 'Pré-fatigado (2 séries)')).label).toBe('Pré-fatigado (2 séries)');
  });

  it('sem leitura: "Fadiga indisponível", nunca "Descansado"', () => {
    for (const value of [null, undefined, reading('rested', ''), reading('rested', '   ')]) {
      const view = fatigueChipView(value);
      expect(view.available).toBe(false);
      expect(view.label).toBe(FATIGUE_UNAVAILABLE_LABEL);
      expect(view.label).not.toMatch(/descansad|recuperad/i);
      expect(view.tone).toBe('neutral');
      expect(view.icon).toBe('battery-off');
    }
  });

  it('faixa desconhecida com rótulo: mostra o rótulo em tom neutro', () => {
    const view = fatigueChipView(reading('nova_faixa', 'Nova faixa'));
    expect(view.available).toBe(true);
    expect(view.band).toBeNull();
    expect(view.label).toBe('Nova faixa');
    expect(view.tone).toBe('neutral');
  });
});
