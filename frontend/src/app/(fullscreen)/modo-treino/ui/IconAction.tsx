'use client';

import { Loader } from '@mantine/core';
import type { ReactNode } from 'react';
import styles from '../workout-mode.module.css';

export type WorkoutIconAction = {
  key: string;
  /** Rótulo completo, acessível (`aria-label`) e no tooltip (`title`): "Trocar exercício". */
  label: string;
  /** Legenda curta visível sob o ícone: "Trocar". */
  caption: string;
  icon: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  loading?: boolean;
  testId?: string;
};

/**
 * Ação explícita de ícone com legenda curta (Trocar / Pular / Encerrar),
 * alvo ≥ 44 px. Nunca escondida em menu: o ícone e a legenda ficam visíveis,
 * e o rótulo completo vai no `aria-label` e no `title` (tooltip).
 */
export function IconAction({ action }: { action: WorkoutIconAction }) {
  return (
    <button
      type="button"
      className={styles.iconAction}
      onClick={action.onClick}
      disabled={action.disabled || action.loading}
      aria-label={action.label}
      aria-busy={action.loading || undefined}
      title={action.label}
      data-testid={action.testId}
    >
      <span className={styles.iconActionIcon} aria-hidden="true">
        {action.loading ? <Loader size={16} color="brand" /> : action.icon}
      </span>
      <span className={styles.iconActionCaption} aria-hidden="true">
        {action.caption}
      </span>
    </button>
  );
}
