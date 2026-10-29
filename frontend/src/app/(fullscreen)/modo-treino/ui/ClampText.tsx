'use client';

import { useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { isLongText } from '@/lib/workout-mode-execution-view';
import styles from '../workout-mode.module.css';

/**
 * Texto cortado em `lines` linhas com "ver mais" INLINE, na mesma linha (PR4:
 * o resumo fica na tela, o texto longo abre no lugar, sem gaveta). O texto
 * inteiro está sempre no DOM — o corte é só visual (CSS). O botão aparece
 * quando o texto é longo ou quando ele de fato transborda o corte (medido no
 * navegador, inclusive ao girar a tela).
 */
export function ClampText({
  children,
  text,
  lines = 1,
  lead,
  testId,
  className,
}: {
  // Conteúdo extra depois do texto, dentro do mesmo bloco cortado.
  children?: ReactNode;
  text: string;
  lines?: number;
  // Prefixo em destaque, na mesma linha ("Dica da IA:").
  lead?: ReactNode;
  testId?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [overflowing, setOverflowing] = useState(false);
  const textId = useId();
  const ref = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || open) return;
    const measure = () => setOverflowing(el.scrollHeight > el.clientHeight + 1);
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [text, open, lines]);

  const showToggle = open || overflowing || isLongText(text);
  const style = { '--wm-clamp': lines } as CSSProperties;

  return (
    <div className={styles.clampRow}>
      <p
        ref={ref}
        id={textId}
        className={`${styles.clampText} ${open ? styles.clampTextOpen : ''} ${className ?? ''}`.trim()}
        style={style}
        data-testid={testId}
      >
        {lead && <>{lead} </>}
        {text}
        {children}
      </p>
      {showToggle && (
        <button
          type="button"
          className={styles.textToggle}
          aria-expanded={open}
          aria-controls={textId}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? 'ver menos' : 'ver mais'}
        </button>
      )}
    </div>
  );
}
