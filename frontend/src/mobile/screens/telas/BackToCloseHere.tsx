'use client';

import { useBackToClose } from './common';

/**
 * Registra na pilha de visões do celular a folha/modal em que este componente está montado: o voltar
 * (gesto do iOS, botão do Android) fecha só ela, sem sair do editor e perder o rascunho. Renderize
 * dentro da folha, só no ramo mobile; ao desmontar a entrada sai da pilha.
 */
export function BackToCloseHere({ onClose }: { onClose: () => void }) {
  useBackToClose(true, onClose);
  return null;
}
