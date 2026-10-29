import { Suspense } from 'react';
import { DashboardMusculosClient } from './DashboardMusculosClient';

export default function DashboardMusculosPage() {
  return (
    <Suspense fallback={null}>
      <DashboardMusculosClient />
    </Suspense>
  );
}
