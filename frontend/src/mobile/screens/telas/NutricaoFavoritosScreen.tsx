'use client';

import { FavoritesManager } from '@/components/nutrition/FavoritesManager';
import { ScreenHeader } from '../../ui';
import { MobileNutritionPresentation } from './MobileNutritionPresentation';

// No celular o gerenciador não vira "cartão dentro de cartão": os blocos do Mantine ficam sem moldura.
const FLAT = `[data-mf-flat] .mantine-Paper-root { border: 0 !important; background: transparent !important; padding: 0 !important; }`;

/** Preferidos da Nutrição (celular): mesma lista pessoal do desktop (`FavoritesManager`), com a seção no topo. */
export function NutricaoFavoritosScreen() {
  return (
    <MobileNutritionPresentation active="/nutricao/favoritos">
      <ScreenHeader title="Preferidos" subtitle="Os alimentos que você mais usa, com a porção padrão" />
      <style>{FLAT}</style>
      <div data-mf-flat="true">
        <FavoritesManager />
      </div>
    </MobileNutritionPresentation>
  );
}
