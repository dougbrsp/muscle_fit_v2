import { MantineProvider } from '@mantine/core';
import { createElement, type ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FALLBACK_ADVANCED_TECHNIQUES } from '@/lib/advanced-techniques';
import {
  createIntensiveBlock,
  intensiveReducer,
  resolveIntensiveProfile,
  type IntensiveBlockState,
  type IntensiveEvent,
  type IntensiveProfile,
} from '@/lib/workout-mode-intensive';
import { theme } from '@/theme';
import { IntensiveBlock, type IntensiveBlockProps } from './IntensiveBlock';
import { IntensiveBriefing, intensiveHeadline } from './IntensiveBriefing';
import { IntensiveRest } from './IntensiveRest';
import { IntensiveReview } from './IntensiveReview';
import { IntensiveSetEntry, intensiveRpeInitialText, parseRepsInput, parseRpeInput } from './IntensiveSetEntry';
import { QuickLoadAdjustment } from './QuickLoadAdjustment';

function render(element: ReactElement): string {
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, element)).replace(/<style[^>]*>[\s\S]*?<\/style>/g, '');
}

const T0 = Date.parse('2026-10-24T12:00:00Z');

function profile(targetRpe: number | null = 10): IntensiveProfile {
  const p = resolveIntensiveProfile({
    techniqueCode: 'fst7',
    catalog: FALLBACK_ADVANCED_TECHNIQUES,
    parameters: targetRpe != null ? { technique_code: 'fst7', target_rpe: targetRpe } : null,
    plannedSetsCount: 7,
    itemRestSeconds: 30,
  });
  if (!p) throw new Error('perfil');
  return p;
}

function run(state: IntensiveBlockState, ...events: IntensiveEvent[]) {
  return events.reduce(intensiveReducer, state);
}

const noop = () => {};

/** Tag de abertura do elemento com `data-testid` (a ordem dos atributos não importa). */
function tagOf(html: string, testId: string): string {
  const match = html.match(new RegExp(`<[^>]*data-testid="${testId}"[^>]*>`));
  return match ? match[0] : '';
}

describe('IntensiveBriefing', () => {
  it('cabeçalho, carga inicial, RPE-alvo do backend e "Iniciar FST-7"', () => {
    const html = render(
      createElement(IntensiveBriefing, {
        profile: profile(),
        exerciseName: 'Crucifixo na máquina',
        equipmentName: 'Peck deck',
        plannedKg: 45,
        weightUnit: 'kg',
        loadSuffix: null,
        repsTarget: '8-12',
        onStart: noop,
      }),
    );
    expect(intensiveHeadline(profile())).toBe('FST-7 — 7 séries · 30s de descanso');
    expect(html).toContain('FST-7 — 7 séries · 30s de descanso');
    expect(html).toContain('Crucifixo na máquina');
    expect(html).toContain('Peck deck');
    expect(html).toContain('45 kg');
    expect(html).toContain('8-12');
    expect(html).toContain('RPE 10');
    expect(html).toMatch(/alongue/i);
    expect(html).toContain('Iniciar FST-7');
  });

  it('sem `target_rpe` no snapshot: não inventa número', () => {
    const html = render(
      createElement(IntensiveBriefing, {
        profile: profile(null),
        exerciseName: 'X',
        equipmentName: null,
        plannedKg: null,
        weightUnit: 'kg',
        repsTarget: null,
        onStart: noop,
      }),
    );
    expect(html).toContain('Definido pelo servidor');
    expect(html).not.toMatch(/RPE \d/);
  });
});

describe('IntensiveSetEntry', () => {
  it('"Série 4 de 7", carga planejada, stepper vazio e RPE registrado pré-preenchido com o alvo', () => {
    const html = render(
      createElement(IntensiveSetEntry, {
        profile: profile(9),
        setIndex: 4,
        totalSets: 7,
        plannedKg: 60,
        weightUnit: 'kg',
        loadSuffix: 'por halter',
        repsTarget: '8-12',
        onComplete: noop,
      }),
    );
    expect(html.replace(/<!-- -->/g, '')).toContain('Série 4 de 7');
    expect(html).toContain('60 kg');
    expect(html).toContain('por halter');
    expect(html).toContain('RPE 9');
    expect(html).toContain('Concluir série');
    // Campo sempre visível (sem o link "Informar RPE"), com o alvo do backend.
    expect(html).not.toContain('Informar RPE');
    expect(html).toContain('RPE registrado nesta série');
    expect(tagOf(html, 'intensive-rpe-input')).toContain('value="9"');
    // Reps nunca pré-preenchidas com a meta.
    expect(tagOf(html, 'intensive-reps-input')).toContain('value=""');
    expect(html).toContain('a série fica pendente');
  });

  it('sem `target_rpe` (snapshot antigo): campo de RPE visível e vazio', () => {
    const html = render(
      createElement(IntensiveSetEntry, {
        profile: profile(null),
        setIndex: 1,
        totalSets: 7,
        plannedKg: 60,
        weightUnit: 'kg',
        repsTarget: '8-12',
        onComplete: noop,
      }),
    );
    expect(tagOf(html, 'intensive-rpe-input')).toContain('value=""');
    expect(html).toContain('Definido pelo servidor');
    expect(html).not.toMatch(/RPE \d/);
  });

  it('RPE: valor inicial = alvo do backend; editado vale; apagado/inválido = null', () => {
    expect(intensiveRpeInitialText(10)).toBe('10');
    expect(intensiveRpeInitialText(9.5)).toBe('9.5');
    expect(intensiveRpeInitialText(null)).toBe('');
    // Pré-preenchido sem edição: o alvo é o que vai como `rpe`.
    expect(parseRpeInput(intensiveRpeInitialText(10))).toBe(10);
    // Editado (vírgula aceita).
    expect(parseRpeInput('8')).toBe(8);
    expect(parseRpeInput('8,5')).toBe(8.5);
    // Apagado ou lixo: sem RPE.
    expect(parseRpeInput('')).toBeNull();
    expect(parseRpeInput('   ')).toBeNull();
    expect(parseRpeInput('abc')).toBeNull();
    expect(parseRpeInput(intensiveRpeInitialText(null))).toBeNull();
  });

  it('reps digitadas: só inteiros > 0; o resto vira pendente', () => {
    expect(parseRepsInput('12')).toBe(12);
    expect(parseRepsInput('0')).toBeNull();
    expect(parseRepsInput('')).toBeNull();
    expect(parseRepsInput('7.5')).toBeNull();
  });
});

describe('QuickLoadAdjustment', () => {
  it('−1 incremento, −5%, −10%, Editar e Desfazer na unidade exibida', () => {
    const html = render(
      createElement(QuickLoadAdjustment, {
        plannedKg: 70,
        weightUnit: 'kg',
        loadIncrementKg: 5,
        canUndo: false,
        onAdjust: noop,
        onUndo: noop,
      }),
    );
    for (const label of ['−5 kg', '−5%', '−10%', 'Editar', 'Desfazer']) expect(html).toContain(label);
    expect(html).toContain('70 kg');
    expect(tagOf(html, 'quick-adjust-undo')).toContain('disabled');
  });

  it('lb: passo convertido e rótulo da convenção', () => {
    const html = render(
      createElement(QuickLoadAdjustment, {
        plannedKg: 70.31,
        weightUnit: 'lb',
        loadIncrementKg: null,
        loadSuffix: 'por lado',
        canUndo: true,
        onAdjust: noop,
        onUndo: noop,
      }),
    );
    expect(html).toContain('−5 lb');
    expect(html).toContain('155 lb');
    expect(html).toContain('por lado');
  });
});

describe('IntensiveRest', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(T0);
  });
  afterEach(() => vi.useRealTimers());

  const resting = () =>
    run(createIntensiveBlock(profile(), 60), { type: 'START' }, {
      type: 'COMPLETE_SET',
      reps: null,
      clientKey: 'k1',
      setNumber: 3,
      now: T0,
      restSeconds: 30,
    });

  it('30 s do item, próxima série com a carga planejada, alongamento e chip de pendentes', () => {
    const state = run(resting(), { type: 'SAVE_OK', clientKey: 'k1' });
    const html = render(
      createElement(IntensiveRest, {
        state,
        profile: profile(),
        weightUnit: 'kg',
        onRestAdjusted: noop,
        onRestExpired: noop,
        onContinue: noop,
        onRetry: noop,
        onConfirmReps: async () => {},
      }),
    );
    expect(html).toContain('00:30');
    expect(html).toContain('Série 2 de 7');
    expect(html).toContain('60 kg');
    expect(html).toMatch(/alongue/i);
    expect(html).toContain('Repetições pendentes (1)');
    expect(html).toContain('Pular descanso');
    expect(html).not.toContain('Pronto para a próxima série');
  });

  it('descanso zerado: "Pronto para a próxima série" + "Iniciar próxima série" (sem início automático)', () => {
    vi.setSystemTime(T0 + 31_000);
    const state = run(resting(), { type: 'SAVE_OK', clientKey: 'k1' }, { type: 'REST_EXPIRED' });
    expect(state.phase).toBe('PROXIMA_SERIE_PRONTA');
    const html = render(
      createElement(IntensiveRest, {
        state,
        profile: profile(),
        weightUnit: 'kg',
        onRestAdjusted: noop,
        onRestExpired: noop,
        onContinue: noop,
        onRetry: noop,
        onConfirmReps: async () => {},
      }),
    );
    expect(html).toContain('Pronto para a próxima série');
    expect(html).toContain('Iniciar próxima série');
  });

  it('falha de rede: erro visível sobre o cronômetro com "Tentar de novo"', () => {
    const state = run(resting(), { type: 'SAVE_FAILED', clientKey: 'k1', error: 'Sem conexão' });
    const html = render(
      createElement(IntensiveRest, {
        state,
        profile: profile(),
        weightUnit: 'kg',
        onRestAdjusted: noop,
        onRestExpired: noop,
        onContinue: noop,
        onRetry: noop,
        onConfirmReps: async () => {},
      }),
    );
    expect(html.indexOf('intensive-sync-error')).toBeLessThan(html.indexOf('rest-clock'));
    expect(html).toContain('Série 1 não foi salva: Sem conexão');
    expect(html).toContain('Tentar de novo');
  });
});

describe('IntensiveReview', () => {
  function reviewState(): IntensiveBlockState {
    let s = createIntensiveBlock({ techniqueCode: 'fst7', totalSets: 2 }, 50);
    s = run(
      s,
      { type: 'START' },
      { type: 'COMPLETE_SET', reps: 12, clientKey: 'a', setNumber: 1, now: T0, restSeconds: 30 },
      { type: 'SAVE_OK', clientKey: 'a' },
      { type: 'SKIP_REST' },
      { type: 'COMPLETE_SET', reps: null, clientKey: 'b', setNumber: 2, now: T0 + 60_000, restSeconds: 30 },
    );
    return s;
  }

  it('tabela Série | Reps | Carga | Status e pendentes destacadas antes de encerrar', () => {
    const state = run(reviewState(), { type: 'SAVE_OK', clientKey: 'b' });
    expect(state.phase).toBe('REVISAO_FINAL');
    const html = render(
      createElement(IntensiveReview, {
        state,
        profile: profile(),
        weightUnit: 'kg',
        onCorrect: async () => {},
        onRetry: noop,
        onFinish: noop,
      }),
    );
    // Lista acessível: botão "Corrigir" próprio por série, sem células dentro de botão.
    expect(html).not.toContain('role="table"');
    expect(html).not.toContain('role="cell"');
    expect(html).toContain('<ul');
    expect(html).toContain('aria-label="Corrigir série 2"');
    const review = html.slice(0, html.indexOf('</section>'));
    expect(review).not.toMatch(/<button[^>]*>(?:(?!<\/button>)[\s\S])*<span/);
    expect(html).toContain('Série 1');
    expect(html).toContain('Concluída');
    expect(html).toContain('Pendente');
    expect(html).toContain('A série 2 está sem repetições');
    expect(html).toContain('Concluir FST-7 com 1 pendente');
    expect(tagOf(html, 'intensive-finish')).not.toContain('disabled');
  });

  it('lista as reps extras da família além da falha', () => {
    let s = createIntensiveBlock({ techniqueCode: 'forced_reps', totalSets: 1 }, 50);
    s = run(
      s,
      { type: 'START' },
      { type: 'COMPLETE_SET', reps: 8, clientKey: 'a', setNumber: 1, now: T0, restSeconds: 30, extraReps: 3, extraClientKey: 'x' },
      { type: 'SAVE_OK', clientKey: 'a' },
    );
    const html = render(
      createElement(IntensiveReview, {
        state: { ...s, phase: 'REVISAO_FINAL' },
        profile: profile(),
        weightUnit: 'kg',
        onCorrect: async () => {},
        onRetry: noop,
        onFinish: noop,
      }),
    );
    expect(html).toContain('data-testid="intensive-review-extra-1"');
    expect(html).toContain('+ 3 reps extras · 50 kg');
  });

  it('série ainda não gravada: CTA bloqueado com aviso', () => {
    const state = reviewState();
    const html = render(
      createElement(IntensiveReview, {
        state: { ...state, phase: 'REVISAO_FINAL' },
        profile: profile(),
        weightUnit: 'kg',
        onCorrect: async () => {},
        onRetry: noop,
        onFinish: noop,
      }),
    );
    expect(html).toContain('Salvando');
    expect(tagOf(html, 'intensive-finish')).toContain('disabled');
  });
});

describe('IntensiveBlock (fase -> tela)', () => {
  const base: Omit<IntensiveBlockProps, 'state'> = {
    profile: profile(),
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
    onSwap: noop,
  };

  it('série nova remonta a entrada (key = série atual) e o RPE volta ao alvo', () => {
    const executing = run(createIntensiveBlock(profile(), 40), { type: 'START' });
    const entryOf = (state: IntensiveBlockState) => {
      // IntensiveBlock não usa hooks: a árvore de elementos expõe a `key` da entrada.
      const tree = IntensiveBlock({ ...base, state }) as ReactElement<{ children: ReactElement[] }>;
      const entry = tree.props.children.find((child) => child?.type === IntensiveSetEntry);
      if (!entry) throw new Error('entrada da série');
      return entry;
    };
    expect(entryOf(executing).key).toBe('1');
    const second = run(
      executing,
      { type: 'COMPLETE_SET', reps: 10, rpe: 8, clientKey: 'a', setNumber: 1, now: T0, restSeconds: 30 },
      { type: 'SAVE_OK', clientKey: 'a' },
      { type: 'SKIP_REST' },
    );
    expect(second.phase).toBe('SERIE_EM_EXECUCAO');
    expect(entryOf(second).key).toBe('2');
    const html = render(createElement(IntensiveBlock, { ...base, state: second }));
    expect(tagOf(html, 'intensive-rpe-input')).toContain('value="10"');
  });

  it('briefing, série com Trocar/Pular/Encerrar técnica e registro pendente', () => {
    const briefing = createIntensiveBlock(profile(), 40);
    expect(render(createElement(IntensiveBlock, { ...base, state: briefing }))).toContain('data-testid="intensive-briefing"');
    const executing = run(briefing, { type: 'START' });
    const html = render(createElement(IntensiveBlock, { ...base, state: executing }));
    expect(html).toContain('data-testid="intensive-set-entry"');
    expect(html).toContain('aria-label="Trocar exercício"');
    expect(html).toContain('aria-label="Pular exercício"');
    expect(html).toContain('aria-label="Encerrar técnica"');
    const registering = run(executing, {
      type: 'COMPLETE_SET',
      reps: 10,
      clientKey: 'z',
      setNumber: 1,
      now: T0,
      restSeconds: 30,
    }, { type: 'END_TECHNIQUE' });
    expect(registering.phase).toBe('REGISTRO_PENDENTE');
    expect(render(createElement(IntensiveBlock, { ...base, state: registering }))).toContain('Confirmando as séries');
  });
});
