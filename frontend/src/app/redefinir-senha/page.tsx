import { Suspense } from 'react';
import { RedefinirSenhaClient } from './RedefinirSenhaClient';

// `RedefinirSenhaClient` lê `?token=` com `useSearchParams`: em export
// estático isso exige um limite de Suspense (mesmo padrão do login em
// `src/app/page.tsx`). A URL do e-mail é `${APP_BASE_URL}/redefinir-senha?token=...`;
// com `trailingSlash: true` o Next redireciona para `/redefinir-senha/?token=...`
// no cliente preservando a query string.
export default function RedefinirSenhaPage() {
  return (
    <Suspense fallback={null}>
      <RedefinirSenhaClient />
    </Suspense>
  );
}
