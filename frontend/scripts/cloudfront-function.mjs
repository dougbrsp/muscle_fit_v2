// Carrega a CloudFront Function REAL publicada pelo Terraform
// (infra/cloudfront-functions/spa-index-rewrite.js, via `file()` em
// infra/cloudfront.tf) e devolve o `handler` dela, sem duplicar a regra.
//
// O arquivo é um script do runtime cloudfront-js-2.0 (sem `export`), então não
// é importável como módulo. Em vez de alterá-lo (o que mudaria o código
// publicado), ele é avaliado byte a byte num contexto `vm` isolado e o
// `handler` declarado no topo é lido de lá.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

export const CLOUDFRONT_FUNCTION_PATH = fileURLToPath(
  new URL('../../infra/cloudfront-functions/spa-index-rewrite.js', import.meta.url),
);

/** @returns {(event: { request: object }) => object} */
export function loadSpaIndexRewriteHandler(path = CLOUDFRONT_FUNCTION_PATH) {
  const code = readFileSync(path, 'utf8');
  const context = vm.createContext({});
  vm.runInContext(code, context, { filename: path });
  const handler = vm.runInContext('typeof handler === "function" ? handler : undefined', context);
  if (typeof handler !== 'function') {
    throw new Error(`CloudFront Function sem "handler" em ${path}`);
  }
  return handler;
}

/**
 * Converte `URLSearchParams` no formato de `event.request.querystring` do
 * CloudFront (`{ chave: { value, multiValue? } }`).
 */
export function toCloudFrontQuerystring(searchParams) {
  const querystring = {};
  for (const key of new Set(searchParams.keys())) {
    const values = searchParams.getAll(key);
    querystring[key] =
      values.length > 1
        ? { value: values[0], multiValue: values.map((value) => ({ value })) }
        : { value: values[0] };
  }
  return querystring;
}
