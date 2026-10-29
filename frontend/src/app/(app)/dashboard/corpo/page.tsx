import { Suspense } from 'react';
import { DashboardCorpoClient } from './DashboardCorpoClient';

export default function DashboardCorpoPage() {
  return (
    <Suspense fallback={null}>
      <DashboardCorpoClient />
    </Suspense>
  );
}
