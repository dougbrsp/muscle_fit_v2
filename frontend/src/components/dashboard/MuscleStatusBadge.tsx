'use client';

import type { MuscleVolumeStatus } from '@/lib/dashboard-v2-types';
import { StatusBadge } from './StatusBadge';
import { WEEKLY_VOLUME_STATUS_ORDER } from '@/lib/dashboard-status';

/**
 * Apresentação dos 5 estados de `classify_weekly_volume_status`
 * (`api/app/intelligence/volume_service.py`).
 *
 * Rótulo, cor e ícone vêm do registro único (`lib/dashboard-status.ts`) — este
 * arquivo não define mais nenhum deles, só compõe o selo e
 * reexporta a ordem que o mapa corporal consome.
 */
export const MUSCLE_STATUS_ORDER: MuscleVolumeStatus[] = [...WEEKLY_VOLUME_STATUS_ORDER];

export function MuscleStatusBadge({ status }: { status: MuscleVolumeStatus }) {
  return <StatusBadge domain="weeklyVolume" code={status} />;
}
