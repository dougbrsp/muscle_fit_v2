import type { ReactNode } from 'react';
import styles from './ui.module.css';

/**
 * Título grande da tela (estilo iOS "large title"). Um `<h1>` por tela. O
 * voltar fica no header do shell (2º nível), não aqui; `actions` recebe até
 * dois botões compactos (44 pt) alinhados à direita.
 */
export function ScreenHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className={styles.screenHeader}>
      <div className={styles.screenHeaderText}>
        <h1 className={styles.largeTitle}>{title}</h1>
        {subtitle ? <p className={styles.subtitle}>{subtitle}</p> : null}
      </div>
      {actions ? <div className={styles.headerActions}>{actions}</div> : null}
    </header>
  );
}
