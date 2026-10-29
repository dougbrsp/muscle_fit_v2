'use client';

import { useDebouncedValue } from '@mantine/hooks';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { apiFetch } from '@/lib/api';
import { serializeMethodologyVolumePayload } from '@/lib/methodology-volume';
import type { MethodologyIntentKind, MethodologyVolumeEvaluation } from '@/lib/methodology-volume-types';
import type { VolumeMode, WorkoutPlanTemplateSlotPayload } from '@/lib/workout-plan-templates-types';

/**
 * Query do preview de metodologia de volume
 * (`POST /workout-plan-templates/methodology-volume-preview`, Fase 4, PR E).
 *
 * Mesmo molde de `useActivationPreviewQuery`: corpo mínimo (M10) serializado
 * uma vez, debounce de 400 ms sobre a string e `queryKey` = corpo — editar
 * nome do slot, reps, técnica etc. não muda a string, então não dispara nada.
 * A intenção e os grupos-alvo ENTRAM no corpo (e na chave): trocar o seletor
 * refaz a avaliação; trocar de volta reaproveita o cache.
 *
 * Só dispara com um Modelo minimamente válido (ao menos um slot de treino com
 * item), igual ao preview de ativação. Nada é persistido até o PR H.
 */
export function useMethodologyVolumeQuery(
  slots: WorkoutPlanTemplateSlotPayload[],
  volumeMode: VolumeMode | undefined,
  intent: MethodologyIntentKind,
  targetGroupIds: number[],
  /** Lote 3: modo de contagem do formulário (entra no corpo e na queryKey). */
  countingMode?: string | null,
) {
  const hasAnyFilledSlot = useMemo(
    () => slots.some((slot) => slot.slot_type !== 'descanso' && slot.items.length > 0),
    [slots],
  );

  const payloadSerialized = useMemo(
    () => serializeMethodologyVolumePayload(slots, volumeMode, intent, targetGroupIds, countingMode),
    [slots, volumeMode, intent, targetGroupIds, countingMode],
  );
  const [debouncedPayload] = useDebouncedValue(payloadSerialized, 400);

  const query = useQuery({
    queryKey: ['workout-plan-template-methodology-volume-preview', debouncedPayload],
    queryFn: () =>
      apiFetch<MethodologyVolumeEvaluation>('/workout-plan-templates/methodology-volume-preview', {
        method: 'POST',
        body: debouncedPayload,
      }),
    enabled: hasAnyFilledSlot,
    // Painel informativo: falha não bloqueia o builder e não vale insistir.
    retry: false,
    placeholderData: keepPreviousData,
  });

  return { query, hasAnyFilledSlot };
}
