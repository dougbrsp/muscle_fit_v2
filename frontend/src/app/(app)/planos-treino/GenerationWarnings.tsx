'use client';

import { Alert, Stack, Text } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import { formatInsufficientVolumeGroup } from '@/lib/generation-warnings-format';
import type { WorkoutPlanDraft } from '@/lib/workout-plans-types';
import { exerciseWithGroupLabel, recoveryExcludedGroups } from '@/lib/generation-warnings';
import { BRAND } from '@/theme';

type Props = {
  draft: Pick<
    WorkoutPlanDraft,
    | 'generation_summary'
    | 'excluded_due_to_recovery'
    | 'excluded_due_to_no_equipment'
    | 'reduced_dose_groups'
    | 'insufficient_target_volume_groups'
  >;
};

function asArray<T>(value: T[] | null | undefined): T[] {
  return Array.isArray(value) ? value : [];
}

/**
 * Resumo textual da geração (`generation_summary`) + alertas dos ajustes
 * automáticos que o motor de geração aplicou (exclusão por recuperação
 * insuficiente, exclusão por falta de equipamento, dose reduzida, déficit de
 * volume-alvo por grupo) — mesmas chaves devolvidas tanto pelo fluxo Full
 * Body quanto pelo ABC (ver `workout-plans-types.ts`).
 */
export function GenerationWarnings({ draft }: Props) {
  // `draft` pode vir do motor ou do `raw_payload` de uma ficha antiga: trate
  // toda lista como possivelmente ausente OU com formato diferente do
  // declarado. Array.isArray (e nao `|| []`) porque um campo que virou objeto
  // quebraria o `.map`/`.join` abaixo.
  const excludedRecoveryGroups = recoveryExcludedGroups(asArray(draft.excluded_due_to_recovery));
  const excludedEquipment = asArray(draft.excluded_due_to_no_equipment);
  const reducedDose = asArray(draft.reduced_dose_groups);
  const insufficientVolume = asArray(draft.insufficient_target_volume_groups);

  return (
    <Stack gap="sm">
      {draft.generation_summary && (
        <Alert color="brand" variant="light" icon={<IconInfoCircle />} title="Resumo da geração">
          <Text size="sm" c={BRAND.textPrimary} style={{ whiteSpace: 'pre-wrap' }}>
            {draft.generation_summary}
          </Text>
        </Alert>
      )}

      {excludedRecoveryGroups.length > 0 && (
        <Alert color="yellow" variant="light" title="Removidos por recuperação insuficiente">
          <Text size="sm">Grupos musculares: {excludedRecoveryGroups.join(', ')}</Text>
        </Alert>
      )}

      {excludedEquipment.length > 0 && (
        <Alert color="yellow" variant="light" title="Removidos por falta de equipamento na academia">
          {excludedEquipment.map((ex, i) => (
            <Text key={i} size="sm">
              {exerciseWithGroupLabel(ex)}
            </Text>
          ))}
        </Alert>
      )}

      {reducedDose.length > 0 && (
        <Alert color="yellow" variant="light" title="Dose reduzida por recuperação parcial">
          <Text size="sm">{reducedDose.join(', ')}</Text>
        </Alert>
      )}

      {insufficientVolume.length > 0 && (
        <Alert color="orange" variant="light" title="Volume-alvo não atingido por falta de equipamento">
          {insufficientVolume.map((g, i) => (
            <Text key={i} size="sm">
              {formatInsufficientVolumeGroup(g)}
            </Text>
          ))}
        </Alert>
      )}
    </Stack>
  );
}
