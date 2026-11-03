'use client';

import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { toActivationPayloadItem } from '@/lib/activation-payload';
import { apiFetch } from '@/lib/api';
import type { MuscleActivationRow } from '@/lib/analytics-types';
import type { MuscleActivationPreviewInput, WorkoutPlanExercise } from '@/lib/workout-plans-types';
import { EmptyState, ErrorState, ListGroup, ListRow, LoadingState } from '../../ui';

function fmt(value: number) {
  return value.toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

/**
 * Prévia de ativação muscular do rascunho/ficha (lista no celular). Mesma
 * query e payload de `MuscleActivationPreview` do desktop; valores prontos da API.
 */
export function PlanActivationPreviewList({ exercises }: { exercises: WorkoutPlanExercise[] }) {
  const payload: MuscleActivationPreviewInput = useMemo(
    () => ({ exercises: exercises.filter((ex) => ex.mapping_id != null).map(toActivationPayloadItem) }),
    [exercises],
  );
  const query = useQuery({
    queryKey: ['muscle-activation-preview', payload],
    queryFn: () =>
      apiFetch<{ items: MuscleActivationRow[] }>('/analytics/muscle-activation-preview', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    enabled: payload.exercises.length > 0,
  });

  if (payload.exercises.length === 0) {
    return <EmptyState title="Sem exercícios vinculados ao catálogo canônico ainda para calcular a ativação muscular." />;
  }
  if (query.isLoading) return <LoadingState label="Calculando ativação..." />;
  if (query.isError) return <ErrorState title="Não foi possível calcular a ativação" onRetry={() => void query.refetch()} />;
  const items = query.data?.items || [];
  if (items.length === 0) return <EmptyState title="Nenhum dado de ativação calculado." />;
  return (
    <ListGroup title="Prévia de ativação muscular">
      {items.map((row) => (
        <ListRow
          key={row.muscle_group}
          title={row.muscle_group}
          subtitle={`Primário ${fmt(row.primary_equivalent)} · Secundário ${fmt(row.secondary_equivalent)} · Estabilizador ${fmt(row.stabilizer_equivalent)}`}
          value={fmt(row.total_equivalent)}
        />
      ))}
    </ListGroup>
  );
}
