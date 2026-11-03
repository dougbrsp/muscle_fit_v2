import { Suspense } from 'react';
import { DashboardNutricaoClient } from './DashboardNutricaoClient';

export default function DashboardNutricaoPage() {
  return (
    <Suspense fallback={null}>
      <DashboardNutricaoClient />
    </Suspense>
  );
}
