// Apresentação da fadiga atual do grupo muscular no Modo Treino (novo visual,
// PR1/3). Mapeamento de ENUM para cor e ícone, e nada mais:
//
// - a faixa (`band`) e o rótulo (`band_label`) chegam prontos do backend
//   (dono: `api/app/domain/prior_fatigue.py`, via `current_group_fatigue` de
//   `GET /exercises/last-session-sets`); aqui não há limiar, contagem de
//   séries nem nível, e o rótulo nunca é reescrito;
// - sem leitura (resposta antiga, grupo sem catálogo, faixa desconhecida sem
//   rótulo) a tela diz "Fadiga indisponível" — ausência de dado NUNCA vira
//   "Descansado";
// - a cor nunca é o único sinal: o chip sempre leva rótulo e ícone.
//
// Puro e sem React: o ícone é uma chave que o componente
// (`modo-treino/ui/MuscleFatigueChip.tsx`) traduz para o ícone Tabler.

import { BRAND, WORKOUT_MODE_TOKENS } from '@/theme';
import type { GroupFatigueBand, GroupFatigueReading } from './workout-mode-types';

export type FatigueTone = 'success' | 'attention' | 'warning' | 'danger' | 'neutral';
export type FatigueIconKey = 'battery-4' | 'battery-3' | 'battery-2' | 'battery-1' | 'battery-off';

export type FatigueChipView = {
  available: boolean;
  band: GroupFatigueBand | null;
  label: string;
  tone: FatigueTone;
  color: string;
  icon: FatigueIconKey;
  /** Texto para leitor de tela: "Fadiga atual do grupo Peito: Pré-fatigado". */
  ariaLabel: string;
};

export const FATIGUE_UNAVAILABLE_LABEL = 'Fadiga indisponível';

const BAND_VISUAL: Record<GroupFatigueBand, { tone: FatigueTone; icon: FatigueIconKey }> = {
  rested: { tone: 'success', icon: 'battery-4' },
  pre_fatigued: { tone: 'attention', icon: 'battery-3' },
  fatigued: { tone: 'warning', icon: 'battery-2' },
  heavily_fatigued: { tone: 'danger', icon: 'battery-1' },
};

export const FATIGUE_TONE_COLOR: Record<FatigueTone, string> = {
  success: WORKOUT_MODE_TOKENS.success,
  attention: WORKOUT_MODE_TOKENS.orangeSoft,
  warning: BRAND.orange,
  danger: WORKOUT_MODE_TOKENS.danger,
  neutral: BRAND.textMuted,
};

function isKnownBand(value: unknown): value is GroupFatigueBand {
  return typeof value === 'string' && Object.prototype.hasOwnProperty.call(BAND_VISUAL, value);
}

/**
 * Leitura do backend -> chip. `null`/`undefined` = sem leitura: o chip diz
 * "Fadiga indisponível" em cinza. Faixa nova que o front ainda não conhece,
 * mas com rótulo, aparece com o rótulo do backend em tom neutro.
 */
export function fatigueChipView(reading: GroupFatigueReading | null | undefined): FatigueChipView {
  const label = (reading?.band_label || '').trim();
  const group = (reading?.muscle_group_name || '').trim();
  if (!reading || !label) {
    return {
      available: false,
      band: null,
      label: FATIGUE_UNAVAILABLE_LABEL,
      tone: 'neutral',
      color: FATIGUE_TONE_COLOR.neutral,
      icon: 'battery-off',
      ariaLabel: FATIGUE_UNAVAILABLE_LABEL,
    };
  }
  const visual = isKnownBand(reading.band) ? BAND_VISUAL[reading.band] : { tone: 'neutral' as const, icon: 'battery-off' as const };
  return {
    available: true,
    band: isKnownBand(reading.band) ? reading.band : null,
    label,
    tone: visual.tone,
    color: FATIGUE_TONE_COLOR[visual.tone],
    icon: visual.icon,
    ariaLabel: `Fadiga atual do grupo${group ? ` ${group}` : ''}: ${label}`,
  };
}
