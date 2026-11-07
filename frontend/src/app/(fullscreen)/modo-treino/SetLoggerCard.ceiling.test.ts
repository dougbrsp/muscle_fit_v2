// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { theme } from '@/theme';
import { SetLoggerCard } from './SetLoggerCard';
import type { LoadCeilingControl } from './useEquipmentLoadCeiling';

let root: Root | null = null;
let host: HTMLElement;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  window.matchMedia ||= ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  globalThis.ResizeObserver ||= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});
beforeEach(() => {
  host = document.createElement('div');
  document.body.appendChild(host);
});
afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  root = null;
  host.remove();
});

async function montar(loadCeiling: LoadCeilingControl) {
  root = createRoot(host);
  await act(async () => {
    root!.render(
      createElement(
        MantineProvider,
        { theme, env: 'test' },
        createElement(SetLoggerCard, {
          exerciseName: 'Leg press',
          setLabel: 'Série 1 de 3',
          defaultReps: 10,
          defaultWeight: 40,
          defaultRpe: 8,
          saving: false,
          onSave: () => {},
          showMuscleMap: false,
          loadCeiling,
        }),
      ),
    );
  });
}

describe('SetLoggerCard — clique no teto de carga', () => {
  it('"Carga máxima do equipamento" chama onMark com a carga do campo em kg', async () => {
    const onMark = vi.fn();
    await montar({ maxLoadKg: null, busy: false, onMark, onClear: () => {} });
    const button = host.querySelector<HTMLButtonElement>('[data-testid="load-ceiling-mark"]');
    expect(button).not.toBeNull();
    await act(async () => button!.click());
    expect(onMark).toHaveBeenCalledWith(40);
  });

  it('"Desfazer" chama onClear', async () => {
    const onClear = vi.fn();
    await montar({ maxLoadKg: 100, busy: false, onMark: () => {}, onClear });
    const button = host.querySelector<HTMLButtonElement>('[data-testid="load-ceiling-undo"]');
    expect(button).not.toBeNull();
    await act(async () => button!.click());
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it('ocupado: a ação fica desabilitada', async () => {
    const onMark = vi.fn();
    await montar({ maxLoadKg: null, busy: true, onMark, onClear: () => {} });
    const button = host.querySelector<HTMLButtonElement>('[data-testid="load-ceiling-mark"]');
    expect(button?.disabled).toBe(true);
    await act(async () => button!.click());
    expect(onMark).not.toHaveBeenCalled();
  });
});
