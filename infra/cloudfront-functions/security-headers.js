// CloudFront Function (viewer-response) do frontend estatico Next.js
// (musclefit-cdn, default_cache_behavior). SEG-08/AWS-09 do relatorio
// docs/auditoria/2026-10-08-varredura-codigo-aws-seguranca.md.
//
// O que faz: acrescenta `Content-Security-Policy-Report-Only` nas respostas
// HTML. SO Report-Only: o navegador NAO bloqueia nada, apenas registra no
// console (DevTools) cada recurso que a politica abaixo bloquearia. Sem
// report-uri/report-to de proposito (nenhum endpoint externo coletando
// violacoes): a verificacao e manual no console do navegador.
//
// Por que function e nao aws_cloudfront_response_headers_policy: a role de
// deploy (musclefit-gha-deploy) ja gerencia functions `musclefit-*`
// (Create/Update/Publish/Describe/GetFunction) e ja tem UpdateDistribution na
// musclefit-cdn; response headers policy exigiria
// cloudfront:Create/Get/Update/DeleteResponseHeadersPolicy, que a role NAO
// tem (permissao nova = excecao manual). Ver o comentario em infra/cloudfront.tf.
//
// Limite conhecido do caminho por function: o CloudFront NAO invoca function
// de viewer-response quando a ORIGEM responde >= 400. As rotas que caem no
// custom_error_response (403/404 -> /index.html com 200) saem SEM o cabecalho.
// As rotas reais do app (reescritas pela spa-index-rewrite.js para o objeto
// index.html certo) respondem 200 da origem e recebem o cabecalho.
//
// Origem de cada fonte da politica (o que o app realmente carrega):
// - script-src 'self' 'unsafe-inline': chunks /_next/static/* e os scripts
//   INLINE que o export estatico do Next gera (self.__next_f.push(...)) e o
//   ColorSchemeScript do Mantine (src/app/layout.tsx). Export estatico nao tem
//   nonce por requisicao; hash mudaria a cada build. Sem 'unsafe-eval'.
// - https://challenges.cloudflare.com (script e frame): widget Turnstile,
//   frontend/src/components/TurnstileWidget.tsx (script api.js + iframe).
// - style-src 'self' 'unsafe-inline': CSS do Mantine/globals e os atributos
//   style= e <style> que o Mantine injeta em runtime.
// - img-src: 'self' (assets, /equipment/*, /gyms/*, /exercise-cards/* na
//   mesma origem), data: (SVG/icones inline), blob: (mapa muscular do plano
//   e foto do usuario via URL.createObjectURL - lib/plan-muscle-map.ts,
//   lib/user-photo.ts) e o bucket musclefit-uploads (URL pre-assinada de
//   rascunho de card ilustrado - api/app/exercise_card_storage.py; o host
//   depende do endpoint que o boto3 escolher, entao os dois formatos).
// - connect-src: 'self' e a API (https://api.musclefit.com.br,
//   DEFAULT_API_BASE_URL em frontend/src/lib/config.ts).
// - font-src 'self' data:: sem Google Fonts nem CDN de fonte no app.
// - manifest-src 'self': /manifest.json (PWA, src/app/layout.tsx).
// - object-src 'none', base-uri 'self', form-action 'self',
//   frame-ancestors 'none': endurecimento padrao; o app nao usa <object>,
//   <base> nem e embutido em iframe de terceiros.
var CSP_REPORT_ONLY = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' https://challenges.cloudflare.com",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https://musclefit-uploads.s3.amazonaws.com https://musclefit-uploads.s3.sa-east-1.amazonaws.com",
  "font-src 'self' data:",
  "connect-src 'self' https://api.musclefit.com.br",
  "frame-src https://challenges.cloudflare.com",
  "worker-src 'self' blob:",
  "manifest-src 'self'",
  "media-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join('; ');

function isHtml(response) {
  var contentType = response.headers && response.headers['content-type'];
  if (!contentType || !contentType.value) {
    return false;
  }
  return String(contentType.value).toLowerCase().indexOf('text/html') === 0;
}

function handler(event) {
  var response = event.response;
  if (!response.headers) {
    response.headers = {};
  }
  if (isHtml(response)) {
    response.headers['content-security-policy-report-only'] = { value: CSP_REPORT_ONLY };
  }
  return response;
}
