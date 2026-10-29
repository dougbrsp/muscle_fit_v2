import { Suspense } from 'react';
import { LoginClient } from './LoginClient';

// `LoginClient` lê a query string (`useSearchParams`, para `?next=`). Em
// export estático (`output: 'export'` em next.config.mjs) isso exige um
// limite de Suspense, senão o `next build` falha — mesmo padrão usado nas
// sub-rotas do Dashboard v2 (ver `src/lib/dashboard-filters.ts`).
export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginClient />
    </Suspense>
  );
}
