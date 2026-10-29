'use client';

import { Alert, Badge, Group, Loader, Stack, Table, Text } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { memo } from 'react';
import { ApiError } from '@/lib/api';
import type { VolumeMode, WorkoutPlanTemplateSlotPayload } from '@/lib/workout-plan-templates-types';
import { BRAND } from '@/theme';
import { useActivationPreviewQuery } from './useActivationPreviewQuery';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function formatSets(value: number | undefined): string {
  if (!value) return '-';
  return value.toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

type Props = {
  slots: WorkoutPlanTemplateSlotPayload[];
  // Mesmo Literal de WorkoutPlanTemplateIn.volume_mode — omitido preserva o
  // comportamento legado (default do backend, 'percent_budget').
  volumeMode?: VolumeMode;
  // Tipo de treino do Modelo (modo 'percent_budget'): o item de grupo entra
  // pelo orçamento projetado. Mesmo valor das demais consultas do builder,
  // para a queryKey coincidir.
  trainingTypeId?: number | null;
  // Qual campo de PreviewActivationGroup somar/exibir. 'total_equivalent'
  // (padrão) considera primário+secundário+estabilizador; 'primary_equivalent'
  // conta só a ativação primária do exercício — usado por uma segunda
  // instância desta tabela, filtrada só a grupos primários.
  metric?: 'total_equivalent' | 'primary_equivalent';
  // Prefixo da coluna de total. O sufixo é o tamanho da janela devolvido pelo
  // backend (`window_days`): 7 dias sempre que o ciclo tem slot de treino.
  totalColumnPrefix?: string;
};

/**
 * Prévia de ativação semanal — `POST /workout-plan-templates/preview-activation`
 * (sem tocar o banco), reaproveitando os MESMOS slots em memória do builder
 * (ainda não salvos). Monta o ciclo de repetição dos slots ao longo da janela
 * e soma a ativação muscular equivalente por grupo. Recalculado no
 * servidor a cada mudança (debounced), nunca no client — mesma regra de
 * "motor oficial é sempre a fonte de verdade" das demais telas do projeto.
 *
 * Fase 7: a janela é a mesma da metodologia de volume — 7 dias a partir do
 * primeiro slot de treino, um slot por dia. Um ciclo curto dá a volta dentro
 * dela; num ciclo com MAIS de 7 slots o que passa do 7º dia fica de fora
 * (antes o ciclo inteiro era projetado em `max(7, ciclo)` dias).
 */
function WeeklyActivationPreviewTableImpl({
  slots,
  volumeMode,
  trainingTypeId,
  metric = 'total_equivalent',
  totalColumnPrefix = 'Total',
}: Props) {
  // Query compartilhada (mesma queryKey) com a outra tabela e com a linha de
  // ativação por item do builder (M11): uma requisição só por mudança de corpo.
  const { query: previewQuery, hasAnyFilledSlot } = useActivationPreviewQuery(slots, volumeMode, trainingTypeId);

  if (!hasAnyFilledSlot) {
    return (
      <Text c={BRAND.textMuted} size="sm">
        Preencha ao menos um slot para ver o preview de ativação.
      </Text>
    );
  }

  if (previewQuery.isPending) {
    return (
      <Stack align="center" py="md">
        <Loader size="sm" color="brand" />
      </Stack>
    );
  }

  if (previewQuery.isError) {
    return (
      <Alert color="red" icon={<IconAlertTriangle size={16} />}>
        {errorMessage(previewQuery.error, 'Erro ao calcular o preview de ativação muscular.')}
      </Alert>
    );
  }

  const days = previewQuery.data?.days ?? [];
  const cycleLength = previewQuery.data?.cycle_length ?? 0;
  // `window_days` é o tamanho real da janela projetada. Resposta antiga (sem o
  // campo) cai no comprimento da lista de dias, que é o mesmo número.
  const windowDays = previewQuery.data?.window_days ?? days.length;
  const totalColumnLabel = `${totalColumnPrefix} (janela de ${windowDays} dias)`;
  const cycleIsLonger = cycleLength > 7;
  // O nome do slot não vai no corpo da requisição (não entra no cálculo); é
  // exibido a partir dos slots locais, então renomear não refaz o preview.
  const slotNameBySequence = new Map(slots.map((slot) => [slot.sequence, slot.name.trim()]));

  if (days.length === 0) {
    return (
      <Text c={BRAND.textMuted} size="sm">
        {previewQuery.data?.message || 'Preencha ao menos um slot para ver o preview de ativação.'}
      </Text>
    );
  }

  const muscleGroupNames = Array.from(
    new Set(
      days.flatMap((day) => day.groups.filter((group) => (group[metric] ?? 0) > 0).map((group) => group.muscle_group)),
    ),
  ).sort((a, b) => a.localeCompare(b));

  const weeklyTotalsByGroup: Record<string, number> = {};
  for (const groupName of muscleGroupNames) {
    weeklyTotalsByGroup[groupName] = days.reduce(
      (sum, day) => sum + (day.groups.find((g) => g.muscle_group === groupName)?.[metric] ?? 0),
      0,
    );
  }

  return (
    <Stack gap="xs">
      <Group gap="xs">
        <Badge size="sm" variant="light" color="brand">
          Janela de {windowDays} dias
        </Badge>
        <Text size="xs" c={BRAND.textMuted}>
          Ciclo de {cycleLength} slot{cycleLength === 1 ? '' : 's'}
          {cycleIsLonger
            ? ' — maior que a semana: a janela começa no primeiro treino e os slots além do 7º dia ficam de fora.'
            : ' — a janela começa no primeiro treino e o ciclo se repete dentro dos 7 dias.'}
        </Text>
      </Group>
      <Table.ScrollContainer minWidth={800}>
      <Table striped highlightOnHover withTableBorder>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Grupo muscular</Table.Th>
            {days.map((day) => (
              <Table.Th key={day.day}>
                <Text size="sm" fw={600} c={BRAND.textPrimary}>
                  Dia {day.day}
                </Text>
                <Badge size="xs" variant="light" color={day.slot_type === 'descanso' ? 'gray' : 'brand'}>
                  {slotNameBySequence.get(day.slot_sequence) || day.slot_name}
                </Badge>
              </Table.Th>
            ))}
            <Table.Th>
              <Text size="sm" fw={700} c={BRAND.textPrimary}>
                {totalColumnLabel}
              </Text>
            </Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {muscleGroupNames.map((groupName) => (
            <Table.Tr key={groupName}>
              <Table.Td c={BRAND.textPrimary}>{groupName}</Table.Td>
              {days.map((day) => {
                const group = day.groups.find((g) => g.muscle_group === groupName);
                return <Table.Td key={day.day}>{formatSets(group?.[metric])}</Table.Td>;
              })}
              <Table.Td>
                <Text fw={700} c={BRAND.textPrimary}>
                  {formatSets(weeklyTotalsByGroup[groupName])}
                </Text>
              </Table.Td>
            </Table.Tr>
          ))}
          {muscleGroupNames.length === 0 && (
            <Table.Tr>
              <Table.Td colSpan={days.length + 2}>
                <Text c={BRAND.textMuted} ta="center" py="sm">
                  Nenhuma ativação muscular calculada ainda para os slots preenchidos.
                </Text>
              </Table.Td>
            </Table.Tr>
          )}
        </Table.Tbody>
      </Table>
      </Table.ScrollContainer>
    </Stack>
  );
}

// `memo`: só re-renderiza quando `slots`/`volumeMode` mudam de identidade (o
// builder passa `slots` memoizado), não a cada re-render do builder.
export const WeeklyActivationPreviewTable = memo(WeeklyActivationPreviewTableImpl);
