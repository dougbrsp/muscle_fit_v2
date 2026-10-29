import { Suspense } from 'react';
import { DashboardTreinosClient } from './DashboardTreinosClient';

// `useDashboardFilters` lê a query string (`useSearchParams`). Em export
// estático (`output: 'export'`) isso exige um limite de Suspense, senão o
// `next build` falha. Mesmo padrão em todas as sub-rotas do Dashboard v2.
export default function DashboardTreinosPage() {
  return (
    <Suspense fallback={null}>
      <DashboardTreinosClient />
    </Suspense>
  );
}
