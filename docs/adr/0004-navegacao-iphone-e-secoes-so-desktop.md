# ADR 0004: Navegação do iPhone (tab bar de 5 abas, hub Evolução) e seções só do computador

- **Status**: Aceita
- **Data**: 2026-11-03

## Contexto

O shell mobile da [ADR 0001](0001-selecao-layout-mobile-por-viewport.md) tinha seis abas (Início, Treino, Histórico, Biblioteca, Perfil e Mais, backlog MB-7), "Mais" como lista plana e as mesmas páginas do desktop em todas as rotas. O pedido do usuário (2026-11-03) foi reformular toda a interface no iPhone com as melhores práticas de navegação e visual, manter o desktop igual e deixar só no computador seis seções: Academias, Equipamentos x Exercícios, Domínio de Exercícios, Regras de carga, Metas de Ativação e Modelos de treino.

Restrições: URL única (sem `/m/...`); desktop idêntico (goldens em `frontend/e2e/desktop-baseline`); só frontend, sem backend, migração ou motor (`.claude/CLAUDE.md`, "Motores"; layout e navegação não são domínio de motor); `AdminOnlyRoute` e as permissões da API continuam valendo.

## Alternativas consideradas

1. **Esconder só o item de menu.** Rejeitada: a URL continua abrindo a página desktop dentro do shell, e há links compartilhados (`InsightCard`, `DataQuality`, `SafeMarkdown`, `CardioRecommendationPanel`) que levam a essas rotas.
2. **Redirecionar a rota só-desktop para o Início.** Rejeitada: perde o deep link, esconde o motivo e quebra favoritos.
3. **Bloquear no servidor ou na API.** Rejeitada: é restrição de interface, não de segurança (D3).
4. **Rota nova `/evolucao` para o hub.** Rejeitada: apareceria no desktop (export estático). O hub vive em `/dashboard/?hub=evolucao`, parâmetro ignorado pelo desktop.
5. **Atributo `desktopOnly` na lista única de navegação, com tela "Disponível no computador" no shell (escolhida).**

## Decisão

- **Tab bar de 5 abas** (HIG): Início, Treino, Histórico, Evolução, Mais. Perfil e Biblioteca passam para "Mais"; Perfil também é o avatar do header.
- **Evolução é um hub** (lista por objetivo: Treino, Corpo e recuperação, Cardio e nutrição, Análises) em `/dashboard/?hub=evolucao`, resolvido pelo registry de telas mobile. Itens condicionais (Intervals.icu) seguem o papel.
- **"Mais"** é uma folha com lista agrupada (Treino, Corpo e nutrição, Conta, Outros) e "Sair". Itens fora de grupo caem em "Outros" para nenhuma rota ficar inacessível.
- **Telas de 2º nível** ganham header com voltar e o rótulo do destino; as raízes das abas guardam a rolagem.
- **Seções só do computador (D1):** atributo `desktopOnly` em `frontend/src/lib/nav-items.ts` (sem efeito no desktop) e `isDesktopOnlyPath`/`desktopOnlySectionLabel`, por prefixo de segmento. Cobrem `/academias`, `/equipamentos-exercicios`, `/dominio-exercicios`, `/regras-carga`, `/metas-ativacao` (+ `[id]`), `/modelos-treino` (+ `[id]`), `/diagnostico-volume/*` (parte de Modelos) e o redirect legado `/tipos-treinamento`. No iPhone, a rota mantém a URL e mostra "Disponível no computador" com ação para o Início; a página da rota não é montada, então não há chamada de API dela.
- **Paisagem (D2):** `(max-width: 47.99em), (pointer: coarse) and (max-height: 31.25em)` em `layout-mode.ts`. iPhone deitado, inclusive Pro Max, fica no layout mobile; tablet e desktop seguem no desktop. Sem User-Agent. Resolve MB-9 para celular.
- **Registry** de telas mobile carrega por `next/dynamic`, aceita rotas dinâmicas (`[id]`) e compõe `registry.b.ts` e `registry.c.ts`; o desktop nunca o consulta.
- **Teclado (MB-8):** com campo de texto focado, a tab bar se esconde e a ação fixa desce.

## Trade-offs

- **Ganho:** navegação padrão do iOS, desktop intocado, fonte única da lista de seções só-desktop, nenhuma rota nova no desktop.
- **Custo:** o hub compartilha a URL do Início (`/dashboard/`), diferenciado por query; quem abrir o link no desktop vê a visão geral normal.
- **Custo:** `?mobile=0` (override de QA, P2-6) reabre o desktop no aparelho, inclusive as seis seções.
- **Custo:** `(pointer: coarse)` pode casar com notebook touch em janela baixa (menos de 500 px de altura); aceito.
- **Custo:** o iPhone perde acesso às seis seções; o usuário assume isso (D1).

## Consequências

- Nova seção só-desktop: marcar `desktopOnly` em `nav-items.ts`; menu, folha Mais, hub e tela D1 acompanham sozinhos (coberto por `primary-nav.test.ts` e `nav-items.test.ts`).
- Telas mobile novas (frentes B e C) registram-se em `registry.b.ts`/`registry.c.ts` seguindo `docs/mobile/DESIGN_IPHONE.md`.
- Atalhos para rotas só-desktop não aparecem em telas mobile (`isDesktopOnlyPath`); links compartilhados caem na tela D1.
- Supera o item MB-7 e a parte de MB-8 e MB-9 do backlog para celular; registrado em `docs/mobile/BACKLOG_MOBILE.md`.
