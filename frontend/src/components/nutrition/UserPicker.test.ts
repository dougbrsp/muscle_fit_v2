// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { UserPicker } from './UserPicker';

// Regressão (2026-11-03): rótulo inicial ("Doug") diferente do rótulo do lookup
// ("Doug (doug)") fazia o Select searchable do Mantine e o UserPicker se
// realimentarem sem fim (React #185, "Maximum update depth exceeded").
// Aqui o componente é MONTADO de verdade (jsdom, efeitos rodando).

const fetchUserLookup = vi.hoisted(() => vi.fn());
const fetchMyStudents = vi.hoisted(() => vi.fn());
vi.mock('@/lib/user-lookup', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/user-lookup')>()),
  fetchUserLookup,
  fetchMyStudents,
}));

const DOUG = { id: 1, username: 'doug', full_name: 'Doug' };
const ANA = { id: 2, username: 'ana', full_name: 'Ana Souza' };

let root: Root | null = null;
let host: HTMLElement;
let erros: unknown[][];

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
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver ||= RO;
  window.HTMLElement.prototype.scrollIntoView ||= () => {};
});

beforeEach(() => {
  erros = [];
  vi.spyOn(console, 'error').mockImplementation((...args) => {
    erros.push(args);
  });
  fetchUserLookup.mockImplementation(async (termo: string) => ({
    items: [DOUG, ANA].filter((u) => !termo || `${u.full_name} ${u.username}`.toLowerCase().includes(termo.toLowerCase())),
    total: 2,
  }));
  host = document.createElement('div');
  document.body.appendChild(host);
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  root = null;
  host.remove();
  vi.restoreAllMocks();
  fetchUserLookup.mockReset();
  fetchMyStudents.mockReset();
});

const esperar = async (vezes = 5) => {
  for (let i = 0; i < vezes; i++) await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
};

let valorDoPai: number | null = null;
let onChangeDoPai: ReturnType<typeof vi.fn>;

// Pai real: guarda o `value` em estado, como o PlanManager em producao.
function Pai({ initial, source }: { initial?: { id: number; label: string }; source?: 'all' | 'students' }) {
  const [value, setValue] = useState<number | null>(initial?.id ?? null);
  valorDoPai = value;
  return createElement(UserPicker, {
    value,
    initial,
    source,
    onChange: (id: number | null) => {
      onChangeDoPai(id);
      setValue(id);
    },
  });
}

async function montar(initial?: { id: number; label: string }, source?: 'all' | 'students') {
  onChangeDoPai = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  root = createRoot(host);
  await act(async () => {
    root!.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(MantineProvider, null, createElement(Pai, { initial, source })),
      ),
    );
  });
  await esperar();
}

const input = () => host.querySelector('input:not([type="hidden"])') as HTMLInputElement;
const loop = () => erros.filter((e) => e.some((parte) => String(parte).includes('Maximum update depth')));
const opcoes = () => Array.from(document.querySelectorAll('[role="option"]')).map((o) => o.textContent);
const limparBotao = () => host.querySelector('[class*="ClearButton"], [class*="CloseButton"]') as HTMLElement | null;

async function abrir() {
  await act(async () => {
    input().focus();
    input().click();
  });
  await esperar(2);
}

async function escolher(rotulo: string) {
  const alvo = Array.from(document.querySelectorAll('[role="option"]')).find((o) => o.textContent === rotulo) as HTMLElement | undefined;
  expect(alvo, `opção "${rotulo}" na lista: ${JSON.stringify(opcoes())}`).toBeDefined();
  await act(async () => {
    alvo!.click();
  });
  await esperar(3);
}

async function limpar() {
  const botao = limparBotao();
  expect(botao).not.toBeNull();
  await act(async () => {
    botao!.click();
  });
  await esperar(3);
}

async function digitar(texto: string) {
  const el = input();
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
    setter.call(el, texto);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await esperar(3);
}

describe('UserPicker montado (efeitos rodando)', () => {
  it('rótulos iguais: abre estável e mostra o aluno', async () => {
    await montar({ id: 1, label: 'Doug (doug)' });
    expect(loop()).toEqual([]);
    expect(input().value).toBe('Doug (doug)');
  });

  it('rótulo inicial divergente do lookup: sem ciclo, aluno continua visível', async () => {
    await montar({ id: 1, label: 'Doug' });
    expect(loop()).toEqual([]);
    expect(input().value).toBe('Doug');
    expect(fetchUserLookup.mock.calls.length).toBeLessThan(6);
  });

  it('lookup renomeou o aluno depois do rótulo inicial: sem ciclo, rótulo escolhido preservado', async () => {
    fetchUserLookup.mockImplementation(async () => ({ items: [{ ...DOUG, full_name: 'Douglas Silva' }, ANA], total: 2 }));
    await montar({ id: 1, label: 'Doug (doug)' });
    expect(loop()).toEqual([]);
    expect(input().value).toBe('Doug (doug)');
  });

  it('escolher outra opção atualiza o pai e a caixa', async () => {
    await montar({ id: 1, label: 'Doug' });
    await abrir();
    await escolher('Ana Souza (ana)');
    expect(valorDoPai).toBe(2);
    expect(onChangeDoPai).toHaveBeenLastCalledWith(2);
    expect(input().value).toBe('Ana Souza (ana)');
    expect(loop()).toEqual([]);
  });

  it('limpar e escolher de novo: a lista não esvazia e o aluno volta', async () => {
    await montar({ id: 1, label: 'Doug' });
    await limpar();
    expect(valorDoPai).toBeNull();
    expect(input().value).toBe('');
    await abrir();
    expect(opcoes().length).toBeGreaterThan(0);
    await escolher('Doug (doug)');
    expect(valorDoPai).toBe(1);
    expect(input().value).toBe('Doug (doug)');
    expect(loop()).toEqual([]);
  });

  it('digitar e depois escolher consulta o servidor com o texto e fixa o escolhido', async () => {
    await montar({ id: 1, label: 'Doug' });
    await digitar('ana');
    expect(fetchUserLookup).toHaveBeenCalledWith('ana');
    await escolher('Ana Souza (ana)');
    expect(valorDoPai).toBe(2);
    expect(input().value).toBe('Ana Souza (ana)');
    expect(loop()).toEqual([]);
  });

  it('preserva a ordem da API e não duplica o escolhido', async () => {
    fetchUserLookup.mockImplementation(async () => ({ items: [ANA, DOUG], total: 2 }));
    await montar({ id: 1, label: 'Doug' });
    await abrir();
    expect(opcoes()).toEqual(['Ana Souza (ana)', 'Doug']);
    expect(loop()).toEqual([]);
  });

  it('modo alunos: usa só /access/my-students, filtra a busca no cliente e escolhe', async () => {
    fetchMyStudents.mockResolvedValue([DOUG, ANA]);
    await montar(undefined, 'students');
    expect(fetchUserLookup).not.toHaveBeenCalled();
    expect(fetchMyStudents).toHaveBeenCalledTimes(1);
    await abrir();
    expect(opcoes()).toEqual(['Doug (doug)', 'Ana Souza (ana)']);
    await digitar('ANA');
    expect(opcoes()).toEqual(['Ana Souza (ana)']);
    expect(fetchMyStudents).toHaveBeenCalledTimes(1);
    await escolher('Ana Souza (ana)');
    expect(valorDoPai).toBe(2);
    expect(loop()).toEqual([]);
  });

  it('modo alunos sem vínculo: avisa e não consulta o lookup geral', async () => {
    fetchMyStudents.mockResolvedValue([]);
    await montar(undefined, 'students');
    expect(host.textContent).toContain('Você não tem alunos vinculados');
    expect(fetchUserLookup).not.toHaveBeenCalled();
  });

  it('modo padrão não consulta /access/my-students', async () => {
    await montar({ id: 1, label: 'Doug' });
    expect(fetchMyStudents).not.toHaveBeenCalled();
    expect(host.textContent).not.toContain('alunos vinculados');
  });

  it('escolhido fora do resultado da busca vai ao início', async () => {
    fetchUserLookup.mockImplementation(async () => ({ items: [ANA], total: 1 }));
    await montar({ id: 1, label: 'Doug' });
    await abrir();
    expect(opcoes()).toEqual(['Doug', 'Ana Souza (ana)']);
    expect(loop()).toEqual([]);
  });
});
