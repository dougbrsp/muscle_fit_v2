import { LegacyPathRedirect } from '@/components/LegacyPathRedirect';

// Rota legada: /tipos-treinamento/{id} -> /metas-ativacao/{id}. Mesmo padrão
// de static export das demais rotas dinâmicas (um único slug "placeholder");
// o id real é lido de `window.location` dentro de `LegacyPathRedirect`.
export function generateStaticParams() {
  return [{ id: 'placeholder' }];
}

export default function TipoTreinamentoLegacyRedirectPage() {
  return <LegacyPathRedirect from="/tipos-treinamento" to="/metas-ativacao" />;
}
