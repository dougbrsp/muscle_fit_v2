# Plano de implementação mobile-first + PWA (MuscleFit)

Status: Fase 0 (auditoria e plano). Nenhum código de produto alterado.
Base: `origin/develop` @ `f974afce` (2026-11-01). Especificação: anexo "MUSCLEFIT — EVOLUÇÃO MOBILE-FIRST COM PWA SEM ALTERAR O DESKTOP".
Regra permanente: motores = fonte única da verdade (`docs/motores/FONTES_DA_VERDADE.md`; guarda `api/tests/unit/test_guarda_fontes_da_verdade.py`). O frontend não cria regra nem limiar.

## 1. Comandos reais e baseline

Todos em `frontend/`.

| Ação | Comando |
|---|---|
| Instalar | `npm ci` |
| Dev (0.0.0.0:3000, sem `output: export`) | `npm run dev` |
| Lint | `npm run lint` (`next lint`) |
| Typecheck | `npm run typecheck` (`tsc --noEmit`) |
| Testes unitários | `npm test` (`vitest run`; só unit, sem E2E) |
| Build (export estático em `out/`) | `npm run build` |
| Prévia fiel à produção | `npm run preview:prod` (build + `scripts/serve-export.mjs`) |
| Smoke do export | `npm run smoke:export` |
| Ícones PWA | `npm run icons:generate` |

Baseline no worktree `docs/mobile-plano` (develop @ `f974afce`, node_modules por junction):

| Comando | Exit | Observação |
|---|---|---|
| `npm run lint` | 0 | 15 warnings preexistentes (`react-hooks/exhaustive-deps`, ex.: `VolumeGrupamentosClient.tsx:740`) |
| `npx tsc --noEmit` | 0 | limpo |
| `npx vitest run` | 0 | 153 arquivos, 2609 testes, ~111 s |
| `npx next build` | 0 | `/` 127 kB, `/dashboard` 206 kB, `/modo-treino` 295 kB first load; shared 88,4 kB |

Esses números são o orçamento de regressão de bundle desktop: nenhuma frente pode aumentar o first load de `/dashboard` nem o shared de 88,4 kB (views mobile entram por `next/dynamic`).
Não há screenshots desktop nem E2E (sem Playwright/Cypress no repo). Capturar screenshots de referência é tarefa da frente de QA (seção 8).

## 2. Arquitetura observada

### 2.1 Stack e entrega
- Next.js 14.2 export estático (`frontend/next.config.mjs`: `output: 'export'` fora do `next dev`, `trailingSlash: true`, `images.unoptimized`). Sem middleware, sem rewrites, sem cabeçalhos configuráveis pelo Next (não existe servidor).
- Mantine 7 + TanStack Query 5 (`frontend/src/app/providers.tsx`). Tema escuro fixo, tokens em `frontend/src/theme.ts` (`BRAND`, `WORKOUT_MODE_TOKENS`). `BRAND` é lido por teste do backend (`test_cores_sao_os_tokens_reais_do_theme_ts`): não alterar chaves.
- Entrega: `aws s3 sync out/ s3://musclefit-frontend-assets --delete` + invalidação CloudFront por lotes (`.github/workflows/deploy-producao.yml:571`), função `infra/cloudfront-functions/spa-index-rewrite.js` para rotas dinâmicas. O `s3 sync` não define `Cache-Control`; o default behavior usa `Managed-CachingOptimized` (`infra/cloudfront.tf`, TTL default 1 dia) e depende da invalidação a cada deploy. Há CSP em modo report-only no default behavior.

### 2.2 Route groups (`frontend/src/app/`)
- `(app)/**` (49 páginas): `layout.tsx` + `components/AppShellLayout.tsx` (Mantine `AppShell`, navbar colapsável, `Burger hiddenFrom="sm"`, `useMediaQuery(min-width: theme.breakpoints.sm)`). Breakpoint Mantine `sm` = 48em (768 px). Ou seja, já existe uma casca responsiva com menu lateral que vira drawer abaixo de 768 px.
- `(fullscreen)/modo-treino/page.tsx`: layout próprio sem AppShell (`(fullscreen)/layout.tsx`, `useSessionGuard` = `GET /auth/me`). Foi concebido para uso no celular (`100dvh`, `env(safe-area-inset-bottom)` em `workout-mode.module.css`, `WorkoutActionBar`, `ui/*`). O "novo visual do Modo Treino" (PR #606–#611) já é mobile-first: 10,4 mil linhas em 31 `.tsx`.
- Públicas: `/` (login), `/esqueci-senha`, `/redefinir-senha`.

### 2.3 Mapa de funcionalidades -> rotas
| Funcionalidade | Rota / arquivo |
|---|---|
| Home/dashboard | `(app)/dashboard/page.tsx` + subpáginas (`progressao`, `recovery`, `musculos`, `corpo`, `treinos`, `exercicios/[exerciseId]`, `insights`, `relatorios`, `cardio`, `ciclo`, `checkin`, `contexto`, `engine`, `data-quality`) |
| Planejamento / fichas / Modelos | `(app)/planos-treino[/id]`, `(app)/modelos-treino[/id]` (builder com painéis flutuantes, desktop-heavy), `preferencias-treino`, `metas-ativacao` |
| Briefing do exercício | `modo-treino/ExerciseBriefingScreen.tsx`, `IntensiveBriefing.tsx`, `lib/exercise-briefing*.ts` (consome `api/app/exercise_briefing.py`) |
| Execução / séries | `modo-treino/PlanExecutionScreen.tsx` (3068 linhas), `SetLoggerCard.tsx`, `FreeformScreen.tsx`, `ModeSelectScreen.tsx` |
| Descanso | `RestTimer.tsx`, `lib/workout-mode-rest-timer.ts`, `IntensiveRest.tsx` |
| Técnicas avançadas / Intensivo | `Intensive*.tsx`, `lib/workout-mode-intensive.ts`, `lib/advanced-techniques.ts`, `workout-mode-advanced-*.ts` |
| Biblioteca / cards ilustrados | `(app)/exercicios`, `ExerciseCardViewer.tsx`, `lib/exercise-cards*.ts`; assets em `/exercise-cards/*` (CloudFront, imutável 1 ano) |
| Histórico | `(app)/registro-exercicios[/activityId]`, `(app)/treinos` |
| Progressão | `(app)/dashboard/progressao`, `lib/exercise-progress.ts` |
| Perfil/configurações | `(app)/perfil`, `indicadores-fisicos`, `academias`, `intervals-icu`, `usuarios` (admin) |
| Login/logout | `app/LoginClient.tsx`, `lib/auth.ts`, logout em `AppShellLayout.tsx:279` |
| Nutrição, cardio, relatórios, admin | `(app)/nutricao/**`, `cardio`, `agente-cardio`, `relatorios`, catálogos admin (P2) |

### 2.4 Estado, séries, cronômetros (leitura dirigida)
- **Estado da sessão de treino**: `localStorage` chave única `musclefit.workout-mode-session` (+ `musclefit.workout-mode-parked-session`) em `lib/workout-mode-storage.ts`; tipo `StoredWorkoutModeSession` em `lib/workout-mode-types.ts:174` (skips, overrides de equipamento, warmup, `briefed_keys`, `rest_timer`, progresso das sub-etapas avançadas). O estado vive em `PlanExecutionScreen`/`WorkoutModeClient` (React state + `saveStoredSession`). Já sobrevive a reload. A sessão é reidratada pelo servidor (`GET /activities/{session_id}`) + `mergeResumedSession` (`lib/workout-mode-plan-flow.ts`).
- **Lógica pura compartilhada**: `lib/workout-mode-core/` (espelho gerado para o relógio, `workout-mode-core.golden.json`, script `scripts/build-workout-core.mjs`). Reutilizar, não duplicar.
- **Gravação de séries** (`api/app/workout_sessions.py`):
  - `POST /workout-sessions/start` (:488), `POST .../{id}/exercises` (:770), `POST .../{id}/exercises/{eid}/sets` (:1225), `PATCH .../sets/{n}` (:1452), `POST .../{id}/finish` (:1561), `PATCH .../{id}/meta` (:623).
  - **Idempotência já existe para série e exercício**: `SessionSetIn.client_key` + UNIQUE `uq_exercise_sets_exercise_client_key` e `uq_exercises_activity_client_key`; replay devolve o registro original (`_find_set_by_client_key`, `_duplicate_set_response`, :353–:380, :1225–:1260). O frontend envia `client_key: setClientKeyFor(exerciseId, set_number)` (`PlanExecutionScreen.tsx:1301–1461`) e o "Tentar de novo" reenvia a mesma chave (`IntensiveRest.tsx:17`).
  - Lacunas: `start`, `finish`, `PATCH meta` e `PATCH set` não têm chave de idempotência/versão. O PATCH de série não tem controle de concorrência (last-write-wins). A fila offline só pode reenviar com segurança `POST sets` e `POST exercises`.
- **Cronômetros**: já usam timestamp absoluto (`endsAt` epoch ms persistido em `rest_timer`; `lib/workout-mode-rest-timer.ts`, `RestTimer.tsx`, `ExecutionTimer.tsx` com `visibilitychange`; pausa de técnica em `IntensiveApplicationCard.tsx`). `setInterval` só redesenha. Não há Wake Lock; som/vibração com fallback em `timer-feedback.ts`; sem Notification API.
- **RPE-alvo das técnicas**: vem do backend (`rpe_target_source: 'protocol'` enviado em `workout-mode-intensive.ts:734/763/873`; o servidor resolve a meta efetiva, `rpe_target_effective`, e o dono é `effective_intensity` / `advanced_techniques.py`). O cliente não pede RPE a cada série.
- **Incrementos de carga válidos**: `load_increment_kg` do equipamento vem da API; `lib/workout-mode-load-suggestions.ts` usa `loadStep()` com fallback `FACTORS.default_load_step_kg` (espelho gerado). Ajuste rápido em `QuickLoadAdjustment.tsx`.
- **Fadiga/recuperação no treino**: `lib/workout-mode-fatigue-view.ts` + `MuscleFatigueChip.tsx`, só apresentação; dono `api/app/domain/prior_fatigue.py` (`current_group_fatigue`), lido por `intelligence/session_fatigue_reader`.
- **Trocar/pular exercício**: `SkipExerciseModal.tsx`, `EquipmentSubstituteModal.tsx`, `lib/exercise-swap-options.ts`; estado local em `skipped_exercise_ids`, `jump_target_exercise_id`, `equipment_overrides`.

### 2.5 Mobile existente
- `AppShellLayout`: navbar vira drawer + Burger abaixo de 768 px. Já há `useMediaQuery` em ~17 arquivos (admin/builder).
- Modo Treino: já tem safe-area inferior, `100dvh`, alvos de toque. **Não há `viewport-fit=cover`** em `layout.tsx` (`viewport` só tem `width/initialScale/themeColor`), logo `env(safe-area-inset-*)` retorna 0 no iPhone hoje: o CSS existente não está efetivo. Corrigir é mudança global de `<meta viewport>` e precisa de prova de regressão desktop (inócua em desktop).
- `(app)/**` fora do Modo Treino: responsivo "espremido" (tabelas, painéis flutuantes do builder). Sem bottom nav. Sem teclado virtual (`visualViewport`) tratado.

### 2.6 PWA existente
- Existem: `public/manifest.json` (`standalone`, `start_url`/`scope` `/`, ícones 180/192/512, maskable reaproveitando o 512 comum), `apple-touch-icon.png`, `metadata.manifest` e `appleWebApp` em `app/layout.tsx`, `scripts/generate-pwa-icons.mjs` (a partir de `public/logo.svg`).
- Não existem: service worker, registro de SW, cache offline, ícone maskable com safe-zone dedicado, `viewport-fit`, onboarding de instalação, E2E (Playwright), feature flags (só `NEXT_PUBLIC_*` de API e Turnstile em `lib/config.ts`), Wake Lock.
- Cabeçalhos: o Next export não os emite; o `sw.js` precisará de `Cache-Control: no-cache` (ver 3.7).

### 2.7 Autenticação
- Cookie httpOnly (`credentials: 'include'`, `lib/api.ts:157`); o token nunca fica em JS (`lib/auth.ts`). `clearSession()` limpa apenas chaves legadas (`musclefit.session_token`, usuário legado). Logout: `POST /auth/logout` + `clearSession()` (`AppShellLayout.tsx:279–284`). 401 -> `clearSession` + redirect (`api.ts:63`).
- **Risco de vazamento entre contas hoje**: `musclefit.workout-mode-session` e `...-parked-session` não têm escopo de usuário e **não são apagadas no logout** (grep: só `WorkoutModeClient.tsx:107` chama `clearStoredSession`). Outro usuário logando no mesmo aparelho pode ver/retomar estado local alheio. A frente B corrige (3.5).

## 3. Decisões

### 3.1 Seleção de layout (sem UA)
- Hook `useLayoutMode()` no cliente baseado em `matchMedia` (`(max-width: 767.98px)` e `(pointer: coarse)` como sinal auxiliar, não decisivo). Sem User-Agent.
- **Breakpoint**: smartphone = largura <= 767 px, coerente com o `sm` do Mantine (768 px) que o `AppShell` já usa. Isso evita estado híbrido: o corte do shell e o da view mobile coincidem.
- **Tablet (768–1023 px) e paisagem de celular grande**: recebem o layout desktop atual (decisão explícita, fallback seguro); o Modo Treino, que já é mobile-first, continua igual nessas larguras. A paisagem de celular <= 767 px mantém mobile; larguras >= 768 px caem em desktop. Documentar e testar 768/820/1024.
- Renderização: o servidor exporta HTML estático, então o primeiro paint não sabe a largura. Para não haver flash nem hydration mismatch: o componente seletor devolve `null`/skeleton neutro até montar, e só então escolhe a view; a view mobile é `next/dynamic` (`ssr: false`) para não pesar o bundle desktop. Desktop segue renderizando o componente atual sem passar pelo seletor quando a flag está desligada.
- **Estado durável acima das views**: o estado e os controllers sobem para um `WorkoutSessionProvider` por cima do seletor; as views (desktop existente e mobile nova) são apenas apresentação do mesmo contexto. Trocar viewport/rotação desmonta a view, não o provider, e nunca reinicializa a sessão nem duplica instâncias de persistência. Em `/modo-treino` (fullscreen) o ponto de montagem é `WorkoutModeClient`; a extração de estado de `PlanExecutionScreen` (3068 linhas) é incremental: primeiro só o que persiste (sessão + fila + timers), mantendo o desktop idêntico.

### 3.2 Feature flag
- `NEXT_PUBLIC_MOBILE_UI` (build-time) + override por usuário no cliente via `localStorage` `musclefit.flag.mobile-ui` (`on`/`off`) e query `?mobile=0|1` (só para QA). **Default: desligada em produção** até o lançamento; ligada em DEV. Resolver em `lib/mobile/flags.ts` (módulo único). Flag desligada = comportamento idêntico ao de hoje (nem o seletor monta). Rollback = desligar a variável no build/redeploy ou limpar override; o bundle mobile permanece inerte.
- Nenhuma regra de negócio na flag. Backend: nada nesta etapa (decidido: flag cliente; uma flag por usuário no servidor só se o usuário pedir canário).

### 3.3 Design system mobile
- Derivado de `BRAND`/`WORKOUT_MODE_TOKENS` e dos primitivos já existentes em `modo-treino/ui/` (`MetricTile`, `StatusChip`, `WorkoutActionBar`, `IconAction`, etc.); promover para `src/mobile/ui/` apenas o que for reutilizado fora do Modo Treino, sem mover arquivos existentes. Alvos >= 44 px, `prefers-reduced-motion`, estados loading/erro/vazio/offline/sincronizando/conflito como componentes únicos. Logo apenas via `public/logo.svg` / `GymLogo`; nenhuma imagem gerada.
- Bottom nav com 4–5 itens (Início, Treino, Histórico, Biblioteca, Perfil) lendo a mesma lista de itens do `AppShellLayout` (extrair a lista de rotas para um módulo compartilhado sem mudar o desktop). Itens restantes ficam em "Mais" (nenhuma rota fica inacessível).

### 3.4 Modo Treino mobile
- Ele já é a experiência mobile do produto. A "view mobile" do treino é, portanto, um refinamento da UI existente por viewport (tela de série mais direta, FST-7 com registro durante o descanso curto, redução de carga em um toque via `QuickLoadAdjustment` com `loadStep` vindo da API), não uma segunda implementação. Auditar primeiro quais telas já cumprem os cenários de aceite e só então alterar.
- RPE-alvo, incrementos, fadiga e restrições de trocar/pular: **apenas consumo** do backend (ver 6).

### 3.5 Persistência, fila offline, idempotência
- **IndexedDB** (banco `musclefit`, versionado, migrações em `onupgradeneeded`) via módulo próprio `src/lib/offline/` (sem nova dependência pesada; `idb` só se justificado), com stores: `workout_session`, `op_queue`, `meta`. Todas as chaves prefixadas por `userId` (de `/auth/me`) e por `schemaVersion`.
- **Migração** do `localStorage` atual: na primeira leitura, importar `musclefit.workout-mode-session`/`parked` para o IndexedDB do usuário atual somente se `session_id` pertencer a ele (validar com `GET /activities/{id}`; 404/403 descarta) e depois remover a chave antiga. Mantém `workout-mode-storage.ts` como fachada para o desktop não mudar de comportamento.
- **Fila de operações**: cada op = `{opId (uuid), userId, kind, sessionId, exerciseId, payload, clientKey, createdAt, attempts, status}`. Ordem FIFO por exercício. Só são enfileiráveis offline as ops que o servidor já deduplica: `addExercise` e `addSet` (`client_key`). `start`, `finish`, `PATCH set` e `PATCH meta` **não** entram na fila offline no MVP: exigem conexão e mostram estado "pendente de rede"/erro recuperável, sem prometer garantia (lacuna L1).
- **Estados por série**: `saved_local` -> `syncing` -> `synced` | `error` | `conflict`. O selo "sincronizado" só aparece depois de a resposta 2xx do servidor ser aplicada. A UI distingue "salvo no aparelho" de "sincronizado".
- **Conflito**: replay com mesma `client_key` e payload diferente, ou 409/422 na reconciliação, vira `conflict` com ação do usuário (manter servidor / reenviar); nunca sobrescrever silenciosamente. `PATCH set` offline fica fora do escopo (L2).
- **Quando sincronizar**: ao voltar `online`, no `visibilitychange` para visível, ao reabrir e a cada tick com a app ativa; backoff exponencial; sem Background Sync (iOS).
- **Limpeza por usuário**: no logout e no 401, antes de redirecionar, apagar o banco do usuário (ou, se houver ops `pending`, pedir confirmação explícita; padrão = reter ops pendentes cifradas por usuário NÃO é adotado: ops pendentes bloqueiam o logout com aviso "N séries não sincronizadas"). Troca de conta: banco por `userId`, sem leitura cruzada. `clearSession()` passa a chamar o purge.
- Limite iOS: o Safari pode expulsar IndexedDB após ~7 dias sem uso (ITP) e em pressão de armazenamento; mitigar com `navigator.storage.persist()` quando disponível, sincronização imediata sempre que online e indicador de pendências. Documentar; não prometer garantia.

### 3.6 Cronômetros e segundo plano
- Manter o modelo atual (timestamp absoluto + recálculo no `visibilitychange`). Acrescentar: `pageshow`/`focus` como gatilhos extras e exibição "expirado há mm:ss" quando `endsAt` já passou. Wake Lock opcional em `src/lib/wake-lock.ts` (`navigator.wakeLock`, reaquisição no `visibilitychange`, falha silenciosa com texto "a tela pode apagar"). Sem Notification/push. Som/vibração permanecem opcionais com fallback visual (já em `timer-feedback.ts`).

### 3.7 Service worker e PWA
- `public/sw.js` escrito à mão (sem Workbox, sem dependência), registrado só em produção e só se a flag estiver ligada, escopo `/`. `CACHE_VERSION` embutido no build (script pós-build gera `out/sw.js` com hash dos arquivos estáticos; ou `sw.js` lê `/_next/static/` por runtime caching).
- **Cache**: app shell e estáticos (`/_next/static/**`, `/manifest.json`, ícones, `/logo.svg`), cache-first versionado; HTML em network-first com fallback para o shell em cache; imagens de cards/equipamentos (`/exercise-cards/*`, `/equipment/*`, `/gyms/*`, imutáveis) stale-while-revalidate com teto de entradas.
- **Nunca** cachear respostas da API (origem `api.musclefit.com.br`), `/auth/*`, nem qualquer requisição com `credentials`/`Authorization`. Regra implementada por allowlist de origem+caminho, não por denylist.
- **Atualização sem interromper treino**: novo SW instala em `waiting`; não chama `skipWaiting()` automaticamente. A UI mostra "Atualização disponível" fora de sessão ativa e só aplica (`postMessage SKIP_WAITING` + reload) quando não há `in_progress` local nem ops pendentes. Rollback: versão anterior permanece servida pelo S3 (próximo deploy sem o SW registra `sw.js` vazio que se desinstala e limpa caches versionados); kill switch = flag desligada + `sw.js` "autodestrutivo".
- **CloudFront/S3**: `sw.js` e `manifest.json` precisam de `Cache-Control: no-cache` (ou invalidação garantida). Hoje `s3 sync` não define metadata e o default behavior usa TTL de 1 dia: um `sw.js` velho ficaria até 24 h. Exige alteração em `.github/workflows/deploy-producao.yml` (`aws s3 cp --cache-control` para `sw.js`/`manifest.json`) e/ou na lista de invalidação — mudança de infra/workflow: revisão `revisor-impacto-producao`, PR separado, sem `terraform apply` manual. Escopo/CSP: a CSP é report-only; revisar `worker-src 'self'` na política (`infra/cloudfront.tf`).
- **Manifest**: manter `id/start_url/scope /`, `standalone`, cores; trocar o 4º ícone para um ícone **maskable dedicado** (safe zone 80 %) gerado por `scripts/generate-pwa-icons.mjs` a partir do logo oficial, sem redesenhar; adicionar `orientation: "portrait-primary"` apenas se validado (paisagem deve continuar permitida para tablet: preferir não fixar). `viewport-fit=cover` no `viewport` do layout raiz (global, inócuo no desktop).
- **Onboarding de instalação**: banner discreto, só em iOS Safari fora de standalone (`navigator.standalone`/`display-mode`), texto "Safari -> Compartilhar -> Adicionar à Tela de Início"; dispensável (flag `localStorage`, reaparece após 30 dias); em Android usar `beforeinstallprompt` quando existir; nenhuma promessa de prompt automático.

### 3.8 Autenticação e privacidade offline
- O cookie é httpOnly e não pode ser lido pelo SW; o SW nunca toca em chamadas de API. O `userId` usado para particionar o IndexedDB vem de `/auth/me`. Offline total sem sessão validada: abrir o app mostra o estado local do último usuário apenas se o cookie ainda existir; sem conexão e com 401 na primeira checagem não há como validar: mostrar "sem conexão" (já há `RecoverableError`/`useSessionGuard` com estado `unavailable`, que não desloga).

## 4. Onde a regra de negócio teria de ser criada no frontend (PROIBIDO) e o dono no backend

| Necessidade mobile | Risco | Dono / fonte |
|---|---|---|
| RPE-alvo da técnica (10 vs 9) | tentar derivar do equipamento no cliente | `api/app/domain` / `effective_intensity` + `advanced_techniques.py` (campo `rpe_target_effective` na resposta da série e `rpe_target_source: protocol`). O cliente só envia a fonte e exibe o valor devolvido. |
| Incrementos válidos de carga | arredondar/derivar passo no cliente | `load_increment_kg` do equipamento (API) e `session_loads.round_to_step`; o espelho `FACTORS` em `lib/workout-mode-load-suggestions.ts` já tem fixture de paridade. Sem novo limiar. |
| Redução de carga rápida em FST-7/Drop | cliente decidir quanto reduzir | Sugestão de carga: `next_set_load_rules.py` / `workout-mode-next-set.ts` (espelho com paridade). O toque só aplica o próximo passo válido já calculado. |
| Fadiga atual no treino | recalcular em JS | `domain/prior_fatigue.py` via leitura do servidor (`workout-mode-fatigue-view.ts` só mapeia status->visual). |
| Pular/trocar exercício e restrições | validar elegibilidade no cliente | `exercise_swap_options` / `workout_plan_edit_rules.py` / `equipment_exercises.py`; o cliente só consome a lista permitida. |
| Duração/expiração do descanso | número fixo no cliente | `resolveRestSeconds` / dono do descanso (já vem pronto); fila offline não altera. |
| Status "sincronizado" de série | inferir sucesso | Só a resposta 2xx do servidor; não é regra de domínio. |
| Contagem/resumo de sessão offline | somar séries no cliente para mostrar volume/progresso | Não exibir métricas derivadas calculadas localmente; mostrar apenas contagem de séries locais pendentes. Valores de progressão/volume só do servidor. |

Nenhum novo número, limiar, fórmula ou default pode entrar em `frontend/src/mobile` ou `frontend/src/lib/offline`. Se o aceite exigir um valor que o backend ainda não expõe, abrir tarefa de backend no módulo dono (e atualizar `FONTES_DA_VERDADE.md`, guarda verde, "Impacto em produção").

## 5. Lacunas de backend identificadas

- L1. `POST /workout-sessions/start`, `POST .../finish`, `PATCH .../meta` sem chave de idempotência: reenvio pode criar segunda sessão ou finalizar duas vezes (finish provavelmente tolerante; não confirmado). Proposta: `client_key` em `start` (UNIQUE por usuário, migração) — avaliar necessidade só se a fila offline precisar iniciar treino sem rede (fora do MVP).
- L2. `PATCH .../sets/{n}` sem versão/ETag: sem detecção de edição obsoleta. Fora da fila offline no MVP.
- L3. Sem endpoint "resumo da sessão em andamento do usuário" já otimizado para restauração (usa `GET /activities/{id}`): suficiente; confirmar com QA.
- L4. `set_number` pode ser fornecido pelo cliente; duas ops offline com o mesmo `set_number` e `client_key` diferentes falham na UNIQUE de `set_number` (não no `client_key`). A chave atual é derivada de exercício+número, então o caso é replay legítimo; confirmar com teste de contrato (`api/tests`) que a resposta de replay é igual e que colisão de número devolve erro recuperável, sem corromper. Se faltar, ajuste pequeno no backend (guardiao-motores não envolvido: não é regra de treino).

Nenhum bloqueio real para iniciar. L1/L2 são riscos aceitos e documentados no MVP.

## 6. Riscos

1. Desktop regredir por mudança global (`viewport-fit=cover`, extração de estado de `PlanExecutionScreen`, `AppShellLayout`). Mitigação: screenshots Playwright 1280/1440 antes/depois, diff por rota, flag OFF = comportamento idêntico, orçamento de bundle (seção 1).
2. `PlanExecutionScreen` com 3068 linhas: extrair estado sem alterar comportamento. Mitigação: extração mecânica atrás de testes existentes (`SetLoggerCard`, `RestScreen`, `workout-mode-*.test.ts`) e golden `workout-mode-core.golden.json`.
3. Divergência entre `localStorage` e IndexedDB (duas fontes). Mitigação: fachada única e migração com remoção; nunca dois escritores.
4. Service worker preso em versão antiga (CloudFront 1 dia sem `no-cache` em `sw.js`). Mitigação: 3.7 + kill switch.
5. iOS: IndexedDB pode ser evictado; sem Background Sync, sem notificação precisa; Wake Lock inconsistente. Documentado.
6. Duplicidade de série: coberta por `client_key`; teste de integração de contrato necessário (replay, concorrência, offline->online).
7. Vazamento entre contas no aparelho compartilhado (hoje presente). Corrigir na frente B (purge no logout/401 + partição por usuário).
8. Escopo CSP/`worker-src` e cabeçalhos exigem PR de infra (revisor de impacto em produção). Sem `terraform apply` manual.
9. Custo do Actions: respeitar regras (sem `ci-completo` na abertura; integração MySQL só na promoção; mudanças de workflow/infra sobem em PR próprio).

## 7. Critérios de aceite (resumo, por entrega)

- Desktop: lint/tsc/vitest/build iguais à baseline; screenshots de referência 1280/1440 sem diferença (ou diferença explicada); flag OFF não monta nada novo; bundle de `/dashboard` e shared não crescem.
- Mobile: 320/375/390/414/430 retrato e paisagem sem overflow horizontal; mesmo URL; rotação/redimensionar não perde nem duplica sessão/série; todas as rotas acessíveis via bottom nav/"Mais".
- Treino: iniciar -> séries -> descanso -> background -> voltar com tempo correto ou "expirado"; offline -> online sincroniza uma vez (teste com `client_key`); reabrir navegador restaura; falha de API mostra pendência/erro, nunca "sincronizado" falso; FST-7 sem RPE manual por série; carga efetiva gravada é a usada.
- PWA: manifest válido, SW registrado só em prod com flag, sem cache de API/auth, atualização não interrompe sessão, logout/troca de conta apaga dados locais do usuário anterior, onboarding iOS discreto.
- Evidência manual obrigatória em iPhone real (PWA instalada, teclado, safe areas, background) — registrar como pendente, nunca como feito sem validação.

## 8. Sequência de PRs e ownership exclusivo de arquivos

Todos os PRs miram `develop`; branch a partir de `origin/develop`; revisores locais antes do 1º push (`guardiao-motores` quando tocar domínios de motores — esperado só no consumo; `revisor-impacto-producao` para infra/workflow/CSP); testes unitários direcionados; sem `ci-completo` na abertura.

### PR0 (este) `docs/mobile-plano`
Dono: `docs/mobile/MOBILE_IMPLEMENTATION_PLAN.md`.

### Pré-requisito comum (PR1, curto, sequencial, antes das frentes)
Fundações compartilhadas e de baixo risco, para as três frentes não disputarem arquivos:
- `frontend/src/lib/mobile/flags.ts`, `frontend/src/lib/mobile/layout-mode.ts` (+ testes): flag e `useLayoutMode()` (sem consumi-los ainda).
- `frontend/src/app/layout.tsx`: `viewport-fit=cover` (único toque no layout raiz; as demais frentes NÃO editam este arquivo).
- Prova negativa de desktop: scripts/Playwright de screenshots (devDependency `@playwright/test`, `frontend/playwright.config.ts`, `frontend/e2e/desktop-baseline/**`). Dono: QA. Mudança de `package.json`/lockfile só neste PR; as frentes não mexem em `package.json` (se precisarem de dependência, pedem ao dono do PR1 ou fazem PR dedicado antes).

### Frente A — layout, flag, design system, navegação (depende de PR1)
Ownership exclusivo:
- `frontend/src/mobile/**` (layout, `ui/`, `nav/`, telas P1/P2 mobile)
- `frontend/src/components/AppShellLayout.tsx` (somente: extrair a lista de rotas para `frontend/src/lib/nav-items.ts` e montar o seletor atrás da flag; desktop idêntico)
- `frontend/src/lib/nav-items.ts`
- `frontend/src/app/(app)/layout.tsx`
- `frontend/src/theme.ts` (somente adições em novo export `MOBILE_TOKENS`; sem tocar `BRAND`)
Entrega em PRs: A1 provider/seletor + shell mobile + bottom nav; A2 P1 (dashboard, histórico, biblioteca, perfil); A3 P2 (demais telas com fallback utilizável).

### Frente B — persistência, fila offline, timers (depende de PR1; independente de A)
Ownership exclusivo:
- `frontend/src/lib/offline/**` (IndexedDB, migração, fila, sync engine, purge por usuário)
- `frontend/src/lib/workout-mode-storage.ts` (vira fachada) e `frontend/src/lib/workout-mode-types.ts` (apenas tipos de persistência)
- `frontend/src/lib/wake-lock.ts`, `frontend/src/lib/workout-mode-rest-timer.ts` (somente os gatilhos de recálculo), `frontend/src/app/(fullscreen)/modo-treino/WorkoutModeClient.tsx`, `.../PlanExecutionScreen.tsx` (somente integração do provider de sessão/fila; refatoração mecânica), `.../SetLoggerCard.tsx`, `.../IntensiveRest.tsx`, `.../RestTimer.tsx`
- `frontend/src/lib/auth.ts` (purge no `clearSession`) e `frontend/src/lib/api.ts` (sem mudanças de contrato; só se necessário expor erro de rede tipado)
- Backend (somente se a auditoria confirmar L4): `api/app/workout_sessions.py` + `api/tests/**` correspondente; L1/L2 só por decisão posterior.
Entrega: B1 camada IndexedDB + migração + purge (sem mudar telas); B2 fila + estados de série + indicador; B3 integração no Modo Treino + timers/wake lock/expirado.
Observação de conflito: A e B ficam em `modo-treino/` em momentos diferentes: A só **cria** telas em `src/mobile/**` e não edita `modo-treino/**`; B é o único dono de `modo-treino/**` enquanto durar. Refinos de UI do treino (3.4) são feitos por B ou, depois, por A3 em PR sequencial.

### Frente C — PWA: manifest, SW, onboarding (depende só de PR1; paralelizável com A e B)
Ownership exclusivo:
- `frontend/public/manifest.json`, `frontend/public/sw.js`, `frontend/public/icons/**`, `frontend/scripts/generate-pwa-icons.mjs`, `frontend/scripts/build-sw.mjs`
- `frontend/src/lib/pwa/**` (registro do SW, hook de atualização, detecção standalone/iOS) e `frontend/src/components/pwa/**` (banner de instalação, aviso de atualização)
- `frontend/src/app/providers.tsx` (somente: montar `PwaProvider`)
- Infra (PR separado C-infra, `revisor-impacto-producao` obrigatório): `.github/workflows/deploy-producao.yml` (Cache-Control de `sw.js`/`manifest.json` e invalidação) e, se necessário, `infra/cloudfront.tf` (CSP `worker-src`). Sem `terraform apply` manual.
Entrega: C1 manifest/ícones/banner iOS; C2 SW + atualização segura (depende de B para saber "há sessão ativa/ops pendentes" via interface `hasActiveWork()` definida em PR1 como contrato em `frontend/src/lib/mobile/active-work.ts`; B implementa, C consome); C-infra.

### Paralelismo
- PR0 -> PR1 (sequencial). Depois **A, B e C em paralelo** sem sobreposição de arquivos (tabelas acima).
- Dependências cruzadas: C2 consome `hasActiveWork()` (contrato criado em PR1, implementado por B1; até lá retorna `false` por padrão => C2 não pode ser ligado em produção antes de B3). A3 depende de A1. B3 depende de B2 depende de B1.
- Fecho (sequencial, após A/B/C): D — QA e2e: Playwright (Chromium + WebKit) nas larguras 320–430/768/1024/1280/1440, cenários de treino resiliente com `context.setOffline`, regressão visual desktop; revisão independente (`code-reviewer`, `security-reviewer`, `test-engineer`); docs finais (flag, rede local/HTTPS, validação iPhone, limites iOS, conflitos, atualização do SW) em `docs/mobile/`; ADR em `docs/adr/` para a decisão de IndexedDB + fila e para a de SW.

### Ordem de liberação
1) PR0, PR1; 2) A1, B1, C1 em paralelo; 3) A2, B2, C2 e C-infra; 4) A3, B3; 5) D; 6) ligar a flag em DEV, validar em iPhone real; 7) produção só com aval do usuário (flag default OFF até lá).

## 9. Itens fora de escopo desta fase
Nenhum código de produto, infra ou deploy. Backend só se L4 for confirmado. Melhorias descobertas fora do escopo (ex.: warnings de lint preexistentes) vão para o backlog, não para estes PRs.
