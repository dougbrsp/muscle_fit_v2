// Modo Intensivo, PR 3: telas das etapas (drop set / rest-pause), reps extras
// da família além da falha e a confirmação das pendentes ao finalizar.
import { MantineProvider } from '@mantine/core';
import { createElement, type ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FALLBACK_ADVANCED_TECHNIQUES } from '@/lib/advanced-techniques';
import {
  createIntensiveBlock,
  resolveIntensiveProfile,
  type IntensiveProfile,
} from '@/lib/workout-mode-intensive';
import { theme } from '@/theme';
import { applicationLoadSourceText, IntensiveApplicationCard } from './IntensiveApplicationCard';
import { IntensiveBlock, type IntensiveBlockProps } from './IntensiveBlock';
import { IntensiveSetEntry } from './IntensiveSetEntry';
import { PendingRepsFinishPanel } from './PendingRepsFinishModal';

function render(element: ReactElement): string {
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, element)).replace(/<style[^>]*>[\s\S]*?<\/style>/g, '');
}

const T0 = Date.parse('2026-10-24T12:00:00Z');
const noop = () => {};

function tagOf(html: string, testId: string): string {
  const match = html.match(new RegExp(`<[^>]*data-testid="${testId}"[^>]*>`));
  return match ? match[0] : '';
}

function profileOf(code: string, overrides: Partial<Parameters<typeof resolveIntensiveProfile>[0]> = {}): IntensiveProfile {
  const p = resolveIntensiveProfile({
    techniqueCode: code,
    catalog: FALLBACK_ADVANCED_TECHNIQUES,
    parameters: { technique_code: code, target_rpe: 10 },
    plannedSetsCount: 3,
    itemRestSeconds: 90,
    ...overrides,
  });
  if (!p) throw new Error(code);
  return p;
}

const cardBase = {
  weightUnit: 'kg' as const,
  loadIncrementKg: 2.5,
  saving: false,
  canUndo: false,
  onAdjust: noop,
  onUndo: noop,
  onComplete: noop,
};

describe('IntensiveApplicationCard — drop set', () => {
  const drop = profileOf('drop_set', { parameters: { technique_code: 'drop_set', applications: 3, target_rpe: 10 } });

  it('"Drop 2 de 3" em destaque, carga do protocolo, ajuste imediato e RPE-alvo automático', () => {
    const html = render(
      createElement(IntensiveApplicationCard, {
        ...cardBase,
        profile: drop,
        stageLabel: 'Drop 2 de 3',
        stageNoun: 'Drop',
        planned: { kg: 32.5, source: 'protocol' },
        appliedItems: ['Drop 1: 8 reps · 40 kg'],
      }),
    );
    expect(html).toContain('Drop 2 de 3');
    expect(html).toContain('data-testid="intensive-application-load"');
    expect(html).toContain('32.5 kg');
    expect(html).toContain(applicationLoadSourceText('protocol'));
    expect(html).toContain('data-testid="quick-load-adjustment"');
    expect(html).toContain('Carga do drop');
    expect(html).toContain('RPE 10');
    expect(html).toContain('sem RPE por etapa');
    expect(html).toContain('Concluir drop');
    expect(html).toContain('Drop 1: 8 reps · 40 kg');
    // Sem descanso artificial: nenhum cronômetro de pausa.
    expect(html).not.toContain('data-testid="intensive-pause"');
  });

  it('redução extra aparece como fadiga (não como protocolo)', () => {
    const html = render(
      createElement(IntensiveApplicationCard, {
        ...cardBase,
        profile: drop,
        stageLabel: 'Drop 1 de 3',
        stageNoun: 'Drop',
        planned: { kg: 35, source: 'fatigue' },
        canUndo: true,
      }),
    );
    expect(html).toContain(applicationLoadSourceText('fatigue'));
    expect(html).not.toContain(applicationLoadSourceText('protocol') as string);
    expect(applicationLoadSourceText(null)).toBeNull();
  });

  it('Trocar, Pular exercício e Encerrar técnica continuam no rodapé', () => {
    const html = render(
      createElement(IntensiveApplicationCard, {
        ...cardBase,
        profile: drop,
        stageLabel: 'Drop 1 de 3',
        stageNoun: 'Drop',
        planned: { kg: 40, source: 'protocol' },
        onSkipApplication: noop,
        iconActions: [
          { key: 'swap', label: 'Trocar exercício', caption: 'Trocar', icon: null, onClick: noop },
          { key: 'skip', label: 'Pular exercício', caption: 'Pular', icon: null, onClick: noop },
          { key: 'end', label: 'Encerrar técnica', caption: 'Encerrar', icon: null, onClick: noop },
        ],
      }),
    );
    expect(html).toContain('aria-label="Trocar exercício"');
    expect(html).toContain('aria-label="Pular exercício"');
    expect(html).toContain('aria-label="Encerrar técnica"');
    expect(html).toContain('Pular drop');
  });
});

describe('IntensiveApplicationCard — rest-pause', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(T0);
  });
  afterEach(() => vi.useRealTimers());

  const rp = profileOf('rest_pause', {
    parameters: { technique_code: 'rest_pause', applications: 3, pause_seconds: 15, target_rpe: 9 },
  });

  it('pausa da prescrição com cronômetro, carga constante e sem RPE individual', () => {
    const html = render(
      createElement(IntensiveApplicationCard, {
        ...cardBase,
        profile: rp,
        stageLabel: 'Mini-bloco 2 de 3',
        stageNoun: 'Mini-bloco',
        planned: { kg: 60, source: null },
        pause: { key: 'item', endsAt: T0 + 15_000, totalSeconds: 15 },
      }),
    );
    expect(html).toContain('data-testid="intensive-pause"');
    expect(html).toContain('Pausa · 15s');
    expect(html).toContain('data-testid="intensive-pause-remaining"');
    expect(html).toContain('>15s<');
    expect(html).toContain('Carga (constante)');
    expect(html).toContain('RPE 9');
    expect(html).toContain('sem RPE por etapa');
    expect(html).not.toContain('data-testid="intensive-application-source"');
    expect(html).toContain('Mini-bloco 2 de 3');
    // Durante a pausa o "Concluir" fica bloqueado; só "Pular pausa" libera.
    expect(tagOf(html, 'intensive-application-complete')).toContain('disabled');
    expect(html).toContain('data-testid="intensive-skip-pause"');
    expect(html).toContain('Pular pausa');
    expect(html).toContain('data-testid="intensive-pause-hold"');
  });

  it('pausa vencida: "Pronto" sem iniciar sozinho', () => {
    const html = render(
      createElement(IntensiveApplicationCard, {
        ...cardBase,
        profile: rp,
        stageLabel: 'Mini-bloco 3 de 3',
        stageNoun: 'Mini-bloco',
        planned: { kg: 60, source: null },
        pause: { key: 'item', endsAt: T0 - 1000, totalSeconds: 15 },
      }),
    );
    expect(html).toContain('data-testid="intensive-pause-ready"');
    expect(html).toContain('Concluir mini-bloco');
    expect(tagOf(html, 'intensive-application-complete')).not.toContain('disabled');
    expect(html).not.toContain('data-testid="intensive-skip-pause"');
  });

  it('sem pausa em curso: "Concluir" livre e sem "Pular pausa"', () => {
    const html = render(
      createElement(IntensiveApplicationCard, {
        ...cardBase,
        profile: rp,
        stageLabel: 'Mini-bloco 1 de 3',
        stageNoun: 'Mini-bloco',
        planned: { kg: 60, source: null },
        pause: null,
      }),
    );
    expect(tagOf(html, 'intensive-application-complete')).not.toContain('disabled');
    expect(html).not.toContain('Pular pausa');
  });
});

describe('família além da falha — reps extras com um toque', () => {
  const forced = profileOf('forced_reps', { plannedSetsCount: 3 });

  it('última série: contador "+1" de reps extras e campo de RPE visível', () => {
    const html = render(
      createElement(IntensiveSetEntry, {
        profile: forced,
        setIndex: 3,
        totalSets: 3,
        plannedKg: 50,
        weightUnit: 'kg',
        repsTarget: '8-10',
        extraRepsEnabled: true,
        onComplete: noop,
      }),
    );
    expect(html).toContain('data-testid="intensive-extra-reps"');
    expect(html).toContain('+1');
    expect(html).not.toContain('Informar RPE');
    expect(html).toContain('data-testid="intensive-rpe-input"');
  });

  it('o bloco começa direto na série (sem briefing) e só a última mostra as extras', () => {
    const base: Omit<IntensiveBlockProps, 'state'> = {
      profile: forced,
      exerciseName: 'Rosca',
      equipmentName: null,
      repsTarget: '10',
      weightUnit: 'kg',
      loadIncrementKg: 1,
      dispatch: noop,
      onCompleteSet: noop,
      onRetry: noop,
      onCorrect: async () => {},
      onFinish: noop,
      onSkipExercise: noop,
    };
    const first = createIntensiveBlock(forced, 20);
    const html = render(createElement(IntensiveBlock, { ...base, state: first }));
    expect(html).not.toContain('data-testid="intensive-briefing"');
    expect(html).toContain('data-testid="intensive-set-entry"');
    expect(html).not.toContain('data-testid="intensive-extra-reps"');
    const last = { ...first, currentSet: 3 };
    expect(render(createElement(IntensiveBlock, { ...base, state: last }))).toContain('data-testid="intensive-extra-reps"');
  });
});

describe('regressão do FST-7 na tela', () => {
  it('briefing continua e não há reps extras', () => {
    const fst7 = profileOf('fst7', { plannedSetsCount: 7, itemRestSeconds: 30 });
    const html = render(
      createElement(IntensiveBlock, {
        profile: fst7,
        state: { ...createIntensiveBlock(fst7, 40), phase: 'SERIE_EM_EXECUCAO', currentSet: 7 },
        exerciseName: 'Crucifixo',
        equipmentName: null,
        repsTarget: '10',
        weightUnit: 'kg',
        loadIncrementKg: 2.5,
        dispatch: noop,
        onCompleteSet: noop,
        onRetry: noop,
        onCorrect: async () => {},
        onFinish: noop,
        onSkipExercise: noop,
      }),
    );
    expect(html).toContain('Série 7 de 7');
    expect(html).not.toContain('data-testid="intensive-extra-reps"');
    expect(render(createElement(IntensiveBlock, {
      profile: fst7,
      state: createIntensiveBlock(fst7, 40),
      exerciseName: 'Crucifixo',
      equipmentName: null,
      repsTarget: '10',
      weightUnit: 'kg',
      loadIncrementKg: 2.5,
      dispatch: noop,
      onCompleteSet: noop,
      onRetry: noop,
      onCorrect: async () => {},
      onFinish: noop,
      onSkipExercise: noop,
    }))).toContain('data-testid="intensive-briefing"');
  });
});

describe('PendingRepsFinishPanel — confirmação antes de finalizar', () => {
  const items = [
    { exerciseId: 11, exerciseName: 'Crucifixo', setNumber: 4, setType: 'working', weightKg: 45, label: 'série 4' },
    { exerciseId: 11, exerciseName: 'Crucifixo', setNumber: 6, setType: 'rest_pause', weightKg: 35, label: 'série 2 · mini-bloco 2' },
  ];

  it('lista compacta com confirmação por série e "Finalizar mesmo assim"', () => {
    const html = render(
      createElement(PendingRepsFinishPanel, {
        items,
        weightUnit: 'kg',
        finishing: false,
        onConfirm: async () => {},
        onFinish: noop,
        onCancel: noop,
      }),
    );
    expect(html).toContain('2 séries com repetições pendentes');
    expect(html.match(/data-testid="pending-reps-row"/g)).toHaveLength(2);
    expect(html).toContain('Crucifixo · série 4 · 45 kg');
    expect(html).toContain('Crucifixo · série 2 · mini-bloco 2 · 35 kg');
    expect(html).toContain('aria-label="Repetições de Crucifixo, série 2 · mini-bloco 2"');
    expect(html).toContain('data-testid="pending-reps-confirm"');
    expect(html).toContain('Finalizar mesmo assim');
    expect(html).toContain('Voltar ao treino');
  });

  it('tudo confirmado: "Finalizar treino"; erro do fim visível na própria confirmação', () => {
    const html = render(
      createElement(PendingRepsFinishPanel, {
        items: [],
        weightUnit: 'kg',
        finishing: false,
        error: 'Não foi possível finalizar o treino.',
        onConfirm: async () => {},
        onFinish: noop,
        onCancel: noop,
      }),
    );
    expect(html).toContain('Repetições confirmadas');
    expect(html).toContain('data-testid="pending-reps-finish-now"');
    expect(html).toContain('Não foi possível finalizar o treino.');
  });
});
