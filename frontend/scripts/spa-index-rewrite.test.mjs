// Testes da CloudFront Function real (infra/cloudfront-functions/spa-index-rewrite.js),
// carregada byte a byte pelo mesmo loader usado no preview/smoke de produção.
import { describe, expect, it } from 'vitest';

import { loadSpaIndexRewriteHandler, toCloudFrontQuerystring } from './cloudfront-function.mjs';

const handler = loadSpaIndexRewriteHandler();

function run(uri, search = '') {
  return handler({
    request: { uri, querystring: toCloudFrontQuerystring(new URLSearchParams(search)), headers: {} },
  });
}

describe('spa-index-rewrite (CloudFront Function)', () => {
  it.each([
    ['/metas-ativacao/novo/', '/metas-ativacao/placeholder/index.html'],
    ['/metas-ativacao/42', '/metas-ativacao/placeholder/index.html'],
    ['/modelos-treino/novo/', '/modelos-treino/placeholder/index.html'],
    ['/planos-treino/7/', '/planos-treino/placeholder/index.html'],
    ['/registro-exercicios/abc/', '/registro-exercicios/placeholder/index.html'],
    // F1.3: prefixo de dois segmentos dentro do hub `/dashboard/*`.
    ['/dashboard/exercicios/123', '/dashboard/exercicios/placeholder/index.html'],
    ['/dashboard/exercicios/123/', '/dashboard/exercicios/placeholder/index.html'],
    // Fase 4 (PR G): diagnóstico de volume é rota DE TOPO com prefixo próprio
    // (aninhada em /modelos-treino/<id>/… cairia no builder). O builder não regride.
    ['/diagnostico-volume/12', '/diagnostico-volume/placeholder/index.html'],
    ['/diagnostico-volume/12/', '/diagnostico-volume/placeholder/index.html'],
    ['/modelos-treino/12', '/modelos-treino/placeholder/index.html'],
    ['/modelos-treino/12/', '/modelos-treino/placeholder/index.html'],
  ])('reescreve rota dinâmica %s -> %s', (uri, expected) => {
    expect(run(uri).uri).toBe(expected);
  });

  it.each([
    ['/metas-ativacao/placeholder/', '/metas-ativacao/placeholder/index.html'],
    ['/metas-ativacao/', '/metas-ativacao/index.html'],
    ['/diagnostico-volume/placeholder/', '/diagnostico-volume/placeholder/index.html'],
    // O prefixo novo não pode capturar as demais sub-rotas do hub.
    ['/dashboard/progressao/', '/dashboard/progressao/index.html'],
    ['/dashboard/musculos', '/dashboard/musculos/index.html'],
    ['/dashboard/', '/dashboard/index.html'],
    ['/academias', '/academias/index.html'],
    ['/', '/index.html'],
    ['/_next/static/chunks/main.js', '/_next/static/chunks/main.js'],
  ])('resolve rota estática %s -> %s', (uri, expected) => {
    expect(run(uri).uri).toBe(expected);
  });

  it.each([
    ['/tipos-treinamento', '', '/metas-ativacao/'],
    ['/tipos-treinamento/5/', '', '/metas-ativacao/5/'],
    ['/tipos-treinamento/novo/', 'a=1&a=2', '/metas-ativacao/novo/?a=1&a=2'],
    ['/evolucao-corporal', '', '/dashboard/corpo/'],
    ['/evolucao-corporal/', 'days=180', '/dashboard/corpo/?days=180'],
    ['/recuperacao-muscular', '', '/dashboard/recovery/'],
    ['/recuperacao-muscular/', 'days=14', '/dashboard/recovery/?days=14'],
    ['/grupos-musculares', '', '/dashboard/musculos/'],
    ['/grupos-musculares/', 'days=90', '/dashboard/musculos/?days=90'],
    ['/treinos', '', '/dashboard/treinos/'],
    ['/treinos/', 'days=90', '/dashboard/treinos/?days=90'],
    ['/relatorios', '', '/dashboard/relatorios/'],
    ['/relatorios/', 'days=90', '/dashboard/relatorios/?days=90'],
    ['/cardio', '', '/dashboard/cardio/'],
    ['/cardio/', 'days=90', '/dashboard/cardio/?days=90'],
  ])('redireciona legado %s?%s -> %s', (uri, search, location) => {
    const res = run(uri, search);
    expect(res.statusCode).toBe(301);
    expect(res.headers.location.value).toBe(location);
  });

  it('não confunde prefixo parecido com rota legada', () => {
    expect(run('/tipos-treinamentoX').uri).toBe('/tipos-treinamentoX/index.html');
  });

  // `/treinos` é prefixo curto: não pode capturar rotas vizinhas.
  it.each([
    ['/dashboard/treinos', '/dashboard/treinos/index.html'],
    ['/dashboard/treinos/', '/dashboard/treinos/index.html'],
    ['/treinosX', '/treinosX/index.html'],
    ['/modo-treino', '/modo-treino/index.html'],
    ['/modelos-treino', '/modelos-treino/index.html'],
    ['/planos-treino', '/planos-treino/index.html'],
    ['/planos-treino/7', '/planos-treino/placeholder/index.html'],
    ['/dashboard/relatorios', '/dashboard/relatorios/index.html'],
    ['/relatoriosX', '/relatoriosX/index.html'],
  ])('/treinos não afeta %s -> %s', (uri, expected) => {
    const res = run(uri);
    expect(res.statusCode).toBeUndefined();
    expect(res.uri).toBe(expected);
  });
});
