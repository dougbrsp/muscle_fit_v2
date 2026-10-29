import { LegacyPathRedirect } from '@/components/LegacyPathRedirect';

// Rota legada (F2.4): "Evolução corporal" foi absorvida pelo hub v3.
//
// Paridade verificada antes de aposentar (nada que esta tela mostrava ficou
// sem lugar):
// - peso, massa muscular, gordura e água ao longo do tempo, com os MESMOS
//   dois gráficos (peso + massa muscular; gordura + água %) -> já existem,
//   byte a byte, em `/indicadores-fisicos` (BioimpedanciaSection);
// - a tabela de série temporal -> `/indicadores-fisicos` tem a mesma tabela
//   com MAIS colunas (inclusive IMC e gordura visceral, que esta tela
//   mostrava, e ainda subcutânea, massa óssea, TMB, proteínas, idade
//   metabólica);
// - a leitura analítica (tendência de peso com média móvel de 7 dias, sinal
//   de recomposição, alinhamento com e1RM) -> `/dashboard/corpo`, que a
//   sucede (F1.4) e é para onde este caminho passa a apontar.
//
// Mantida só para não quebrar favoritos/links antigos. Em produção a
// CloudFront Function `spa-index-rewrite` já responde 301 antes de chegar
// aqui; este componente é o fallback para ambientes sem ela (`next dev`).
export default function EvolucaoCorporalLegacyRedirectPage() {
  return <LegacyPathRedirect from="/evolucao-corporal" to="/dashboard/corpo" />;
}
