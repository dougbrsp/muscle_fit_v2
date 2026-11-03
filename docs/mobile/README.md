# Guia operacional mobile e PWA (MuscleFit)

Este guia descreve a frente mobile-first e PWA como ela está no código. Decisões: [docs/adr/README.md](../adr/README.md). Plano e status: [MOBILE_IMPLEMENTATION_PLAN.md](MOBILE_IMPLEMENTATION_PLAN.md). Backlog: [BACKLOG_MOBILE.md](BACKLOG_MOBILE.md).

Os comandos abaixo vêm de `frontend/package.json` e das configs de teste. Nada aqui foi executado por este documento; a validação de cada comando é do agente que rodar o teste.

## 1. Arquitetura real

| Peça | Arquivo | Papel |
|---|---|---|
| Flag | `frontend/src/lib/mobile/flags.ts` | Fonte única da flag da UI mobile |
| Seletor por largura | `frontend/src/lib/mobile/layout-mode.ts` | `useLayoutMode()`, corte `(max-width: 47.99em)` (menor que 768 px) |
| Ponto de entrada | `frontend/src/components/AppShellLayout.tsx` | Flag desligada: `DesktopAppShell` direto (linhas 142–143) |
| Seletor mobile | `frontend/src/mobile/MobileLayoutSelector.tsx` | Carregado com `lazy` só com a flag ligada |
| Shell mobile | `frontend/src/mobile/layout/MobileShell.tsx` | Bottom nav e folha "Mais" (`MoreSheet.tsx`) |
| Abas | `frontend/src/mobile/layout/primary-nav.ts` | Abas fixas; o resto vai para "Mais" |
| Telas próprias | `frontend/src/mobile/screens/registry.ts` | Hoje só `/dashboard` (`MobileHome.tsx`) |
| Modo Treino | `frontend/src/app/(fullscreen)/modo-treino/` | Layout próprio, sem `AppShellLayout` |
| Fila offline | `frontend/src/lib/offline/` | IndexedDB, fila, purge, lock entre abas |
| Service worker | `frontend/public/sw.js`, `frontend/scripts/build-sw.mjs` | Cache com allowlist; carimbo e kill switch |
| Registro e atualização | `frontend/src/lib/pwa/` | Registro, `hasActiveWork()`, gate de atualização |

Regras de comportamento:

- **Desktop e tablet**: com a flag ligada, largura de 768 px ou mais cai no layout desktop atual. Paisagem de celular acima de 768 px também cai no desktop (MB-9).
- **Mesma URL**: não há rota duplicada nem detecção por User-Agent.
- **Remontagem**: cruzar 768 px remonta a página. URL, cache do TanStack Query e sessão de treino (storage) sobrevivem; estado local de página (filtros, abas) se perde (MB-10).
- **Modo Treino**: não passa pelo seletor. Já é a experiência de celular do produto (plano, seção 3.4); não há segunda implementação de treino.
- **Tela inicial mobile**: `/dashboard` usa `MobileHome`, com os mesmos hooks e chave de cache da visão geral. Todo número vem do backend. O painel completo continua disponível em "Ver visão geral completa".
- **Viewport**: `viewportFit: 'cover'` em `frontend/src/app/layout.tsx`, linha 30. Afeta o desktop só de forma inócua, porque `env()` vale zero sem safe area.

## 2. Flag mobile: ativar, desativar e rollback

Precedência, da maior para a menor (`flags.ts`):

1. `?mobile=1` ou `?mobile=0` na URL. Persiste o override no aparelho (uso de QA).
2. `localStorage['musclefit.flag.mobile-ui']` = `on` ou `off`.
3. `NEXT_PUBLIC_MOBILE_UI` no build. Ausente = desligada no build de produção e LIGADA no `next dev` (NODE_ENV=development); valor explícito sempre vale.

Estado atual, verificado no repositório: `NEXT_PUBLIC_MOBILE_UI` não aparece em nenhum workflow de `.github/` nem no restante do repositório. Portanto o build de produção a lê como ausente, ou seja, desligada. (Desatualizado: o workflow de deploy já liga em produção e, desde o ajuste pós-teste da nutrição, o `next dev` liga por padrão.)

Como ligar em desenvolvimento local, sem alterar nada no repositório:

- PowerShell: `$env:NEXT_PUBLIC_MOBILE_UI='1'; npm run dev` (em `frontend/`).
- Ou abrir a aplicação com `?mobile=1` uma vez. O override fica salvo no navegador.

Rollback, em ordem de alcance:

1. **No aparelho ou navegador**: abrir a URL com `?mobile=0`, ou apagar a chave com `localStorage.removeItem('musclefit.flag.mobile-ui')` no console. Atenção: `?mobile=0` tem precedência sobre o build, e um override `on` sobrevive à flag de build desligada.
2. **No build**: deixar `NEXT_PUBLIC_MOBILE_UI` ausente e publicar de novo. O bundle mobile fica inerte.
3. **Service worker**: o workflow de deploy tem a entrada `pwa_kill_switch` (`.github/workflows/deploy-producao.yml`, linha 49). Ela publica o `sw.js` autodestrutivo, que apaga os caches `musclefit-*` e se desregistra. Rollback completo = kill switch mais flag desligada. Só o kill switch com a flag ligada não basta: o app registra de novo e o SW se desregistra de novo.

Liberar a flag em produção exige aval explícito do usuário (plano, seção 8, "Ordem de liberação").

## 3. Desenvolver e testar em desktop e smartphone

Comandos reais (`frontend/package.json`):

- `npm ci` instala.
- `npm run dev` roda `next dev -H 0.0.0.0 -p 3000`. Sem `output: export`.
- `npm run lint`, `npm run typecheck`, `npm test` (Vitest, só unitário), `npm run build`.
- `npm run preview:prod` faz build e serve o export pelo `scripts/serve-export.mjs`. É a forma fiel de testar o service worker.

Desktop: abrir `http://localhost:3000` e usar o modo responsivo do navegador (ex.: 390 px de largura) para o layout mobile.

Smartphone na rede local: `npm run dev` escuta em todas as interfaces, então o celular pode abrir `http://<IP-do-computador>:3000`. Limitações:

- Este endereço não é contexto seguro. O service worker exige `localhost` ou HTTPS, então o SW não registra. A UI mobile, a fila em IndexedDB e o layout funcionam normalmente.
- Em `next dev` o SW nunca registra, porque o registro exige `NODE_ENV=production` (`frontend/src/lib/pwa/sw-register.ts`). Para testar SW use `npm run preview:prod` em `localhost`.
- A API precisa estar acessível a partir do celular.

Opções para HTTPS na rede local. Nenhuma delas está no repositório; são sugestões que exigem instalação por conta do desenvolvedor:

- Sugestão: certificado local com mkcert e um proxy HTTPS na frente de `localhost:3000`.
- Sugestão: túnel HTTPS temporário apontando para `localhost:3000`.

## 4. Testes

Antes de qualquer E2E, gerar o export de teste com `node e2e/build.mjs` (build com `NEXT_PUBLIC_API_BASE_URL=http://mock-api.test`). Os comandos abaixo usam o `out/` gerado por esse passo.

Instalação única dos navegadores do Playwright: `npx playwright install chromium` (e `webkit` se for usar `e2e:webkit`).

| Suíte | Comando | Porta | Observação |
|---|---|---|---|
| Baseline desktop | `npm run e2e` | 4179 (`E2E_PORT`) | Build e Chromium 1280 e 1440. `maxDiffPixels: 0`. Goldens gerados no Windows |
| WebKit desktop | `npm run e2e:webkit` | 4179 | Opcional. Oscila no Windows |
| Só os testes | `npm run e2e:test` | 4179 | Reusa `out/` |
| PWA | `npx playwright test -c e2e/pwa/playwright.config.ts` | 4174 (API falsa em 4175) | Manifest, SW, banner de instalação |
| Resiliência do treino | `npx playwright test -c playwright.resilience.config.ts` | 4174 | Inclui `sw-update.spec.ts` (projeto `chromium-sw`) |
| Layout mobile | `npx playwright test -c e2e/mobile-layout/playwright.mobile.config.ts` | 4192 | Rotas 320 px, remontagem |

Pontos de atenção:

- PWA e resiliência usam a mesma porta padrão (4174). Ao rodar em paralelo, defina `E2E_PORT` diferente.
- Os E2E não rodam no CI: não há Playwright em `.github/` (verificado). Ver pendências.
- Os goldens do baseline desktop foram gerados no Windows e só são válidos nesse sistema.

Testes unitários relevantes: `frontend/src/lib/mobile/*.test.ts`, `frontend/src/lib/offline/*.test.ts` (fila, purge, backend, eco, lock entre abas), `frontend/src/lib/pwa/*.test.ts`, `frontend/src/mobile/**/*.test.ts`.

## 5. Validar a instalação no iPhone

Pré-requisito: um build de produção servido em HTTPS, com a flag ligada para o aparelho. Hoje isso não é o padrão da produção. Caminho possível: override `?mobile=1` em um ambiente que sirva o build de produção por HTTPS.

Instalação:

1. Abrir a URL no Safari (não no Chrome do iOS, que não instala a PWA).
2. Tocar em Compartilhar e depois em "Adicionar à Tela de Início".
3. Abrir pelo ícone da tela de início.

Checklist de validação manual (todo item segue pendente até ser executado em aparelho real):

- [ ] Abre em modo standalone, sem a barra do Safari; nome "MuscleFit"; ícone oficial.
- [ ] Safe areas: entalhe no topo e barra inferior do home indicator não cobrem o conteúdo.
- [ ] Teclado: campos de formulário visíveis. A bottom nav pode cobrir campos em formulários longos (MB-8).
- [ ] Rotação: retrato e paisagem. Paisagem acima de 768 px cai no layout desktop (MB-9).
- [ ] Treino offline: entrar em modo avião, registrar série, ver "salvo no aparelho" (não "sincronizado"). Voltar à rede e ver a série sincronizada uma única vez, sem duplicata.
- [ ] Segundo plano: no descanso, sair do app por alguns minutos e voltar. O cronômetro deve estar correto ou mostrar "expirado".
- [ ] Reabertura: fechar o app com treino em andamento e reabrir. O estado recuperável deve voltar.
- [ ] Logout com séries pendentes: deve pedir confirmação. Cancelar mantém a sessão e a fila.
- [ ] Troca de conta: os registros locais da conta anterior não aparecem para a nova (ver pendências sobre séries não sincronizadas).
- [ ] Atualização: com uma versão nova publicada, o aviso "Atualização disponível" aparece fora do treino e nunca recarrega dentro de `/modo-treino`.
- [ ] Convite de instalação: aparece só em iOS Safari fora do standalone; dispensado, volta depois de 30 dias.

## 6. Limitações reais do iOS e o que não é suportado

Limitações:

- Não há prompt automático de instalação no iOS. A instrução é manual (Compartilhar, Adicionar à Tela de Início).
- Não há Background Sync. A sincronização acontece com a aplicação ativa: ao voltar online, em `visibilitychange`, `pageshow`, `focus` e a cada 20 s com a aba visível.
- IndexedDB e Cache Storage podem ser apagados pelo sistema em pressão de armazenamento e, no Safari, após período sem uso. `navigator.storage.persist()` é pedido quando disponível, sem garantia. O app mostra o modo degradado quando não está em IndexedDB.
- Wake Lock é parcial. Se houver suporte, é pedido; se falhar ou for perdido, o aviso "a tela pode apagar" aparece (`frontend/src/lib/wake-lock.ts`, `WakeLockNotice.tsx`).
- Sons, vibração e notificações não são garantidos em segundo plano. Há fallback visual (`timer-feedback.ts`). Não há Notification API nem push.
- Haptics nunca são dependência.

Deliberadamente não suportado:

- Background Sync e push.
- Fila offline para `start`, `finish`, `PATCH` de série e `PATCH` de metadados da sessão (lacunas L1 e L2 do plano). Essas operações exigem rede.
- Navegação offline para rotas nunca visitadas: cai no shell (ver pendências).
- Acesso offline sem sessão validada: depois de 7 dias sem `/auth/me` confirmado, o app exige rede.
- Distribuição nativa, App Store, TestFlight ou build iOS.

## 7. Persistência, recuperação, sincronização e conflitos

Armazenamento (`frontend/src/lib/offline/`):

- Banco IndexedDB `musclefit`, versão 1, stores `workout_session`, `op_queue` e `meta` (`keys.ts`). Toda chave é prefixada pelo `userId`.
- Fallbacks em ordem: `localStorage` com chaves escopadas (degradado), depois memória.
- Migração da chave legada `musclefit.workout-mode-session` só acontece se o servidor confirmar que a sessão pertence ao usuário atual (`GET /activities/{id}`). Sem prova, a chave não é atribuída a ninguém.

Fila de operações:

- Só `addExercise` e `addSet` entram na fila. O backend deduplica pela `client_key` (UNIQUE em `uq_exercises_activity_client_key` e `uq_exercise_sets_exercise_client_key`). Replay devolve `duplicate: true`.
- A fila só marca `synced` depois de 2xx do servidor.
- Estados: `saved_local` → `syncing` → `synced` | `error` | `conflict` (`op-types.ts`).
- Falhas de rede, 408, 429 e 5xx viram `error` com backoff de 2 s, 5 s, 15 s, 30 s e 60 s. 401 interrompe sem confirmar nada. Demais 4xx viram `conflict`.
- Eco: replay com `duplicate: true` cujos valores diferem do enviado vira `conflict` com `echo_mismatch`. Nunca vira `synced`.
- 409 `set_number_conflict` vira `conflict` com `set_number`. A ação "reenviar com outro número" deixa o servidor escolher o próximo número livre.
- FIFO por exercício: uma op com falha trava as seguintes do mesmo exercício.

Retenção:

- Op `synced` há mais de 24 h é removida na reabertura.
- Qualquer op com mais de 30 dias (`OP_TTL_MS`) é removida.
- "Último usuário conhecido" para abrir offline vale 7 dias após `/auth/me` 200 (`scope.ts`).

Duas abas: Web Locks por usuário (`musclefit-offline-<id>`). Uma aba é dona e grava; a outra vira seguidora (`tab-lock.ts`, usado por `use-offline-user.ts`).

Logout e purge:

- `logoutAndRedirect` (`frontend/src/lib/app-logout.ts`) pede confirmação se houver séries não sincronizadas. Só depois chama `POST /auth/logout`, limpa a sessão e redireciona.
- `clearSession` chama `purgeOfflineData`. Caminho do 401 preserva só as ops não sincronizadas, isoladas por usuário (`purge.ts`). Logout explícito, após confirmação, apaga tudo.

## 8. Atualização do service worker

- Registro só em produção com a flag ligada. Com a flag desligada em produção, o cliente desregistra o SW e apaga os caches `musclefit-*`.
- `sw.js` é publicado com `Cache-Control: no-cache` e `CACHE_VERSION` = commit (workflow de deploy).
- Sem `skipWaiting()` automático. A versão nova fica em `waiting`.
- O aviso "Atualização disponível" aparece quando há versão em `waiting` e o trabalho ativo é zero. Sem provedor de trabalho registrado, o app trata a situação como ocupada e nunca oferece a atualização.
- A aplicação reconfere o trabalho ativo no momento de aplicar (`update-gate.ts`).
- Nunca recarrega em `/modo-treino`.
- Allowlist de cache: nenhuma resposta de API, de `/auth/*` ou com `Authorization` entra no cache. Rotas de autenticação (`/esqueci-senha`, `/redefinir-senha`) também ficam fora.

## 9. Pendências e riscos para produção

Validação e prova pendentes:

- Validação manual em iPhone real: PWA instalada, teclado, safe areas, segundo plano. Nada disso foi feito neste trabalho.
- Prova do 409 `set_number_conflict` na RDS Data API: o formato do erro UNIQUE não foi provado no ambiente real.
- Reabertura do IndexedDB sem teste automatizado (BACKLOG).

Decisões a confirmar com o usuário antes de produção:

- Troca de conta confirmada apaga as séries não sincronizadas da conta anterior (`frontend/src/lib/offline/session-store.ts`, linhas 157–163). Esta é uma decisão do código, não do plano, e pode causar perda de dado se o usuário não sincronizou antes de trocar de conta.
- Liberar a flag em produção: só com aval explícito.
- Ligar a flag em DEV: não há configuração versionada.

Testes e CI:

- E2E e goldens fora do CI (P2-4 no backlog). Os goldens são de Windows.
- WebKit desktop é instável; é opcional e não bloqueia.
- Portas padrão de PWA e resiliência coincidem (4174).

Limites funcionais:

- Navegação offline entre rotas nunca visitadas cai no shell.
- Cruzar 768 px remonta a página (MB-10).
- Paisagem acima de 768 px cai no desktop (MB-9).
- Bottom nav sem tratamento de teclado virtual (MB-8).

Riscos de dados, por política de armazenamento do sistema: IndexedDB pode ser apagado pelo iOS. O app mostra indicador de pendências, mas não há garantia de persistência.
