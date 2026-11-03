import { IconChevronRight, type Icon as TablerIcon } from '@tabler/icons-react';
import Link from 'next/link';
import type { ReactNode } from 'react';
import { rowKind } from './pure';
import styles from './ui.module.css';

/**
 * Grupo de linhas sobre uma superfície única (lista agrupada do iOS): título
 * pequeno acima, rodapé explicativo opcional, divisores finos entre linhas.
 * Agrupar por superfície substitui contornos e cartões soltos.
 */
export function ListGroup({
  title,
  footer,
  children,
  'aria-label': ariaLabel,
}: {
  title?: string;
  footer?: ReactNode;
  children: ReactNode;
  'aria-label'?: string;
}) {
  return (
    <section className={styles.group} aria-label={ariaLabel ?? title}>
      {title ? <h2 className={styles.groupTitle}>{title}</h2> : null}
      <div className={styles.groupBody}>{children}</div>
      {footer ? <p className={styles.groupFooter}>{footer}</p> : null}
    </section>
  );
}

/** Bloco livre (texto, selos) dentro de um ListGroup, com o respiro das linhas. */
export function ListBlock({ children }: { children: ReactNode }) {
  return <div className={styles.block}>{children}</div>;
}

export type ListRowProps = {
  title: ReactNode;
  subtitle?: ReactNode;
  /** Valor curto à direita (ex.: "12 kg"). */
  value?: ReactNode;
  icon?: TablerIcon;
  /** Caminho interno: a linha vira link com chevron. */
  href?: string;
  /** Sem `href`: a linha vira botão (abre sheet, executa ação). */
  onClick?: () => void;
  /** Substitui o chevron (ex.: Switch). Sem `href`/`onClick` a linha é estática. */
  trailing?: ReactNode;
  current?: boolean;
  destructive?: boolean;
};

/**
 * Linha de lista com alvo de toque >= 44 pt (52 pt de altura mínima). 1 título,
 * 1 subtítulo opcional e 1 valor opcional: quem precisa de mais usa DataCard.
 */
export function ListRow({
  title,
  subtitle,
  value,
  icon: Icon,
  href,
  onClick,
  trailing,
  current,
  destructive,
}: ListRowProps) {
  const kind = rowKind({ href, onClick });
  const className = [
    styles.row,
    Icon ? styles.rowWithIcon : '',
    kind === 'static' ? styles.rowStatic : '',
    destructive ? styles.rowDestructive : '',
  ]
    .filter(Boolean)
    .join(' ');
  const content = (
    <>
      {Icon ? (
        <span className={styles.rowIcon} aria-hidden>
          <Icon size={18} />
        </span>
      ) : null}
      <span className={styles.rowBody}>
        <span className={styles.rowTitle}>{title}</span>
        {subtitle ? <span className={styles.rowSubtitle}>{subtitle}</span> : null}
      </span>
      {value ? <span className={styles.rowValue}>{value}</span> : null}
      {trailing ??
        (kind !== 'static' && !destructive ? (
          <IconChevronRight size={18} className={styles.chevron} aria-hidden />
        ) : null)}
    </>
  );
  const ariaCurrent = current ? ('page' as const) : undefined;
  if (kind === 'link') {
    return (
      <Link href={href as string} className={className} aria-current={ariaCurrent}>
        {content}
      </Link>
    );
  }
  if (kind === 'button') {
    return (
      <button type="button" className={className} onClick={onClick} aria-current={ariaCurrent}>
        {content}
      </button>
    );
  }
  return (
    <div className={className} aria-current={ariaCurrent}>
      {content}
    </div>
  );
}
