// CloudFront Function (viewer-request) para o frontend estatico Next.js
// (output: 'export') atras de CloudFront + S3 via Origin Access Control.
//
// A origem S3 e acessada via API REST (nao "S3 Website Hosting", que exige
// bucket publico e e incompativel com OAC) - por isso o S3 NAO resolve
// sozinho "/academias" para o objeto "/academias/index.html" como faria em
// modo website hosting. Sem essa reescrita, qualquer rota que nao seja a
// raiz exata "/" bate um 404/NoSuchKey no S3, cai no custom_error_response
// (403/404 -> /index.html, 200) e serve silenciosamente a tela de login em
// vez da pagina real - ver docs/aws-migration-plan.md, Fase 4.
//
// Rotas dinamicas (ex.: /planos-treino/123): usam generateStaticParams fixo
// com um unico slug literal "placeholder" (ver next.config.mjs/cada
// page.tsx) - o Next so pre-renderiza .../placeholder/index.html, e o React
// client-side le o ID de verdade da URL do navegador (que continua
// .../123, o rewrite so muda qual OBJETO o CloudFront busca no S3, nao a
// URL exibida). Sem tratamento especial, "/planos-treino/123" cairia na
// regra generica acima (".../123/index.html", que nao existe -> 404 ->
// custom_error_response -> tela de login por engano, "pisca e volta pro
// dashboard" - bug real encontrado em producao em 2026-09-07). Por isso
// qualquer segmento debaixo de um desses prefixos que NAO seja literalmente
// "placeholder" e reescrito direto para o objeto placeholder da rota.
var DYNAMIC_ROUTE_PREFIXES = [
  '/registro-exercicios/',
  '/planos-treino/',
  '/metas-ativacao/',
  '/modelos-treino/',
  '/dashboard/exercicios/',
  // Fase 4 (PR G): diagnostico de volume por metodologia de um Modelo. Rota DE
  // TOPO de proposito - aninhada em /modelos-treino/<id>/... ela cairia no
  // prefixo acima (que reescreve pelo PRIMEIRO segmento) e entregaria o builder.
  '/diagnostico-volume/',
];

// Rotas renomeadas: redirect 301 na borda, preservando o restante do path
// (ex.: id de rota dinamica) e a query string. "/tipos-treinamento" virou
// "/metas-ativacao" (PR-15). O frontend tambem mantem paginas de redirect
// client-side nesses caminhos antigos como fallback (ex.: next dev local).
// "/evolucao-corporal" foi aposentada na F2.4 do Novo Dashboard: a leitura
// analitica virou "/dashboard/corpo" (F1.4) e a serie historica bruta (com
// mais colunas: IMC, gordura visceral, TMB...) ja estava em
// "/indicadores-fisicos". Nada do que a tela antiga mostrava ficou sem lugar
// - ver o comentario de paridade em
// frontend/src/app/(app)/evolucao-corporal/page.tsx.
// "/recuperacao-muscular" saiu na 2a passada da F2.4, depois que o M33 levou
// mapa corporal, recomendacao de series e fatores do calculo para
// "/dashboard/recovery" - ver o comentario de paridade em
// frontend/src/app/(app)/recuperacao-muscular/page.tsx.
// "/grupos-musculares" saiu depois que a tendencia por grupo (filtros e
// ranking) foi para "/dashboard/musculos" (#292); catalogo e exercicios sem
// classificacao ja estavam em "/dominio-exercicios" - ver
// frontend/src/app/(app)/grupos-musculares/page.tsx.
// "/treinos" saiu depois que as secoes foram para "/dashboard/treinos" (#294)
// e a ativacao de 7 dias para "/dashboard/musculos" (#295). So casa
// "/treinos" e "/treinos/..." - nunca "/dashboard/treinos" (nao comeca com o
// prefixo) nem "/treinosX" - ver frontend/src/app/(app)/treinos/page.tsx.
// "/relatorios" saiu quando o relatorio PDF com IA virou o relatorio em
// Markdown de "/dashboard/relatorios" - ver
// frontend/src/app/(app)/relatorios/page.tsx.
// "/cardio" saiu na M98 C4: o dashboard AO VIVO (que chamava o Intervals.icu a
// cada carregamento) foi unificado no hub "/dashboard/cardio", que le o cache
// e ganhou zonas, continuo x intensa, planejado x realizado e tendencia - ver
// frontend/src/app/(app)/cardio/page.tsx.
var LEGACY_ROUTE_REDIRECTS = [
  { from: '/tipos-treinamento', to: '/metas-ativacao' },
  { from: '/evolucao-corporal', to: '/dashboard/corpo' },
  { from: '/recuperacao-muscular', to: '/dashboard/recovery' },
  { from: '/grupos-musculares', to: '/dashboard/musculos' },
  { from: '/treinos', to: '/dashboard/treinos' },
  { from: '/relatorios', to: '/dashboard/relatorios' },
  { from: '/cardio', to: '/dashboard/cardio' },
];

// Codifica chave/valor de query string para o header Location sem depender de
// o CloudFront entregar o valor ainda percent-encoded (o que a doc indica:
// valores UTF-8 sao repassados "without changing them") ou ja decodificado:
// - sequencias %XX validas sao mantidas (sem dupla codificacao: %20 != %2520);
// - "%" solto vira %25;
// - "+" e mantido como veio (em query crua ele significa espaco);
// - qualquer outro caractere fora dos permitidos numa query (RFC 3986),
//   incluindo espaco, & = # e nao-ASCII, e codificado em UTF-8.
function encodeQueryComponent(str) {
  return String(str)
    .replace(/%(?![0-9A-Fa-f]{2})/g, '%25')
    .replace(/[^A-Za-z0-9\-._~!$'()*,;:@\/?+%]+/g, function (run) {
      try {
        return encodeURIComponent(run);
      } catch (e) {
        // Surrogate isolado (UTF-16 invalido): substitui por U+FFFD.
        return '%EF%BF%BD';
      }
    });
}

function buildQueryString(querystring) {
  var parts = [];
  for (var key in querystring) {
    var entry = querystring[key];
    var values = entry.multiValue || [entry];
    for (var j = 0; j < values.length; j++) {
      var value = values[j].value;
      parts.push(encodeQueryComponent(key) + '=' + encodeQueryComponent(value == null ? '' : value));
    }
  }
  return parts.length ? '?' + parts.join('&') : '';
}

function legacyRedirect(request) {
  var uri = request.uri;
  for (var i = 0; i < LEGACY_ROUTE_REDIRECTS.length; i++) {
    var rule = LEGACY_ROUTE_REDIRECTS[i];
    // Casa o prefixo exato ou seguido de "/" - nunca "/tipos-treinamentoX".
    if (uri === rule.from || uri.indexOf(rule.from + '/') === 0) {
      var rest = uri.slice(rule.from.length);
      var location = rule.to + (rest === '' ? '/' : rest) + buildQueryString(request.querystring || {});
      return {
        statusCode: 301,
        statusDescription: 'Moved Permanently',
        headers: { location: { value: location } },
      };
    }
  }
  return null;
}

// Apex musclefit.com.br -> 301 para https://app.musclefit.com.br, preservando
// caminho e query string (decisao do usuario, 2026-10-03). O apex aponta para
// esta mesma distribuicao (ALIAS em infra/route53.tf), mas a API (CORS) e o
// Turnstile so aceitam a origem app. - servir o app nos dois hosts quebrava o
// login no apex. Uma origem so: o apex nunca chega ao S3.
// Host normalizado: minusculas, sem porta e sem ponto final de FQDN.
var APEX_HOST = 'musclefit.com.br';
var APP_ORIGIN = 'https://app.musclefit.com.br';

function apexRedirect(request) {
  var host = request.headers && request.headers.host && request.headers.host.value;
  if (!host) {
    return null;
  }
  host = String(host).toLowerCase().split(':')[0].replace(/\.$/, '');
  if (host !== APEX_HOST) {
    return null;
  }
  return {
    statusCode: 301,
    statusDescription: 'Moved Permanently',
    headers: { location: { value: APP_ORIGIN + request.uri + buildQueryString(request.querystring || {}) } },
  };
}

function handler(event) {
  var request = event.request;

  // Antes de tudo (inclusive dos 301 legados): no apex, so redireciona; o
  // app. trata a rota legada no proximo hop.
  var apex = apexRedirect(request);
  if (apex) {
    return apex;
  }

  var redirect = legacyRedirect(request);
  if (redirect) {
    return redirect;
  }

  var uri = request.uri;

  for (var i = 0; i < DYNAMIC_ROUTE_PREFIXES.length; i++) {
    var prefix = DYNAMIC_ROUTE_PREFIXES[i];
    if (uri.indexOf(prefix) === 0) {
      var rest = uri.slice(prefix.length);
      var firstSegment = rest.split('/')[0];
      if (firstSegment !== '' && firstSegment !== 'placeholder') {
        request.uri = prefix + 'placeholder/index.html';
        return request;
      }
      break;
    }
  }

  if (uri.endsWith('/')) {
    request.uri += 'index.html';
  } else if (!uri.includes('.')) {
    request.uri += '/index.html';
  }

  return request;
}
