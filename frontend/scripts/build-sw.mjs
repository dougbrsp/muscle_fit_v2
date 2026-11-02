// Pós-build do service worker (plano mobile 3.7). Não está encadeado ao
// `npm run build` (package.json não é desta frente): o workflow de deploy chama
// este script depois de `next build`, antes do `s3 sync`.
//
//   node scripts/build-sw.mjs            carimba CACHE_VERSION em out/sw.js
//   node scripts/build-sw.mjs --kill     troca out/sw.js pelo SW autodestrutivo
//
// Versão: env CACHE_VERSION, senão .next/BUILD_ID, senão GITHUB_SHA (12 chars).
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const VERSION_LINE = /const CACHE_VERSION = '[^']*';/;

export function stampCacheVersion(source, version) {
  if (!/^[A-Za-z0-9._-]{1,64}$/.test(version)) throw new Error(`CACHE_VERSION inválida: ${version}`);
  if (!VERSION_LINE.test(source)) throw new Error("sw.js sem a linha `const CACHE_VERSION = '...';`");
  return source.replace(VERSION_LINE, `const CACHE_VERSION = '${version}';`);
}

/**
 * SW autodestrutivo: apaga só os caches `musclefit-*` e se desregistra. NÃO
 * recarrega abas (poderia haver treino em andamento): o efeito vale na próxima
 * abertura do app.
 */
export const KILL_SW_SOURCE = `/* MuscleFit service worker KILL SWITCH: remove caches e se desregistra. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(names.filter((n) => n.startsWith('musclefit-')).map((n) => caches.delete(n)));
      await self.registration.unregister();
    })(),
  );
});
// Sem handler de fetch: toda requisição vai direto para a rede.
`;

function resolveVersion() {
  if (process.env.CACHE_VERSION) return process.env.CACHE_VERSION;
  const buildId = path.join(root, '.next', 'BUILD_ID');
  if (existsSync(buildId)) return readFileSync(buildId, 'utf8').trim();
  if (process.env.GITHUB_SHA) return process.env.GITHUB_SHA.slice(0, 12);
  throw new Error('Sem versão: defina CACHE_VERSION ou rode depois do next build (.next/BUILD_ID).');
}

function main() {
  const target = path.join(root, 'out', 'sw.js');
  if (!existsSync(target)) throw new Error('out/sw.js não existe: rode `next build` antes.');
  if (process.argv.includes('--kill')) {
    writeFileSync(target, KILL_SW_SOURCE);
    console.log('out/sw.js substituído pelo service worker autodestrutivo.');
    return;
  }
  const version = resolveVersion();
  writeFileSync(target, stampCacheVersion(readFileSync(target, 'utf8'), version));
  console.log(`out/sw.js carimbado com CACHE_VERSION=${version}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main();
