'use client';

import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Loader, Table, Text } from '@mantine/core';
import { toActivationPayloadItem } from '@/lib/activation-payload';
import { apiFetch } from '@/lib/api';
import type { MuscleActivationRow } from '@/lib/analytics-types';
import type { MuscleActivationPreviewInput, WorkoutPlanExercise } from '@/lib/workout-plans-types';
import { BRAND } from '@/theme';

type Props = {
  exercises: WorkoutPlanExercise[];
};

function formatEquivalent(value: number) {
  return value.toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

/**
 * Prévia de ativação muscular do rascunho em edição — `POST
 * /analytics/muscle-activation-preview` (sem tocar o banco), mesma fórmula
 * de `GET /analytics/muscle-activation-by-activity` usada em
 * `/registro-exercicios/[activityId]`. Só considera exercícios que já têm
 * `mapping_id` resolvido (exercícios recém-adicionados manualmente, sem
 * catálogo vinculado ainda, não entram na prévia — o backend resolve o
 * `mapping_id` deles só ao salvar a ficha). Técnica avançada da linha vai
 * junto (payload único, M76 E3): o backend soma o equivalente dela.
 */
export function MuscleActivationPreview({ exercises }: Props) {
  const payload: MuscleActivationPreviewInput = useMemo(
    () => ({
      exercises: exercises
        .filter((ex) => ex.mapping_id != null)
        .map(toActivationPayloadItem),
    }),
    [exercises],
  );

  const activationQuery = useQuery({
    queryKey: ['muscle-activation-preview', payload],
    queryFn: () => apiFetch<{ items: MuscleActivationRow[] }>('/analytics/muscle-activation-preview', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
    enabled: payload.exercises.length > 0,
  });

  if (payload.exercises.length === 0) {
    return (
      <Text size="sm" c={BRAND.textMuted}>
        Sem exercícios vinculados ao catálogo canônico ainda para calcular a ativação muscular.
      </Text>
    );
  }

  if (activationQuery.isLoading) {
    return <Loader size="sm" color="brand" />;
  }

  const items = activationQuery.data?.items || [];

  return (
    <Table.ScrollContainer minWidth={700}>
      <Table striped highlightOnHover withTableBorder verticalSpacing="xs">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Grupo muscular</Table.Th>
            <Table.Th>Primário (séries eq.)</Table.Th>
            <Table.Th>Secundário (séries eq.)</Table.Th>
            <Table.Th>Estabilizador (séries eq.)</Table.Th>
            <Table.Th>Total equivalente</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {items.map((row) => (
            <Table.Tr key={row.muscle_group}>
              <Table.Td>{row.muscle_group}</Table.Td>
              <Table.Td>{formatEquivalent(row.primary_equivalent)}</Table.Td>
              <Table.Td>{formatEquivalent(row.secondary_equivalent)}</Table.Td>
              <Table.Td>{formatEquivalent(row.stabilizer_equivalent)}</Table.Td>
              <Table.Td fw={700}>{formatEquivalent(row.total_equivalent)}</Table.Td>
            </Table.Tr>
          ))}
          {items.length === 0 && (
            <Table.Tr>
              <Table.Td colSpan={5}>
                <Text c={BRAND.textMuted} ta="center">
                  Nenhum dado de ativação calculado.
                </Text>
              </Table.Td>
            </Table.Tr>
          )}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
