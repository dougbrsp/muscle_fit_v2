'use client';

import { FavoritesManager } from '@/components/nutrition/FavoritesManager';
import { ScreenHeader } from '../../ui';
import { MobileNutritionPresentation } from './MobileNutritionPresentation';
import { NutricaoNav } from './NutricaoNav';

// No celular o gerenciador não vira "cartão dentro de cartão": os blocos do Mantine ficam sem moldura.
const FLAT = `[data-mf-flat] .mantine-Paper-root { border: 0 !important; background: transparent !important; padding: 0 !important; }`;

/** Preferidos da Nutrição (celular): mesma lista pessoal do desktop (`FavoritesManager`), com a seção no topo. */
export function NutricaoFavoritosScreen() {
  return (
    <MobileNutritionPresentation>
      <ScreenHeader title="Preferidos" subtitle="Os alimentos que você mais usa, com a porção que costuma comer" />
      <NutricaoNav active="/nutricao/favoritos" />
      <style>{FLAT}</style>
      <div data-mf-flat="true">
        <FavoritesManager />
      </div>
    </MobileNutritionPresentation>
  );
}
