'use client';

import { CheckinClient } from '@/app/(app)/nutricao/checkin/CheckinClient';
import { MobileNutritionPresentation } from './MobileNutritionPresentation';

/** Check-in semanal (celular): mesmo fluxo e mutações do desktop, com a semana e as metas em lista. */
export function NutricaoCheckinScreen() {
  return (
    <MobileNutritionPresentation active="/nutricao/checkin">
      <CheckinClient />
    </MobileNutritionPresentation>
  );
}
