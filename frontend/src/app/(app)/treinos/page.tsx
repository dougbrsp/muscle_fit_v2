import { LegacyPathRedirect } from '@/components/LegacyPathRedirect';

// Rota legada: a tela "Treinos" foi aposentada.
//
// Destino de cada bloco que ela tinha:
//
// - "Resumo da semana", "Evolução por semana ISO", "Distribuição de RPE" e a
//   "Análise por IA" -> `/dashboard/treinos` (#294), mesmos endpoints
//   (`/analytics/training-evolution`, `/analytics/training-trend`,
//   `/analytics/rpe-distribution`, `POST /workouts-analysis/generate`);
// - "Ativação muscular (últimos 7 dias)" (com o cruzamento de recuperação
//   entre dias consecutivos) -> `/dashboard/musculos` (#295,
//   `MuscleActivation7d`), agora com as metas dos macro-grupos;
// - "Cardio & Recuperação" e "Recuperação muscular" -> já existiam em
//   `/dashboard/cardio` e `/dashboard/recovery` (não migrados por duplicidade).
//
// Mantida só para não quebrar favoritos/links antigos. Em produção a
// CloudFront Function `spa-index-rewrite` já responde 301 antes de chegar
// aqui; este componente é o fallback para ambientes sem ela (`next dev`).
export default function TreinosLegacyRedirectPage() {
  return <LegacyPathRedirect from="/treinos" to="/dashboard/treinos" />;
}
