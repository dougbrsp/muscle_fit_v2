import { Suspense } from 'react';
import { DashboardEngineClient } from './DashboardEngineClient';

export default function DashboardEnginePage() {
  return (
    <Suspense fallback={null}>
      <DashboardEngineClient />
    </Suspense>
  );
}
