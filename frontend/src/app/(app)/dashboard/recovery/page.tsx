import { Suspense } from 'react';
import { DashboardRecoveryClient } from './DashboardRecoveryClient';

export default function DashboardRecoveryPage() {
  return (
    <Suspense fallback={null}>
      <DashboardRecoveryClient />
    </Suspense>
  );
}
