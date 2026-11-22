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
  readOnly?: boolean;
  disabled?: boolean;
  inputMode?: string;
} | null): boolean {
  if (!target) return false;
  // readOnly (ex.: Select do Mantine), desabilitado e inputMode="none" nao abrem teclado.
  if (target.readOnly || target.disabled || target.inputMode === 'none') return false;
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
    // Toque em curso (#252): o navegador move o foco no pointerdown (Chromium: focusin no botão);
    // enquanto pressiona, nada fecha o estado (focusin fora de campo, focusout, observer): ao soltar,
    // o sync agendado roda depois do click.
    let pressing = false;
    const onFocusIn = (event: FocusEvent) => {
      clearTimeout(timer);
      const text = isTextEntry(event.target as HTMLElement | null);
      if (pressing && !text) return;
      setOpen(text);
    };
    const onPointerDown = () => {
      clearTimeout(timer); // sync agendado por um focusout (Tab) não pode rodar durante o toque
      pressing = true;
    };
    const onPointerEnd = () => {
      if (!pressing) return;
      pressing = false;
      clearTimeout(timer);
      timer = setTimeout(sync, 0);
    };
    // Janela perdeu o foco/ficou oculta no meio do toque: não deixa "pressionando" preso.
    const onWindowLeave = () => {
      pressing = false;
      clearTimeout(timer);
      timer = setTimeout(sync, 0);
    };
    // focusout dispara antes do novo foco existir: espera um tick.
    const onFocusOut = () => {
      clearTimeout(timer);
      if (pressing) return;
      timer = setTimeout(sync, 60);
    };
    // Campo focado que SAI da tela (visão fechada pelo voltar, rota trocada): o navegador não
    // avisa o foco, e o estado "teclado aberto" ficaria preso até o próximo foco, que seria o
    // PRÓPRIO toque seguinte (a barra fixa mudaria de lugar entre o toque e a soltura e o
    // toque se perderia). Reconcilia com o foco real quando a árvore muda.
    const observer = new MutationObserver(() => {
      if (!pressing && !isTextEntry(document.activeElement as HTMLElement | null)) setOpen(false);
    });
    observer.observe(document.body, { childList: true, subtree: true });
    document.addEventListener('focusin', onFocusIn);
    document.addEventListener('focusout', onFocusOut);
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('pointerup', onPointerEnd, true);
    document.addEventListener('pointercancel', onPointerEnd, true);
    window.addEventListener('blur', onWindowLeave);
    document.addEventListener('visibilitychange', onWindowLeave);
    return () => {
      observer.disconnect();
      clearTimeout(timer);
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('pointerup', onPointerEnd, true);
      document.removeEventListener('pointercancel', onPointerEnd, true);
      window.removeEventListener('blur', onWindowLeave);
      document.removeEventListener('visibilitychange', onWindowLeave);
      document.removeEventListener('focusin', onFocusIn);
      document.removeEventListener('focusout', onFocusOut);
    };
  }, []);

  return open;
}
