'use client';

import { FavoritesManager } from '@/components/nutrition/FavoritesManager';
import { ScreenHeader } from '../../ui';
import { NutricaoNav } from './NutricaoNav';

/** Preferidos da Nutrição (celular): mesma lista pessoal do desktop (`FavoritesManager`). */
export function NutricaoFavoritosScreen() {
  return (
    <>
      <ScreenHeader title="Preferidos" subtitle="Os alimentos que você mais usa, com a porção que costuma comer" />
      <FavoritesManager />
      <NutricaoNav active="/nutricao/favoritos" />
    </>
  );
}
