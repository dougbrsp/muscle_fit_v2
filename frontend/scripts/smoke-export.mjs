// Smoke test do ARTEFATO DE PRODUÇÃO: sobe scripts/serve-export.mjs sobre o
// `out/` já buildado (mesma lógica da CloudFront Function real) e confere que
// as rotas dinâmicas e os redirects legados respondem como em produção.
// Falha (exit 1) se um PR quebrar o export das rotas dinâmicas.
//
// Uso: npm run build && node scripts/smoke-export.mjs [--dir out]
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { FALLBACK_HEADER, startExportServer } from './serve-export.mjs';

const dirArg = process.argv.indexOf('--dir');
const outDir = path.resolve(
  dirArg !== -1 ? process.argv[dirArg + 1] : fileURLToPath(new URL('../out', import.meta.url)),
);

// Objeto que a CloudFront deve servir para cada URL de rota dinâmica.
const DYNAMIC_ROUTES = [
  ['/metas-ativacao/novo/', 'metas-ativacao/placeholder/index.html'],
  ['/metas-ativacao/1/', 'metas-ativacao/placeholder/index.html'],
  ['/metas-ativacao/1', 'metas-ativacao/placeholder/index.html'],
  ['/modelos-treino/novo/', 'modelos-treino/placeholder/index.html'],
  ['/modelos-treino/1/', 'modelos-treino/placeholder/index.html'],
  ['/planos-treino/1/', 'planos-treino/placeholder/index.html'],
  ['/registro-exercicios/1/', 'registro-exercicios/placeholder/index.html'],
  // Drill-down por exercício do hub (F1.3) — a armadilha que esta suíte cobre:
  // sem o prefixo em `DYNAMIC_ROUTE_PREFIXES` a rota funciona em DEV e dá 404
  // em produção.
  ['/dashboard/exercicios/123/', 'dashboard/exercicios/placeholder/index.html'],
  ['/dashboard/exercicios/123', 'dashboard/exercicios/placeholder/index.html'],
  // Fase 4 (PR G): diagnóstico de volume por metodologia — rota de topo
  // própria (nunca sob /modelos-treino/<id>/…, que entregaria o builder).
  ['/diagnostico-volume/12/', 'diagnostico-volume/placeholder/index.html'],
  ['/diagnostico-volume/12', 'diagnostico-volume/placeholder/index.html'],
  // As sub-rotas estáticas do hub não podem ser capturadas pelo prefixo novo.
  ['/dashboard/progressao/', 'dashboard/progressao/index.html'],
  ['/dashboard/musculos/', 'dashboard/musculos/index.html'],
  // Corpo (F1.4): sub-rota estática nova do hub.
  ['/dashboard/corpo/', 'dashboard/corpo/index.html'],
  // Rotas estáticas vizinhas continuam resolvendo para o próprio index.
  ['/metas-ativacao/', 'metas-ativacao/index.html'],
  ['/modelos-treino/', 'modelos-treino/index.html'],
  // Nutrição L4: o diário é a página raiz da seção (o menu "Nutrição" aponta para ela).
  ['/nutricao/', 'nutricao/index.html'],
  // Nutrição L2b: página estática do plano.
  ['/nutricao/plano/', 'nutricao/plano/index.html'],
  // Nutrição L3a: catálogo de alimentos (TACO + cadastro do usuário).
  ['/nutricao/alimentos/', 'nutricao/alimentos/index.html'],
  // O 301 de /treinos não pode capturar o próprio destino nem rotas vizinhas.
  ['/dashboard/treinos/', 'dashboard/treinos/index.html'],
  ['/modo-treino/', 'modo-treino/index.html'],
  ['/planos-treino/', 'planos-treino/index.html'],
  // O 301 de /relatorios não pode capturar o próprio destino.
  ['/dashboard/relatorios/', 'dashboard/relatorios/index.html'],
  ['/', 'index.html'],
  // Páginas públicas de "Esqueci minha senha" (M5): a URL do e-mail é
  // `/redefinir-senha?token=...` (sem barra final, com query string).
  ['/esqueci-senha/', 'esqueci-senha/index.html'],
  ['/redefinir-senha/', 'redefinir-senha/index.html'],
  ['/redefinir-senha?token=abc', 'redefinir-senha/index.html'],
];

const REDIRECTS = [
  ['/tipos-treinamento', '/metas-ativacao/'],
  ['/tipos-treinamento/', '/metas-ativacao/'],
  ['/tipos-treinamento/1/', '/metas-ativacao/1/'],
  ['/tipos-treinamento/novo/?x=1', '/metas-ativacao/novo/?x=1'],
  // F2.4: "Evolução corporal" foi absorvida pelo hub v3. Link salvo/favorito
  // antigo tem que chegar em /dashboard/corpo com a query preservada.
  ['/evolucao-corporal', '/dashboard/corpo/'],
  ['/evolucao-corporal/?days=180', '/dashboard/corpo/?days=180'],
  // F2.4 (2ª passada): "Recuperação muscular" foi absorvida por
  // /dashboard/recovery depois do M33.
  ['/recuperacao-muscular', '/dashboard/recovery/'],
  ['/recuperacao-muscular/?days=14', '/dashboard/recovery/?days=14'],
  // "Grupos musculares" aposentada: tendência por grupo em /dashboard/musculos.
  ['/grupos-musculares', '/dashboard/musculos/'],
  ['/grupos-musculares/?days=90', '/dashboard/musculos/?days=90'],
  // "Treinos" aposentada: seções em /dashboard/treinos (#294) e ativação de 7
  // dias em /dashboard/musculos (#295).
  ['/treinos', '/dashboard/treinos/'],
  ['/treinos/?days=90', '/dashboard/treinos/?days=90'],
  // Relatório PDF com IA aposentado: relatório em Markdown em
  // /dashboard/relatorios.
  ['/relatorios', '/dashboard/relatorios/'],
  ['/relatorios/?days=90', '/dashboard/relatorios/?days=90'],
];

// Assets do mapa corporal v1 (`MuscleBodyMap`), servidos como arquivo estático.
const BODYMAP_ASSETS = [
  '/bodymap/v1/bodymap-base.webp',
  '/bodymap/v1/bodymap-base@768.webp',
  '/bodymap/v1/regions.json',
];

// Páginas (raiz, estática, aninhada e dinâmica) onde o atalho da Tela de
// Início pode ser criado - todas precisam das mesmas metatags.
const PWA_PAGES = ['/', '/dashboard/', '/academias/', '/dashboard/treinos/', '/planos-treino/1/'];
const PWA_HEAD_TAGS = [
  ['link manifest absoluto', /<link rel="manifest" href="\/manifest\.json"/],
  ['apple-touch-icon absoluto', /<link rel="apple-touch-icon" href="\/apple-touch-icon\.png"/],
  ['apple-mobile-web-app-capable', /<meta name="apple-mobile-web-app-capable" content="yes"/],
  ['apple-mobile-web-app-title', /<meta name="apple-mobile-web-app-title" content="MuscleFit"/],
  ['theme-color', /<meta name="theme-color" content="[^"]+"/],
  // M26: sem `width=device-width, initial-scale=1` o iOS abre a página num
  // viewport de 980px e a exibe escalada (o app "abre com zoom").
  ['viewport device-width + initial-scale=1', /<meta name="viewport" content="width=device-width, initial-scale=1"/],
];

// M26: o zoom NÃO pode ser "resolvido" desligando o zoom. `maximum-scale`/
// `user-scalable=no` escondem o defeito e tiram do usuário o zoom de
// acessibilidade — se alguém reintroduzir isso, o smoke quebra.
const VIEWPORT_PROIBIDO = /<meta name="viewport"[^>]*content="[^"]*(maximum-scale|user-scalable)/;

/**
 * M26 — a correção do zoom no iPhone é CSS (campos com 16px no toque), e CSS
 * some silenciosamente de um build. Aqui o smoke confere que a regra está no
 * bundle REAL exportado, não só no fonte.
 */
const CSS_ESPERADO = [
  [
    'campos com 16px em dispositivos de toque (@media (pointer: coarse))',
    /@media\s*\(pointer:\s*coarse\)\s*\{[\s\S]{0,600}?font-size:\s*16px/,
  ],
];

const failures = [];
const check = (ok, message) => {
  console.log(`${ok ? 'OK  ' : 'FAIL'} ${message}`);
  if (!ok) failures.push(message);
};

const { url, close } = await startExportServer({ dir: outDir, port: 0 });
try {
  for (const [route, objectKey] of DYNAMIC_ROUTES) {
    const objectPath = path.join(outDir, objectKey);
    if (!existsSync(objectPath)) {
      check(false, `${route}: objeto ${objectKey} não existe em out/`);
      continue;
    }
    const res = await fetch(url + route, { redirect: 'manual' });
    const body = await res.text();
    check(res.status === 200, `${route} -> HTTP ${res.status} (esperado 200)`);
    check(!res.headers.has(FALLBACK_HEADER), `${route} não caiu no custom_error_response`);
    check(body === readFileSync(objectPath, 'utf8'), `${route} serve ${objectKey}`);

    // Os chunks JS referenciados pela página precisam existir no artefato.
    const scripts = [...body.matchAll(/<script[^>]+src="([^"]+\.js)"/g)].map((m) => m[1]);
    check(scripts.length > 0, `${route} referencia chunks JS`);
    for (const src of scripts) {
      const asset = await fetch(new URL(src, url + route), { method: 'HEAD' });
      if (asset.status !== 200 || asset.headers.has(FALLBACK_HEADER)) {
        check(false, `${route}: chunk ${src} -> HTTP ${asset.status}`);
      }
    }
  }

  // App instalável (iOS "Adicionar à Tela de Início"): metatags em TODA página
  // (inclusive aninhada/dinâmica), manifest com escopo na raiz e ícones reais
  // servidos pela CloudFront Function (não reescritos para index.html).
  for (const route of PWA_PAGES) {
    const html = await (await fetch(url + route)).text();
    for (const [label, pattern] of PWA_HEAD_TAGS) {
      check(pattern.test(html), `${route} tem ${label}`);
    }
    check(!VIEWPORT_PROIBIDO.test(html), `${route} não desliga o zoom (maximum-scale/user-scalable)`);

    // O CSS da página é o do artefato: se a regra do M26 sumir do bundle, o
    // app volta a dar zoom no iPhone sem que nenhum teste de unidade perceba.
    // A ordem dos atributos do <link> não é contrato do Next: casa a tag
    // inteira e só depois extrai `href`.
    const hrefs = [...html.matchAll(/<link\b[^>]*>/g)]
      .map((m) => m[0])
      .filter((tag) => /rel="stylesheet"/.test(tag))
      .map((tag) => tag.match(/href="([^"]+)"/)?.[1])
      .filter(Boolean);
    check(hrefs.length > 0, `${route} referencia CSS`);
    const css = (
      await Promise.all(hrefs.map(async (href) => (await fetch(new URL(href, url + route))).text()))
    ).join('\n');
    for (const [label, pattern] of CSS_ESPERADO) {
      check(pattern.test(css), `${route}: CSS do artefato tem ${label}`);
    }
  }

  const manifestRes = await fetch(url + '/manifest.json');
  check(
    manifestRes.status === 200 && !manifestRes.headers.has(FALLBACK_HEADER),
    `/manifest.json -> HTTP ${manifestRes.status} sem fallback`,
  );
  let manifest = null;
  try {
    manifest = JSON.parse(await manifestRes.text());
  } catch (error) {
    check(false, `/manifest.json é JSON válido (${error})`);
  }
  if (manifest) {
    check(manifest.start_url === '/', `manifest.start_url === "/" (${manifest.start_url})`);
    check(manifest.scope === '/', `manifest.scope === "/" (${manifest.scope})`);
    check(manifest.display === 'standalone', `manifest.display === "standalone" (${manifest.display})`);
    const icons = Array.isArray(manifest.icons) ? manifest.icons : [];
    check(icons.length > 0, 'manifest declara ícones');
    // src -> tamanho esperado (dedup: o mesmo PNG pode ter purpose any/maskable).
    const expectedIcons = new Map([['/apple-touch-icon.png', '180x180']]);
    for (const icon of icons) expectedIcons.set(icon.src, icon.sizes);
    for (const [src, sizes] of expectedIcons) {
      check(src.startsWith('/'), `ícone ${src} usa caminho absoluto`);
      const res = await fetch(url + src);
      const png = Buffer.from(await res.arrayBuffer());
      check(res.status === 200 && !res.headers.has(FALLBACK_HEADER), `${src} -> HTTP ${res.status} sem fallback`);
      check(res.headers.get('content-type') === 'image/png', `${src} content-type image/png`);
      const isPng = png.subarray(1, 4).toString('latin1') === 'PNG';
      const size = isPng ? `${png.readUInt32BE(16)}x${png.readUInt32BE(20)}` : 'não-PNG';
      check(size === sizes, `${src} tem ${sizes} (${size})`);
    }
  }

  // Mapa corporal v1: a arte base é carregada pelo `<image href>` do SVG em
  // runtime — se o arquivo sumir do `out/` ou cair no custom_error_response,
  // o mapa fica vazio sem nenhum erro de build.
  for (const asset of BODYMAP_ASSETS) {
    const res = await fetch(url + asset);
    const bytes = Buffer.from(await res.arrayBuffer());
    check(res.status === 200 && !res.headers.has(FALLBACK_HEADER), `${asset} -> HTTP ${res.status} sem fallback`);
    if (asset.endsWith('.webp')) {
      check(res.headers.get('content-type') === 'image/webp', `${asset} content-type image/webp`);
      const isWebp =
        bytes.subarray(0, 4).toString('latin1') === 'RIFF' && bytes.subarray(8, 12).toString('latin1') === 'WEBP';
      check(isWebp, `${asset} é WebP de verdade`);
    } else {
      let regions = null;
      try {
        regions = JSON.parse(bytes.toString('utf8'));
      } catch (error) {
        check(false, `${asset} é JSON válido (${error})`);
      }
      if (regions) check(Object.keys(regions.regions ?? {}).length > 0, `${asset} tem regiões`);
    }
  }

  for (const [route, location] of REDIRECTS) {
    const res = await fetch(url + route, { redirect: 'manual' });
    check(
      res.status === 301 && res.headers.get('location') === location,
      `${route} -> ${res.status} ${res.headers.get('location')} (esperado 301 ${location})`,
    );
  }
} finally {
  await close();
}

if (failures.length) {
  console.error(`\n${failures.length} verificação(ões) falharam.`);
  process.exit(1);
}
console.log('\nSmoke do export estático OK.');
