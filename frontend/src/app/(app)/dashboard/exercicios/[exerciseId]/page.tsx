import { Suspense } from 'react';
import { ExerciseProgressClient } from './ExerciseProgressClient';

/**
 * `output: 'export'` não pré-renderiza um HTML por id: gera só
 * `.../exercicios/placeholder/index.html`. As TRÊS peças da rota dinâmica:
 *
 * 1. este `generateStaticParams()` com o slug único `placeholder`;
 * 2. `useDynamicRouteParam()` no client (o `useParams()` devolve literalmente
 *    `"placeholder"`, nunca o id real);
 * 3. o prefixo `/dashboard/exercicios/` em `DYNAMIC_ROUTE_PREFIXES` de
 *    `infra/cloudfront-functions/spa-index-rewrite.js` — sem ele o CloudFront
 *    procura `.../123/index.html`, não acha e o usuário leva 404 em produção
 *    (a tela funcionaria normalmente em DEV).
 */
export function generateStaticParams() {
  return [{ exerciseId: 'placeholder' }];
}

// `useDashboardFilters` lê a query string (`useSearchParams`), o que em export
// estático exige um limite de Suspense — mesmo padrão das demais sub-rotas.
export default function DashboardExercicioPage() {
  return (
    <Suspense fallback={null}>
      <ExerciseProgressClient />
    </Suspense>
  );
}
