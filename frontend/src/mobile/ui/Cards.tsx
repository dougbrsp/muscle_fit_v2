import Link from 'next/link';
import type { ReactNode } from 'react';
import { pickSecondary } from './pure';
import styles from './ui.module.css';

export type DataCardSecondary = { label: string; value: ReactNode };

/**
 * Cartão com 1 dado principal (`value`), até 2 secundários e um detalhe por
 * toque (`href` ou `onClick`). Substitui a linha de tabela. O texto e o número
 * chegam prontos da API: o cartão nunca calcula nem classifica.
 */
export function DataCard({
  title,
  value,
  status,
  secondary,
  href,
  onClick,
  'aria-label': ariaLabel,
}: {
  title: ReactNode;
  value?: ReactNode;
  /** Selo de estado (ex.: StatusBadge) no canto superior direito. */
  status?: ReactNode;
  secondary?: readonly DataCardSecondary[];
  href?: string;
  onClick?: () => void;
  'aria-label'?: string;
}) {
  const extras = pickSecondary(secondary);
  const body = (
    <>
      <span className={styles.cardHead}>
        <span className={styles.cardTitle}>{title}</span>
        {status}
      </span>
      {value !== undefined && value !== null ? <span className={styles.cardValue}>{value}</span> : null}
      {extras.length > 0 ? (
        <span className={styles.cardSecondary}>
          {extras.map((item) => (
            <span key={item.label}>
              <span className={styles.cardSecLabel}>{item.label}</span>
              <span className={styles.cardSecValue}>{item.value}</span>
            </span>
          ))}
        </span>
      ) : null}
    </>
  );
  if (href) {
    return (
      <Link href={href} className={styles.card} aria-label={ariaLabel}>
        {body}
      </Link>
    );
  }
  if (onClick) {
    return (
      <button type="button" className={styles.card} onClick={onClick} aria-label={ariaLabel}>
        {body}
      </button>
    );
  }
  return (
    <div className={styles.card} aria-label={ariaLabel}>
      {body}
    </div>
  );
}

/** Número em destaque com rótulo curto e, opcional, uma dica. */
export function StatTile({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
}) {
  return (
    <div className={styles.tile}>
      <div className={styles.tileLabel}>{label}</div>
      <div className={styles.tileValue}>{value}</div>
      {hint ? <div className={styles.tileHint}>{hint}</div> : null}
    </div>
  );
}

/** Grade de 2 colunas para StatTile (cabe em 320 px). */
export function StatGrid({ children }: { children: ReactNode }) {
  return <div className={styles.tileGrid}>{children}</div>;
}
