# ADR 0001: Seleção de layout mobile por viewport, com feature flag e shell carregado sob demanda

- **Status**: Aceita (a regra de paisagem de celular foi atualizada pela [ADR 0004](0004-navegacao-iphone-e-secoes-so-desktop.md))
- **Data**: 2026-11-02

## Contexto

O MuscleFit é um app Next.js 14 exportado de forma estática (`frontend/next.config.mjs`, `output: 'export'`). Não existe servidor que saiba a largura da tela no primeiro paint. O desktop já tem uma casca responsiva com `AppShell` do Mantine, que troca a navbar por um drawer abaixo de `sm` (48em, 768 px). A experiência de celular precisa ser própria, sem duplicar a rota nem mexer no desktop.

Restrições do projeto (vindas do plano em `docs/mobile/MOBILE_IMPLEMENTATION_PLAN.md`, seção 3.1; layout não é domínio de motor, então `docs/motores/FONTES_DA_VERDADE.md` não se aplica aqui):

- a URL, os deep links e a sessão não podem mudar entre desktop e celular;
- o desktop precisa ficar idêntico quando a flag estiver desligada;
- tablet não pode cair em estado híbrido.

## Alternativas consideradas

1. **Detecção por User-Agent.** Rejeitada. Não reage a rotação, zoom nem janela redimensionada, e o plano veta explicitamente essa abordagem.
2. **Rotas duplicadas (`/m/...`).** Rejeitada. Quebra "mesma URL", duplica páginas e deep links.
3. **Só CSS responsivo sobre a árvore desktop.** Rejeitada. A árvore desktop tem o `AppShell`, painéis flutuantes e tabelas largas; mudar só a apresentação não entrega uma experiência de celular, e o bundle desktop continuaria carregando tudo.
4. **Seletor por largura com `matchMedia`, sem User-Agent (escolhida).** Reage a rotação e redimensionamento, mantém a URL e deixa a árvore mobile fora do bundle desktop.

## Decisão

- Corte de largura: `(max-width: 47.99em)` em `frontend/src/lib/mobile/layout-mode.ts:16`. Smartphone é menor que 768 px. Tablet (768 px ou mais) recebe o layout desktop, e isso é uma decisão explícita de fallback.
- Hook `useLayoutMode()` devolve `null` até montar e só então escolhe o modo. Isso evita hydration mismatch no export estático.
- Flag da UI mobile em `frontend/src/lib/mobile/flags.ts`, módulo único. Precedência: query `?mobile=0|1` (persiste o override, para QA), depois `localStorage['musclefit.flag.mobile-ui']` (`on`/`off`), depois `NEXT_PUBLIC_MOBILE_UI` no build. Sem valor reconhecido, a flag fica desligada.
- Com a flag desligada, `AppShellLayout` renderiza `DesktopAppShell` diretamente (`frontend/src/components/AppShellLayout.tsx:142-143`). O seletor não é montado e o shell mobile não é baixado.
- Com a flag ligada, o seletor e o shell mobile entram com `lazy`/`next/dynamic` (`AppShellLayout.tsx:124`, `frontend/src/mobile/layout/MobileShell.tsx`), então o bundle desktop não cresce.
- O Modo Treino (`frontend/src/app/(fullscreen)/`) não passa por este seletor. Ele tem layout próprio, sem `AppShellLayout`.

## Trade-offs

- **Ganho**: desktop intocado com a flag desligada; rollback por variável de build ou por override no aparelho; URL única.
- **Custo**: cruzar 768 px remonta a página. A URL, o cache do TanStack Query e a sessão de treino (em storage) sobrevivem, mas o estado local da página (filtros digitados, abas) se perde. Registrado como MB-10 em `docs/mobile/BACKLOG_MOBILE.md`.
- **Custo**: a leitura da flag no primeiro render é `null` por um frame. Com a flag desligada isso aparece como um loader neutro antes do desktop (P3-1 no backlog).
- **Custo**: paisagem de celular com 768 px ou mais (ex.: iPhone Pro Max em paisagem) cai no layout desktop (MB-9).

## Consequências

- Uma tela só ganha apresentação mobile própria quando é registrada em `frontend/src/mobile/screens/registry.ts`. As demais rotas são renderizadas pelo shell mobile com a própria página do app, sem perder a URL nem a funcionalidade (ver "Telas" no guia operacional).
- Lógica de negócio não entra no seletor nem no shell. Números e regras continuam no backend ou nos módulos donos.
- A flag não tem regra de negócio e não é consultada pelo backend. Um canário por usuário no servidor só existirá se for pedido.
- Liberar a flag em produção exige aval explícito do usuário (plano, seção 8, "Ordem de liberação").
