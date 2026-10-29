import { VolumeDiagnosticClient } from './VolumeDiagnosticClient';

/**
 * `/diagnostico-volume/[templateId]` — diagnóstico de volume por metodologia
 * de um Modelo salvo (Fase 4, PR G).
 *
 * Rota DE TOPO de propósito: a CloudFront Function `spa-index-rewrite`
 * reescreve pelo PRIMEIRO segmento, então `/modelos-treino/12/diagnostico`
 * viraria `/modelos-treino/placeholder/index.html` e entregaria o builder.
 *
 * As TRÊS peças da rota dinâmica em `output: 'export'`:
 * 1. este `generateStaticParams()` com o slug único `placeholder`;
 * 2. `useDynamicRouteParam()` no client (o `useParams()` devolve literalmente
 *    `"placeholder"`, nunca o id real);
 * 3. o prefixo `/diagnostico-volume/` em `DYNAMIC_ROUTE_PREFIXES` de
 *    `infra/cloudfront-functions/spa-index-rewrite.js` — sem ele o CloudFront
 *    procura `.../12/index.html`, não acha e o usuário leva 404 em produção
 *    (a tela funcionaria normalmente em DEV).
 */
export function generateStaticParams() {
  return [{ templateId: 'placeholder' }];
}

export default function DiagnosticoVolumePage() {
  return <VolumeDiagnosticClient />;
}
