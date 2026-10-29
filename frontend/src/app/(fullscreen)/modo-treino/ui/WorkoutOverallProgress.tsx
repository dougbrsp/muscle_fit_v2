import type { ReactNode } from 'react';
import styles from '../workout-mode.module.css';

/**
 * Andamento GLOBAL do treino numa linha só, dentro da barra fina do topo:
 * "Exercício X de Y", barra de exercícios concluídos e "N concluído(s)". Não
 * é a progressão por séries (rejeitada na spec): a barra só anda quando um
 * exercício termina. Os números chegam prontos de `PlanExecutionScreen`
 * (mesmo cálculo de antes: concluídos / total, total mínimo 1).
 */
export function WorkoutOverallProgress({
  positionLabel,
  completedCount,
  totalCount,
  trailing,
}: {
  positionLabel: string;
  completedCount: number;
  totalCount: number;
  // Selo curto no fim da linha (ex.: "Avançado").
  trailing?: ReactNode;
}) {
  const percent = (completedCount / Math.max(1, totalCount)) * 100;
  return (
    <div className={styles.progressLine} data-testid="workout-overall-progress">
      <span className={styles.progressLabel}>{positionLabel}</span>
      <div
        className={styles.progressTrack}
        role="progressbar"
        aria-label="Exercícios concluídos"
        aria-valuemin={0}
        aria-valuemax={Math.max(1, totalCount)}
        aria-valuenow={completedCount}
      >
        <div className={styles.progressFill} style={{ width: `${percent}%` }} />
      </div>
      <span className={styles.progressDone}>{completedCount} concluído(s)</span>
      {trailing}
    </div>
  );
}
