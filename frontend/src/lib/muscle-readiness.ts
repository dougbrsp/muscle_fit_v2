import type { MuscleRecoveryReadinessStatus } from './analytics-types';
import { statusVisual } from './dashboard-status';

/**
 * Rótulo/cor/ícone para `readiness_status` retornado por
 * `GET /analytics/muscle-recovery` (ver `MuscleRecoveryService`).
 *
 * Desde a F0.4 estas funções são um ATALHO para o registro único
 * (`dashboard-status.ts`), não mais uma tabela própria: a antiga `/treinos` (aposentada) definia
 * `NO_DIRECT_WORK` como "Evitar trabalho direto" enquanto este arquivo dizia
 * "Sem trabalho direto". O rótulo vencedor foi o de `/treinos` — o estado
 * significa `direct_sets_max = 0` com trabalho indireto ainda permitido, ou
 * seja, uma orientação ("evite"), não uma constatação.
 */
export type ReadinessStatus = MuscleRecoveryReadinessStatus | string;

export function readinessLabel(status: ReadinessStatus): string {
  return statusVisual('readiness', status).label;
}

export function readinessColor(status: ReadinessStatus): string {
  return statusVisual('readiness', status).color;
}

export function readinessIcon(status: ReadinessStatus) {
  return statusVisual('readiness', status).Icon;
}
