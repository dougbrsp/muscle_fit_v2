import { describe, expect, it } from 'vitest';
import type { ExerciseBriefingResponse } from './exercise-briefing-types';
import {
  collectCardThumbnailUrls,
  exerciseCardPath,
  preloadImages,
  resolveExerciseCardSource,
  toExerciseCardSummary,
  type PublishedExerciseCardResponse,
} from './workout-mode-exercise-card';
import type { ExerciseCardSummary, WorkoutPlanExercise } from './workout-plans-types';

const CARD: ExerciseCardSummary = {
  url: 'https://app.musclefit.com.br/exercise-cards/42/v3/card.webp',
  thumbnail_url: 'https://app.musclefit.com.br/exercise-cards/42/v3/thumb.webp',
  version: 3,
};

// ---------------------------------------------------------------------------
// Contrato com o backend: payloads como `api/app` os devolve (mesmos valores
// de `api/tests/integration/dataapi/test_exercise_cards_modo_treino.py`).
// `satisfies` reprova o typecheck se o tipo do frontend perder/renomear um
// campo que o backend manda.
// ---------------------------------------------------------------------------

// Um item de `GET /workout-plans/{id}` (`services.get_workout_plan`).
const PLAN_EXERCISE_PAYLOAD = {
  workout_plan_exercise_id: 941,
  sequence: 1,
  exercise_name: 'Supino reto com barra',
  amazfit_name: null,
  mapping_id: 42,
  muscle_primary: 'Peitoral',
  muscle_secondary: 'Tríceps',
  equipment_name: null,
  preferred_equipment_id: null,
  sets_count: 3,
  reps_target: '8-10',
  load_target: null,
  rpe_target: '8',
  rest_seconds: 90,
  warmup: false,
  substitution_options: [],
  notes: null,
  exercise_description: null,
  exercise_card: {
    url: 'https://app.musclefit.com.br/exercise-cards/42/v3/card.webp',
    thumbnail_url: 'https://app.musclefit.com.br/exercise-cards/42/v3/thumb.webp',
    version: 3,
  },
  prescription: null,
  prescription_status: 'LEGACY',
  safety_overlay: null,
  requires_user_choice: false,
} satisfies WorkoutPlanExercise;

// O bloco `exercise` de `GET .../briefing` (`exercise_briefing.py`).
const BRIEFING_EXERCISE_PAYLOAD = {
  id: 42,
  name: 'Supino reto com barra',
  catalog_name: 'Supino reto com barra',
  description: null,
  execution_description: 'Preparação:\n- Ajuste o banco\nExecução:\n- Empurre',
  execution_description_reviewed: false,
  is_unilateral: false,
  exercise_card: {
    url: 'https://app.musclefit.com.br/exercise-cards/42/v3/card.webp',
    thumbnail_url: 'https://app.musclefit.com.br/exercise-cards/42/v3/thumb.webp',
    version: 3,
  },
} satisfies ExerciseBriefingResponse['exercise'];

// `GET /exercises/{id}/card` (`exercise_card_service.card_payload`).
const PUBLISHED_CARD_PAYLOAD = {
  exercise_id: 42,
  exercise_card: {
    url: 'https://app.musclefit.com.br/exercise-cards/42/v3/card.webp',
    thumbnail_url: 'https://app.musclefit.com.br/exercise-cards/42/v3/thumb.webp',
    png_url: 'https://app.musclefit.com.br/exercise-cards/42/v3/card.png',
    version: 3,
    published_at: '2026-10-03T12:00:00',
  },
} satisfies PublishedExerciseCardResponse;

describe('contrato do card no payload do Modo Treino', () => {
  it('ficha, briefing e GET /card descrevem o MESMO card', () => {
    expect(PLAN_EXERCISE_PAYLOAD.exercise_card).toEqual(CARD);
    expect(BRIEFING_EXERCISE_PAYLOAD.exercise_card).toEqual(CARD);
    expect(toExerciseCardSummary(PUBLISHED_CARD_PAYLOAD.exercise_card)).toEqual(CARD);
    expect(exerciseCardPath(42)).toBe('/exercises/42/card');
  });

  it('sem card publicado o backend manda null, e null continua null', () => {
    expect(toExerciseCardSummary(null)).toBeNull();
    expect(toExerciseCardSummary(undefined)).toBeNull();
  });
});

describe('resolveExerciseCardSource (card do exercício efetivo)', () => {
  const item: Pick<WorkoutPlanExercise, 'mapping_id' | 'exercise_card'> = { mapping_id: 42, exercise_card: CARD };

  it('sem troca: usa o card que a ficha já trouxe (nenhuma requisição)', () => {
    expect(resolveExerciseCardSource({ planExercise: item })).toEqual({ kind: 'plan', card: CARD });
  });

  it('ficha sem card publicado: null, também sem requisição', () => {
    expect(resolveExerciseCardSource({ planExercise: { mapping_id: 42, exercise_card: null } })).toEqual({
      kind: 'plan',
      card: null,
    });
  });

  it('troca só de EQUIPAMENTO (mesmo mapping_id): o card continua', () => {
    const override = { mapping_id: 42 };
    expect(resolveExerciseCardSource({ planExercise: item, override })).toEqual({ kind: 'plan', card: CARD });
  });

  it('troca de EXERCÍCIO: busca o card do novo', () => {
    const override = { mapping_id: 77 };
    expect(resolveExerciseCardSource({ planExercise: item, override })).toEqual({ kind: 'fetch', exerciseId: 77 });
  });

  it('retomando sem override: o exercício já lançado (outro) manda', () => {
    expect(resolveExerciseCardSource({ planExercise: item, loggedRow: { mapping_id: 77 } })).toEqual({
      kind: 'fetch',
      exerciseId: 77,
    });
    expect(resolveExerciseCardSource({ planExercise: item, loggedRow: { mapping_id: 42 } })).toEqual({
      kind: 'plan',
      card: CARD,
    });
  });

  it('continuação sintética (sem exercise_card no payload): busca pelo mapping_id', () => {
    expect(resolveExerciseCardSource({ planExercise: { mapping_id: 77 } })).toEqual({ kind: 'fetch', exerciseId: 77 });
  });

  it('item em texto livre (sem mapping_id): nenhum card', () => {
    expect(resolveExerciseCardSource({ planExercise: { mapping_id: null, exercise_card: null } })).toEqual({
      kind: 'none',
    });
  });
});

describe('pré-carga das miniaturas', () => {
  it('coleta as miniaturas da ficha sem repetir e ignora quem não tem card', () => {
    const other: ExerciseCardSummary = { ...CARD, thumbnail_url: 'https://cdn/exercise-cards/7/v1/thumb.webp' };
    expect(
      collectCardThumbnailUrls([
        { exercise_card: CARD },
        { exercise_card: null },
        {},
        { exercise_card: other },
        { exercise_card: CARD },
      ]),
    ).toEqual([CARD.thumbnail_url, other.thumbnail_url]);
    expect(collectCardThumbnailUrls(undefined)).toEqual([]);
  });

  it('no servidor (sem window) é no-op e não levanta', () => {
    expect(() => preloadImages([CARD.thumbnail_url])).not.toThrow();
  });
});
