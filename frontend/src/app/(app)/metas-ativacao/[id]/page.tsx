import { TrainingTypeBuilderClient } from './TrainingTypeBuilderClient';

// output: 'export' exige generateStaticParams para qualquer rota dinâmica —
// sem servidor para resolver `[id]` em runtime. Um único param físico é
// suficiente (mesmo padrão de /registro-exercicios/[activityId]): o HTML
// gerado é servido para qualquer sub-rota de /metas-ativacao/* pelo
// CloudFront, e o id real ("novo" ou o id numérico) é lido em runtime via
// `useParams()` dentro de `TrainingTypeBuilderClient` (Client Component).
export function generateStaticParams() {
  return [{ id: 'placeholder' }];
}

export default function MetaAtivacaoDetailPage() {
  return <TrainingTypeBuilderClient />;
}
