import { WorkoutDetailClient } from './WorkoutDetailClient';

// output: 'export' exige generateStaticParams para qualquer rota dinâmica —
// sem servidor para resolver `[activityId]` em runtime. Um único param
// físico é suficiente: o HTML gerado é servido para qualquer sub-rota de
// /registro-exercicios/* pelo CloudFront (custom error response, hosting
// ainda não provisionado); o id real da atividade é lido em runtime via
// `useParams()` dentro de `WorkoutDetailClient` (Client Component), e o
// fetch acontece no navegador. `generateStaticParams` só pode conviver com
// `'use client'` em arquivos diferentes — daí este wrapper Server Component
// mínimo, mesmo padrão do placeholder que ele substitui.
export function generateStaticParams() {
  return [{ activityId: 'placeholder' }];
}

export default function DetalheTreinoPage() {
  return <WorkoutDetailClient />;
}
