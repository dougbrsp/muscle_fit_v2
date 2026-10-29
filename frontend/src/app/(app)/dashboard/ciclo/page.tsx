import { Suspense } from 'react';
import { DashboardCicloClient } from './DashboardCicloClient';

// `GET /analytics/dashboard-cycle` não aceita filtro de período (a janela do
// ciclo é derivada do número de slots do Modelo), então este client não lê o
// filtro global. O limite de Suspense fica assim mesmo, por uniformidade com
// as demais sub-rotas do hub e porque o export estático quebra se um dia
// alguém introduzir `useSearchParams` aqui sem lembrar de adicioná-lo.
export default function DashboardCicloPage() {
  return (
    <Suspense fallback={null}>
      <DashboardCicloClient />
    </Suspense>
  );
}
