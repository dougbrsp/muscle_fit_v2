// Testes da CloudFront Function real (spa-index-rewrite.js), rodaveis com
// `node --test infra/cloudfront-functions/` - sem dependencias.
//
// O arquivo e um script do runtime cloudfront-js-2.0 (sem `export`/
// `module.exports`), entao e avaliado byte a byte num contexto `vm` isolado e
// o `handler` declarado no topo e lido de la. O arquivo publicado pelo
// Terraform (file() em infra/cloudfront.tf) nao e alterado para ser testado.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const FUNCTION_PATH = fileURLToPath(new URL('./spa-index-rewrite.js', import.meta.url));

function loadHandler() {
  const context = vm.createContext({});
  vm.runInContext(readFileSync(FUNCTION_PATH, 'utf8'), context, { filename: FUNCTION_PATH });
  return vm.runInContext('handler', context);
}

const handler = loadHandler();

// Monta o objeto `querystring` no formato do evento do CloudFront a partir de
// pares [chave, valor] ja no formato em que chegam (a funcao nao deve depender
// de os valores virem codificados ou decodificados).
function qs(pairs) {
  const out = {};
  for (const [key, value] of pairs) {
    if (!(key in out)) {
      out[key] = { value };
    } else {
      const entry = out[key];
      entry.multiValue = entry.multiValue || [{ value: entry.value }];
      entry.multiValue.push({ value });
    }
  }
  return out;
}

function run(uri, pairs = []) {
  return handler({ request: { method: 'GET', uri, querystring: qs(pairs), headers: {}, cookies: {} } });
}

function location(uri, pairs) {
  const res = run(uri, pairs);
  assert.equal(res.statusCode, 301);
  assert.equal(res.statusDescription, 'Moved Permanently');
  return res.headers.location.value;
}

test('tamanho da function abaixo do limite de 10 KB do CloudFront Functions', () => {
  assert.ok(statSync(FUNCTION_PATH).size < 10 * 1024);
});

test('301 legados sem query string', () => {
  assert.equal(location('/tipos-treinamento'), '/metas-ativacao/');
  assert.equal(location('/tipos-treinamento/'), '/metas-ativacao/');
  assert.equal(location('/tipos-treinamento/12'), '/metas-ativacao/12');
});

test('301 de /evolucao-corporal para /dashboard/corpo (F2.4)', () => {
  assert.equal(location('/evolucao-corporal'), '/dashboard/corpo/');
  assert.equal(location('/evolucao-corporal/'), '/dashboard/corpo/');
  // Favorito antigo com a janela na query string chega com ela preservada.
  assert.equal(location('/evolucao-corporal', [['days', '180']]), '/dashboard/corpo/?days=180');
});

test('301 de /recuperacao-muscular para /dashboard/recovery (F2.4, 2a passada)', () => {
  assert.equal(location('/recuperacao-muscular'), '/dashboard/recovery/');
  assert.equal(location('/recuperacao-muscular/'), '/dashboard/recovery/');
  // A tela antiga guardava a janela na query (`?days=14`); favorito preservado.
  assert.equal(location('/recuperacao-muscular', [['days', '14']]), '/dashboard/recovery/?days=14');
});

test('301 de /grupos-musculares para /dashboard/musculos', () => {
  assert.equal(location('/grupos-musculares'), '/dashboard/musculos/');
  assert.equal(location('/grupos-musculares/'), '/dashboard/musculos/');
  assert.equal(location('/grupos-musculares', [['days', '90']]), '/dashboard/musculos/?days=90');
});

test('301 de /treinos para /dashboard/treinos', () => {
  assert.equal(location('/treinos'), '/dashboard/treinos/');
  assert.equal(location('/treinos/'), '/dashboard/treinos/');
  assert.equal(location('/treinos', [['days', '90']]), '/dashboard/treinos/?days=90');
  assert.equal(location('/treinos/index.html'), '/dashboard/treinos/index.html');
});

test('/treinos nao afeta rotas que contem "treinos" ou comecam parecido', () => {
  // Destino do proprio redirect: nao pode virar loop.
  assert.equal(run('/dashboard/treinos').uri, '/dashboard/treinos/index.html');
  assert.equal(run('/dashboard/treinos/').uri, '/dashboard/treinos/index.html');
  assert.equal(run('/treinosX').uri, '/treinosX/index.html');
  assert.equal(run('/treinos-antigos').uri, '/treinos-antigos/index.html');
  assert.equal(run('/modo-treino').uri, '/modo-treino/index.html');
  assert.equal(run('/modelos-treino').uri, '/modelos-treino/index.html');
  assert.equal(run('/modelos-treino/42').uri, '/modelos-treino/placeholder/index.html');
  assert.equal(run('/planos-treino').uri, '/planos-treino/index.html');
  assert.equal(run('/planos-treino/7').uri, '/planos-treino/placeholder/index.html');
});

test('301 de /relatorios para /dashboard/relatorios', () => {
  assert.equal(location('/relatorios'), '/dashboard/relatorios/');
  assert.equal(location('/relatorios/'), '/dashboard/relatorios/');
  assert.equal(location('/relatorios', [['days', '90']]), '/dashboard/relatorios/?days=90');
});

test('/relatorios nao afeta o proprio destino nem prefixo parecido', () => {
  // Destino do proprio redirect: nao pode virar loop.
  assert.equal(run('/dashboard/relatorios').uri, '/dashboard/relatorios/index.html');
  assert.equal(run('/dashboard/relatorios/').uri, '/dashboard/relatorios/index.html');
  assert.equal(run('/relatoriosX').uri, '/relatoriosX/index.html');
});

test('301 de /cardio para /dashboard/cardio (M98 C4)', () => {
  assert.equal(location('/cardio'), '/dashboard/cardio/');
  assert.equal(location('/cardio/'), '/dashboard/cardio/');
  assert.equal(location('/cardio', [['days', '90']]), '/dashboard/cardio/?days=90');
});

test('/cardio nao afeta o proprio destino nem rotas parecidas', () => {
  assert.equal(run('/dashboard/cardio').uri, '/dashboard/cardio/index.html');
  assert.equal(run('/dashboard/cardio/').uri, '/dashboard/cardio/index.html');
  assert.equal(run('/agente-cardio').uri, '/agente-cardio/index.html');
  assert.equal(run('/cardioX').uri, '/cardioX/index.html');
});

test('nao confunde prefixo parecido com rota legada', () => {
  assert.equal(run('/tipos-treinamentoX').uri, '/tipos-treinamentoX/index.html');
  assert.equal(run('/evolucao-corporalX').uri, '/evolucao-corporalX/index.html');
  assert.equal(run('/recuperacao-muscularX').uri, '/recuperacao-muscularX/index.html');
  assert.equal(run('/grupos-muscularesX').uri, '/grupos-muscularesX/index.html');
});

const QUERY_CASES = [
  ['valor simples', [['a', '1']], '?a=1'],
  ['espaco (decodificado)', [['q', 'a b']], '?q=a%20b'],
  ['espaco ja codificado nao gera dupla codificacao', [['q', 'a%20b']], '?q=a%20b'],
  ['& = # decodificados', [['q', 'a&b=c#d']], '?q=a%26b%3Dc%23d'],
  ['& = # ja codificados', [['q', 'a%26b%3Dc%23d']], '?q=a%26b%3Dc%23d'],
  ['% solto vira %25', [['q', '100%']], '?q=100%25'],
  ['% seguido de nao-hex vira %25', [['q', '%zz']], '?q=%25zz'],
  ['+ preservado como veio', [['q', 'a+b']], '?q=a+b'],
  ['%2B preservado', [['q', 'a%2Bb']], '?q=a%2Bb'],
  ['UTF-8 decodificado', [['nome', 'ção']], '?nome=%C3%A7%C3%A3o'],
  ['UTF-8 ja codificado', [['nome', '%C3%A7%C3%A3o']], '?nome=%C3%A7%C3%A3o'],
  ['emoji (par substituto)', [['e', '💪']], '?e=%F0%9F%92%AA'],
  ['chave com caracteres especiais', [['a b&c', 'x']], '?a%20b%26c=x'],
  ['chave sem valor', [['flag', '']], '?flag='],
  ['multiValue repete a chave', [['a', '1'], ['a', '2 3']], '?a=1&a=2%203'],
  [
    'ordem estavel (ordem de chegada)',
    [['z', '1'], ['a', '2'], ['m', '3'], ['a', '4']],
    '?z=1&a=2&a=4&m=3',
  ],
];

for (const [name, pairs, expected] of QUERY_CASES) {
  test(`301 com query: ${name}`, () => {
    assert.equal(location('/tipos-treinamento/12', pairs), `/metas-ativacao/12${expected}`);
  });
}

test('querystring ausente no evento nao quebra o 301', () => {
  const res = handler({ request: { method: 'GET', uri: '/tipos-treinamento', headers: {} } });
  assert.equal(res.headers.location.value, '/metas-ativacao/');
});

test('rotas dinamicas, estaticas e assets continuam inalterados (query nao interfere)', () => {
  const cases = [
    ['/metas-ativacao/42', '/metas-ativacao/placeholder/index.html'],
    ['/metas-ativacao/novo/', '/metas-ativacao/placeholder/index.html'],
    ['/planos-treino/7/', '/planos-treino/placeholder/index.html'],
    ['/registro-exercicios/abc', '/registro-exercicios/placeholder/index.html'],
    ['/modelos-treino/9', '/modelos-treino/placeholder/index.html'],
    // Fase 4 (PR G): diagnostico de volume e rota de topo com prefixo proprio;
    // o prefixo de /modelos-treino/ continua entregando o builder.
    ['/diagnostico-volume/12', '/diagnostico-volume/placeholder/index.html'],
    ['/diagnostico-volume/12/', '/diagnostico-volume/placeholder/index.html'],
    ['/diagnostico-volume/placeholder/', '/diagnostico-volume/placeholder/index.html'],
    ['/modelos-treino/12', '/modelos-treino/placeholder/index.html'],
    ['/modelos-treino/12/', '/modelos-treino/placeholder/index.html'],
    // Drill-down por exercicio do hub (F1.3): o prefixo tem DOIS segmentos, e
    // as demais sub-rotas de /dashboard continuam estaticas.
    ['/dashboard/exercicios/123', '/dashboard/exercicios/placeholder/index.html'],
    ['/dashboard/exercicios/123/', '/dashboard/exercicios/placeholder/index.html'],
    ['/dashboard/exercicios/placeholder/', '/dashboard/exercicios/placeholder/index.html'],
    ['/dashboard/progressao/', '/dashboard/progressao/index.html'],
    ['/dashboard/musculos', '/dashboard/musculos/index.html'],
    ['/dashboard/', '/dashboard/index.html'],
    ['/metas-ativacao/placeholder/', '/metas-ativacao/placeholder/index.html'],
    ['/metas-ativacao/', '/metas-ativacao/index.html'],
    ['/academias', '/academias/index.html'],
    ['/', '/index.html'],
    ['/_next/static/chunks/main.js', '/_next/static/chunks/main.js'],
    ['/favicon.ico', '/favicon.ico'],
  ];
  for (const [uri, expected] of cases) {
    const querystring = qs([['q', 'a b']]);
    const res = handler({ request: { method: 'GET', uri, querystring, headers: {} } });
    assert.equal(res.statusCode, undefined, uri);
    assert.equal(res.uri, expected, uri);
    // A function nao reescreve a query string de requisicoes repassadas a origem.
    assert.deepEqual(res.querystring, qs([['q', 'a b']]), uri);
  }
});

// Apex musclefit.com.br -> 301 para https://app.musclefit.com.br (2026-10-03).
function runHost(host, uri, pairs = []) {
  return handler({
    request: { method: 'GET', uri, querystring: qs(pairs), headers: { host: { value: host } }, cookies: {} },
  });
}

function apexLocation(host, uri, pairs) {
  const res = runHost(host, uri, pairs);
  assert.equal(res.statusCode, 301, `${host}${uri}`);
  assert.equal(res.statusDescription, 'Moved Permanently');
  return res.headers.location.value;
}

test('apex: 301 para app. preservando caminho', () => {
  assert.equal(apexLocation('musclefit.com.br', '/'), 'https://app.musclefit.com.br/');
  assert.equal(apexLocation('musclefit.com.br', '/dashboard/'), 'https://app.musclefit.com.br/dashboard/');
  assert.equal(apexLocation('musclefit.com.br', '/planos-treino/123'), 'https://app.musclefit.com.br/planos-treino/123');
  assert.equal(
    apexLocation('musclefit.com.br', '/_next/static/chunks/main.js'),
    'https://app.musclefit.com.br/_next/static/chunks/main.js',
  );
});

test('apex: 301 preserva query string (simples, multiValue, codificada, vazia)', () => {
  assert.equal(apexLocation('musclefit.com.br', '/dashboard/', [['x', '1']]), 'https://app.musclefit.com.br/dashboard/?x=1');
  assert.equal(
    apexLocation('musclefit.com.br', '/login', [['next', '/dashboard'], ['a', '1'], ['a', '2']]),
    'https://app.musclefit.com.br/login?next=/dashboard&a=1&a=2',
  );
  assert.equal(apexLocation('musclefit.com.br', '/busca', [['q', 'a b%20c']]), 'https://app.musclefit.com.br/busca?q=a%20b%20c');
  assert.equal(apexLocation('musclefit.com.br', '/x', [['flag', '']]), 'https://app.musclefit.com.br/x?flag=');
});

test('apex: rota legada vai para app. sem traduzir (o app. faz o 301 legado no proximo hop)', () => {
  assert.equal(apexLocation('musclefit.com.br', '/treinos', [['d', '7']]), 'https://app.musclefit.com.br/treinos?d=7');
});

test('apex: host em maiusculas, com porta ou com ponto final tambem redireciona', () => {
  for (const host of ['MuscleFit.COM.BR', 'musclefit.com.br:443', 'musclefit.com.br.']) {
    assert.equal(apexLocation(host, '/dashboard/', [['x', '1']]), 'https://app.musclefit.com.br/dashboard/?x=1', host);
  }
});

test('apex: querystring ausente no evento nao quebra o 301', () => {
  const res = handler({ request: { method: 'GET', uri: '/dashboard/', headers: { host: { value: 'musclefit.com.br' } } } });
  assert.equal(res.headers.location.value, 'https://app.musclefit.com.br/dashboard/');
});

test('app. e demais hosts seguem com o comportamento atual, identico ao de sem Host', () => {
  const uris = ['/', '/dashboard/', '/academias', '/planos-treino/123', '/_next/static/chunks/main.js', '/tipos-treinamento/12'];
  const hosts = [
    'app.musclefit.com.br',
    'APP.musclefit.com.br:443',
    'www.musclefit.com.br',
    'musclefit.com.br.evil.com',
    'd123.cloudfront.net',
  ];
  for (const host of hosts) {
    for (const uri of uris) {
      const withHost = runHost(host, uri, [['q', 'a b']]);
      const withoutHost = run(uri, [['q', 'a b']]);
      assert.equal(withHost.statusCode, withoutHost.statusCode, `${host}${uri}`);
      if (withoutHost.statusCode) {
        assert.equal(withHost.headers.location.value, withoutHost.headers.location.value, `${host}${uri}`);
        assert.ok(!withHost.headers.location.value.startsWith('https://'), `${host}${uri}`);
      } else {
        assert.equal(withHost.uri, withoutHost.uri, `${host}${uri}`);
        assert.deepEqual(withHost.querystring, qs([['q', 'a b']]), `${host}${uri}`);
      }
    }
  }
});
