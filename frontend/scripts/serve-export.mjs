// Preview local do ARTEFATO DE PRODUÇÃO do frontend: serve `out/` (gerado por
// `npm run build`, output: 'export') imitando a borda real:
//   1. viewer-request: a CloudFront Function real
//      (infra/cloudfront-functions/spa-index-rewrite.js), carregada sem cópia
//      da regra - ver scripts/cloudfront-function.mjs;
//   2. origem S3 via API REST/OAC: só devolve o objeto com a key exata (sem
//      resolver "/rota" -> "/rota/index.html" sozinho);
//   3. custom_error_response 403/404 -> /index.html com 200 (infra/cloudfront.tf).
//
// Uso: node scripts/serve-export.mjs [--port 4000] [--dir out]
// Node puro, sem dependências. NÃO é usado em produção.
import { createReadStream, existsSync, statSync } from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { loadSpaIndexRewriteHandler, toCloudFrontQuerystring } from './cloudfront-function.mjs';

const CONTENT_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.ico': 'image/x-icon',
  '.webp': 'image/webp',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.webmanifest': 'application/manifest+json',
};

/** Header só do preview: indica que a resposta veio do custom_error_response. */
export const FALLBACK_HEADER = 'x-preview-error-fallback';

function resolveObject(rootDir, uri) {
  let key;
  try {
    key = decodeURIComponent(uri);
  } catch {
    return null;
  }
  const filePath = path.resolve(rootDir, `.${key}`);
  if (filePath !== rootDir && !filePath.startsWith(rootDir + path.sep)) return null;
  if (!existsSync(filePath) || !statSync(filePath).isFile()) return null;
  return filePath;
}

function sendFile(req, res, filePath, extraHeaders = {}) {
  res.writeHead(200, {
    'content-type': CONTENT_TYPES[path.extname(filePath).toLowerCase()] ?? 'application/octet-stream',
    'content-length': statSync(filePath).size,
    ...extraHeaders,
  });
  if (req.method === 'HEAD') {
    res.end();
    return;
  }
  createReadStream(filePath).pipe(res);
}

/**
 * @param {{ dir?: string, port?: number, host?: string }} options
 * @returns {Promise<{ server: http.Server, url: string, close: () => Promise<void> }>}
 */
export async function startExportServer({ dir = 'out', port = 4000, host = '127.0.0.1' } = {}) {
  const rootDir = path.resolve(dir);
  if (!existsSync(path.join(rootDir, 'index.html'))) {
    throw new Error(`"${rootDir}/index.html" não existe - rode "npm run build" antes.`);
  }
  const handler = loadSpaIndexRewriteHandler();

  const server = http.createServer((req, res) => {
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.writeHead(405, { allow: 'GET, HEAD' }).end();
      return;
    }
    const url = new URL(req.url ?? '/', 'http://preview.local');
    const request = {
      method: req.method,
      uri: url.pathname,
      querystring: toCloudFrontQuerystring(url.searchParams),
      headers: {},
    };

    let result;
    try {
      result = handler({ version: '1.0', context: { eventType: 'viewer-request' }, request });
    } catch (error) {
      res.writeHead(500, { 'content-type': 'text/plain; charset=utf-8' }).end(`CloudFront Function falhou: ${error}`);
      return;
    }

    if (result && typeof result.statusCode === 'number') {
      const headers = {};
      for (const [name, entry] of Object.entries(result.headers ?? {})) headers[name] = entry.value;
      res.writeHead(result.statusCode, result.statusDescription, headers).end();
      return;
    }

    const filePath = resolveObject(rootDir, result.uri);
    if (filePath) {
      sendFile(req, res, filePath);
      return;
    }
    // S3 privado via OAC responde 403 para key inexistente; a distribuição
    // converte 403/404 em /index.html com 200.
    sendFile(req, res, path.join(rootDir, 'index.html'), { [FALLBACK_HEADER]: '1' });
  });

  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, host, resolve);
  });
  const address = server.address();
  const actualPort = typeof address === 'object' && address ? address.port : port;
  return {
    server,
    url: `http://${host}:${actualPort}`,
    close: () => new Promise((resolve) => server.close(() => resolve())),
  };
}

function parseArgs(argv) {
  const options = {};
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--port') options.port = Number(argv[++i]);
    else if (argv[i] === '--dir') options.dir = argv[++i];
    else if (argv[i] === '--host') options.host = argv[++i];
  }
  return options;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const options = parseArgs(process.argv.slice(2));
  const defaultDir = fileURLToPath(new URL('../out', import.meta.url));
  startExportServer({ dir: defaultDir, ...options })
    .then(({ url }) => {
      console.log(`Preview de produção (out/ + CloudFront Function real) em ${url}`);
    })
    .catch((error) => {
      console.error(error.message);
      process.exit(1);
    });
}
