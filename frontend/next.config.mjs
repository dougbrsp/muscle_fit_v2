import { PHASE_DEVELOPMENT_SERVER } from 'next/constants.js';

/**
 * @param {string} phase fase do Next (next/constants)
 * @returns {import('next').NextConfig}
 */
export default function nextConfig(phase) {
  return {
    // Fase 4 (frontend AWS): export estático de verdade — sem servidor Node,
    // sem camada de proxy /api/*. O navegador chama a API (api.musclefit.com.br)
    // diretamente. Ver docs/aws-migration-plan.md, seção "Frontend estático".
    //
    // Só o `next dev` fica sem `output: 'export'`: em produção a CloudFront
    // Function `infra/cloudfront-functions/spa-index-rewrite.js` serve
    // `.../placeholder/index.html` para qualquer id de rota dinâmica, mas o dev
    // server não tem esse rewrite e, com export ligado, responde 500 ("missing
    // param in generateStaticParams()") para `/metas-ativacao/novo`,
    // `/modelos-treino/123` etc. Toda outra fase (inclusive `next build`, que
    // gera o `out/` publicado) continua com export — o artefato de produção não
    // muda. Para validar o comportamento real de produção localmente, use
    // `npm run preview:prod` (ver docs/local-dev.md).
    output: phase === PHASE_DEVELOPMENT_SERVER ? undefined : 'export',
    reactStrictMode: true,
    poweredByHeader: false,
    // next/image não tem o otimizador de servidor disponível em export estático.
    images: { unoptimized: true },
    // Sem servidor para reescrever trailing slash — mantém previsível no S3/CloudFront.
    trailingSlash: true,
  };
}
