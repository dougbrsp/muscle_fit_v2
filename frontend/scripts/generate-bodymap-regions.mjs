// Gera `src/components/bodymap/regions.v1.ts` a partir dos arquivos aprovados
// do mapa corporal v1 (`public/bodymap/v1/regions.json` + `manifest.json`).
//
// Uso:
//   node scripts/generate-bodymap-regions.mjs          # (re)escreve o .ts
//   node scripts/generate-bodymap-regions.mjs --check  # exit 1 se estiver desatualizado
//
// Sem dependência nova: só `node:` builtins. O teste
// `scripts/generate-bodymap-regions.test.mjs` roda `buildRegionsModule` e
// compara com o arquivo versionado — editar o .ts à mão quebra o CI.
//
// O que o gerador faz além de copiar:
// - separa cada path por VISTA (frente/costas) usando as caixas do manifest:
//   cada subpath (`M ... Z`) vai para a vista que contém o centro da sua caixa.
//   Assim o componente desenha e torna clicável só o que aparece em cada
//   recorte (o tríceps, por exemplo, existe nas duas vistas);
// - resolve aliases: `abductors` não vira região própria; o componente traduz
//   para `adductor`. Se o path do alias divergir do alvo, o gerador falha;
// - recusa comandos de path fora de M/L/C/Z absolutos (o cálculo da caixa
//   depende de coordenadas absolutas).
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const SOURCE_FILES = {
  regions: fileURLToPath(new URL('../public/bodymap/v1/regions.json', import.meta.url)),
  manifest: fileURLToPath(new URL('../public/bodymap/v1/manifest.json', import.meta.url)),
};
export const OUTPUT_FILE = fileURLToPath(new URL('../src/components/bodymap/regions.v1.ts', import.meta.url));

const VIEW_ORDER = ['front', 'back'];

/** Quebra um path em subpaths, cada um começando no seu `M`. */
export function splitSubpaths(d) {
  const invalid = d.match(/[^MLCZ0-9.\s,-]/);
  if (invalid) {
    throw new Error(`comando de path não suportado: "${invalid[0]}" (só M/L/C/Z absolutos)`);
  }
  return d
    .split(/(?=M)/)
    .map((part) => part.trim())
    .filter(Boolean);
}

/** Caixa (pelos pontos e pontos de controle) de um subpath absoluto. */
export function subpathBox(subpath) {
  const numbers = (subpath.match(/-?\d+(?:\.\d+)?/g) || []).map(Number);
  if (numbers.length < 2 || numbers.length % 2 !== 0) {
    throw new Error(`subpath com coordenadas ímpares: ${subpath.slice(0, 40)}...`);
  }
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (let i = 0; i < numbers.length; i += 2) {
    minX = Math.min(minX, numbers[i]);
    maxX = Math.max(maxX, numbers[i]);
    minY = Math.min(minY, numbers[i + 1]);
    maxY = Math.max(maxY, numbers[i + 1]);
  }
  return { minX, maxX, minY, maxY };
}

/** Vista que contém o centro da caixa; fora das duas, a de centro mais próximo. */
export function viewForBox(box, views) {
  const cx = (box.minX + box.maxX) / 2;
  const cy = (box.minY + box.maxY) / 2;
  let best = null;
  let bestDistance = Infinity;
  for (const name of VIEW_ORDER) {
    const [x0, y0, x1, y1] = views[name];
    if (cx >= x0 && cx <= x1 && cy >= y0 && cy <= y1) return name;
    const distance = Math.hypot(cx - (x0 + x1) / 2, cy - (y0 + y1) / 2);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = name;
    }
  }
  return best;
}

/** `{ front?: d, back?: d }` de um slug, preservando a ordem dos subpaths. */
export function splitByView(d, views) {
  const parts = { front: [], back: [] };
  for (const subpath of splitSubpaths(d)) {
    parts[viewForBox(subpathBox(subpath), views)].push(subpath);
  }
  const out = {};
  for (const name of VIEW_ORDER) {
    if (parts[name].length) out[name] = parts[name].join('');
  }
  return out;
}

function sha256(text) {
  return createHash('sha256').update(text).digest('hex');
}

/** Literal de string em aspas simples (estilo do projeto). */
function q(value) {
  return `'${String(value).replace(/\\/g, '\\\\').replace(/'/g, "\\'")}'`;
}

/** Chave de objeto: identificador puro sem aspas; o resto entre aspas simples. */
function key(value) {
  return /^[A-Za-z_$][\w$]*$/.test(value) ? value : q(value);
}

/**
 * Conteúdo do módulo TS. `regionsText` é o texto CRU do regions.json (o sha256
 * entra no cabeçalho, para o teste de sincronia e para comparar com o backend).
 */
export function buildRegionsModule(regionsText, manifest) {
  const regions = JSON.parse(regionsText);
  const [vx, vy, vw, vh] = regions.viewBox;
  const [cw, ch] = manifest.canvas;
  if (vx !== 0 || vy !== 0 || vw !== cw || vh !== ch) {
    throw new Error(`viewBox ${regions.viewBox} não bate com o canvas do manifest ${manifest.canvas}`);
  }
  const aliases = manifest.aliases || {};
  for (const [alias, target] of Object.entries(aliases)) {
    if (!regions.regions[target]) throw new Error(`alias ${alias} aponta para slug sem região: ${target}`);
    if (regions.regions[alias] && regions.regions[alias] !== regions.regions[target]) {
      throw new Error(`alias ${alias} tem path diferente do alvo ${target}`);
    }
  }
  const expected = [...(manifest.slugs_db || []), ...(manifest.slugs_extra || [])];
  for (const slug of expected) {
    if (!regions.regions[slug]) throw new Error(`slug do manifest sem região: ${slug}`);
  }

  const slugs = Object.keys(regions.regions)
    .filter((slug) => !(slug in aliases))
    .sort();
  const c = manifest.compositor;
  const lines = [];
  lines.push('// ARQUIVO GERADO por scripts/generate-bodymap-regions.mjs. NÃO EDITE À MÃO.');
  lines.push('// Fonte: public/bodymap/v1/regions.json + public/bodymap/v1/manifest.json');
  lines.push('// (pacote aprovado pelo usuário em 2026-10-03). Regenerar:');
  lines.push('//   node scripts/generate-bodymap-regions.mjs');
  lines.push('');
  lines.push(`export const BODYMAP_VERSION = ${q(manifest.version)};`);
  lines.push('');
  lines.push('/** sha256 do `regions.json` de origem (o backend versiona o MESMO arquivo). */');
  lines.push(`export const BODYMAP_REGIONS_SHA256 = ${q(sha256(regionsText))};`);
  lines.push('');
  lines.push('/** Tamanho da arte base; os paths estão neste sistema de coordenadas. */');
  lines.push(`export const BODYMAP_CANVAS = { width: ${cw}, height: ${ch} } as const;`);
  lines.push('');
  lines.push(`export const BODYMAP_FILL_RULE = ${q(regions.fillRule)};`);
  lines.push('');
  lines.push(`export const BODYMAP_VIEW_ORDER = [${VIEW_ORDER.map(q).join(', ')}] as const;`);
  lines.push('export type BodyMapView = (typeof BODYMAP_VIEW_ORDER)[number];');
  lines.push('');
  lines.push('export type BodyMapBox = { x: number; y: number; width: number; height: number };');
  lines.push('');
  lines.push('/** Recorte de cada figura na arte (o vão entre elas é grande; cada vista é desenhada à parte). */');
  lines.push('export const BODYMAP_VIEWS: Readonly<Record<BodyMapView, BodyMapBox>> = {');
  for (const name of VIEW_ORDER) {
    const [x0, y0, x1, y1] = manifest.views[name];
    lines.push(`  ${name}: { x: ${x0}, y: ${y0}, width: ${x1 - x0}, height: ${y1 - y0} },`);
  }
  lines.push('};');
  lines.push('');
  lines.push('/** Parâmetros do compositor (mesma fórmula do render em Pillow do backend). */');
  lines.push('export const BODYMAP_COMPOSITOR = {');
  lines.push(`  lumLo: ${c.lum_lo},`);
  lines.push(`  lumHi: ${c.lum_hi},`);
  lines.push(`  k0: ${c.K0},`);
  lines.push(`  k1: ${c.K1},`);
  lines.push(`  dim: ${c.dim},`);
  lines.push('} as const;');
  lines.push('');
  lines.push('export const BODYMAP_SLUGS = [');
  for (const slug of slugs) lines.push(`  ${q(slug)},`);
  lines.push('] as const;');
  lines.push('export type BodyMapSlug = (typeof BODYMAP_SLUGS)[number];');
  lines.push('');
  lines.push('/** Slugs do banco que desenham a região de outro slug. */');
  lines.push('export const BODYMAP_ALIASES: Readonly<Record<string, BodyMapSlug>> = {');
  for (const [alias, target] of Object.entries(aliases).sort(([a], [b]) => a.localeCompare(b))) {
    lines.push(`  ${key(alias)}: ${q(target)},`);
  }
  lines.push('};');
  lines.push('');
  lines.push('/** Path SVG (coordenadas do canvas, `fill-rule: evenodd`) de cada slug, por vista. */');
  lines.push('export const BODYMAP_REGIONS: Readonly<Record<BodyMapSlug, Partial<Record<BodyMapView, string>>>> = {');
  for (const slug of slugs) {
    const byView = splitByView(regions.regions[slug], manifest.views);
    lines.push(`  ${key(slug)}: {`);
    for (const name of VIEW_ORDER) {
      if (byView[name]) lines.push(`    ${name}: ${q(byView[name])},`);
    }
    lines.push('  },');
  }
  lines.push('};');
  lines.push('');
  return lines.join('\n');
}

export function generate() {
  const regionsText = readFileSync(SOURCE_FILES.regions, 'utf8');
  const manifest = JSON.parse(readFileSync(SOURCE_FILES.manifest, 'utf8'));
  return buildRegionsModule(regionsText, manifest);
}

// Comparação sem diferenciar maiúsculas: no Windows o drive pode vir "c:" ou "C:".
const isMain =
  Boolean(process.argv[1]) &&
  path.resolve(process.argv[1]).toLowerCase() === fileURLToPath(import.meta.url).toLowerCase();
if (isMain) {
  const content = generate();
  if (process.argv.includes('--check')) {
    const current = readFileSync(OUTPUT_FILE, 'utf8').replace(/\r\n/g, '\n');
    if (current !== content) {
      console.error('regions.v1.ts desatualizado: rode node scripts/generate-bodymap-regions.mjs');
      process.exit(1);
    }
    console.log('regions.v1.ts em sincronia com regions.json.');
  } else {
    writeFileSync(OUTPUT_FILE, content);
    console.log(`Gerado ${OUTPUT_FILE}`);
  }
}
