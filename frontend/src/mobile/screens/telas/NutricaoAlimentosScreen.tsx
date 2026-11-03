'use client';

import { AlimentosClient } from '@/app/(app)/nutricao/alimentos/AlimentosClient';
import { MobileNutritionPresentation } from './MobileNutritionPresentation';

/** Alimentos (celular): busca, código de barras, USDA e cadastro como no desktop; resultados em lista. */
export function NutricaoAlimentosScreen() {
  return (
    <MobileNutritionPresentation>
      <AlimentosClient />
    </MobileNutritionPresentation>
  );
}
