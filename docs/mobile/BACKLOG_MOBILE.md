# Backlog de refinamento mobile (Frente A)

Origem: sonda das 31 rotas autenticadas no shell mobile (320 e 390 px, flag ligada) e `frontend/e2e/mobile-layout/routes-320.spec.ts`.
Estado: todas as rotas do menu são alcançáveis (bottom nav + "Mais") e nenhuma faz a página rolar na horizontal. Os itens abaixo são refinos, não bloqueios. Nenhum envolve regra de motor.

| # | Tela | Gap | Sugestão |
|---|---|---|---|
| MB-1 | `registro-exercicios`, `exercicios`, `volume-grupamentos`, `planos-treino` | Tabelas largas rolam dentro do container (`TableScrollContainer`), mas sem pista visual: colunas de ação ficam fora da tela e o nome é cortado. | Variante em cartões (lista) em `src/mobile/screens` reutilizando os mesmos hooks/queries, ou sombra/gradiente de borda indicando rolagem. |
| MB-2 | `registro-exercicios` | Filtros (busca, período, academia, condutor) em quatro colunas apertadas; rótulo "Buscar treino" quebra em duas linhas. | Empilhar filtros em 1-2 colunas abaixo de 768 px (muda a página compartilhada: exige prova de desktop idêntico com `npm run e2e`). |
| MB-3 | `dashboard/*` (gráficos Recharts) | Gráficos mantêm a altura de desktop e eixos densos em 320 px. | Altura/ticks responsivos por prop, sem alterar dados. |
| MB-4 | `modelos-treino/[id]` (builder) | Painéis flutuantes e grade de slots são desktop-heavy (`APP_HEADER_HEIGHT = 64` assume o header do desktop; no mobile o header tem 56 px + safe-area). | Tela mobile própria só de leitura/ajuste simples, ou manter desktop-only com aviso. Não verificado com usuário admin (os mocks do e2e usam role `user`). |
| MB-5 | Páginas admin (`usuarios`, `equipamentos-exercicios`, `dominio-exercicios`, `regras-carga`, `metas-ativacao`) | Não exercitadas com role admin no e2e; só a rota abrir foi provada pela sonda como `user`. | Mock de admin no e2e e repetir a sonda. |
| MB-6 | Nutrição, Agente de Cardio, Insights | Estados de erro/vazio próprios de cada tela; os componentes `src/mobile/ui` (`LoadingState`, `EmptyState`, `ErrorState`, `OfflineState`) ainda não foram adotados por elas. | Adotar gradualmente ao criar variantes mobile. |
| MB-7 | Shell | Seis abas a 320 px deixam ~53 px por aba (rótulos 11 px). Passa o alvo de 44 px, mas é justo. | Validar em iPhone SE real; se apertar, mover "Perfil" para "Mais". |
| MB-8 | Shell | Sem tratamento de teclado virtual (`visualViewport`): o bottom nav fixo pode cobrir campos em formulários longos. | Ocultar o bottom nav com campo focado, validar em iPhone real. |
| MB-9 | Paisagem >= 768 px (ex.: iPhone Pro Max 844 px) | Cai no layout desktop por decisão do plano (3.1). | Reavaliar após validar em aparelho real. |
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
