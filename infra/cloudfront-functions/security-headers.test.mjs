// Testes da CloudFront Function real (security-headers.js), rodaveis com
// `node --test infra/cloudfront-functions/` - sem dependencias. Mesmo padrao
// de spa-index-rewrite.test.mjs: o arquivo publicado pelo Terraform e
// avaliado byte a byte num contexto `vm` isolado.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const FUNCTION_PATH = fileURLToPath(new URL('./security-headers.js', import.meta.url));

function loadHandler() {
  const context = vm.createContext({});
  vm.runInContext(readFileSync(FUNCTION_PATH, 'utf8'), context, { filename: FUNCTION_PATH });
  return vm.runInContext('handler', context);
}

const handler = loadHandler();

function run(headers) {
  return handler({ request: { uri: '/' }, response: { statusCode: 200, headers } });
}

function directives(value) {
  const out = new Map();
  for (const part of value.split(';')) {
    const [name, ...sources] = part.trim().split(/\s+/);
    if (name) out.set(name, sources);
  }
  return out;
}

const html = () => ({ 'content-type': { value: 'text/html' } });

test('HTML recebe Content-Security-Policy-Report-Only', () => {
  const res = run(html());
  assert.ok(res.headers['content-security-policy-report-only']);
});

test('nunca envia a CSP que BLOQUEIA (so Report-Only)', () => {
  const res = run(html());
  assert.equal(res.headers['content-security-policy'], undefined);
});

test('content-type com charset e maiusculas tambem conta como HTML', () => {
  const res = run({ 'content-type': { value: 'Text/HTML; charset=utf-8' } });
  assert.ok(res.headers['content-security-policy-report-only']);
});

test('JS, CSS, imagem e resposta sem content-type ficam sem o cabecalho', () => {
  for (const headers of [
    { 'content-type': { value: 'application/javascript' } },
    { 'content-type': { value: 'text/css' } },
    { 'content-type': { value: 'image/webp' } },
    {},
  ]) {
    assert.equal(run(headers).headers['content-security-policy-report-only'], undefined);
  }
});

test('resposta sem objeto headers nao quebra', () => {
  const res = handler({ request: { uri: '/' }, response: { statusCode: 200 } });
  // Objeto criado dentro do contexto vm (outro realm): compara as chaves.
  assert.equal(Object.keys(res.headers).length, 0);
});

test('preserva os cabecalhos que vieram da origem', () => {
  const headers = { ...html(), 'cache-control': { value: 'max-age=60' }, etag: { value: '"abc"' } };
  const res = run(headers);
  assert.equal(res.headers['cache-control'].value, 'max-age=60');
  assert.equal(res.headers.etag.value, '"abc"');
});

test('politica cobre o que o app carrega e nao tem report-uri externo', () => {
  const csp = directives(run(html()).headers['content-security-policy-report-only'].value);
  assert.deepEqual(csp.get('default-src'), ["'self'"]);
  assert.ok(csp.get('script-src').includes('https://challenges.cloudflare.com'));
  assert.ok(!csp.get('script-src').includes("'unsafe-eval'"));
  assert.deepEqual(csp.get('frame-src'), ['https://challenges.cloudflare.com']);
  assert.ok(csp.get('connect-src').includes('https://api.musclefit.com.br'));
  for (const src of ['data:', 'blob:', 'https://musclefit-uploads.s3.amazonaws.com']) {
    assert.ok(csp.get('img-src').includes(src), src);
  }
  assert.deepEqual(csp.get('object-src'), ["'none'"]);
  assert.deepEqual(csp.get('frame-ancestors'), ["'none'"]);
  assert.equal(csp.has('report-uri'), false);
  assert.equal(csp.has('report-to'), false);
});

test('cabe no limite de 10 KB de codigo de CloudFront Function', () => {
  assert.ok(statSync(FUNCTION_PATH).size < 10 * 1024);
});
