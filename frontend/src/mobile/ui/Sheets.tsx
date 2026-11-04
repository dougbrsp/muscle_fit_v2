'use client';

import { Button, Drawer } from '@mantine/core';
import { useReducedMotion } from '@mantine/hooks';
import { IconAdjustmentsHorizontal } from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { MOBILE_CSS_VARS } from '../css-vars';
import { MOBILE_TOKENS } from '../tokens';
import { filterButtonLabel, normalizeCount } from './pure';
import styles from './ui.module.css';

/**
 * Folha que sobe da base (modal curto, filtros, escolhas). Foco preso, Esc e
 * toque fora fecham (Mantine Drawer). Altura pelo conteúdo, no máximo 85% da
 * tela; conteúdo longo rola dentro da folha. Modal muito longo vira tela cheia
 * (rota), não folha. Abra sempre a partir de um gesto do usuário.
 */
export function BottomSheet({
  opened,
  onClose,
  title,
  children,
  footer,
  closeOnClickOutside = true,
  closeOnEscape = true,
}: {
  opened: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  /** Ação fixa no rodapé da folha (ex.: Aplicar). */
  footer?: ReactNode;
  /** `false` quando a folha guarda um rascunho que um toque fora não pode descartar. */
  closeOnClickOutside?: boolean;
  /** `false` enquanto outra camada (ex.: o leitor de código) por cima deve tratar o Esc sozinha. */
  closeOnEscape?: boolean;
}) {
  const reduceMotion = useReducedMotion();
  return (
    <Drawer
      opened={opened}
      onClose={onClose}
      closeOnClickOutside={closeOnClickOutside}
      closeOnEscape={closeOnEscape}
      position="bottom"
      size="auto"
      title={title}
      transitionProps={{ duration: reduceMotion ? 0 : 220 }}
      styles={{
        content: {
          background: MOBILE_TOKENS.bg,
          borderRadius: `${MOBILE_TOKENS.radiusSheet}px ${MOBILE_TOKENS.radiusSheet}px 0 0`,
          width: '100%',
          height: 'auto',
          flex: '0 0 auto',
          maxHeight: '85dvh',
        },
        header: {
          background: MOBILE_TOKENS.bg,
          minHeight: 56,
          paddingLeft: 'calc(16px + env(safe-area-inset-left))',
          paddingRight: 'calc(8px + env(safe-area-inset-right))',
        },
        title: { fontSize: 17, fontWeight: 700, color: MOBILE_TOKENS.text },
        close: { width: MOBILE_TOKENS.touchTarget, height: MOBILE_TOKENS.touchTarget },
        body: { paddingTop: 0, paddingLeft: 0, paddingRight: 0 },
      }}
    >
      {/* o Drawer renderiza num portal fora do shell: reaplica as variaveis --m-* */}
      <div style={MOBILE_CSS_VARS} className={styles.sheetBody}>
        {children}
      </div>
      {footer ? (
        <div style={MOBILE_CSS_VARS} className={styles.sheetFooter}>
          {footer}
        </div>
      ) : null}
    </Drawer>
  );
}

/** Botão "Filtros (n)" que abre a FilterSheet; n = filtros ativos. */
export function FilterButton({
  activeCount,
  onClick,
}: {
  activeCount: number;
  onClick: () => void;
}) {
  const n = normalizeCount(activeCount);
  return (
    <button
      type="button"
      className={styles.filterButton}
      onClick={onClick}
      aria-haspopup="dialog"
      aria-label={filterButtonLabel(n)}
    >
      <IconAdjustmentsHorizontal size={18} aria-hidden />
      <span aria-hidden>Filtros</span>
      {n > 0 ? (
        <span className={styles.filterBadge} aria-hidden>
          {n}
        </span>
      ) : null}
    </button>
  );
}

/**
 * Folha "Filtros": os campos entram como `children` (use inputs de 16 px).
 * "Limpar" zera tudo; "Aplicar" fecha a folha. O estado dos filtros é da tela
 * (idealmente na URL), nunca da folha.
 */
export function FilterSheet({
  opened,
  onClose,
  activeCount,
  onClear,
  children,
}: {
  opened: boolean;
  onClose: () => void;
  activeCount: number;
  onClear: () => void;
  children: ReactNode;
}) {
  const n = normalizeCount(activeCount);
  return (
    <BottomSheet
      opened={opened}
      onClose={onClose}
      title={filterButtonLabel(n)}
      footer={
        <>
          <Button
            variant="default"
            size="md"
            style={{ flex: 1, minHeight: MOBILE_TOKENS.touchTarget }}
            onClick={onClear}
            disabled={n === 0}
          >
            Limpar
          </Button>
          <Button
            color="brand"
            size="md"
            style={{ flex: 2, minHeight: MOBILE_TOKENS.touchTarget }}
            onClick={onClose}
          >
            Aplicar
          </Button>
        </>
      }
    >
      <div className={styles.filterFields}>{children}</div>
    </BottomSheet>
  );
}
