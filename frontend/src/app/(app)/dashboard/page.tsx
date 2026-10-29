import { Suspense } from 'react';
import { DashboardOverviewClient } from './DashboardOverviewClient';

// A visão geral passou a usar o filtro GLOBAL do hub (`useDashboardFilters`,
// que lê `useSearchParams`). Em export estático (`output: 'export'`) isso
// exige um limite de Suspense, senão o `next build` falha — mesmo padrão das
// demais sub-rotas.
export default function DashboardPage() {
  return (
    <Suspense fallback={null}>
      <DashboardOverviewClient />
    </Suspense>
  );
}
