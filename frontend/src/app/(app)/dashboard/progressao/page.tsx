import { Suspense } from 'react';
import { DashboardProgressaoClient } from './DashboardProgressaoClient';

export default function DashboardProgressaoPage() {
  return (
    <Suspense fallback={null}>
      <DashboardProgressaoClient />
    </Suspense>
  );
}
