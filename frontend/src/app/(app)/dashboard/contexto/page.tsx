import { Suspense } from 'react';
import { DashboardContextoClient } from './DashboardContextoClient';

export default function DashboardContextoPage() {
  return (
    <Suspense fallback={null}>
      <DashboardContextoClient />
    </Suspense>
  );
}
