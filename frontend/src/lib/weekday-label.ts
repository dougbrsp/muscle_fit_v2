// Rótulos de dia da semana do slot do Modelo (só apresentação).
// Convenção do backend (`WorkoutPlanTemplateSlotIn.weekday`): 1=segunda..7=domingo.
// A abreviação deriva de `WEEKDAY_OPTIONS` (mesma fonte do select do editor),
// sem uma segunda tabela de dias.

import { WEEKDAY_OPTIONS } from './workout-plan-templates-types';

/** "SEG", "TER", ..., "SÁB", "DOM"; null sem dia ou com valor desconhecido. */
export function weekdayShortLabel(weekday: number | null | undefined): string | null {
  if (weekday == null) return null;
  const label = WEEKDAY_OPTIONS.find((option) => option.value === String(weekday))?.label;
  return label ? label.slice(0, 3).toLocaleUpperCase('pt-BR') : null;
}

/** Título do slot recolhido: "Dia N · SEG · nome" (sem dia: "Dia N · nome"). */
export function collapsedSlotTitle(
  slotIndex: number,
  name: string,
  weekday: number | null | undefined,
): string {
  const short = weekdayShortLabel(weekday);
  const parts = [`Dia ${slotIndex + 1}`, ...(short ? [short] : []), name || 'Sem nome'];
  return parts.join(' · ');
}
