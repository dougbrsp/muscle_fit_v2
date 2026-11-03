'use client';

import { PlanoClient } from '@/app/(app)/nutricao/plano/PlanoClient';
import { MobileNutritionPresentation } from './MobileNutritionPresentation';

/**
 * Plano nutricional (celular): o mesmo fluxo do desktop (PlanoClient: UserPicker e
 * seletor de aluno só aqui, rascunho, preview e publicação), com cabeçalho e
 * semana em lista. Diário, check-in, painel e card de hoje seguem do usuário logado.
 */
export function NutricaoPlanoScreen() {
  return (
    <MobileNutritionPresentation>
      <PlanoClient />
    </MobileNutritionPresentation>
  );
}
