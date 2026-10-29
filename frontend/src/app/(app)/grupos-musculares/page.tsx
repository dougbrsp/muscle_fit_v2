import { LegacyPathRedirect } from '@/components/LegacyPathRedirect';

// Rota legada: a tela "Grupos musculares" foi aposentada.
//
// Destino de cada bloco que ela tinha:
//
// - gráfico "Tendência por grupo muscular" (filtros de período/janela/métrica,
//   secundários e ranking) -> `/dashboard/musculos` (#292), mesmo endpoint
//   `GET /analytics/muscle-groups-mapped-trend`;
// - "Exercícios sem classificação" (associar ao catálogo canônico) ->
//   `/dominio-exercicios`, aba "Sem classificação" (mesmo componente
//   `UnmappedExercisesAssociation`);
// - edição do catálogo canônico -> `/dominio-exercicios`, abas "Exercícios"
//   e "Mapeamentos externos".
//
// DESCARTADOS por decisão do produto (sem substituto): KPIs de cobertura, a
// "Tabela completa por grupo muscular" e o "Catálogo de mapeamento canônico"
// com "Exportar Markdown".
//
// Mantida só para não quebrar favoritos/links antigos. Em produção a
// CloudFront Function `spa-index-rewrite` já responde 301 antes de chegar
// aqui; este componente é o fallback para ambientes sem ela (`next dev`).
export default function GruposMuscularesLegacyRedirectPage() {
  return <LegacyPathRedirect from="/grupos-musculares" to="/dashboard/musculos" />;
}
