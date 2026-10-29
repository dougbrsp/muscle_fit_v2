import { LegacyPathRedirect } from '@/components/LegacyPathRedirect';

// Rota legada: o relatório em PDF com IA foi substituído pelo relatório em
// Markdown, só com dados, gerado na hora em `/dashboard/relatorios`
// (`GET /reports/markdown`). As rotas antigas do PDF (`POST /reports/generate`,
// `GET /reports[/{id}[/pdf]]`, `DELETE /reports/{id}`) saíram do backend.
//
// Mantida só para não quebrar favoritos/links antigos. Em produção a
// CloudFront Function `spa-index-rewrite` já responde 301 antes de chegar
// aqui; este componente é o fallback para ambientes sem ela (`next dev`).
export default function RelatoriosLegacyRedirectPage() {
  return <LegacyPathRedirect from="/relatorios" to="/dashboard/relatorios" />;
}
