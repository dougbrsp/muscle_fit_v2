// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { DayTraining } from '@/lib/nutrition-types';
import { TrainingKcalRow } from './TrainingKcalRow';

// As três formas da linha de gasto do dia (a tela só exibe o que o motor mandou):
// sem extra, treino + cardio e dia só com cardio (sem o rótulo "Treino").

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
  document.body.innerHTML = '';
});

async function montar(training: DayTraining) {
  root = createRoot(host);
  await act(async () => {
    root!.render(createElement(MantineProvider, { env: 'test' }, createElement(TrainingKcalRow, { training })));
  });
}

const BASE: DayTraining = { is_training_day: true, training_known: true, sessions: [], comparison: null };
const sessao = { id: 1, name: 'Treino A', calories_kcal: 428, calories_source: 'intervals', calories_source_label: 'Intervals', duration_seconds: 3600 };

describe('TrainingKcalRow: gasto do dia com cardio e caminhadas', () => {
  it('sem extra: como antes, sem linha de total', async () => {
    await montar({ ...BASE, sessions: [sessao], extra_activity: { kcal: 0, kcal_nature: null, items: [] }, activity_kcal: { training_kcal: 428, extra_kcal: 0, total_kcal: 428 } });
    expect(host.textContent).toContain('Treino do dia');
    expect(host.querySelector('[data-testid="gasto-atividade"]')).toBeNull();
  });

  it('resposta antiga sem os campos novos não quebra', async () => {
    await montar({ ...BASE, is_training_day: false });
    expect(host.textContent).toContain('Nenhum treino concluído neste dia.');
  });

  it('dia de fallback do modo automático: diz que o gasto foi estimado pelo fator, sem sugerir soma', async () => {
    await montar({
      ...BASE,
      sessions: [sessao],
      tdee_basis: 'auto_fallback',
      tdee_basis_note: 'Estimado pelo fator: Intervals sem sincronizar. O treino e o cardio deste dia não foram somados ao gasto.',
      extra_activity: { kcal: 0, kcal_nature: null, items: [] },
      activity_kcal: { training_kcal: 428, extra_kcal: 0, total_kcal: 428 },
    });
    expect(host.querySelector('[data-testid="gasto-fallback"]')?.textContent).toContain('Intervals sem sincronizar');
    expect(host.querySelector('[data-testid="gasto-atividade"]')).toBeNull();
  });

  it('dia de plano adaptativo (sem aviso de fallback): mostra treino e cardio como atividade, sem rótulo de modo legado', async () => {
    await montar({
      ...BASE,
      sessions: [sessao],
      tdee_basis: 'adaptive',
      tdee_basis_note: null,
      extra_activity: { kcal: 0, kcal_nature: null, items: [] },
      activity_kcal: { training_kcal: 428, extra_kcal: 0, total_kcal: 428 },
    });
    expect(host.querySelector('[data-testid="gasto-fallback"]')).toBeNull();
    expect(host.textContent).not.toMatch(/fator|embutido|somado à parte/i);
  });

  it('dia misto: mostra o usado pelo motor e que é parcialmente estimado', async () => {
    const valor = (kcal: number | null) => ({ kcal, kcal_nature: null, nature: null, quality: null, limitations: [] }) as never;
    await montar({
      ...BASE,
      sessions: [sessao],
      comparison: {
        reported: valor(848),
        estimated: valor(900),
        used: valor(880),
        sides: [],
        partial: true,
      },
    });
    const texto = (host.querySelector('[data-testid="treino-parcial"]')?.textContent ?? '').replace(/ /g, ' ');
    expect(texto).toContain('Usado pelo motor: 880 kcal');
    expect(texto).toContain('estimado pela duração');
  });

  it('treino + cardio: mostra a separação e o total', async () => {
    await montar({
      ...BASE,
      sessions: [sessao],
      extra_activity: {
        kcal: 395,
        kcal_nature: 'reported',
        items: [
          { kind: 'cardio', calories_kcal: 51, label: 'Walk' },
          { kind: 'cardio', calories_kcal: 79, label: 'Walk' },
          { kind: 'cardio', calories_kcal: 265, label: 'VirtualRun' },
        ],
      },
      activity_kcal: { training_kcal: 428, extra_kcal: 395, total_kcal: 823 },
    });
    const linha = (host.querySelector('[data-testid="gasto-atividade"]')?.textContent ?? '').replace(/ /g, ' ');
    expect(linha).toBe('Treino 428 kcal + Cardio e caminhadas 395 kcal = 823 kcal');
    expect(host.textContent).toContain('VirtualRun');
    expect(host.textContent).toContain('Treino do dia');
  });

  it('dia só com cardio: sem o rótulo "Treino" e sem "nenhum treino concluído"', async () => {
    await montar({
      is_training_day: false,
      training_known: true,
      sessions: [],
      comparison: null,
      extra_activity: { kcal: 427, kcal_nature: 'reported', items: [{ kind: 'cardio', calories_kcal: 427, label: 'VirtualRun' }] },
      activity_kcal: { training_kcal: null, extra_kcal: 427, total_kcal: 427 },
    });
    const texto = (host.textContent ?? '').replace(/ /g, ' ');
    expect(texto).toContain('Atividades do dia');
    expect(texto).toContain('Cardio e caminhadas 427 kcal');
    expect(texto).not.toContain('Treino do dia');
    expect(texto).not.toContain('Nenhum treino');
    expect(texto).not.toMatch(/Treino \d/);
  });
});
