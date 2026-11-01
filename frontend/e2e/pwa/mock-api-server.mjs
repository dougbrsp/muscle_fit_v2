// API falsa REAL (servidor HTTP) para os testes de service worker: com
// `page.route` o Playwright faz o Chromium ignorar o SW, o que invalidaria a
// prova de "a API nunca vai para o cache". O Chromium resolve `mock-api.test`
// para este servidor via --host-resolver-rules (ver playwright.config.ts).
// /auth/me responde 401 (tela de login); qualquer outro GET responde `{}`.
import http from 'node:http';

const port = Number(process.argv[2] ?? 4175);

http
  .createServer((req, res) => {
    const origin = req.headers.origin ?? '*';
    const cors = {
      'access-control-allow-origin': origin,
      'access-control-allow-credentials': 'true',
      'access-control-allow-headers': '*',
      'access-control-allow-methods': '*',
    };
    if (req.method === 'OPTIONS') {
      res.writeHead(204, cors).end();
      return;
    }
    const status = req.url === '/auth/me' ? 401 : 200;
    res.writeHead(status, { ...cors, 'content-type': 'application/json' });
    res.end(status === 401 ? '{"detail":"Nao autenticado"}' : '{}');
  })
  .listen(port, '127.0.0.1');
