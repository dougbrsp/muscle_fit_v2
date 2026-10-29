'use client';

import { useDebouncedValue } from '@mantine/hooks';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { serializeActivationPreviewPayload } from '@/lib/activation-preview-payload';
import { apiFetch } from '@/lib/api';
import type {
  PreviewActivationResponse,
  VolumeMode,
  WorkoutPlanTemplateSlotPayload,
} from '@/lib/workout-plan-templates-types';

/**
 * Query ÚNICA do preview de ativação (`POST /workout-plan-templates/preview-activation`).
 *
 * Usada pelas duas tabelas de preview e pela linha de ativação de cada item
 * do builder (M11). Todas montam a MESMA `queryKey` a partir do MESMO corpo
 * mínimo (M10, `serializeActivationPreviewPayload`), então o React Query
 * deduplica: uma requisição por mudança de corpo, não uma por consumidor —
 * e todos atualizam juntos, com `keepPreviousData` mantendo o valor anterior
 * durante o refetch.
 */
export function useActivationPreviewQuery(
  slots: WorkoutPlanTemplateSlotPayload[],
  volumeMode?: VolumeMode,
  trainingTypeId?: number | null,
) {
  const hasAnyFilledSlot = useMemo(
    () => slots.some((slot) => slot.slot_type !== 'descanso' && slot.items.length > 0),
    [slots],
  );

  // M10: o corpo leva SÓ o que entra no cálculo. Nome do slot, reps/RPE,
  // técnica etc. não alteram a string, então não trocam a queryKey. O tipo
  // de treino (modo 'percent_budget') entra no corpo e, portanto, na queryKey:
  // todos os consumidores precisam passar o MESMO valor para deduplicar.
  const payloadSerialized = useMemo(
    () => serializeActivationPreviewPayload(slots, volumeMode, trainingTypeId),
    [slots, volumeMode, trainingTypeId],
  );
  const [debouncedPayload] = useDebouncedValue(payloadSerialized, 400);

  const query = useQuery({
    queryKey: ['workout-plan-template-preview-activation', debouncedPayload],
    queryFn: () =>
      apiFetch<PreviewActivationResponse>('/workout-plan-templates/preview-activation', {
        method: 'POST',
        body: debouncedPayload,
      }),
    enabled: hasAnyFilledSlot,
    // Mantém o resultado anterior na tela enquanto o novo carrega (sem piscar
    // o Loader a cada mudança que altera o cálculo).
    placeholderData: keepPreviousData,
  });

  return { query, hasAnyFilledSlot };
}
