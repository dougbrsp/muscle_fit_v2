import { Suspense } from 'react';
import { DashboardCardioClient } from './DashboardCardioClient';

export default function DashboardCardioPage() {
  return (
    <Suspense fallback={null}>
      <DashboardCardioClient />
    </Suspense>
  );
}
