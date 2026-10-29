'use client';

import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useMemo } from 'react';

/**
 * Filtro GLOBAL do hub `/dashboard/*` (plano §11, F0.4).
 *
 * O estado vive na URL em vez de Context/Zustand: dá deep-linking, back/forward
 * do navegador e sobrevive a refresh sem nenhuma camada de estado global nova.
 * O app é export estático (`output: 'export'` em next.config.mjs), então TODA
 * página que usa este hook precisa envolver seu client component em
 * `<Suspense>` — sem isso o `next build` falha com "useSearchParams() should be
 * wrapped in a suspense boundary".
 *
 * ## Por que `window` e não `days`
 *
 * Os endpoints novos (v3, `/analytics/dashboard/*`) aceitam janelas NOMEADAS
 * (`cycle|7d|28d|90d|180d|custom`, `contracts.WindowKind`), resolvidas no fuso
 * do usuário. Os endpoints v2 (`/analytics/dashboard-*`, com hífen) continuam
 * aceitando um `days` inteiro e só serão aposentados na F2.4. Até lá as duas
 * gerações coexistem, então:
 *
 * - `window` é o valor CANÔNICO — é o que vai para a query string e o que o
 *   seletor de período escreve;
 * - `days` é DERIVADO por `dashboardWindowDays()` e só existe na borda de
 *   chamada das rotas v2.
 *
 * O mapa é literal, nunca "arredondado": `28d` vira `days=28`, não 30. As
 * telas v2 que antes ofereciam 30 dias passam a oferecer 28 (o rótulo muda
 * junto com o número) — rotular 30 dias como "28 dias" seria mentir sobre a
 * janela que o backend de fato usou.
 *
 * ## Quais filtros entram aqui
 *
 * Só os que têm consumidor HOJE. `templateId`, `slot`, `gymId` e `exerciseId`
 * do §11 ficam de fora porque nenhum endpoint do hub os aceita ainda (chegam
 * com `/dashboard/exercicios/[id]` na F1.3 e com `adherence_service` na F1.1);
 * criar o parâmetro antes do consumidor só produziria filtro morto.
 *
 * `trainingTypeId` foi REMOVIDO nesta etapa: o único consumidor era
 * `dashboard-muscle-status`, que já resolve sozinho a Meta de Ativação
 * atribuída ao usuário, e a lista que popularia um seletor
 * (`GET /training-types`) é admin/PT-only — não havia (nem poderia haver, para
 * o usuário comum) UI que o definisse.
 */

export const DASHBOARD_WINDOWS = ['cycle', '7d', '28d', '90d', '180d', 'custom'] as const;

/** Espelho de `contracts.WindowKind` (backend). */
export type DashboardWindow = (typeof DASHBOARD_WINDOWS)[number];

/**
 * Janelas que o seletor oferece.
 *
 * `cycle` entrou na F1.1: o tamanho do ciclo passou a ser resolvido pelo
 * Modelo de Ficha atribuído (`7 × nº de slots`, mesma convenção de
 * `/analytics/dashboard-cycle`) e TODOS os endpoints v3 a aceitam. Quem não
 * tem Modelo atribuído recebe 400 com a mensagem do que fazer — por isso ela
 * fica no fim da lista, e não como padrão.
 */
export const DASHBOARD_WINDOW_OPTIONS = [
  '7d',
  '28d',
  '90d',
  '180d',
  'custom',
  'cycle',
] as const satisfies readonly DashboardWindow[];

export type DashboardWindowOption = (typeof DASHBOARD_WINDOW_OPTIONS)[number];

/** Janelas rolling: as unicas com um numero fixo de dias. */
export type DashboardRollingWindow = '7d' | '28d' | '90d' | '180d';

export const DEFAULT_DASHBOARD_WINDOW: DashboardRollingWindow = '28d';

/** Mesmo teto de `time_windows.MAX_WINDOW_DAYS` (plano §15). */
export const MAX_CUSTOM_WINDOW_DAYS = 365;

/**
 * Mapa ÚNICO `window` → `days` das rotas v2. Todos os endpoints v2 aceitam
 * `days` livre (`Query(ge=1, le=3650)` em `api/app/analytics.py`), então não
 * há nenhum valor "de compatibilidade" a preservar: o número enviado é
 * exatamente o número do rótulo.
 */
export const WINDOW_TO_DAYS: Readonly<Record<DashboardRollingWindow, number>> = {
  '7d': 7,
  '28d': 28,
  '90d': 90,
  '180d': 180,
};

export const DASHBOARD_WINDOW_LABELS: Readonly<Record<DashboardWindow, string>> = {
  cycle: 'Ciclo atual',
  '7d': 'Últimos 7 dias',
  '28d': 'Últimos 28 dias',
  '90d': 'Últimos 90 dias',
  '180d': 'Últimos 180 dias',
  custom: 'Período personalizado',
};

export type DashboardFilters = {
  window: DashboardWindow;
  /** `YYYY-MM-DD`; só relevante com `window === 'custom'`. */
  dateFrom: string | null;
  dateTo: string | null;
  /** Grupo selecionado em `/dashboard/musculos` (drill-down compartilhável). */
  muscleGroupId: number | null;
  /** Exercício canônico de `/dashboard/progressao`. */
  exerciseMappingId: number | null;
  exerciseName: string | null;
  /** Ficha auditada em `/dashboard/engine`. */
  planId: number | null;
};

export const EMPTY_DASHBOARD_FILTERS: DashboardFilters = {
  window: DEFAULT_DASHBOARD_WINDOW,
  dateFrom: null,
  dateTo: null,
  muscleGroupId: null,
  exerciseMappingId: null,
  exerciseName: null,
  planId: null,
};

// ---------------------------------------------------------------------------
// Funções puras (testadas em dashboard-filters.test.ts, sem React)
// ---------------------------------------------------------------------------

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** `YYYY-MM-DD` existente no calendário (rejeita `2026-02-31`). */
export function isIsoDate(value: string | null | undefined): value is string {
  if (!value || !ISO_DATE.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function daysBetweenInclusive(from: string, to: string): number {
  const start = Date.parse(`${from}T00:00:00Z`);
  const end = Date.parse(`${to}T00:00:00Z`);
  return Math.round((end - start) / 86_400_000) + 1;
}

/**
 * Valida a janela `custom` com as MESMAS regras do backend
 * (`time_windows.resolve_window`): ambas as datas presentes e válidas, fim ≥
 * início e no máximo 365 dias. Devolve a mensagem de erro, ou `null` quando a
 * faixa é válida.
 */
export function customRangeError(dateFrom: string | null, dateTo: string | null): string | null {
  if (!dateFrom || !dateTo) return 'Informe as datas de início e fim do período.';
  if (!isIsoDate(dateFrom) || !isIsoDate(dateTo)) return 'Datas inválidas.';
  if (dateFrom > dateTo) return 'A data final não pode ser anterior à inicial.';
  if (daysBetweenInclusive(dateFrom, dateTo) > MAX_CUSTOM_WINDOW_DAYS) {
    return `O período personalizado é de no máximo ${MAX_CUSTOM_WINDOW_DAYS} dias.`;
  }
  return null;
}

function parseNumberParam(raw: string | null): number | null {
  if (!raw) return null;
  const parsed = Number(raw);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

/**
 * `?days=` legado → janela nomeada equivalente. Só converte equivalência
 * EXATA: um link antigo com `days=14` não vira "28 dias" caladamente, cai no
 * default e o seletor mostra o que de fato será consultado.
 */
function windowFromLegacyDays(raw: string | null): DashboardRollingWindow | null {
  const days = parseNumberParam(raw);
  if (days == null) return null;
  const found = (Object.keys(WINDOW_TO_DAYS) as Array<keyof typeof WINDOW_TO_DAYS>).find(
    (key) => WINDOW_TO_DAYS[key] === days,
  );
  return found ?? null;
}

function parseWindow(params: URLSearchParams): DashboardWindow {
  const raw = params.get('window');
  if (raw && (DASHBOARD_WINDOW_OPTIONS as readonly string[]).includes(raw)) {
    return raw as DashboardWindowOption;
  }
  return windowFromLegacyDays(params.get('days')) ?? DEFAULT_DASHBOARD_WINDOW;
}

export function parseDashboardFilters(params: URLSearchParams): DashboardFilters {
  const dateFrom = params.get('date_from');
  const dateTo = params.get('date_to');
  return {
    window: parseWindow(params),
    dateFrom: isIsoDate(dateFrom) ? dateFrom : null,
    dateTo: isIsoDate(dateTo) ? dateTo : null,
    muscleGroupId: parseNumberParam(params.get('muscleGroupId')),
    exerciseMappingId: parseNumberParam(params.get('exerciseMappingId')),
    exerciseName: params.get('exerciseName') || null,
    planId: parseNumberParam(params.get('planId')),
  };
}

/**
 * Serializa os filtros de volta para a query string. Valores no default
 * (`window`) e nulos não poluem a URL; `date_from`/`date_to` só aparecem com
 * `window=custom`, para um link não carregar datas que nada consome.
 *
 * `base` preserva parâmetros de fora do hub que estejam na URL (ex.: `next`).
 */
export function serializeDashboardFilters(
  filters: DashboardFilters,
  base?: URLSearchParams,
): URLSearchParams {
  const params = new URLSearchParams(base?.toString() ?? '');

  params.delete('days'); // parâmetro legado: a URL passa a falar só `window`

  if (filters.window === DEFAULT_DASHBOARD_WINDOW) params.delete('window');
  else params.set('window', filters.window);

  const isCustom = filters.window === 'custom';
  for (const [key, value] of [
    ['date_from', filters.dateFrom],
    ['date_to', filters.dateTo],
  ] as const) {
    if (isCustom && value) params.set(key, value);
    else params.delete(key);
  }

  for (const [key, value] of [
    ['muscleGroupId', filters.muscleGroupId],
    ['exerciseMappingId', filters.exerciseMappingId],
    ['planId', filters.planId],
  ] as const) {
    if (value == null) params.delete(key);
    else params.set(key, String(value));
  }

  if (filters.exerciseName) params.set('exerciseName', filters.exerciseName);
  else params.delete('exerciseName');

  return params;
}

/**
 * `days` para as rotas v2. `custom` vira o tamanho real da faixa (limitado a
 * `MAX_CUSTOM_WINDOW_DAYS`); faixa inválida cai no default.
 *
 * `cycle` também cai no default AQUI de propósito: as rotas v2 recebem um
 * inteiro de dias e não sabem nada de Modelo, então o tamanho do ciclo só
 * existe do lado v3. Quem precisa do ciclo de verdade usa um endpoint v3.
 */
export function dashboardWindowDays(filters: DashboardFilters): number {
  if (filters.window === 'custom') {
    if (customRangeError(filters.dateFrom, filters.dateTo) != null) {
      return WINDOW_TO_DAYS[DEFAULT_DASHBOARD_WINDOW];
    }
    const total = daysBetweenInclusive(filters.dateFrom as string, filters.dateTo as string);
    return Math.min(Math.max(total, 1), MAX_CUSTOM_WINDOW_DAYS);
  }
  if (filters.window === 'cycle') return WINDOW_TO_DAYS[DEFAULT_DASHBOARD_WINDOW];
  return WINDOW_TO_DAYS[filters.window];
}

/**
 * Recorte das rotas v2 de `/dashboard/progressao` (M61): `days` sempre e,
 * com `custom` VÁLIDO, também as duas datas — o backend então usa
 * exatamente `[date_from, date_to]` em vez de "`days` dias terminando hoje".
 * Sem isso a faixa escolhida virava só um número e as datas eram ignoradas.
 * `custom` inválido cai no default, como em `dashboardWindowDays`.
 */
export type DashboardDayRange = { days: number; date_from?: string; date_to?: string };

export function dashboardDayRange(filters: DashboardFilters): DashboardDayRange {
  const days = dashboardWindowDays(filters);
  if (filters.window === 'custom' && customRangeError(filters.dateFrom, filters.dateTo) == null) {
    return { days, date_from: filters.dateFrom as string, date_to: filters.dateTo as string };
  }
  return { days };
}

/** Query params das rotas v3 (`window` + datas quando `custom`). */
export type DashboardWindowQuery = { window: DashboardWindow; date_from?: string; date_to?: string };

export function dashboardWindowQuery(filters: DashboardFilters): DashboardWindowQuery {
  if (filters.window === 'custom' && customRangeError(filters.dateFrom, filters.dateTo) == null) {
    return {
      window: 'custom',
      date_from: filters.dateFrom as string,
      date_to: filters.dateTo as string,
    };
  }
  // `custom` sem faixa válida cairia em 400 no backend: manda o default e a
  // tela mostra o erro da faixa ao lado do seletor.
  if (filters.window === 'custom') {
    return { window: DEFAULT_DASHBOARD_WINDOW };
  }
  // `cycle` VAI para o backend desde a F1.1 (ele resolve o tamanho pelo
  // Modelo atribuído). Sem Modelo a resposta é 400 com mensagem clara, que a
  // tela exibe — bem melhor do que consultar em silêncio outro período.
  return { window: filters.window };
}

/** `/dashboard/musculos` + filtros atuais — preserva o contexto na navegação. */
export function dashboardHref(pathname: string, filters: DashboardFilters): string {
  const query = serializeDashboardFilters(filters).toString();
  return query ? `${pathname}?${query}` : pathname;
}

/** Opções prontas para o `<Select>` de período do hub. */
export function dashboardWindowSelectData(
  allowed: readonly DashboardWindow[] = DASHBOARD_WINDOW_OPTIONS,
) {
  return allowed.map((value) => ({ value, label: DASHBOARD_WINDOW_LABELS[value] }));
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useDashboardFilters() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const filters = useMemo(
    () => parseDashboardFilters(new URLSearchParams(searchParams.toString())),
    [searchParams],
  );

  const setFilters = useCallback(
    (patch: Partial<DashboardFilters>) => {
      const merged: DashboardFilters = { ...filters, ...patch };
      const next = serializeDashboardFilters(merged, new URLSearchParams(searchParams.toString()));
      const query = next.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [filters, pathname, router, searchParams],
  );

  const days = useMemo(() => dashboardWindowDays(filters), [filters]);
  const dayRange = useMemo(() => dashboardDayRange(filters), [filters]);
  const windowQuery = useMemo(() => dashboardWindowQuery(filters), [filters]);
  const rangeError = useMemo(
    () => (filters.window === 'custom' ? customRangeError(filters.dateFrom, filters.dateTo) : null),
    [filters],
  );

  return { filters, setFilters, days, dayRange, windowQuery, rangeError };
}
