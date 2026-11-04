import type { ReactNode } from 'react';
import styles from './ui.module.css';

/**
 * Ação principal da tela, fixa logo acima da tab bar (zona do polegar). Uma
 * por tela. Reserva o espaço no fluxo (não cobre o fim do conteúdo) e desce
 * para a base quando o teclado abre e a tab bar se esconde (MB-8). Passe um
 * botão de 44 pt ou mais, em largura total.
 */
export function StickyAction({ children }: { children: ReactNode }) {
  return (
    <>
      <div className={styles.stickySpacer} aria-hidden />
      <div className={styles.sticky}>{children}</div>
    </>
  );
}
