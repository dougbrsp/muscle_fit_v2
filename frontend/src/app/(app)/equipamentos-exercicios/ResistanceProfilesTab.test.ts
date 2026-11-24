// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { theme } from '@/theme';
import { ApiError } from '@/lib/api';
import type { ResistanceProfile } from '@/lib/resistance-profiles-api';
import { ResistanceProfilesTab, changedFields, formToPayload, profileToForm, resistanceErrorMessage } from './ResistanceProfilesTab';

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', async (importOriginal) => ({ ...(await importOriginal<typeof import('@/lib/api')>()), apiFetch }));

const perfil = {
  source: 's1', mechanism: 'm1', trajectory: 't1', profile: 'p1', line: 'l1', direction_changes: 'd1',
  flags: ['f1'], confidence: null, status: null, rationale: null, evidence: null,
} as unknown as ResistanceProfile;

describe('formulário de perfil de resistência', () => {
  it('par sem estado nasce com o primeiro estado da API e textos vazios', () => {
    const form = profileToForm(perfil, 'st0');
    expect(form.status).toBe('st0');
    expect(form.components).toEqual({ source: 's1', mechanism: 'm1', trajectory: 't1', profile: 'p1', line: 'l1', direction_changes: 'd1' });
    expect(form.rationale).toBe('');
  });

  it('payload troca texto em branco por null e apara espaços', () => {
    const form = { ...profileToForm(perfil, 'st0'), rationale: '  porque  ', evidence: '   ', reason: '' };
    const payload = formToPayload(form);
    expect(payload).toMatchObject({ source: 's1', flags: ['f1'], status: 'st0', rationale: 'porque', evidence: null, reason: null });
  });

  it('changedFields lista só o que mudou', () => {
    expect(changedFields({ a: 1, b: 2 }, { a: 1, b: 3, c: 4 }).sort()).toEqual(['b', 'c']);
    expect(changedFields(null, { a: 1 })).toEqual(['a']);
  });

  it('erros: 422 tipado, 403 e erro desconhecido', () => {
    expect(resistanceErrorMessage(new ApiError('x', 422, { code: 'k', field: 'f' }), 'fb')).toContain('f: k');
    expect(resistanceErrorMessage(new ApiError('x', 403), 'fb')).toContain('restrita');
    expect(resistanceErrorMessage(new Error('x'), 'fb')).toBe('fb');
    expect(resistanceErrorMessage(new ApiError('limite excedido', 422, { error: 'e' }), 'fb')).toBe('limite excedido');
  });
});

// Ordem dos estados propositalmente diferente de qualquer convenção: só `actions` decide.
const vocab = {
  version: 'v', source: { s1: 'S1' }, mechanism: { m1: 'M1' }, trajectory: { t1: 'T1' }, profile: { p1: 'P1' },
  line: { l1: 'L1' }, direction_changes: { d1: 'D1' }, flags: { f1: 'F1' }, confidence: { c1: 'C1' }, origin: { o1: 'O1' },
  status: { z: 'Zeta', y: 'Ypsilon', x: 'Xi', w: 'Omega' },
  default_status: 'w', actions: { approve: 'x', reject: 'z' }, pending_statuses: ['z'], human_review_statuses: ['x'], suggest_max_pairs: 7,
};
const row = {
  ...perfil, equipment_exercise_id: 5, equipment_id: 1, equipment_name: 'Maq', exercise_id: 2, exercise_name: 'Supino',
  has_profile: true, status: 'y', origin: 'o1', signature_label: 'assinatura', confidence: 'c1',
};
const coverage = { vocabulary_version: 'v', active_pairs: 11, with_profile: 8, without_profile: 3, by_status: { y: 4 }, unknown: 1 };

let listRow: unknown = row;
function routeImpl(url: string, init?: { method?: string }) {
  if (init?.method === 'PUT') return row;
  if (url.includes('/vocabulary')) return vocab;
  if (url.includes('/coverage')) return coverage;
  if (url.startsWith('/equipment-exercises')) return { items: [{ exercise_id: 2, exercise_name: 'Supino' }] };
  return { items: [listRow], total: 1, limit: 25, offset: 0 };
}
function routeApi(list: unknown = row) {
  listRow = list;
  apiFetch.mockImplementation(async (url: string, init?: { method?: string }) => routeImpl(url, init));
}
const puts = () => apiFetch.mock.calls.filter((c) => c[1]?.method === 'PUT');
const byLabel = (l: string) => document.body.querySelector(`[aria-label="${l}"]`) as HTMLElement;
const byText = (t: string) => [...document.body.querySelectorAll('button')].find((b) => b.textContent === t) as HTMLElement;
async function click(el: HTMLElement) {
  await act(async () => el.click());
  await act(async () => new Promise((r) => setTimeout(r, 10)));
}
async function type(el: HTMLTextAreaElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

let root: Root | null = null;
let host: HTMLElement;
beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} };
  window.matchMedia ??= ((q: string) => ({
    matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    onchange: null, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
});
beforeEach(() => {
  apiFetch.mockReset();
  routeApi();
});
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  host?.remove();
});

async function mount(canEdit: boolean) {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root!.render(
      createElement(QueryClientProvider, { client }, createElement(MantineProvider, { theme },
        createElement(ResistanceProfilesTab, { equipment: [{ id: 1, name: 'Maq' }], canView: true, canEdit }))),
    );
  });
  for (let i = 0; i < 5; i++) await act(async () => new Promise((r) => setTimeout(r, 10)));
}
const buttons = () => [...host.querySelectorAll('button')].map((b) => b.textContent);

describe('aba de perfis de resistência (renderização)', () => {
  it('cartão de cobertura, teto da API no texto e filtro por exercício', async () => {
    await mount(true);
    expect(host.textContent).toContain('Pares ativos: 11');
    expect(host.textContent).toContain('Sem perfil: 3');
    expect(host.textContent).toContain('até 7 pares');
    expect(host.textContent).toContain('Exercício');
  });

  it('aprovar e rejeitar pedem confirmação e gravam os estados de actions com o motivo', async () => {
    await mount(true);
    await click(byLabel('Aprovar Supino / Maq'));
    expect(puts()).toHaveLength(0);
    await type(document.body.querySelector('textarea')!, 'ok visual');
    await click(byText('Confirmar'));
    await click(byLabel('Rejeitar Supino / Maq'));
    await click(byText('Confirmar'));
    const p = puts();
    expect(p).toHaveLength(2);
    expect(p[0][0]).toBe('/resistance-profiles/5');
    expect(JSON.parse(p[0][1].body)).toMatchObject({ status: 'x', reason: 'ok visual' });
    expect(JSON.parse(p[1][1].body)).toMatchObject({ status: 'z', reason: null });
  });

  it('cancelar a confirmação não grava', async () => {
    await mount(true);
    await click(byLabel('Aprovar Supino / Maq'));
    await click(byText('Cancelar'));
    expect(puts()).toHaveLength(0);
  });

  it('editar par sem estado usa default_status da API', async () => {
    routeApi({ ...row, status: null, has_profile: true });
    await mount(true);
    await click(byLabel('Editar Supino / Maq'));
    await click(byText('Salvar'));
    expect(JSON.parse(puts()[0][1].body).status).toBe('w');
  });

  it('página presa volta para a última válida e a paginação aparece com page > 1', async () => {
    const offsets: number[] = [];
    apiFetch.mockImplementation(async (url: string, init?: { method?: string }) => {
      if (init?.method === 'PUT') return row;
      if (url.includes('/vocabulary')) return vocab;
      if (url.includes('/coverage')) return coverage;
      if (url.startsWith('/equipment-exercises')) return { items: [] };
      const offset = Number(new URL(url, 'http://x').searchParams.get('offset'));
      offsets.push(offset);
      return offset === 0 ? { items: [row], total: 30, limit: 25, offset } : { items: [], total: 5, limit: 25, offset };
    });
    await mount(true);
    await click(byText('2'));
    for (let i = 0; i < 3; i++) await act(async () => new Promise((r) => setTimeout(r, 10)));
    expect(offsets).toContain(25);
    expect(offsets[offsets.length - 1]).toBe(0);
    expect(host.textContent).toContain('Supino');
  });

  it('limpar seleção e mudar de filtro zeram a seleção', async () => {
    await mount(true);
    await click(byLabel('Selecionar Supino / Maq'));
    expect(host.textContent).toContain('1 selecionados');
    await click(byText('Limpar seleção'));
    expect(host.textContent).not.toContain('1 selecionados');
  });

  it('sugestão que falha também atualiza a lista', async () => {
    await mount(true);
    const listCalls = () => apiFetch.mock.calls.filter((c) => String(c[0]).startsWith('/resistance-profiles?')).length;
    apiFetch.mockImplementation(async (url: string, init?: { method?: string }) => {
      if (url.includes('/suggest')) throw new Error('job falhou');
      return routeImpl(url, init);
    });
    const before = listCalls();
    await click(byText('Sugerir por IA (pendentes)'));
    for (let i = 0; i < 3; i++) await act(async () => new Promise((r) => setTimeout(r, 10)));
    expect(listCalls()).toBeGreaterThan(before);
  });

  it('botões de linha trazem o nome do par no aria-label', async () => {
    await mount(true);
    for (const a of ['Aprovar', 'Rejeitar', 'Editar', 'Histórico']) expect(byLabel(`${a} Supino / Maq`)).toBeTruthy();
  });

  it('sem canEdit não há ações de escrita nem sugestão por IA', async () => {
    await mount(false);
    const b = buttons().join('|');
    expect(b).not.toMatch(/Aprovar|Rejeitar|Editar|Sugerir/);
    expect(b).toContain('Histórico');
  });
});
