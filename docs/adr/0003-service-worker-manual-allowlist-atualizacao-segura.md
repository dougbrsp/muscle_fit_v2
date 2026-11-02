# ADR 0003: Service worker escrito à mão, com allowlist de cache e atualização segura

- **Status**: Aceita
- **Data**: 2026-11-02

## Contexto

O app precisa de um service worker para o shell offline e para cache de estáticos e imagens, sem dado autenticado em cache. A entrega é um export estático publicado em S3 com CloudFront (`.github/workflows/deploy-producao.yml`). O default behavior do CloudFront tem TTL de 1 dia, então um `sw.js` antigo poderia ficar servido por 24 h sem cabeçalho próprio.

Restrições: não se pode interromper um treino em andamento com recarregamento, e um SW com defeito precisa de rollback sem reverter o app inteiro.

## Alternativas consideradas

1. **Workbox.** Não adotado. Adicionaria dependência e uma camada de configuração que o plano pede para evitar. A política de cache aqui é pequena e precisa de allowlist explícita.
2. **Denylist de caminhos da API.** Rejeitada. Uma rota nova de API cairia no cache por omissão. Adotou-se o oposto: só o que está listado é cacheado.
3. **`skipWaiting()` automático.** Rejeitada. Uma nova versão tomaria o controle no meio de um treino.
4. **Cachear HTML autenticado.** Rejeitada. O HTML é estático, sem dado de usuário, então o cache de navegação é seguro; respostas de API e de `/auth/*` nunca entram.
5. **Desregistrar o SW sem kill switch.** Rejeitada. Não há como forçar aparelhos que já têm uma versão ruim a parar sem um SW novo que se desinstale.

## Decisão

- **Arquivo**: `frontend/public/sw.js`, escrito à mão, sem dependência. A versão de cache é carimbada no build por `frontend/scripts/build-sw.mjs` com `CACHE_VERSION` = commit (`GITHUB_SHA`), no workflow de deploy.
- **Registro**: só em produção e só com a flag mobile ligada (`frontend/src/lib/pwa/sw-register.ts:10-21`). Com a flag desligada em produção, o cliente desregistra o SW e apaga os caches `musclefit-*`.
- **Allowlist**: só GET da mesma origem, e só os caminhos listados. Nunca cacheia `/api/`, `/auth/`, `/esqueci-senha`, `/redefinir-senha`, requisição com `Authorization`, `Range`, nem `credentials: include` em modo CORS (`frontend/public/sw.js`, `decideStrategy`).
- **Estratégias**: estáticos (`/_next/static/**`, ícones, manifest) em cache-first; navegação em network-first com tempo limite de 4 s e fallback para a página em cache; imagens de cards, equipamentos e academias em stale-while-revalidate. Limites de entradas: 400 estáticos, 40 páginas, 200 imagens.
- **Navegação**: só grava no cache URLs sem query string. O fallback offline usa a chave sem query, então token não vira chave do Cache Storage.
- **Atualização**: sem `skipWaiting()` automático. A nova versão fica em `waiting`. O aviso "Atualização disponível" só aparece quando o trabalho ativo é zero, e a aplicação reconfere essa condição no momento de aplicar (`frontend/src/lib/pwa/update-gate.ts`). Sem provedor de trabalho registrado, o aplicativo trata a situação como ocupado.
- **Recarregamento**: nunca em `/modo-treino` (`frontend/src/lib/pwa/constants.ts`, `PWA_SILENT_PATH_PREFIXES`, lido por `isSilentPath` em `install-banner-policy.ts`).
- **Cabeçalhos**: `sw.js` e `manifest.json` sobem com `Cache-Control: no-cache`, depois do `s3 sync`, no workflow de deploy (`.github/workflows/deploy-producao.yml`, em torno das linhas 617–641).
- **Kill switch**: entrada `pwa_kill_switch` do workflow (linha 49) publica um `sw.js` autodestrutivo que apaga os caches `musclefit-*` e se desregistra, sem recarregar abas.

## Trade-offs

- **Ganho**: nenhuma API e nenhum dado de auth em cache; a atualização não derruba um treino; rollback não exige reverter o app.
- **Custo**: a lógica de "ocupado" depende de os provedores estarem registrados. Enquanto a fila não registrar um provedor, a atualização nunca é oferecida. Isso é intencional, mas significa que o aviso pode demorar a aparecer.
- **Custo**: o kill switch com a flag ligada não basta. O app registra de novo o `sw.js` autodestrutivo a cada abertura e ele se desregistra de novo. O rollback completo é kill switch mais flag desligada (documentado no próprio `sw.js`).
- **Custo**: a CSP está em modo report-only, e `worker-src 'self' blob:` está em `infra/cloudfront-functions/security-headers.js`, linha 54. Não há enforcement.

## Consequências

- Qualquer nova rota de API ou de autenticação não precisa de ação no SW para ficar fora do cache. Uma nova rota de estáticos ou de imagens precisa ser adicionada à allowlist, de propósito.
- O teste de política em `frontend/src/lib/pwa/sw-policy.test.ts` e os E2E em `frontend/e2e/pwa/service-worker.spec.ts` e `frontend/e2e/workout-resilience/sw-update.spec.ts` cobrem o comportamento. Esses E2E não rodam no CI (ver o guia operacional).
- Validação de atualização em iPhone real e instalação na tela de início continuam pendentes.
