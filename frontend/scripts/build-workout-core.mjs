// Empacota o núcleo PURO do Modo Treino (`src/lib/workout-mode-core/index.ts`)
// num único ES module para o relógio (Zepp OS), PR B da Evolução ZEPP.
//
//   npm run build:workout-core
//
// Saída (ignorada no git):
//   dist/workout-mode-core/workout-mode-core.js         ESM, ES2020, sem externals
//   dist/workout-mode-core/workout-mode-core.golden.js  `export default [...]` do golden
//   dist/workout-mode-core/meta.json                    commit, sha256, tamanho; golden_sha256 = hash do
//                                                       .golden.js (não do .json) e golden_cases
//   dist/workout-mode-core/package.json                 {"type": "module"}
//
// O golden sai também como módulo JS (PR B2) porque o build do Zepp OS não
// importa `.json`: é o MESMO array de `src/lib/workout-mode-core.golden.json`.
//
// Funciona com qualquer cwd (caminhos por `import.meta.url`), inclusive da
// raiz do checkout: `node frontend/scripts/build-workout-core.mjs`.
//
// `buildWorkoutCore()` também é usado pelo vitest
// (`src/lib/workout-mode-core.test.ts`), que compila em memória e confere o
// texto e o comportamento do MESMO bundle que vai para o relógio.

import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { gzipSync } from 'node:zlib';
import { build } from 'esbuild';

const FRONTEND_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const ENTRY_POINT = path.join(FRONTEND_DIR, 'src', 'lib', 'workout-mode-core', 'index.ts');
export const OUT_DIR = path.join(FRONTEND_DIR, 'dist', 'workout-mode-core');
export const BUNDLE_FILE = 'workout-mode-core.js';
export const GOLDEN_JSON = path.join(FRONTEND_DIR, 'src', 'lib', 'workout-mode-core.golden.json');
export const GOLDEN_FILE = 'workout-mode-core.golden.js';

/** Texto do módulo `workout-mode-core.golden.js`: o golden commitado como `export default`. */
export function goldenModuleSource(goldenJsonText = readFileSync(GOLDEN_JSON, 'utf8')) {
  const cases = JSON.parse(goldenJsonText);
  if (!Array.isArray(cases)) throw new Error('workout-mode-core.golden.json precisa ser um array de casos');
  return `// GERADO por frontend/scripts/build-workout-core.mjs a partir de src/lib/workout-mode-core.golden.json.
export default ${JSON.stringify(cases)};
`;
}

function currentCommit() {
  try {
    return execFileSync('git', ['rev-parse', 'HEAD'], { cwd: FRONTEND_DIR, encoding: 'utf8' }).trim();
  } catch {
    return process.env.GITHUB_SHA || 'unknown';
  }
}

/**
 * Compila o núcleo. Com `write: true` grava o bundle e o `meta.json` em
 * `dist/workout-mode-core/`; sem, só devolve o texto e os metadados.
 */
export async function buildWorkoutCore({ write = false } = {}) {
  const result = await build({
    entryPoints: [ENTRY_POINT],
    bundle: true,
    format: 'esm',
    target: 'es2020',
    platform: 'neutral',
    // Sem externals: tudo que o núcleo importa entra no arquivo.
    external: [],
    minify: true,
    legalComments: 'none',
    charset: 'utf8',
    write: false,
    logLevel: 'silent',
  });
  const code = result.outputFiles[0].text;
  const bytes = Buffer.from(code, 'utf8');
  const goldenJsonText = readFileSync(GOLDEN_JSON, 'utf8');
  const goldenCode = goldenModuleSource(goldenJsonText);
  const goldenBytes = Buffer.from(goldenCode, 'utf8');
  const meta = {
    commit: currentCommit(),
    sha256: createHash('sha256').update(bytes).digest('hex'),
    bytes: bytes.length,
    gzip_bytes: gzipSync(bytes).length,
    format: 'esm',
    target: 'es2020',
    // Hash do MÓDULO `workout-mode-core.golden.js` (o arquivo que o relógio importa), não do JSON.
    golden_sha256: createHash('sha256').update(goldenBytes).digest('hex'),
    golden_cases: JSON.parse(goldenJsonText).length,
  };
  if (write) {
    mkdirSync(OUT_DIR, { recursive: true });
    writeFileSync(path.join(OUT_DIR, BUNDLE_FILE), bytes);
    writeFileSync(path.join(OUT_DIR, GOLDEN_FILE), goldenBytes);
    writeFileSync(path.join(OUT_DIR, 'meta.json'), `${JSON.stringify(meta, null, 2)}\n`);
    // O `.js` é ESM: sem isto o Node (fora de um pacote "type": "module")
    // reanalisa o arquivo e avisa a cada import.
    writeFileSync(path.join(OUT_DIR, 'package.json'), `${JSON.stringify({ type: 'module' }, null, 2)}\n`);
  }
  return { code, goldenCode, meta };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const { meta } = await buildWorkoutCore({ write: true });
  console.log(`workout-mode-core: ${meta.bytes} bytes (gzip ${meta.gzip_bytes}), sha256 ${meta.sha256}`);
  console.log(`workout-mode-core.golden: ${meta.golden_cases} casos, sha256 ${meta.golden_sha256}`);
}
