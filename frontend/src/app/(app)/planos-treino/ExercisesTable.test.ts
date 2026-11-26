// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { theme } from '@/theme';
import type { WorkoutPlanExercise } from '@/lib/workout-plans-types';
import { apiFetch } from '@/lib/api';
import { ExercisesTable } from './ExercisesTable';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  apiFetch: vi.fn(async () => ({ items: [] })),
}));

let root: Root | null = null;
let host: HTMLElement;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  window.matchMedia ??= ((q: string) => ({
    matches: false,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  root = null;
  host?.remove();
});

// Dados de exemplo de resposta da API (a tela só exibe).
const row = (over: Partial<WorkoutPlanExercise>): WorkoutPlanExercise => ({
  sequence: 1,
  exercise_name: 'Supino reto',
  muscle_primary: 'Peito',
  sets_count: 3,
  reps_target: '8-10',
  warmup: false,
  ...over,
});

type TableProps = Partial<Parameters<typeof ExercisesTable>[0]>;

async function render(exercises: WorkoutPlanExercise[], readOnly = false, props: TableProps = {}) {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root!.render(
      createElement(
        QueryClientProvider,
        { client: new QueryClient({ defaultOptions: { queries: { retry: false } } }) },
        createElement(
          MantineProvider,
          { theme },
          createElement(ExercisesTable, { exercises, onChange: () => {}, readOnly, ...props }),
        ),
      ),
    );
  });
}

const rpeInputs = () =>
  Array.from(host.querySelectorAll<HTMLInputElement>('input[aria-label^="RPE esperado da série"]'));

describe('ExercisesTable: RPE por série', () => {
  it('mostra um campo por série com os alvos vindos da ficha', async () => {
    await render([row({ rpe_targets_by_set: [8, 9, 10], rpe_edited: false })]);
    expect(host.textContent).toContain('RPE esperado por série');
    expect(rpeInputs().map((i) => i.value)).toEqual(['8', '9', '10']);
  });

  it('linha só com texto gravado mostra o alvo literal, sem campos por série', async () => {
    await render([row({ rpe_target: '8-9', rpe_targets_by_set: [], rpe_edited: false })]);
    expect(host.textContent).toContain('Alvo gravado: 8-9');
    expect(rpeInputs()).toHaveLength(0);
  });

  it('linha sem alvos e sem texto mostra os campos em branco, um por série', async () => {
    await render([row({ rpe_targets_by_set: [], rpe_edited: false })]);
    expect(rpeInputs().map((i) => i.value)).toEqual(['', '', '']);
  });

  it('somente leitura usa o campo de texto, sem campos por série', async () => {
    await render([row({ rpe_targets_by_set: [8, 9, 10] })], true);
    expect(rpeInputs()).toHaveLength(0);
  });
});

const byLabel = <T extends HTMLElement = HTMLElement>(label: string) =>
  document.body.querySelector<T>(`[aria-label="${label}"]`);

async function click(el: Element | null) {
  expect(el).not.toBeNull();
  await act(async () => {
    (el as HTMLElement).click();
  });
}

describe('ExercisesTable: RPE em texto', () => {
  it('editar o texto (linha sem séries) zera a lista por série vinda da sugestão', async () => {
    const onChange = vi.fn();
    await render([row({ sets_count: null, rpe_target: '8', rpe_targets_by_set: [7, 8, 9], rpe_edited: true })], false, { onChange });
    const input = Array.from(host.querySelectorAll<HTMLInputElement>('input')).find((i) => i.value === '8')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '9');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    const next = onChange.mock.calls.at(-1)![0] as WorkoutPlanExercise[];
    expect(next[0].rpe_target).toBe('9');
    expect(next[0].rpe_targets_by_set).toEqual([]);
    expect(next[0].rpe_edited).toBe(false);
  });
});

describe('ExercisesTable: layout da tabela', () => {
  it('não tem mais a coluna "Aquec." nem checkbox de aquecimento', async () => {
    await render([row({ warmup: true })]);
    const headers = Array.from(host.querySelectorAll('th')).map((th) => th.textContent);
    expect(headers).not.toContain('Aquec.');
    expect(host.querySelector('input[type="checkbox"]')).toBeNull();
    // O aquecimento ligado aparece na badge da Config. avançada.
    expect(host.textContent).toContain('Aquecimento');
  });

  it('warmup é editado pela Config. avançada e volta pelo onApply', async () => {
    const onChange = vi.fn();
    await render([row({ warmup: false, warmup_sets_count: 2 })], false, { onChange });
    await click(byLabel('Editar configuração avançada'));
    // A Modal do Mantine monta o portal com transição.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 300));
    });
    const label = Array.from(document.body.querySelectorAll('label')).find((l) => l.textContent === 'Fazer aquecimento neste exercício');
    const toggle = document.getElementById(label?.htmlFor ?? '') as HTMLInputElement | null;
    expect(toggle?.checked).toBe(false);
    await click(toggle!);
    const apply = Array.from(document.body.querySelectorAll('button')).find((b) => b.textContent === 'Aplicar');
    await click(apply!);
    const next = onChange.mock.calls.at(-1)![0] as WorkoutPlanExercise[];
    expect(next[0].warmup).toBe(true);
    expect(next[0].warmup_sets_count).toBe(2);
  });
});

describe('ExercisesTable: controle de equipamento na célula do exercício', () => {
  it('rascunho: o chip abre a escolha de equipamento da linha', async () => {
    const onPickDraftEquipment = vi.fn();
    await render([row({ mapping_id: 5, equipment_name: 'Barra' })], false, { onPickDraftEquipment });
    await click(byLabel('Trocar equipamento: Barra'));
    expect(onPickDraftEquipment).toHaveBeenCalledWith(0);
    // Sem botões duplicados na coluna Ações.
    expect(byLabel('Escolher equipamento')).toBeNull();
    expect(byLabel('Trocar equipamento')).toBeNull();
  });

  it('ficha salva: o chip abre a troca com a linha', async () => {
    const onSwapEquipment = vi.fn();
    const saved = row({ workout_plan_exercise_id: 11, mapping_id: 5, equipment_name: 'Halter' });
    await render([saved], false, { onSwapEquipment });
    await click(byLabel('Trocar equipamento: Halter'));
    expect(onSwapEquipment).toHaveBeenCalledWith(saved);
  });

  it('sem troca permitida mostra só o nome, sem controle', async () => {
    await render([row({ workout_plan_exercise_id: 11, mapping_id: 5, equipment_name: 'Halter' })], false, {
      onPickDraftEquipment: vi.fn(), // rascunho não vale para linha salva
    });
    expect(byLabel('Trocar equipamento: Halter')).toBeNull();
    expect(host.textContent).toContain('Halter');
  });

  it('somente leitura não mostra o controle', async () => {
    await render([row({ mapping_id: 5, equipment_name: 'Barra' })], true, { onPickDraftEquipment: vi.fn() });
    expect(byLabel('Trocar equipamento: Barra')).toBeNull();
  });
});

describe('ExercisesTable: reordenação', () => {
  it('mover para baixo pelas setas reordena e reindexa a sequência', async () => {
    const onChange = vi.fn();
    await render([row({ sequence: 1, exercise_name: 'A' }), row({ sequence: 2, exercise_name: 'B' })], false, { onChange });
    await click(host.querySelector('[aria-label="Mover para baixo"]'));
    const next = onChange.mock.calls.at(-1)![0] as WorkoutPlanExercise[];
    expect(next.map((e) => [e.exercise_name, e.sequence])).toEqual([
      ['B', 1],
      ['A', 2],
    ]);
  });
});

describe('ExercisesTable: sugestão ao escolher exercício', () => {
  it('preenche o RPE por série vindo do servidor', async () => {
    vi.mocked(apiFetch).mockImplementation(async (path: string) => {
      if (path.startsWith('/mapping-canonical/options')) {
        return { items: [{ id: 9, exercise_name: 'Remada curvada', primary_group: 'Costas' }] } as never;
      }
      if (path.startsWith('/exercises/suggested-progression')) {
        return { reps_target: '8-10', load_target: '', rpe_target: '8', progression_note: null, has_history: false, rpe_targets_by_set: [7, 8, 9] } as never;
      }
      return { items: [] } as never;
    });
    const onChange = vi.fn();
    await render([row({ exercise_name: '', rpe_targets_by_set: [10, 10, 10], rpe_edited: true })], false, { onChange });
    const input = host.querySelector<HTMLInputElement>('input[placeholder="Nome do exercício"]')!;
    await act(async () => {
      input.focus();
      input.click();
    });
    const option = Array.from(document.body.querySelectorAll('[role="option"]')).find((o) => o.textContent?.includes('Remada curvada'));
    await click(option ?? null);
    await act(async () => {});
    const next = onChange.mock.calls.at(-1)![0] as WorkoutPlanExercise[];
    expect(next[0].rpe_targets_by_set).toEqual([7, 8, 9]);
    expect(next[0].rpe_edited).toBe(false);
    expect(apiFetch).toHaveBeenCalledWith('/exercises/suggested-progression?mapping_id=9');
  });
});
