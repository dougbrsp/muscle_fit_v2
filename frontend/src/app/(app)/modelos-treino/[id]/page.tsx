import { WorkoutPlanTemplateBuilderClient } from './WorkoutPlanTemplateBuilderClient';

// output: 'export' exige generateStaticParams para qualquer rota dinâmica —
// sem servidor para resolver `[id]` em runtime. Um único param físico é
// suficiente (mesmo padrão de /metas-ativacao/[id]): o HTML gerado é
// servido para qualquer sub-rota de /modelos-treino/* pelo CloudFront, e o
// id real ("novo" ou o id numérico) é lido em runtime via `useParams()`
// dentro de `WorkoutPlanTemplateBuilderClient` (Client Component).
export function generateStaticParams() {
  return [{ id: 'placeholder' }];
}

export default function ModeloTreinoDetailPage() {
  return <WorkoutPlanTemplateBuilderClient />;
}
