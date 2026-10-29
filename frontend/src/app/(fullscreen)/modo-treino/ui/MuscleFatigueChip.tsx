import { IconBattery1, IconBattery2, IconBattery3, IconBattery4, IconBatteryOff } from '@tabler/icons-react';
import { fatigueChipView, type FatigueIconKey } from '@/lib/workout-mode-fatigue-view';
import type { GroupFatigueReading } from '@/lib/workout-mode-types';
import { StatusChip } from './StatusChip';

const ICONS: Record<FatigueIconKey, typeof IconBattery4> = {
  'battery-4': IconBattery4,
  'battery-3': IconBattery3,
  'battery-2': IconBattery2,
  'battery-1': IconBattery1,
  'battery-off': IconBatteryOff,
};

/**
 * Fadiga atual do grupo muscular (M79), num chip único para briefing,
 * execução e descanso. Faixa e rótulo prontos do backend; cor e ícone pelo
 * mapa de `lib/workout-mode-fatigue-view.ts`. Sem leitura: "Fadiga
 * indisponível" (ou nada, com `hideWhenUnavailable`) — nunca "Descansado".
 */
export function MuscleFatigueChip({
  reading,
  hideWhenUnavailable = false,
  size = 'sm',
}: {
  reading: GroupFatigueReading | null | undefined;
  hideWhenUnavailable?: boolean;
  size?: 'sm' | 'md';
}) {
  const view = fatigueChipView(reading);
  if (!view.available && hideWhenUnavailable) return null;
  const Icon = ICONS[view.icon];
  return (
    <StatusChip
      icon={<Icon size={size === 'sm' ? 14 : 16} />}
      label={view.label}
      color={view.color}
      ariaLabel={view.ariaLabel}
      testId="muscle-fatigue-chip"
      size={size}
    />
  );
}
