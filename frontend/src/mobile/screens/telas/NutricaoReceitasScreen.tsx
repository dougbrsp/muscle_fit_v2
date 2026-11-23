'use client';

import { ReceitasClient } from '@/app/(app)/nutricao/receitas/ReceitasClient';
import { MobileNutritionPresentation } from './MobileNutritionPresentation';

/** Receitas (celular): alimento composto por ingredientes, como no desktop. */
export function NutricaoReceitasScreen() {
  return (
    <MobileNutritionPresentation active="/nutricao/receitas">
      <ReceitasClient />
    </MobileNutritionPresentation>
  );
}
