import type { ReactNode } from 'react';
import styles from '../workout-mode.module.css';

/**
 * Métrica condensada (rótulo pequeno + valor grande): "SÉRIES X REPS",
 * "CARGA", "ESFORÇO", "DESCANSO". Só apresenta o valor já formatado por quem
 * chama — nenhum cálculo aqui. Números tabulares vêm do `.root`.
 */
export function MetricTile({
  icon,
  label,
  value,
  testId,
}: {
  icon?: ReactNode;
  label: string;
  value: string;
  testId?: string;
}) {
  return (
    <div className={styles.metricTile} data-testid={testId}>
      <div className={styles.metricLabel}>
        {icon && (
          <span className={styles.metricIcon} aria-hidden="true">
            {icon}
          </span>
        )}
        <span>{label}</span>
      </div>
      <div className={styles.metricValue}>{value}</div>
    </div>
  );
}
