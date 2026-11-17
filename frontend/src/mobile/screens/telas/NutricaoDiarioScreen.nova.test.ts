// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { act, createElement, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { theme } from '@/theme';

// Aba "Refeição" do menu inferior: `/nutricao/?nova=1` abre o mesmo editor do botão "Adicionar refeição".

const h = vi.hoisted(() => ({
  search: '',
  back: vi.fn(),
  replace: vi.fn(),
  origem: false,
  backToClose: vi.fn(),
  criar: vi.fn(),
  invalidar: vi.fn(() => Promise.resolve()),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ back: h.back, replace: h.replace }),
  useSearchParams: () => new URLSearchParams(h.search),
}));
vi.mock('../../layout/nav-active', () => ({ temOrigemInterna: () => h.origem }));
vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: h.invalidar }),
  useMutation: () => ({ mutate: vi.fn(), isError: false, isPending: false }),
  useQuery: ({ queryKey }: { queryKey: unknown[] }) => {
    const chave = String(queryKey[queryKey.length - 1] ?? '');
    if (queryKey[0] === 'current-user') return { isLoading: false, isError: false, isSuccess: true, data: {} };
    if (chave.includes('2026')) {
      return { isLoading: false, isError: false, data: { meals: [], totals: null, today: '2026-11-17', closure: null, training: null } };
    }
    return { isLoading: false, isError: false, data: { today: '2026-11-17' } };
  },
}));
vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }));
vi.mock('@/lib/screen-permissions', () => ({ useIaControls: () => ({ canNutricao: false }) }));
vi.mock('@/lib/nutrition-dashboard', () => ({ dashboardKeys: { all: ['dash'] } }));
vi.mock('@/lib/nutrition-api', () => ({
  deleteMeal: vi.fn(),
  diaryKeys: { all: ['diary'], week: (a: unknown) => ['diary', 'week', a], day: (d: string) => ['diary', 'day', d] },
  fetchNutritionDay: vi.fn(),
  fetchNutritionWeek: vi.fn(),
  lastMealTimeKey: ['last'],
  nutritionErrorInfo: () => ({ message: 'erro' }),
}));
vi.mock('./common', () => ({ SheetButton: () => null, useBackToClose: h.backToClose }));
vi.mock('./MobileNutritionPresentation', () => ({ MobileNutritionPresentation: ({ children }: { children: ReactNode }) => children }));
vi.mock('./NutricaoNav', () => ({ NutricaoNav: () => null }));
vi.mock('@/components/nutrition/CheckinCard', () => ({ CheckinCard: () => null }));
vi.mock('@/components/nutrition/DayHeader', () => ({ DayHeader: () => null }));
vi.mock('@/components/nutrition/DayNotesInput', () => ({ DayNotesInput: () => null }));
vi.mock('@/components/nutrition/MealParseModal', () => ({ MealParseModal: () => null }));
vi.mock('@/components/nutrition/QuickEntryForm', () => ({ QuickEntryForm: () => null }));
vi.mock('@/components/nutrition/MealCard', () => ({ quantidade: () => '', TotalsBadges: () => null, TotalsLine: () => null }));
vi.mock('@/components/nutrition/TargetVsIntakeCard', () => ({ TargetVsIntakeCard: () => null }));
vi.mock('@/components/nutrition/DayClosureControl', () => ({ DayClosureControl: () => null }));
vi.mock('@/components/nutrition/DayEnergySection', () => ({ DayEnergySection: () => null }));
vi.mock('@/components/nutrition/TrainingKcalRow', () => ({ TrainingKcalRow: () => null }));
vi.mock('@/components/nutrition/WeekStrip', () => ({ WeekStrip: () => null }));
vi.mock('@/components/nutrition/MealEditor', () => ({
  // Editor de mentira: salvar NÃO chama `criar` aqui; cancelar nunca deve chamá-lo.
  MealEditor: ({ onSaved, onCancel }: { onSaved: () => void; onCancel: () => void }) =>
    createElement(
      'div',
      { 'data-testid': 'editor' },
      createElement('button', { type: 'button', onClick: onSaved }, 'salvar-fake'),
      createElement('button', { type: 'button', onClick: onCancel }, 'cancelar-fake'),
    ),
}));
vi.mock('../../ui', () => {
  const passa = ({ children }: { children?: ReactNode }) => createElement('div', null, children);
  return {
    BottomSheet: () => null,
    ListBlock: passa,
    ListGroup: passa,
    ListRow: () => null,
    LoadingState: () => null,
    ScreenHeader: ({ title }: { title: string }) => createElement('h1', null, title),
    StickyAction: passa,
  };
});

import { NutricaoDiarioScreen } from './NutricaoDiarioScreen';

let root: Root | null = null;
let host: HTMLElement;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  // jsdom não tem matchMedia, que o MantineProvider consulta.
  window.matchMedia = ((query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
});

beforeEach(() => {
  vi.clearAllMocks();
  h.search = '';
  h.origem = false;
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  root = null;
  host?.remove();
});

async function montar() {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  const el = () => createElement(MantineProvider, { theme }, createElement(NutricaoDiarioScreen));
  await act(async () => {
    root!.render(el());
  });
  return async () => {
    await act(async () => {
      root!.render(el());
    });
  };
}

const editor = () => host.querySelector('[data-testid="editor"]');
const clicar = async (texto: string) => {
  const botao = [...host.querySelectorAll('button')].find((b) => b.textContent?.includes(texto));
  expect(botao, texto).toBeTruthy();
  await act(async () => {
    botao!.click();
  });
};

describe('NutricaoDiarioScreen com ?nova=1', () => {
  it('sem o parâmetro o editor não abre sozinho', async () => {
    await montar();
    expect(editor()).toBeNull();
    expect(h.replace).not.toHaveBeenCalled();
  });

  it('o botão "Adicionar refeição" continua abrindo o editor, com voltar do navegador fechando-o', async () => {
    await montar();
    await clicar('Adicionar refeição');
    expect(editor()).not.toBeNull();
    expect(h.backToClose).toHaveBeenCalledWith(true, expect.any(Function));
  });

  it('com nova=1 o editor abre, dirigido pela URL (sem entrada extra de histórico)', async () => {
    h.search = 'nova=1';
    await montar();
    expect(editor()).not.toBeNull();
    expect(h.backToClose.mock.calls.some(([aberto]) => aberto === true)).toBe(false);
  });

  it('cancelar com origem volta no histórico, fecha o editor e não cria refeição', async () => {
    h.search = 'nova=1';
    h.origem = true;
    await montar();
    await clicar('cancelar-fake');
    expect(h.back).toHaveBeenCalledTimes(1);
    expect(h.replace).not.toHaveBeenCalled();
    expect(h.criar).not.toHaveBeenCalled();
    expect(editor()).toBeNull();
  });

  it('cancelar sem origem (URL direta) fica no diário sem o parâmetro', async () => {
    h.search = 'nova=1';
    await montar();
    await clicar('cancelar-fake');
    expect(h.back).not.toHaveBeenCalled();
    expect(h.replace).toHaveBeenCalledWith('/nutricao/');
    expect(editor()).toBeNull();
  });

  it('salvar fica no diário, limpa o parâmetro por replace e invalida o diário', async () => {
    h.search = 'nova=1';
    h.origem = true;
    await montar();
    await clicar('salvar-fake');
    expect(h.back).not.toHaveBeenCalled();
    expect(h.replace).toHaveBeenCalledWith('/nutricao/');
    expect(h.invalidar).toHaveBeenCalled();
    expect(editor()).toBeNull();
  });

  it('voltar do navegador (parâmetro some) fecha o editor sem mexer no histórico', async () => {
    h.search = 'nova=1';
    const rerender = await montar();
    expect(editor()).not.toBeNull();
    h.search = '';
    await rerender();
    expect(editor()).toBeNull();
    expect(h.back).not.toHaveBeenCalled();
    expect(h.replace).not.toHaveBeenCalled();
  });

  it('nova=1 com o editor já aberto pelo botão não abre outro: só limpa o parâmetro', async () => {
    const rerender = await montar();
    await clicar('Adicionar refeição');
    h.search = 'nova=1';
    await rerender();
    expect(host.querySelectorAll('[data-testid="editor"]')).toHaveLength(1);
    expect(h.replace).toHaveBeenCalledWith('/nutricao/');
  });
});
