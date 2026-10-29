import type { ReactNode } from 'react';
import styles from '../workout-mode.module.css';

/**
 * Chip de estado compacto (ícone + rótulo, contorno na cor do estado). A cor
 * nunca é o único sinal: o rótulo é sempre texto visível. `color` é uma cor
 * CSS pronta (token de `BRAND` ou variável do Mantine) escolhida por quem
 * chama a partir de um mapeamento existente.
 */
export function StatusChip({
  icon,
  label,
  color,
  ariaLabel,
  testId,
  size = 'md',
}: {
  icon?: ReactNode;
  label: string;
  color: string;
  ariaLabel?: string;
  testId?: string;
  /** `sm`: chip baixo (22 px) para linhas compactas (título, próximo exercício). */
  size?: 'sm' | 'md';
}) {
  return (
    <span
      className={size === 'sm' ? `${styles.chip} ${styles.chipSm}` : styles.chip}
      style={{ color }} aria-label={ariaLabel} role={ariaLabel ? 'img' : undefined} data-testid={testId}>
      {icon && (
        <span aria-hidden="true" style={{ display: 'inline-flex' }}>
          {icon}
        </span>
      )}
      <span className={styles.chipLabel}>{label}</span>
    </span>
  );
}
