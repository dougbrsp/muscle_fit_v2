// Quantos leitores de código de barras estão abertos agora. O leitor é um modal por cima do cadastro
// de alimento; o Esc que fecha o leitor não pode fechar também o cadastro (e perder o que foi digitado).
// Só estado de interface, sem regra de domínio.

import { useSyncExternalStore } from 'react';

let abertos = 0;
const ouvintes = new Set<() => void>();

function notificar() {
  ouvintes.forEach((ouvinte) => ouvinte());
}

/** Registra um leitor aberto; devolve a função que o desregistra. */
export function registerOpenScanner(): () => void {
  abertos += 1;
  notificar();
  let ativo = true;
  return () => {
    if (!ativo) return;
    ativo = false;
    abertos -= 1;
    notificar();
  };
}

export function hasOpenScanner(): boolean {
  return abertos > 0;
}

function subscribe(ouvinte: () => void) {
  ouvintes.add(ouvinte);
  return () => {
    ouvintes.delete(ouvinte);
  };
}

/** Há leitor aberto? (Reativo; no servidor, `false`.) */
export function useScannerOpen(): boolean {
  return useSyncExternalStore(subscribe, hasOpenScanner, () => false);
}
