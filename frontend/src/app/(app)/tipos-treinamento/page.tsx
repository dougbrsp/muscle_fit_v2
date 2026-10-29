import { LegacyPathRedirect } from '@/components/LegacyPathRedirect';

// Rota legada: "Tipos de treinamento" virou "Metas de Ativação" (/metas-ativacao).
// Mantida só para não quebrar favoritos/links antigos. Em produção a
// CloudFront Function `spa-index-rewrite` já responde 301 antes de chegar aqui.
export default function TiposTreinamentoLegacyRedirectPage() {
  return <LegacyPathRedirect from="/tipos-treinamento" to="/metas-ativacao" />;
}
