// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { theme } from '@/theme';
import { FinishWorkoutConfirmModal } from './FinishWorkoutConfirmModal';

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

async function mount(props: Partial<Parameters<typeof FinishWorkoutConfirmModal>[0]> = {}) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root!.render(
      createElement(
        MantineProvider,
        { theme },
        createElement(FinishWorkoutConfirmModal, {
          opened: true,
          finishing: false,
          error: null,
          onConfirm,
          onCancel,
          ...props,
        }),
      ),
    );
    await new Promise((r) => setTimeout(r, 50));
  });
  return { onConfirm, onCancel };
}

const button = (label: string) =>
  Array.from(document.body.querySelectorAll('button')).find((b) => b.textContent?.includes(label)) as
    | HTMLButtonElement
    | undefined;

const click = (el: HTMLElement) =>
  act(async () => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });

describe('FinishWorkoutConfirmModal', () => {
  it('fechado não renderiza os botões', async () => {
    await mount({ opened: false });
    expect(button('Confirmar encerramento')).toBeUndefined();
    expect(button('Continuar treino')).toBeUndefined();
  });

  it('aberto mostra as duas ações; Continuar treino cancela sem confirmar', async () => {
    const { onConfirm, onCancel } = await mount();
    expect(button('Confirmar encerramento')).toBeDefined();
    await click(button('Continuar treino')!);
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('cancelLabel troca o rótulo de desistir e ainda cancela', async () => {
    const { onCancel } = await mount({ cancelLabel: 'Voltar' });
    expect(button('Continuar treino')).toBeUndefined();
    await click(button('Voltar')!);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('fechar pelo X cancela', async () => {
    const { onConfirm, onCancel } = await mount();
    await click(document.body.querySelector('button[aria-label="Fechar"]') as HTMLElement);
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('Confirmar encerramento chama onConfirm uma vez', async () => {
    const { onConfirm, onCancel } = await mount();
    await click(button('Confirmar encerramento')!);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('finishing desabilita os botões e o clique extra não confirma de novo', async () => {
    const { onConfirm } = await mount({ finishing: true });
    const confirmar = button('Confirmar encerramento')!;
    expect(confirmar.disabled).toBe(true);
    expect(button('Continuar treino')!.disabled).toBe(true);
    await click(confirmar);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('erro aparece com role alert e o modal continua aberto', async () => {
    await mount({ error: 'Falhou ao encerrar' });
    expect(document.body.querySelector('[role="alert"]')?.textContent).toBe('Falhou ao encerrar');
    expect(button('Confirmar encerramento')).toBeDefined();
  });
});
