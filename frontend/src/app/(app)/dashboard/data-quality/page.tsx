import { Suspense } from 'react';
import { DataQualityClient } from './DataQualityClient';

// `useDashboardFilters` lê a query string (`useSearchParams`). Em export
// estático (`output: 'export'`) isso exige um limite de Suspense, senão o
// `next build` falha. Mesmo padrão das demais sub-rotas do hub.
export default function DashboardDataQualityPage() {
  return (
    <Suspense fallback={null}>
      <DataQualityClient />
    </Suspense>
  );
}
