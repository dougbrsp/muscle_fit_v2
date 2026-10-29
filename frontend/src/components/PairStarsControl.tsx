'use client';

import { ActionIcon, Group, Rating, Tooltip } from '@mantine/core';
import { IconX } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo } from 'react';
import { apiFetch } from '@/lib/api';
import {
  applyPreferenceChange,
  EQUIPMENT_EXERCISE_PREFERENCES_PATH,
  EQUIPMENT_EXERCISE_PREFERENCES_QUERY_KEY,
  MAX_PREFERENCE_STARS,
  starsByPairId,
  starsLabel,
  type EquipmentExercisePreferencesResponse,
  type PairPreferenceChange,
} from '@/lib/equipment-recommend';

/**
 * Estrelas do USUÁRIO CORRENTE por par exercício+equipamento
 * (`/me/equipment-exercise-preferences`). Não é dado do catálogo: qualquer
 * usuário logado avalia os próprios pares, sem permissão de admin. Atualização
 * otimista com rollback (`applyPreferenceChange` + snapshot do cache).
 */
export function usePairStars() {
  const qc = useQueryClient();
  const key = EQUIPMENT_EXERCISE_PREFERENCES_QUERY_KEY;

  const query = useQuery({
    queryKey: key,
    queryFn: () => apiFetch<EquipmentExercisePreferencesResponse>(EQUIPMENT_EXERCISE_PREFERENCES_PATH),
    staleTime: 60_000,
    retry: false,
  });

  const mutation = useMutation({
    mutationFn: (change: PairPreferenceChange) => {
      const path = `${EQUIPMENT_EXERCISE_PREFERENCES_PATH}/${change.equipmentExerciseId}`;
      return change.stars === null
        ? apiFetch(path, { method: 'DELETE' })
        : apiFetch(path, { method: 'PUT', body: JSON.stringify({ stars: change.stars }) });
    },
    onMutate: async (change) => {
      await qc.cancelQueries({ queryKey: key });
      const snapshot = qc.getQueryData<EquipmentExercisePreferencesResponse>(key);
      qc.setQueryData<EquipmentExercisePreferencesResponse>(key, (current) => applyPreferenceChange(current, change));
      return { snapshot };
    },
    onError: (_error, _change, context) => {
      qc.setQueryData(key, context?.snapshot); // rollback
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: key });
    },
  });

  const stars = useMemo(() => starsByPairId(query.data?.items), [query.data]);
  return { stars, query, mutation };
}

export function PairStarsControl({
  value,
  label,
  disabled,
  onChange,
}: {
  value: number | null;
  label: string;
  disabled?: boolean;
  onChange: (stars: number | null) => void;
}) {
  return (
    <Group gap={4} wrap="nowrap">
      <Tooltip label={starsLabel(value)} withArrow>
        <Rating
          value={value ?? 0}
          count={MAX_PREFERENCE_STARS}
          color="orange"
          readOnly={disabled}
          onChange={(next) => onChange(next)}
          aria-label={`Sua nota para ${label}: ${starsLabel(value)}`}
        />
      </Tooltip>
      <Tooltip label="Limpar minha nota" withArrow disabled={value == null}>
        <ActionIcon
          variant="subtle"
          color="gray"
          size="sm"
          aria-label={`Limpar minha nota para ${label}`}
          disabled={disabled || value == null}
          onClick={() => onChange(null)}
        >
          <IconX size={14} />
        </ActionIcon>
      </Tooltip>
    </Group>
  );
}
