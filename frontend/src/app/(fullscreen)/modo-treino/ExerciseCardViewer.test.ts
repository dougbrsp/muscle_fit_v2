import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, type ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { ExerciseBriefingResponse } from '@/lib/exercise-briefing-types';
import type { ExerciseCardSummary } from '@/lib/workout-plans-types';
import { theme } from '@/theme';
import { buildBriefingSections, parseExecutionDescription } from '@/lib/exercise-briefing';
import { BriefingInstructions, ExerciseBriefingScreen } from './ExerciseBriefingScreen';
import { ExerciseCardViewer } from './ExerciseCardViewer';
import { SetLoggerCard } from './SetLoggerCard';

const CARD: ExerciseCardSummary = {
  url: 'https://app.musclefit.com.br/exercise-cards/42/v3/card.webp',
  thumbnail_url: 'https://app.musclefit.com.br/exercise-cards/42/v3/thumb.webp',
  version: 3,
};

function render(element: ReactElement, client?: QueryClient): string {
  const themed = createElement(MantineProvider, { theme }, element);
  return renderToStaticMarkup(client ? createElement(QueryClientProvider, { client }, themed) : themed);
}

// ---------------------------------------------------------------------------
// Viewer
// ---------------------------------------------------------------------------

describe('ExerciseCardViewer', () => {
  const viewer = (props: Partial<Parameters<typeof ExerciseCardViewer>[0]> = {}) =>
    render(
      createElement(ExerciseCardViewer, {
        card: CARD,
        opened: true,
        onClose: () => {},
        exerciseName: 'Supino reto com barra',
        withinPortal: false,
        ...props,
      }),
    );

  it('aberto: card 2:3 inteiro (contain), miniatura de fundo e "Fechar"', () => {
    const html = viewer();
    expect(html).toContain('Como executar');
    expect(html).toContain(`src="${CARD.url}"`);
    expect(html).toContain('alt="Como executar: Supino reto com barra"');
    expect(html).toContain('object-fit:contain');
    // Miniatura (já pré-carregada) no mesmo enquadramento enquanto a cheia chega.
    expect(html).toContain(CARD.thumbnail_url);
    expect(html).toContain('touch-action:manipulation');
    expect(html).toContain('Fechar');
  });

  it('fechado ou sem card: nada é renderizado', () => {
    expect(viewer({ opened: false })).not.toContain(CARD.url);
    expect(viewer({ card: null })).not.toContain('Como executar');
  });
});

// ---------------------------------------------------------------------------
// Atalho "Como executar" no SetLoggerCard
// ---------------------------------------------------------------------------

describe('SetLoggerCard — atalho "Como executar"', () => {
  const card = (exerciseCard?: ExerciseCardSummary | null) =>
    render(
      createElement(SetLoggerCard, {
        exerciseName: 'Supino reto com barra',
        setLabel: 'Série 1 de 3',
        defaultReps: 10,
        defaultWeight: 40,
        defaultRpe: 8,
        saving: false,
        onSave: () => {},
        onSubstituteClick: () => {},
        onFinishExercise: () => {},
        showMuscleMap: false,
        exerciseCard,
      }),
    );

  it('com card publicado: ação "Info" no hero, ao lado de trocar/encerrar', () => {
    const html = card(CARD);
    expect(html).toContain('aria-label="Info: como executar"');
    expect(html).toContain('data-testid="exercise-card-open"');
    expect(html).toContain('aria-label="Trocar equipamento/exercício"');
    expect(html).toContain('aria-label="Encerrar exercício"');
    // A imagem cheia só baixa ao abrir o viewer.
    expect(html).not.toContain(CARD.url);
  });

  it('sem card (null ou ausente): nenhum ícone, nada reservado', () => {
    for (const html of [card(null), card(undefined)]) {
      expect(html).not.toContain('como executar');
      expect(html).not.toContain('exercise-card-open');
      expect(html).toContain('aria-label="Encerrar exercício"');
    }
  });
});

// ---------------------------------------------------------------------------
// Miniatura no briefing
// ---------------------------------------------------------------------------

const BRIEFING: ExerciseBriefingResponse = {
  plan_id: 3,
  workout_plan_exercise_id: 777,
  sequence: 1,
  exercise: {
    id: 42,
    name: 'Supino reto com barra',
    catalog_name: 'Supino reto com barra',
    description: null,
    execution_description: 'Execução:\n- Empurre a barra',
    execution_description_reviewed: true,
    is_unilateral: false,
    exercise_card: CARD,
  },
  equipment: null,
  muscles: [],
  prescription: {
    sets_count: 3,
    reps_target: '8-10',
    load_target: null,
    suggested_load_kg: null,
    rpe_target: '8',
    effective_rpe_target: null,
    effective_rpe_ceiling: null,
    failure_allowed: null,
    advanced_technique: null,
    advanced_technique_applications: null,
    rest_seconds: 90,
    status: 'LEGACY',
    warnings: [],
  },
  last_execution: null,
  trend: { status: 'unknown', label: 'Primeira vez', reason: 'Sem execução anterior.', source: 'none' },
  track: null,
  requires_user_choice: false,
};

describe('ExerciseBriefingScreen — miniatura do card', () => {
  function briefing(payload: ExerciseBriefingResponse, exerciseCard?: ExerciseCardSummary | null): string {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(['exercise-briefing', 3, 777], payload);
    return render(
      createElement(ExerciseBriefingScreen, {
        planId: 3,
        workoutPlanExerciseId: 777,
        exerciseName: 'Supino reto com barra',
        onStart: () => {},
        exerciseCard,
      }),
      client,
    );
  }

  // PR4: o texto e o card ilustrado ficam na gaveta "Como executar"; na
  // tela, o botão dela mostra a miniatura do card quando ele existe.
  function instructions(exerciseCard: ExerciseCardSummary | null): string {
    return render(
      createElement(BriefingInstructions, {
        sections: buildBriefingSections(parseExecutionDescription(BRIEFING.exercise.execution_description)),
        muscleGroups: [],
        exerciseCard,
        exerciseName: 'Supino reto com barra',
      }),
    );
  }

  it('com card: miniatura no botão "Como executar" e card clicável na gaveta, depois do texto', () => {
    const html = briefing(BRIEFING);
    expect(html).toContain('data-testid="briefing-instructions-open"');
    expect(html).toContain(`src="${CARD.thumbnail_url}"`);
    const drawer = instructions(CARD);
    expect(drawer).toContain('data-testid="briefing-exercise-card"');
    expect(drawer).toContain('Toque para ampliar');
    expect(drawer).toContain('data-testid="briefing-section-execution"');
    expect(drawer.indexOf('Empurre a barra')).toBeLessThan(drawer.indexOf(CARD.thumbnail_url));
  });

  it('sem card: gaveta só com o texto, sem miniatura nem espaço vazio', () => {
    const html = briefing({ ...BRIEFING, exercise: { ...BRIEFING.exercise, exercise_card: null } });
    expect(html).toContain('data-testid="briefing-instructions-open"');
    expect(html).not.toContain('Toque para ampliar');
    const drawer = instructions(null);
    expect(drawer).not.toContain('briefing-exercise-card');
    expect(drawer).toContain('data-testid="briefing-section-execution"');
    expect(drawer).toContain('Empurre a barra');
  });

  it('sem card e sem texto: nenhuma seção de orientação aparece', () => {
    const html = briefing({
      ...BRIEFING,
      exercise: { ...BRIEFING.exercise, execution_description: null, exercise_card: null },
    });
    expect(html).not.toContain('briefing-section-preparation');
    expect(html).not.toContain('briefing-section-execution');
    expect(html).not.toContain('briefing-section-attention');
  });

  it('troca de exercício antes do briefing: o card do exercício EFETIVO vence', () => {
    const efetivo: ExerciseCardSummary = { ...CARD, thumbnail_url: 'https://cdn/exercise-cards/77/v1/thumb.webp' };
    expect(briefing(BRIEFING, efetivo)).toContain(`src="${efetivo.thumbnail_url}"`);
    // Exercício efetivo sem card: nada, mesmo que o item da ficha tenha.
    expect(briefing(BRIEFING, null)).not.toContain('briefing-exercise-card');
  });
});
