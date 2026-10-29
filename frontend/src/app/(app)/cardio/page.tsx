import { LegacyPathRedirect } from '@/components/LegacyPathRedirect';

// Rota legada (M98 C4): a tela "Cardio" consultava o Intervals.icu AO VIVO a
// cada carregamento (`GET /integrations/intervals/cardio-dashboard`, removido)
// e classificava cardio com uma cópia própria da heurística. Ela foi unificada
// no hub `/dashboard/cardio`, que lê o CACHE do Intervals
// (`GET /analytics/dashboard/cardio`) e classifica pelo dono único
// (`api/app/intervals_activity_kind.py`).
//
// Destino de cada bloco que ela tinha:
//
// - totais (sessões, minutos, distância, calorias, carga, FC média/máx) ->
//   "Resumo do período";
// - "por modalidade" -> "Por modalidade";
// - minutos por dia -> "Cardio por semana" (minutos, zonas, contínuo × intensa,
//   kcal por semana);
// - tendência de CTL/ATL/TSB, HRV e FC de repouso -> "Carga e recuperação";
// - lista de atividades -> "Atividades de cardio";
// - atividades de força e "outras" fora do cardio -> contadores do resumo.
//
// Mantida só para não quebrar favoritos/links antigos. Em produção a
// CloudFront Function `spa-index-rewrite` já responde 301 antes de chegar
// aqui; este componente é o fallback para ambientes sem ela (`next dev`).
export default function CardioLegacyRedirectPage() {
  return <LegacyPathRedirect from="/cardio" to="/dashboard/cardio" />;
}
