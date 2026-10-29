import { Suspense } from 'react';
import { DashboardInsightsClient } from './DashboardInsightsClient';

// Lê o filtro global do hub (`useSearchParams`); em export estático isso
// exige um limite de Suspense — mesmo padrão das demais sub-rotas.
export default function DashboardInsightsPage() {
  return (
    <Suspense fallback={null}>
      <DashboardInsightsClient />
    </Suspense>
  );
}
