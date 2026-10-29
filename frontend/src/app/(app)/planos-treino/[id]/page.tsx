import { PlanDetailClient } from './PlanDetailClient';

// output: 'export' exige generateStaticParams para qualquer rota dinâmica —
// mesmo padrão de /registro-exercicios/[activityId]/page.tsx: um único
// param físico placeholder é suficiente, o id real é lido em runtime via
// `useParams()` dentro do Client Component.
export function generateStaticParams() {
  return [{ id: 'placeholder' }];
}

export default function PlanoTreinoDetailPage() {
  return <PlanDetailClient />;
}
