# Linguagem de interface do iPhone (mobile v2)

Guia que as frentes B (Evolução e dashboards) e C (Treino, corpo, nutrição e conta) seguem ao criar telas em `frontend/src/mobile/screens/**`. Decisões de navegação: [ADR 0004](../adr/0004-navegacao-iphone-e-secoes-so-desktop.md). Plano: `.omc/plans/mobile-v2-iphone-2026-11-03.md` (decisões D1 a D7 não se reabrem).

Referência: Apple HIG e convenções nativas do iOS, sem imitar Liquid Glass nem copiar um app. Visual escuro, marca atual (`BRAND`), acento laranja com moderação.

## 1. Princípios

1. **Hierarquia por tipo, não por moldura.** Título grande, rótulos pequenos em caixa alta, números em destaque. Sem contorno em cartão: o grupo é uma superfície (`#141414`) sobre o fundo (`#0A0A0A`), com divisores finos entre linhas.
2. **Um dado principal por item.** Lista ou cartão com 1 dado principal, até 2 secundários e o detalhe por toque (push ou folha). Tabela nunca é o padrão (nenhum `<table>` do Mantine nas telas mobile). Comparação realmente tabular: no máximo 3 colunas, sem rolagem horizontal da página.
3. **Acento laranja só para o que é ativo ou primário:** aba atual, linha atual, botão principal, link de voltar. Nunca para decoração. Estado crítico usa cor semântica mais ícone e texto (cor nunca é o único sinal).
4. **Só consumir.** Valor, texto, status e cor de status chegam prontos da API. Nenhum limiar, faixa, cor por número, fórmula ou classificação no frontend (regra de motores: `.claude/CLAUDE.md`, mapa `docs/motores/FONTES_DA_VERDADE.md`, guarda `api/tests/unit/test_guarda_fontes_da_verdade.py`). Constantes de layout (px, pt) não são regra de motor.
5. **Mesma URL, mesmas queries.** A tela mobile reutiliza as chaves e os clients do TanStack Query do desktop. Se faltar hook, extraia função pura sem mudar comportamento e liste o arquivo no PR (invariante 3 do plano).

## 2. Base

| Item | Regra |
|---|---|
| Alvo de toque | 44 pt no mínimo (linhas de lista têm 52 pt). |
| Safe area | `env(safe-area-inset-*)` já tratado pelo shell; não repita. Elementos `position: fixed` próprios devem usar `var(--m-nav-offset)` para ficar acima da tab bar. |
| Campos | Fonte de 16 px (o shell força); o iOS dá zoom abaixo disso. |
| Teclado (MB-8) | Com campo de texto focado, o shell esconde a tab bar (`data-keyboard="open"`) e `StickyAction` desce para a base. Não crie lógica própria. |
| Estados | Toda tela trata carregando (skeleton), vazio, erro (com tentar de novo) e offline. Use `LoadingState`, `EmptyState`, `ErrorState`, `OfflineState` (ou `ResponsiveDataList`, que resolve os três primeiros). |
| Movimento | Respeite `prefers-reduced-motion` (os primitivos já respeitam). Sem animação que dependa de hover. |
| Contraste | AA. Texto secundário usa `textSecondary` (~6:1 sobre a superfície). `textTertiary` é só decoração (chevron). |
| Fonte | De sistema (`MOBILE_FONT_FAMILY`), aplicada no shell. |
| Largura | Funciona de 320 a 430 px em retrato e em paisagem (844x390 com toque). Nada de largura fixa em px maior que 320 menos o respiro de 16 px de cada lado. |
| Hover | Nada depende de hover ou `Tooltip`; toque abre detalhe. |

## 3. Tipografia (`MOBILE_TYPE` em `src/mobile/tokens.ts`)

| Token | px / linha / peso | Uso |
|---|---|---|
| `largeTitle` | 28 / 34 / 700 | Título da tela (`ScreenHeader`). Um por tela. |
| `title` | 22 / 28 / 700 | Título de seção dentro da tela, estado vazio. |
| `headline` | 17 / 22 / 600 | Frase principal de um bloco. |
| `body` | 16 / 22 / 400 | Texto corrido, campos. |
| `callout` | 15 / 20 / 400 | Resposta curta, valor de linha. |
| `subhead` | 14 / 19 / 400 | Subtítulo de tela. |
| `footnote` | 13 / 18 / 400 | Rodapé de grupo, subtítulo de linha, dados secundários. |
| `caption` | 11 / 14 / 500 | Rótulo de aba. |
| `metric` | 26 / 30 / 700 | Número em destaque (`DataCard`, `StatTile`). |

Use `typeStyle('headline')` para estilo inline ou copie os valores para o módulo CSS da tela. Não invente tamanhos.

## 4. Primitivos (`src/mobile/ui`, importe de `@/mobile/ui`)

Todos só apresentam. Exemplo mínimo de cada um:

### `ScreenHeader`

Título grande da tela. O voltar fica no header do shell (automático nas telas de 2º nível); `actions` leva até dois botões de 44 pt.

```tsx
<ScreenHeader title="Planos de treino" subtitle="3 ativos" actions={<FilterButton activeCount={n} onClick={open} />} />
```

### `ListGroup`, `ListRow`, `ListBlock`

Lista agrupada. `ListRow` vira link com `href`, botão com `onClick`, ou linha estática. Máximo: 1 título, 1 subtítulo, 1 valor à direita. `ListBlock` abriga texto livre dentro do grupo.

```tsx
<ListGroup title="Treino" footer="Atualizado há pouco">
  <ListRow icon={IconNotes} title="Plano A" subtitle="4 exercícios" value="82 kg" href="/planos-treino/1" />
  <ListRow title="Notificações" trailing={<Switch checked={on} onChange={toggle} aria-label="Notificações" />} />
</ListGroup>
```

### `DataCard`

1 dado principal (`value`), até 2 secundários (o terceiro é descartado) e detalhe por toque. `status` recebe o selo pronto (ex.: `StatusBadge`).

```tsx
<DataCard
  title="Supino reto"
  value="82 kg"
  status={<StatusBadge domain="severity" code={row.level} size="sm" />}
  secondary={[{ label: 'Séries', value: row.sets }, { label: 'RPE', value: row.rpe }]}
  href={`/dashboard/exercicios/${row.id}`}
/>
```

### `StatTile` e `StatGrid`

Número em destaque com rótulo. A grade tem 2 colunas e cabe em 320 px.

```tsx
<StatGrid>
  <StatTile label="Séries" value={fmtInt(week.sets)} hint="na semana" />
  <StatTile label="Volume" value={week.volume_label} />
</StatGrid>
```

### `ResponsiveDataList`

Resolve carregando, erro, vazio e pronto. Modo `renderItem` (linhas ou cartões, `variant="cards"`) ou `columns` (comparação, no máximo 3 colunas, `role="table"` em grade, nunca `<table>`).

```tsx
<ResponsiveDataList
  aria-label="Treinos recentes"
  items={query.data?.items}
  getKey={(w) => w.id}
  isLoading={query.isLoading}
  isError={query.isError}
  onRetry={() => void query.refetch()}
  emptyTitle="Nenhum treino no período"
  renderItem={(w) => <ListRow title={w.name} subtitle={w.date_label} href={`/registro-exercicios/${w.id}`} />}
/>
```

### `BottomSheet`

Folha que sobe da base (escolha curta, detalhe). Altura pelo conteúdo, máximo 85%. Modal longo vira tela cheia (rota), não folha.

```tsx
<BottomSheet opened={opened} onClose={close} title="Série 3" footer={<Button fullWidth mih={44}>Salvar</Button>}>
  <Text>Detalhe...</Text>
</BottomSheet>
```

### `SegmentedFilter`, `FilterSheet`, `FilterButton`

Um filtro principal em segmentado (2 a 4 opções curtas) e os demais numa folha "Filtros (n)". Nunca 4 colunas de filtro. O estado dos filtros é da tela (de preferência na URL).

```tsx
<SegmentedFilter label="Período" value={period} onChange={setPeriod} options={[{ value: '7d', label: '7 dias' }, { value: '28d', label: '28 dias' }]} />
<FilterButton activeCount={active} onClick={openFilters} />
<FilterSheet opened={filters} onClose={closeFilters} activeCount={active} onClear={reset}>
  <Select label="Academia" data={gyms} />
</FilterSheet>
```

### `MobileChart`

Moldura de gráfico: resumo textual acima (leitura principal, vinda pronta da API), altura compacta (180, máx. 240), poucos ticks, toque em vez de hover. `children` é um gráfico do Recharts sem `ResponsiveContainer`. Mesmos dados do desktop (MB-3).

```tsx
<MobileChart summary={payload.headline} label="Peso nos últimos 28 dias">
  <LineChart data={points} margin={MOBILE_CHART_MARGIN}>
    <XAxis dataKey="date" {...MOBILE_CHART_AXIS} />
    <YAxis {...MOBILE_CHART_AXIS} width={40} />
    <Line dataKey="value" stroke={MOBILE_TOKENS.accent} dot={false} />
  </LineChart>
</MobileChart>
```

### `StickyAction`

A ação principal da tela, fixa acima da tab bar (zona do polegar). Uma por tela, botão de 44 pt ou mais em largura total. Reserva o espaço no fluxo.

```tsx
<StickyAction>
  <Button fullWidth size="lg" color="brand" mih={48}>Salvar medidas</Button>
</StickyAction>
```

## 5. Como registrar uma tela

1. Crie `src/mobile/screens/<frente>/MinhaTela.tsx` com export nomeado, começando por `<ScreenHeader>`.
2. Registre no arquivo da frente (`registry.b.ts` ou `registry.c.ts`), com carregamento sob demanda e rota dinâmica por `[param]`:

```ts
import { lazyScreen } from './lazy';
export const SCREENS_B = {
  '/dashboard/progressao': lazyScreen(() => import('./evolucao/ProgressaoScreen').then((m) => ({ default: m.ProgressaoScreen }))),
  '/dashboard/exercicios/[exerciseId]': lazyScreen(() => import('./evolucao/ExercicioScreen').then((m) => ({ default: m.ExercicioScreen }))),
};
```

3. O desktop nunca passa pelo registry. Não altere CSS global; estilos ficam em módulo CSS em `src/mobile/`.
4. **Tela registrada NÃO monta a página da rota.** Qualquer guarda que a página desktop faz (papel admin, 403, redirect, `enabled` da query depois de conhecer o usuário) precisa ser replicada na tela mobile. Exemplo: `UsuariosClient.tsx:96-108,309` só dispara `GET /users` depois de `/auth/me` e mostra acesso restrito para não admin ou 403; a tela mobile de Usuários faz o mesmo. `AdminOnlyRoute` continua valendo para o que a página usa, mas a tela nova precisa do próprio equivalente.
5. **Rota dinâmica: use `useDynamicRouteParam`** (`@/lib/use-dynamic-route-param`), nunca o valor cru de `useParams()`. No export estático `useParams()` devolve o slug literal `placeholder`; o id real está na barra de endereços (ver `ExerciseProgressClient.tsx:105-106`):

```tsx
const params = useParams<{ exerciseId: string }>();
const rawId = useDynamicRouteParam(params?.exerciseId); // string | null
const exerciseId = rawId != null && /^\d+$/.test(rawId) ? Number(rawId) : null;
```

6. Início e hub Evolução são estáticos no shell (a PWA abre offline sem depender de chunk fora do precache). Só as telas de B e C usam `lazyScreen`, que já mostra "Sem conexão" com tentar de novo se o chunk falhar.
7. Estados de lista: `ResponsiveDataList` nunca trata `items === undefined` como vazio; dado já carregado tem prioridade sobre erro de refetch; passe `isOffline` (ex.: `query.fetchStatus === 'paused'`) para o estado offline.
8. Links para as seções só do computador (`isDesktopOnlyPath`, `@/lib/nav-items`) não devem aparecer como botão ou atalho na tela mobile; links vindos de componentes compartilhados caem na tela "Disponível no computador" (D1).

## 6. O que o shell já entrega

- Tab bar de 5 abas (Início, Treino, Histórico, Evolução, Mais), com rolagem preservada ao voltar às raízes das abas.
- Header de 2º nível com voltar e rótulo do destino (Evolução, Histórico, Mais...). Telas fora das raízes das abas ganham o voltar sozinhas.
- Hub Evolução em `/dashboard/?hub=evolucao`; folha Mais agrupada por papel; tela "Disponível no computador".
- Celular em paisagem (toque e menor lado da tela física < 500 px, estável com teclado) continua no layout mobile, com barra e header compactos.
