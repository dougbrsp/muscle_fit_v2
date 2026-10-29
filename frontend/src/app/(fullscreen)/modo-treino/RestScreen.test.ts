import { MantineProvider } from '@mantine/core';
import { createElement, type ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SetEvaluationResult, WorkoutModePlanExercise } from '@/lib/workout-mode-types';
import { restSavedSetView } from '@/lib/workout-mode-rest-view';
import { theme } from '@/theme';
import { MuscleMapDisclosure } from './MuscleMapDisclosure';
import { LastExerciseNotice, NextExercisePreviewCard } from './NextExercisePreviewCard';
import { RestAiEvaluationCard, RestSavedSetPanel } from './RestRecommendation';
import { RestTimerHero } from './RestTimer';

function render(element: ReactElement): string {
  // Sem os <style> globais do MantineProvider: "vazio" = o componente não desenhou nada.
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, element)).replace(/<style[^>]*>[\s\S]*?<\/style>/g, '');
}

const T0 = new Date('2026-10-23T10:00:00Z').getTime();

const NEXT_EXERCISE = {
  workout_plan_exercise_id: 22,
  mapping_id: 9,
  exercise_name: 'Flexão de joelhos sentada na máquina',
  muscle_primary: 'Posterior de coxas',
  muscle_secondary: 'Panturrilhas',
  equipment_name: 'Cadeira flexora',
  sets_count: 3,
  reps_target: '10-12',
  load_target: '40 kg',
  rpe_target: '8',
} as unknown as WorkoutModePlanExercise;

describe('RestTimerHero', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(T0);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const base = {
    restKey: 'wpe-1#s1@1',
    initialSeconds: 90,
    onFinish: () => {},
    isExerciseTransition: false,
    hasNextExercise: true,
  };

  it('MM:SS do descanso, próxima série e carga vindas das props (nada fixo)', () => {
    const html = render(
      createElement(RestTimerHero, { ...base, nextSet: { label: 'Série 2 de 3', weightKg: 32.5 }, weightUnit: 'kg' }),
    );
    expect(html).toContain('data-testid="rest-clock"');
    expect(html).toContain('01:30');
    expect(html).toContain('Série 2 de 3');
    expect(html).toContain('32.5 kg');
    expect(html).toContain('Pular descanso');
    expect(html).toContain('aria-label="Menos 15 segundos"');

    const other = render(createElement(RestTimerHero, { ...base, nextSet: { label: 'Série 3 de 3', weightKg: 20 } }));
    expect(other).toContain('20 kg');
    expect(other).not.toContain('32.5 kg');
  });

  it('reload: reconstrói o relógio a partir de rest_ends_at salvo', () => {
    const stored = { rest_key: base.restKey, rest_ends_at: T0 + 42_000, total_seconds: 90 };
    const html = render(createElement(RestTimerHero, { ...base, getStored: () => stored }));
    expect(html).toContain('00:42');
  });

  it('transição já expirada: CTA vira "Continuar para o próximo exercício"', () => {
    const stored = { rest_key: base.restKey, rest_ends_at: T0 - 5_000, total_seconds: 90 };
    const html = render(
      createElement(RestTimerHero, { ...base, isExerciseTransition: true, getStored: () => stored, transitionNote: 'Exercício concluído' }),
    );
    expect(html).toContain('00:00');
    expect(html).toContain('Continuar para o próximo exercício');
    expect(html).toContain('Exercício concluído');
  });
});

describe('Série salva + recomendação', () => {
  const saved = {
    intensity_status: 'BELOW_TARGET',
    intensity_status_label: 'Esforço abaixo do alvo',
    next_set_load: { direction: 'INCREASE_LOAD', load_kg: 47.5, reason: 'X', reason_label: 'Motivo curto', rule_text: 'Regra longa' },
  };

  it('"SÉRIE SALVA" só depois da confirmação; antes, nada (sem card vazio)', () => {
    const before = render(
      createElement(RestSavedSetPanel, {
        view: restSavedSetView({ setSaved: false, saved, isExerciseTransition: false, weightUnit: 'kg' }),
      }),
    );
    expect(before).toBe('');

    const after = render(
      createElement(RestSavedSetPanel, {
        view: restSavedSetView({ setSaved: true, saved, isExerciseTransition: false, weightUnit: 'kg' }),
      }),
    );
    expect(after).toContain('data-testid="set-saved-chip"');
    expect(after).toContain('Esforço abaixo do alvo');
    expect(after).toContain('→ 47.5 kg');
    expect(after).toContain('Motivo curto');
    // Explicação recolhida por padrão, com o botão para abrir.
    // Regra do motor numa linha; a explicação fica recolhida atrás de "ver mais".
    expect(after).toContain('ver mais');
    expect(after).toContain('aria-expanded="false"');
    expect(after).toMatch(/<p[^>]*hidden=""[^>]*>Regra longa<\/p>/);
  });

  it('PR4: a carga da próxima série aparece UMA vez na tela de descanso (no relógio)', () => {
    vi.useFakeTimers();
    vi.setSystemTime(T0);
    try {
      const html = render(
        createElement(
          RestTimerHero,
          {
            restKey: 'wpe-1#s1@1',
            initialSeconds: 90,
            onFinish: () => {},
            isExerciseTransition: false,
            hasNextExercise: true,
            nextSet: { label: 'Série 2 de 3', weightKg: 47.5 },
            weightUnit: 'kg',
          },
          createElement(RestSavedSetPanel, {
            view: restSavedSetView({ setSaved: true, saved, isExerciseTransition: false, weightUnit: 'kg' }),
            loadShownElsewhere: true,
          }),
        ),
      );
      expect(html.split('47.5 kg').length - 1).toBe(1);
      expect(html).toContain('data-testid="rest-next-load"');
      // A ação do motor continua visível, só sem repetir o número.
      expect(html).toContain('Aumentar carga');
    } finally {
      vi.useRealTimers();
    }
  });

  it('sem a carga no relógio (ex.: série medida), o card do motor mostra a carga dele', () => {
    const html = render(
      createElement(RestSavedSetPanel, {
        view: restSavedSetView({ setSaved: true, saved, isExerciseTransition: false, weightUnit: 'kg' }),
        loadShownElsewhere: false,
      }),
    );
    expect(html).toContain('→ 47.5 kg');
  });
});

describe('Card da IA', () => {
  const result: SetEvaluationResult = { model: 'm', category: 'INCREASE_LOAD', note: 'Boa série, suba a carga.', raw_text: '' };

  it('toggle desligado: nenhum card, mesmo com avaliação', () => {
    expect(render(createElement(RestAiEvaluationCard, { enabled: false, loading: false, error: false, result }))).toBe('');
  });

  it('toggle ligado: texto real da IA; sem texto, nada', () => {
    const html = render(createElement(RestAiEvaluationCard, { enabled: true, loading: false, error: false, result }));
    expect(html).toContain('data-testid="rest-ai-card"');
    expect(html).toContain('Boa série, suba a carga.');
    expect(
      render(createElement(RestAiEvaluationCard, { enabled: true, loading: false, error: false, result: { ...result, note: ' ' } })),
    ).toBe('');
  });
});

describe('Próximo exercício', () => {
  it('nome completo e a foto do PRÓXIMO exercício', () => {
    const html = render(
      createElement(NextExercisePreviewCard, {
        planExercise: NEXT_EXERCISE,
        equipmentName: 'Cadeira flexora',
        equipmentPhotoUrl: 'https://cdn.example/equipamentos/flexora.jpg',
      }),
    );
    expect(html).toContain('Flexão de joelhos sentada na máquina');
    expect(html).toContain('src="https://cdn.example/equipamentos/flexora.jpg"');
    expect(html).toContain('Posterior de coxas');
    // Entre séries o card é só informativo.
    expect(html).not.toContain('Encerrar treino');
  });

  it('sem foto: placeholder; na transição, as ações de antes', () => {
    const html = render(
      createElement(NextExercisePreviewCard, {
        planExercise: NEXT_EXERCISE,
        equipmentPhotoUrl: null,
        actions: { onSkip: () => {}, onCancel: () => {}, onFinish: () => {} },
      }),
    );
    expect(html).not.toContain('<img');
    expect(html).toContain('Sem foto: Flexão de joelhos sentada na máquina');
    expect(html).toContain('Pular (fazer depois)');
    expect(html).toContain('Remover da sessão');
    expect(html).toContain('Encerrar treino');
  });

  it('fim da ficha: mensagem própria, sem card de próximo', () => {
    const html = render(createElement(LastExerciseNotice, { isExerciseTransition: false }));
    expect(html).toContain('Último exercício da ficha');
    expect(html).not.toContain('data-testid="next-exercise-card"');
  });
});

describe('Mapa muscular', () => {
  it('recolhido por padrão, dizendo de qual exercício são os músculos', () => {
    const html = render(
      createElement(MuscleMapDisclosure, {
        items: [{ muscle_group: 'Posterior de coxas', intensity: 1, tooltip: 'Principal' }],
        subject: 'Exercício atual: Mesa flexora',
      }),
    );
    expect(html).toContain('Conecte-se com os seus músculos');
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain('Exercício atual: Mesa flexora');
    expect(html).toMatch(/role="region"[^>]*hidden=""/);
  });
});
