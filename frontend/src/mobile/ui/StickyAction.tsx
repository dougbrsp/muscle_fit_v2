import type { ReactNode } from 'react';
import styles from './ui.module.css';

/**
 * Ação principal da tela, fixa logo acima da tab bar (zona do polegar). Uma
 * por tela. Reserva o espaço no fim da página (não cobre o fim do conteúdo, esteja onde estiver na árvore) e desce
 * para a base quando o teclado abre e a tab bar se esconde (MB-8). Passe um
 * botão de 44 pt ou mais, em largura total.
 */
export function StickyAction({ children }: { children: ReactNode }) {
  // A folga do fim da página não é um espaçador no fluxo (ele ficaria no meio quando há
  // conteúdo depois da ação): o shell soma a altura da barra ao respiro inferior do
  // `<main>` sempre que há uma `[data-sticky-action]` na tela (mobile-shell.module.css).
  return (
    <div className={styles.sticky} data-sticky-action>
      {children}
    </div>
  );
}
