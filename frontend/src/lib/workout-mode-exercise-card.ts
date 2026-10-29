// Card ilustrado do exercício no Modo Treino (cards ilustrados, PR5).
//
// Fonte do card, sempre do exercício EFETIVO (o mesmo de nome/descrição —
// `resolveEffectiveMappingId`):
// - exercício efetivo = o do item da ficha -> `exercise_card` que o
//   `GET /workout-plans/{id}` já trouxe (zero requisição a mais; `null` = sem
//   card, nada aparece);
// - trocou o EXERCÍCIO (override, exercício já lançado diferente, ou
//   continuação sintética, que não tem `exercise_card`) ->
//   `GET /exercises/{id}/card` do novo, em cache do React Query;
// - trocou só o EQUIPAMENTO -> o `mapping_id` não muda e o card continua.
//
// Fallback: erro/404 na busca vira "sem card" (`retry: false`) — o treino
// nunca espera nem quebra por causa do card. A UI confia no objeto não nulo
// e não depende de `onError` (a CDN devolve `index.html` 200 em 403/404).

import { useQuery } from '@tanstack/react-query';
import { apiFetch } from './api';
import { resolveEffectiveMappingId } from './workout-mode-plan-flow';
import type { ExerciseLogRow, WorkoutModeEquipmentOverride } from './workout-mode-types';
import type { ExerciseCardSummary, WorkoutPlanExercise } from './workout-plans-types';

// Contrato de `GET /exercises/{id}/card` (`api/app/exercise_cards.py`,
// qualquer autenticado).
export type PublishedExerciseCardResponse = {
  exercise_id: number;
  exercise_card: (ExerciseCardSummary & { png_url: string; published_at: string | null }) | null;
};

export function exerciseCardPath(exerciseId: number): string {
  return `/exercises/${exerciseId}/card`;
}

// Card publicado muda raramente (só numa nova aprovação do admin) e a URL é
// imutável por versão: cache longo na sessão do treino.
export const EXERCISE_CARD_STALE_TIME_MS = 30 * 60 * 1000;

export type ExerciseCardSource =
  | { kind: 'plan'; card: ExerciseCardSummary | null }
  | { kind: 'fetch'; exerciseId: number }
  | { kind: 'none' };

/** De onde vem o card do exercício efetivo (ver cabeçalho do módulo). */
export function resolveExerciseCardSource(params: {
  planExercise: Pick<WorkoutPlanExercise, 'mapping_id' | 'exercise_card'>;
  override?: Pick<WorkoutModeEquipmentOverride, 'mapping_id'> | null;
  loggedRow?: Pick<ExerciseLogRow, 'mapping_id'> | null;
}): ExerciseCardSource {
  const { planExercise } = params;
  const mappingId = resolveEffectiveMappingId(params);
  if (mappingId == null) return { kind: 'none' };
  if (mappingId === (planExercise.mapping_id ?? null) && planExercise.exercise_card !== undefined) {
    return { kind: 'plan', card: planExercise.exercise_card ?? null };
  }
  return { kind: 'fetch', exerciseId: mappingId };
}

/** Só os 3 campos do Modo Treino (a resposta do GET traz png/published_at). */
export function toExerciseCardSummary(
  card: PublishedExerciseCardResponse['exercise_card'] | ExerciseCardSummary | null | undefined,
): ExerciseCardSummary | null {
  if (!card) return null;
  return { url: card.url, thumbnail_url: card.thumbnail_url, version: card.version };
}

/**
 * Card do exercício efetivo. `source` null (nenhum exercício atual) -> null.
 * Chame SEMPRE (regra dos hooks); a busca só dispara para `kind: 'fetch'`.
 */
export function useEffectiveExerciseCard(source: ExerciseCardSource | null): ExerciseCardSummary | null {
  const fetchId = source?.kind === 'fetch' ? source.exerciseId : null;
  const query = useQuery({
    queryKey: ['exercise-card', fetchId],
    queryFn: () => apiFetch<PublishedExerciseCardResponse>(exerciseCardPath(fetchId as number)),
    enabled: fetchId != null,
    staleTime: EXERCISE_CARD_STALE_TIME_MS,
    retry: false,
  });
  if (!source || source.kind === 'none') return null;
  if (source.kind === 'plan') return source.card;
  return toExerciseCardSummary(query.data?.exercise_card);
}

// ---------------------------------------------------------------------------
// Pré-carga das miniaturas (§66 da especificação)
// ---------------------------------------------------------------------------

/** Miniaturas dos cards da ficha, sem repetição, na ordem da ficha. */
export function collectCardThumbnailUrls(
  exercises: ReadonlyArray<Pick<WorkoutPlanExercise, 'exercise_card'>> | null | undefined,
): string[] {
  const urls: string[] = [];
  for (const exercise of exercises || []) {
    const url = exercise.exercise_card?.thumbnail_url;
    if (url && !urls.includes(url)) urls.push(url);
  }
  return urls;
}

// Referências mantidas para o navegador não descartar o download no meio e
// para a mesma URL não ser pedida duas vezes na sessão (as URLs são imutáveis
// por versão, o cache HTTP faz o resto).
const preloadedImages = new Map<string, HTMLImageElement>();

/**
 * Dispara o download em segundo plano (`new Image()`), sem bloquear nada nem
 * reservar espaço na tela. No servidor (SSR/teste) é no-op. A imagem cheia
 * não entra aqui: é carregada sob demanda, ao abrir o "Como executar".
 */
export function preloadImages(urls: ReadonlyArray<string>): void {
  if (typeof window === 'undefined' || typeof Image === 'undefined') return;
  for (const url of urls) {
    if (!url || preloadedImages.has(url)) continue;
    const image = new Image();
    image.decoding = 'async';
    image.src = url;
    preloadedImages.set(url, image);
  }
}
