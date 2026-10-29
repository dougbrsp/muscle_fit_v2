'use client';

import { Button, Group, Modal, NumberInput, Select, SimpleGrid, Stack, Text } from '@mantine/core';
import { useEffect, useState } from 'react';
import {
  getAdvancedTechniqueDefaultSetsCount,
  getAdvancedTechniqueLabel,
  getAdvancedTechniqueOptions,
  isSetsOnlyTechnique,
} from '@/lib/advanced-techniques';
import { useAdvancedTechniques } from '@/lib/use-advanced-techniques';
import type { WorkoutPlanExercise } from '@/lib/workout-plans-types';
import { BRAND } from '@/theme';

// Campos editados aqui: técnica avançada, aquecimento, série de ajuste e pico
// de contração — os mesmos do card do Modelo (`EquipmentSeriesItemCard`).
export type AdvancedConfigPatch = Pick<
  WorkoutPlanExercise,
  | 'sets_count'
  | 'advanced_technique'
  | 'advanced_technique_applications'
  | 'advanced_technique_duration_seconds'
  | 'warmup_sets_count'
  | 'warmup_reps'
  | 'warmup_rest_seconds'
  | 'feeler_set_sets_count'
  | 'feeler_set_reps'
  | 'feeler_set_rest_seconds'
  | 'peak_contraction_seconds'
>;

function patchOf(exercise: WorkoutPlanExercise | null): AdvancedConfigPatch {
  return {
    sets_count: exercise?.sets_count ?? null,
    advanced_technique: exercise?.advanced_technique ?? null,
    advanced_technique_applications: exercise?.advanced_technique_applications ?? null,
    advanced_technique_duration_seconds: exercise?.advanced_technique_duration_seconds ?? null,
    warmup_sets_count: exercise?.warmup_sets_count ?? null,
    warmup_reps: exercise?.warmup_reps ?? null,
    warmup_rest_seconds: exercise?.warmup_rest_seconds ?? null,
    feeler_set_sets_count: exercise?.feeler_set_sets_count ?? null,
    feeler_set_reps: exercise?.feeler_set_reps ?? null,
    feeler_set_rest_seconds: exercise?.feeler_set_rest_seconds ?? null,
    peak_contraction_seconds: exercise?.peak_contraction_seconds ?? null,
  };
}

function numberOrNull(value: string | number): number | null {
  return typeof value === 'number' ? value : null;
}

type Props = {
  exercise: WorkoutPlanExercise | null;
  onClose: () => void;
  onApply: (patch: AdvancedConfigPatch) => void;
};

/**
 * Edição da configuração avançada de UMA linha da ficha (técnica, aquecimento,
 * série de ajuste, pico de contração). Só edita o estado local: quem persiste
 * é o "Salvar alterações" da ficha, e o servidor re-congela a prescrição da
 * linha quando a técnica muda (a validade da técnica no par é do motor).
 */
export function AdvancedConfigModal({ exercise, onClose, onApply }: Props) {
  const catalog = useAdvancedTechniques();
  const [draft, setDraft] = useState<AdvancedConfigPatch>(() => patchOf(exercise));

  useEffect(() => {
    setDraft(patchOf(exercise));
  }, [exercise]);

  const update = (patch: Partial<AdvancedConfigPatch>) => setDraft((current) => ({ ...current, ...patch }));
  const technique = draft.advanced_technique ?? null;
  const setsOnly = isSetsOnlyTechnique(technique);

  return (
    <Modal
      opened={exercise != null}
      onClose={onClose}
      title={exercise ? `Configuração avançada — ${exercise.exercise_name}` : 'Configuração avançada'}
      size="lg"
      centered
    >
      <Stack gap="md">
        <Stack gap="xs">
          <Select
            label="Técnica avançada"
            placeholder="Nenhuma"
            clearable
            data={getAdvancedTechniqueOptions(catalog.items, exercise?.advanced_technique)}
            value={technique}
            onChange={(value) => {
              const code = value || null;
              // FST-7/GVT: as séries vêm do padrão do catálogo (continua
              // editável) e não há aplicações nem pausa — mesmo do Modelo.
              const defaultSets = getAdvancedTechniqueDefaultSetsCount(code);
              update(
                defaultSets != null
                  ? {
                      advanced_technique: code,
                      sets_count: defaultSets,
                      advanced_technique_applications: null,
                      advanced_technique_duration_seconds: null,
                    }
                  : { advanced_technique: code },
              );
            }}
          />
          {setsOnly ? (
            <Text size="xs" c={BRAND.textMuted}>
              {getAdvancedTechniqueLabel(technique)} define só o número de séries; não há aplicações nem pausa.
            </Text>
          ) : (
            <SimpleGrid cols={{ base: 1, xs: 2 }}>
              <NumberInput
                label="Nº de aplicações"
                description="drops/mini-blocos/reps extras"
                value={draft.advanced_technique_applications ?? ''}
                onChange={(v) => update({ advanced_technique_applications: numberOrNull(v) })}
                min={1}
                disabled={!technique}
              />
              <NumberInput
                label="Pausa (segundos)"
                value={draft.advanced_technique_duration_seconds ?? ''}
                onChange={(v) => update({ advanced_technique_duration_seconds: numberOrNull(v) })}
                min={1}
                disabled={!technique}
              />
            </SimpleGrid>
          )}
        </Stack>

        <Stack gap={4}>
          <Text size="sm" fw={600} c={BRAND.textPrimary}>
            Aquecimento
          </Text>
          <SimpleGrid cols={{ base: 1, xs: 3 }}>
            <NumberInput
              label="Séries"
              value={draft.warmup_sets_count ?? ''}
              onChange={(v) => update({ warmup_sets_count: numberOrNull(v) })}
              min={1}
            />
            <NumberInput
              label="Reps"
              value={draft.warmup_reps ?? ''}
              onChange={(v) => update({ warmup_reps: numberOrNull(v) })}
              min={1}
            />
            <NumberInput
              label="Descanso (s)"
              value={draft.warmup_rest_seconds ?? ''}
              onChange={(v) => update({ warmup_rest_seconds: numberOrNull(v) })}
              min={1}
            />
          </SimpleGrid>
        </Stack>

        <Stack gap={4}>
          <Text size="sm" fw={600} c={BRAND.textPrimary}>
            Série de ajuste
          </Text>
          <SimpleGrid cols={{ base: 1, xs: 3 }}>
            <NumberInput
              label="Séries"
              value={draft.feeler_set_sets_count ?? ''}
              onChange={(v) => update({ feeler_set_sets_count: numberOrNull(v) })}
              min={1}
            />
            <NumberInput
              label="Reps"
              value={draft.feeler_set_reps ?? ''}
              onChange={(v) => update({ feeler_set_reps: numberOrNull(v) })}
              min={1}
            />
            <NumberInput
              label="Descanso (s)"
              value={draft.feeler_set_rest_seconds ?? ''}
              onChange={(v) => update({ feeler_set_rest_seconds: numberOrNull(v) })}
              min={1}
            />
          </SimpleGrid>
        </Stack>

        <NumberInput
          label="Pico de contração (segundos)"
          description="Segurar a contração na última repetição da série de trabalho. Vazio = sem pico."
          value={draft.peak_contraction_seconds ?? ''}
          onChange={(v) => update({ peak_contraction_seconds: numberOrNull(v) })}
          min={1}
        />

        <Text size="xs" c={BRAND.textMuted}>
          As alterações valem depois de &quot;Salvar alterações&quot;. Ao trocar a técnica, o servidor recalcula a
          prescrição desta linha.
        </Text>

        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancelar
          </Button>
          <Button
            color="brand"
            onClick={() => {
              onApply(draft);
              onClose();
            }}
          >
            Aplicar
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
