'use client';

import { IconChevronDown } from '@tabler/icons-react';
import { useId, useState, type ReactNode } from 'react';
import styles from '../workout-mode.module.css';

/**
 * Linha expansível do Modo Treino (44 px fechada). A LINHA INTEIRA é o
 * botão, com `aria-expanded` e `aria-controls` apontando para o painel, que
 * é rotulado pelo título (`aria-labelledby`). Cada instância guarda o próprio
 * estado; abrir uma não rola a página nem dispara busca de dados — o painel
 * abre no lugar (inline).
 *
 * `layout="inline"` põe título e resumo na MESMA linha ("Ajuste de hoje:
 * Manter"); `stacked` (padrão) deixa o resumo numa 2ª linha curta. O resumo
 * fica visível com a seção fechada.
 */
export function SectionAccordion({
  icon,
  title,
  summary,
  children,
  defaultOpen = false,
  variant = 'default',
  layout = 'stacked',
  openClassName,
  testId,
}: {
  icon?: ReactNode;
  title: ReactNode;
  summary?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  variant?: 'default' | 'accent' | 'warning';
  layout?: 'stacked' | 'inline';
  // Classe extra quando aberta (ex.: ocupar a linha toda numa fileira dupla).
  openClassName?: string;
  testId?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const baseId = useId();
  const headerId = `${baseId}-header`;
  const panelId = `${baseId}-panel`;
  const variantClass =
    variant === 'accent' ? styles.accordionAccent : variant === 'warning' ? styles.accordionWarning : '';
  const className = [styles.accordion, variantClass, open ? openClassName : ''].filter(Boolean).join(' ');

  return (
    <section className={className} data-testid={testId}>
      <button
        type="button"
        id={headerId}
        className={styles.accordionHeader}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
      >
        {icon && (
          <span className={styles.accordionIcon} aria-hidden="true">
            {icon}
          </span>
        )}
        {layout === 'inline' ? (
          <span className={`${styles.accordionText} ${styles.accordionInline}`}>
            <span className={styles.accordionTitle}>
              {title}
              {summary != null ? ':' : ''}
            </span>
            {summary != null && <span className={styles.accordionInlineSummary}>{summary}</span>}
          </span>
        ) : (
          <span className={styles.accordionText}>
            {typeof title === 'string' ? <span className={styles.accordionTitle}>{title}</span> : title}
            {summary != null &&
              (typeof summary === 'string' ? <span className={styles.accordionSummary}>{summary}</span> : summary)}
          </span>
        )}
        <span className={styles.accordionChevron} aria-hidden="true">
          <IconChevronDown size={18} />
        </span>
      </button>
      <div id={panelId} role="region" aria-labelledby={headerId} hidden={!open} className={styles.accordionPanel}>
        {children}
      </div>
    </section>
  );
}
