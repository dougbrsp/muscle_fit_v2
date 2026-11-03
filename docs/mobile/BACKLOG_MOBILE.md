# Backlog de refinamento mobile (Frente A)

Origem: sonda das 31 rotas autenticadas no shell mobile (320 e 390 px, flag ligada) e `frontend/e2e/mobile-layout/routes-320.spec.ts`.
Estado: todas as rotas do menu são alcançáveis (bottom nav + "Mais") e nenhuma faz a página rolar na horizontal. Os itens abaixo são refinos, não bloqueios. Nenhum envolve regra de motor.

| # | Tela | Gap | Sugestão |
|---|---|---|---|
| MB-1 | `registro-exercicios`, `exercicios`, `volume-grupamentos`, `planos-treino` | Tabelas largas rolam dentro do container (`TableScrollContainer`), mas sem pista visual: colunas de ação ficam fora da tela e o nome é cortado. | Variante em cartões (lista) em `src/mobile/screens` reutilizando os mesmos hooks/queries, ou sombra/gradiente de borda indicando rolagem. |
| MB-2 | `registro-exercicios` | Filtros (busca, período, academia, condutor) em quatro colunas apertadas; rótulo "Buscar treino" quebra em duas linhas. | Empilhar filtros em 1-2 colunas abaixo de 768 px (muda a página compartilhada: exige prova de desktop idêntico com `npm run e2e`). |
| MB-3 | `dashboard/*` (gráficos Recharts) | Gráficos mantêm a altura de desktop e eixos densos em 320 px. | Altura/ticks responsivos por prop, sem alterar dados. |
| MB-4 | `modelos-treino/[id]` (builder) | Painéis flutuantes e grade de slots são desktop-heavy (`APP_HEADER_HEIGHT = 64` assume o header do desktop; no mobile o header tem 56 px + safe-area). | Tela mobile própria só de leitura/ajuste simples, ou manter desktop-only com aviso. Não verificado com usuário admin (os mocks do e2e usam role `user`). |
| MB-5 | Páginas admin (**tratado pela frente A**: as 6 seções só-desktop somem no iPhone, e o e2e usa mock de admin/PT; restam `usuarios` e as demais para as frentes B/C) (`usuarios`, `equipamentos-exercicios`, `dominio-exercicios`, `regras-carga`, `metas-ativacao`) | Não exercitadas com role admin no e2e; só a rota abrir foi provada pela sonda como `user`. | Mock de admin no e2e e repetir a sonda. |
| MB-6 | Nutrição, Agente de Cardio, Insights | Estados de erro/vazio próprios de cada tela; os componentes `src/mobile/ui` (`LoadingState`, `EmptyState`, `ErrorState`, `OfflineState`) ainda não foram adotados por elas. | Adotar gradualmente ao criar variantes mobile. |
| MB-7 | Shell | ~~Seis abas a 320 px.~~ **Resolvido (mobile v2, frente A)**: tab bar de 5 abas (Início, Treino, Histórico, Evolução, Mais). | Validar em iPhone SE real. |
| MB-8 | Shell | ~~Sem tratamento de teclado virtual.~~ **Tratado (frente A)**: com campo de texto focado a tab bar se esconde e `StickyAction` desce (`layout/keyboard.ts`). | Validar em iPhone real (a decisão por foco não mede a altura do teclado). |
| MB-9 | Paisagem >= 768 px (ex.: iPhone Pro Max 844 px) | ~~Caía no layout desktop.~~ **Resolvido para celular (D2)**: toque e menor lado da tela física < 500 px (`max-device-width/height`, estável com teclado) mantém o layout mobile. | Validar em iPhone Pro Max real. |
| MB-10 | Troca de viewport | Estado local de página (filtros digitados, abas) reinicia ao cruzar 768 px, pois a árvore muda. Dados do servidor ficam no cache do TanStack Query; a sessão de treino fica em storage. | Aceito; se incomodar, elevar filtros para a URL. |

## Decisão A2b (telas P1)

- **Início**: tela mobile própria (`src/mobile/screens/MobileHome.tsx`), escolhida pelo shell via `screens/registry.ts` só com flag ON + largura de celular. Mesmos hooks/queries da visão geral (`useDashboardFilters`, `fetchOverview`, mesma chave de cache); todo texto e número vem do backend. O painel completo continua disponível, recolhido ("Ver visão geral completa").
- **Histórico** (`registro-exercicios`, `dashboard/treinos`) e **Biblioteca** (`exercicios`): mantidos como estão no shell. Já são colunas empilhadas e não rolam a página; o único ponto fraco é a tabela larga e o filtro em 4 colunas do registro (MB-1/MB-2), que exigem mexer em página compartilhada e ficam para PR próprio com prova de desktop idêntico.
- **Perfil**: formulário em coluna única, bom com os alvos de 44 px e fonte de 16 px do shell.
- **Safe-area**: `viewportFit: 'cover'` no layout raiz e `env(safe-area-inset-*)` no header/navbar do AppShell desktop (celular com flag OFF, PWA, paisagem com notch) e no shell mobile. No desktop `env()` vale 0 e o e2e Chromium 1280/1440 segue idêntico. Falta validar o efeito real em iPhone.

## Registros da revisão

- **MB-10 (confirmado)**: cruzar 768 px remonta a página (estado local da página perdido; URL, cache de dados e sessão de treino preservados). Coberto por e2e. Modo Treino não é afetado (rota fullscreen fora do shell).
- **P2-4**: o CI não roda os e2e Playwright (desktop e mobile são locais). Decidir se entram no CI, respeitando a regra de economia de Actions.
- **P3-1**: item do code-reviewer a completar (texto original não chegou à frente A).

Pendente fora do código: validação em iPhone real (PWA instalada, teclado, safe areas).

## Itens adicionados na consolidação final (2026-11-02)

| # | Item | Origem e situação |
|---|---|---|
| P3-1 | Com flag OFF no desktop entra 1 frame extra de loader (`NeutralScreen` enquanto `useMobileUi()===null`), visualmente igual ao loader do guard, sem flash de conteúdo. Para zerar, ler a flag com `useSyncExternalStore` (`getServerSnapshot` null). | `frontend/src/components/AppShellLayout.tsx:142`; registrado pelo code-reviewer |
| P2-4 (complemento) | E2E e goldens no CI Linux. Hoje não há Playwright em `.github/`; os goldens são de Windows. | Verificado por busca em `.github/` |
| P2-5 | WebKit desktop: histórico de instabilidade (viewport, tolerância de 0,2 %). Opcional, não bloqueia. | `frontend/playwright.config.ts` |
| P2-6 | Override de flag por link persistente (`?mobile=0|1` grava no aparelho). Restringir ou não em produção? | `frontend/src/lib/mobile/flags.ts` |
| P2-7 | Reabertura do IndexedDB sem teste automatizado. | `frontend/src/lib/offline/backend.ts` (`openIndexedDbBackend`) |
| P2-8 | Aviso "Salvando série" no layout de descanso do intensivo: revisar texto e enquadramento. | `frontend/src/app/(fullscreen)/modo-treino/IntensiveRest.tsx:43` |
| P2-9 | Prova do 409 `set_number_conflict` na RDS Data API (formato do erro UNIQUE não provado). | Lacuna de validação em ambiente real |
| P2-10 | Navegação offline entre rotas (RSC): só o shell fica em cache; rota nunca visitada cai no shell. | `frontend/public/sw.js` (`networkFirstNavigation`) |
| P1-1 | Validação em iPhone real: PWA instalada, teclado, safe areas e segundo plano. | Pendente fora do código |
| P1-2 | Troca de conta confirmada apaga séries não sincronizadas da conta anterior. Confirmar a política com o usuário antes de produção. | `frontend/src/lib/offline/session-store.ts:157-163` |
| P1-3 | Ligar `NEXT_PUBLIC_MOBILE_UI` em DEV: não há configuração versionada. | Verificado por busca no repositório |

## Mobile v2 (2026-11-03): frente A, fundações e navegação

Entregue: ADR [0004](../adr/0004-navegacao-iphone-e-secoes-so-desktop.md), [DESIGN_IPHONE.md](DESIGN_IPHONE.md), tab bar de 5 abas, hub Evolução (`/dashboard/?hub=evolucao`), folha Mais agrupada, header de 2º nível, tela "Disponível no computador" (D1), paisagem de celular (D2), teclado (MB-8), primitivos em `src/mobile/ui`, registry com carregamento sob demanda e rotas dinâmicas.

| # | Item | Situação |
|---|---|---|
| MB-11 | Hub Evolução compartilha a URL do Início (`/dashboard/?hub=evolucao`). No desktop o parâmetro é ignorado. | Aceito (nenhuma rota nova no desktop) |
| MB-12 | MB-1 a MB-3 e MB-6 (tabelas, filtros, gráficos, estados) | Frentes B e C, com os primitivos novos |
| MB-13 | Teclado decidido por foco, não por `visualViewport` | Validar em iPhone real |
| MB-14 | Rotas só-desktop abertas por link compartilhado (`InsightCard`, `DataQuality`, `SafeMarkdown`, `CardioRecommendationPanel`) caem na tela D1; o CTA existe nesses componentes | Aceito (D1); esconder o CTA exigiria mexer em componente do desktop |

## Mobile v2: frente C (treino, corpo, nutricao e conta)

Duplicacao aceita entre as telas mobile e os clients do desktop (mesmas chaves e endpoints; extraido so o mecanico em `lib/exercise-log-save.ts`, `lib/workout-plan-payload.ts` e `lib/suggested-progression-apply.ts`):

| # | Item |
|---|---|
| C-1 | Queries de catalogo/grupos/equipamento e estado do formulario de lancamento em `TreinoDetalheScreen` vs `WorkoutDetailClient`. |
| C-2 | Mutations de PUT/refresh/cardio/delete em `PlanoDetalheScreen` vs `PlanDetailClient`; job de geracao em `GerarPlanoView` vs `GenerateDraftModal`. |
| C-3 | Catalogo canonico, swap eligibility e breakdown em `PlanExercisesEditor` vs `ExercisesTable`. |

Herdados do desktop (sem mudar a semantica do backend): limpar o RPE grava 0; edicao de exercicio sem catalogo manda `mapping_id` nulo; cancelar durante o reset de senha perde a senha temporaria.

Resolvido nas rodadas 1 e 2 da frente C (retirado do backlog): busca de Alimentos, Preferidos, Plano (Novo rascunho fixo), Diario (faixa da semana, dia da semana, data uma vez), Indicadores (marcador de 1 ponto, Novo lancamento em largura total, secoes que nao perdem o formulario), voltar fechando a visao de pagina inteira, e2e com payloads, goBack e fluxos com dados.

Pendente (frente C): confirmacao de descarte ao sair de uma visao de pagina inteira com alteracao (hoje o voltar fecha a visao e o rascunho fica); Perfil e Preferencias sem `StickyAction` (aceito: botao de largura total no fim do formulario); faixa de 7 dias da semana do Diario com 37 px de largura em 320 px.
