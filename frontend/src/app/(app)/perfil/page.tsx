import { Suspense } from 'react';
import { PerfilClient } from './PerfilClient';

// `PerfilClient` lê a query string (`useSearchParams`, para `?troque-senha=1`,
// preenchido pelo redirect pós-login quando `must_change_password` é true —
// ver `src/app/LoginClient.tsx`). Em export estático (`output: 'export'`)
// isso exige um limite de Suspense, senão o `next build` falha — mesmo
// padrão usado nas sub-rotas do Dashboard v2.
export default function PerfilPage() {
  return (
    <Suspense fallback={null}>
      <PerfilClient />
    </Suspense>
  );
}
