'use client';

import { useBackToClose } from './common';

/**
 * Registra na pilha de visões do celular a folha/modal que está `open`: o voltar (gesto do iOS, botão do
 * Android) fecha só ela, sem sair do editor e perder o rascunho. Renderize FORA do conteúdo da folha, sempre
 * montado e só no ramo mobile, para que fechar por código (X, salvar, ler o código) desfaça a entrada de
 * histórico em vez de deixá-la órfã (um toque em voltar que não faz nada).
 */
export function BackToCloseHere({ open, onClose }: { open: boolean; onClose: () => void }) {
  useBackToClose(open, onClose);
  return null;
}
