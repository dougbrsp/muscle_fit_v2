import { Suspense } from 'react';
import { DashboardCheckinClient } from './DashboardCheckinClient';

export default function DashboardCheckinPage() {
  return (
    <Suspense fallback={null}>
      <DashboardCheckinClient />
    </Suspense>
  );
}
