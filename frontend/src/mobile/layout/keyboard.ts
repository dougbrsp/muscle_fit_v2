'use client';

import { useEffect, useState } from 'react';

/** Tipos de `<input>` que não abrem o teclado virtual. */
const NON_TEXT_INPUT_TYPES: ReadonlySet<string> = new Set([
  'button',
  'checkbox',
  'color',
  'file',
  'hidden',
  'image',
  'radio',
  'range',
  'reset',
  'submit',
]);

/** O elemento focado abre o teclado virtual? Pura, testável sem DOM. */
export function isTextEntry(target: {
  tagName?: string;
  type?: string;
  isContentEditable?: boolean;
} | null): boolean {
  if (!target) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName?.toUpperCase();
  if (tag === 'TEXTAREA') return true;
  if (tag === 'INPUT') return !NON_TEXT_INPUT_TYPES.has((target.type ?? 'text').toLowerCase());
  return false;
}

/**
 * MB-8: enquanto um campo de texto tem foco (teclado aberto no iOS), a tab bar
 * se esconde e a ação fixa desce, para nada cobrir o campo. Baseado em foco,
 * sem medir a altura do teclado.
 */
export function useKeyboardOpen(): boolean {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const sync = () => setOpen(isTextEntry(document.activeElement as HTMLElement | null));
    const onFocusIn = (event: FocusEvent) => {
      clearTimeout(timer);
      setOpen(isTextEntry(event.target as HTMLElement | null));
    };
    // focusout dispara antes do novo foco existir: espera um tick.
    const onFocusOut = () => {
      clearTimeout(timer);
      timer = setTimeout(sync, 60);
    };
    document.addEventListener('focusin', onFocusIn);
    document.addEventListener('focusout', onFocusOut);
    return () => {
      clearTimeout(timer);
      document.removeEventListener('focusin', onFocusIn);
      document.removeEventListener('focusout', onFocusOut);
    };
  }, []);

  return open;
}
