'use client';

import {
  Alert,
  Button,
  Divider,
  Drawer,
  Group,
  Loader,
  NumberInput,
  Select,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
  Tooltip,
} from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import type { ExerciseDetailResponse, ExerciseUpsertPayload, ExerciseUpsertResponse } from '@/lib/exercise-catalog-admin-types';
import { MEASURE_OPTIONS, parseMeasure } from '@/lib/set-measures';
import { BRAND } from '@/theme';
import { AliasesSection } from './AliasesSection';
import { EquipmentLinksSection } from './EquipmentLinksSection';
import { ExecutionDescriptionSection } from './ExecutionDescriptionSection';
import { ExerciseCardSection } from './ExerciseCardSection';
import { ExternalMappingsSection } from './ExternalMappingsSection';
import { MuscleContributionsSection } from './MuscleContributionsSection';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

type FormState = {
  name: string;
  body_position: string;
  movement: string;
  trajectory: string;
  biomechanical_pattern: string;
  primary_joint: string;
  resistance_vector: string;
  keywords: string;
  description: string;
  is_active: boolean;
  is_unilateral: boolean;
  is_compound: boolean;
  // Medida sugerida da série (Lote T4): '' = sem padrão (grava NULL).
  default_measure: string;
  // Valor como veio do GET, para só mandar `default_measure` no PUT quando o
  // admin mudar o Select (um valor que este bundle não conhece não é apagado).
  loaded_default_measure: string | null;
  // Exercício laboral (M100): padrões '' = sem padrão (grava NULL).
  is_laboral: boolean;
  laboral_default_sets: number | '';
  laboral_default_reps: number | '';
  laboral_default_load_kg: number | '';
};

function numberOrBlank(value: unknown): number | '' {
  if (value === null || value === undefined || value === '') return '';
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : '';
}

function numberOrNull(value: number | string | ''): number | null {
  if (value === '' || value === null || value === undefined) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

// Opções do Select "Medida padrão": vocabulário e rótulos do dono
// (`domain/set_measures.py`, espelho `lib/set-measures.ts`).
const DEFAULT_MEASURE_SELECT_DATA = MEASURE_OPTIONS.map((o) => ({ value: o.value, label: o.label }));

function toFormState(exercise: ExerciseDetailResponse['exercise']): FormState {
  return {
    name: exercise.name || '',
    body_position: exercise.body_position || '',
    movement: exercise.movement || '',
    trajectory: exercise.trajectory || '',
    biomechanical_pattern: exercise.biomechanical_pattern || '',
    primary_joint: exercise.primary_joint || '',
    resistance_vector: exercise.resistance_vector || '',
    keywords: exercise.keywords || '',
    description: exercise.description || '',
    is_active: Boolean(exercise.is_active),
    is_unilateral: Boolean(exercise.is_unilateral),
    is_compound: Boolean(exercise.is_compound),
    default_measure: parseMeasure(exercise.default_measure)?.value ?? '',
    loaded_default_measure: exercise.default_measure ?? null,
    is_laboral: Boolean(Number(exercise.is_laboral ?? 0)),
    laboral_default_sets: numberOrBlank(exercise.laboral_default_sets),
    laboral_default_reps: numberOrBlank(exercise.laboral_default_reps),
    laboral_default_load_kg: numberOrBlank(exercise.laboral_default_load_kg),
  };
}

/**
 * Drawer de detalhe/edição de um exercício canônico: dados principais
 * (`PUT /exercises/{id}`), apelidos e mapeamentos externos (aninhados), e
 * contribuições musculares e equipamentos vinculados — estes dois últimos
 * usam routers separados (`muscle_contributions_router` em
 * `exercise_mapping_catalog.py` e `/equipment-exercises` em
 * `equipment_exercises.py`), ver nota de escopo em `exercise-catalog-admin-types.ts`.
 */
export function ExerciseDetailDrawer({
  opened,
  exerciseId,
  isAdmin,
  exerciseCardsEnabled = false,
  onClose,
  onSaved,
}: {
  opened: boolean;
  exerciseId: number | null;
  isAdmin: boolean;
  /** `features.exercise_cards` de `/auth/me` (flag do ambiente). */
  exerciseCardsEnabled?: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const qc = useQueryClient();

  const detailQueryKey = ['exercise-catalog-admin-exercise-detail', exerciseId] as const;

  const detailQuery = useQuery({
    queryKey: detailQueryKey,
    queryFn: () => apiFetch<ExerciseDetailResponse>(`/exercises/${exerciseId}`),
    enabled: opened && exerciseId != null,
  });

  const [form, setForm] = useState<FormState | null>(null);

  useEffect(() => {
    if (detailQuery.data) {
      setForm(toFormState(detailQuery.data.exercise));
    } else if (!opened) {
      setForm(null);
    }
  }, [detailQuery.data, opened]);

  function set<K extends keyof FormState>(field: K, value: FormState[K]) {
    setForm((prev) => (prev ? { ...prev, [field]: value } : prev));
  }

  const saveMutation = useMutation({
    mutationFn: (payload: ExerciseUpsertPayload) =>
      apiFetch<ExerciseUpsertResponse>(`/exercises/${exerciseId}`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: detailQueryKey });
      onSaved();
    },
  });

  function submitForm() {
    if (!form) return;
    saveMutation.mutate({
      name: form.name.trim(),
      body_position: form.body_position.trim() || null,
      movement: form.movement.trim() || null,
      trajectory: form.trajectory.trim() || null,
      biomechanical_pattern: form.biomechanical_pattern.trim() || null,
      primary_joint: form.primary_joint.trim() || null,
      resistance_vector: form.resistance_vector.trim() || null,
      keywords: form.keywords.trim() || null,
      description: form.description.trim() || null,
      is_active: form.is_active,
      is_unilateral: form.is_unilateral,
      is_compound: form.is_compound,
      // Ausente = não muda (PUT parcial do backend).
      ...(form.default_measure !== (parseMeasure(form.loaded_default_measure)?.value ?? '')
        ? { default_measure: form.default_measure || null }
        : {}),
      is_laboral: form.is_laboral,
      laboral_default_sets: numberOrNull(form.laboral_default_sets),
      laboral_default_reps: numberOrNull(form.laboral_default_reps),
      laboral_default_load_kg: numberOrNull(form.laboral_default_load_kg),
    });
  }

  const detail = detailQuery.data;

  return (
    <Drawer
      opened={opened}
      onClose={onClose}
      title={detail ? detail.exercise.name : 'Exercício'}
      position="right"
      size="lg"
      styles={{ header: { background: BRAND.bgElevated }, body: { background: BRAND.bgElevated } }}
    >
      {exerciseId == null ? null : detailQuery.isLoading || !form ? (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Carregando exercício...</Text>
        </Group>
      ) : detailQuery.isError ? (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {errorMessage(detailQuery.error, 'Não foi possível carregar o exercício.')}
        </Alert>
      ) : (
        <Stack gap="lg">
          <Stack gap="sm">
            <TextInput
              label="Nome"
              value={form.name}
              onChange={(event) => set('name', event.currentTarget.value)}
              disabled={!isAdmin}
              required
            />
            <Group grow>
              <TextInput
                label="Posição corporal"
                value={form.body_position}
                onChange={(event) => set('body_position', event.currentTarget.value)}
                disabled={!isAdmin}
              />
              <TextInput
                label="Movimento"
                value={form.movement}
                onChange={(event) => set('movement', event.currentTarget.value)}
                disabled={!isAdmin}
              />
            </Group>
            <Group grow>
              <TextInput
                label="Trajetória"
                value={form.trajectory}
                onChange={(event) => set('trajectory', event.currentTarget.value)}
                disabled={!isAdmin}
              />
              <TextInput
                label="Padrão biomecânico"
                value={form.biomechanical_pattern}
                onChange={(event) => set('biomechanical_pattern', event.currentTarget.value)}
                disabled={!isAdmin}
              />
            </Group>
            <Group grow>
              <TextInput
                label="Articulação principal"
                value={form.primary_joint}
                onChange={(event) => set('primary_joint', event.currentTarget.value)}
                disabled={!isAdmin}
              />
              <TextInput
                label="Vetor de resistência"
                value={form.resistance_vector}
                onChange={(event) => set('resistance_vector', event.currentTarget.value)}
                disabled={!isAdmin}
              />
            </Group>
            <TextInput
              label="Palavras-chave"
              value={form.keywords}
              onChange={(event) => set('keywords', event.currentTarget.value)}
              disabled={!isAdmin}
            />
            <Textarea
              label="Descrição"
              autosize
              minRows={2}
              value={form.description}
              onChange={(event) => set('description', event.currentTarget.value)}
              disabled={!isAdmin}
            />
            <Group>
              <Switch label="Ativo" checked={form.is_active} onChange={(event) => set('is_active', event.currentTarget.checked)} disabled={!isAdmin} />
              <Switch
                label="Unilateral"
                checked={form.is_unilateral}
                onChange={(event) => set('is_unilateral', event.currentTarget.checked)}
                disabled={!isAdmin}
              />
              <Switch label="Composto" checked={form.is_compound} onChange={(event) => set('is_compound', event.currentTarget.checked)} disabled={!isAdmin} />
            </Group>

            <Select
              label="Medida padrão"
              description="Sugestão para o editor do Modelo ao escolher este exercício"
              placeholder="Sem padrão"
              clearable
              data={DEFAULT_MEASURE_SELECT_DATA}
              value={form.default_measure || null}
              onChange={(v) => set('default_measure', v ?? '')}
              disabled={!isAdmin}
            />

            <Switch
              label="Laboral (sem progressão)"
              description="Aquecimento/alongamento: no Modo Treino só mostra o padrão e oferece Concluir ou Pular. Fica fora de progressão, recordes, volume e recuperação."
              checked={form.is_laboral}
              onChange={(event) => set('is_laboral', event.currentTarget.checked)}
              disabled={!isAdmin}
            />
            {form.is_laboral && (
              <Group grow align="flex-start">
                <NumberInput
                  label="Séries padrão"
                  placeholder="Da ficha"
                  allowDecimal={false}
                  allowNegative={false}
                  value={form.laboral_default_sets}
                  onChange={(v) => set('laboral_default_sets', v === '' ? '' : Number(v))}
                  disabled={!isAdmin}
                />
                <NumberInput
                  label="Reps padrão"
                  placeholder="Da ficha"
                  allowDecimal={false}
                  allowNegative={false}
                  value={form.laboral_default_reps}
                  onChange={(v) => set('laboral_default_reps', v === '' ? '' : Number(v))}
                  disabled={!isAdmin}
                />
                <NumberInput
                  label="Carga padrão (kg)"
                  placeholder="Sem carga"
                  decimalSeparator=","
                  allowNegative={false}
                  value={form.laboral_default_load_kg}
                  onChange={(v) => set('laboral_default_load_kg', v === '' ? '' : Number(v))}
                  disabled={!isAdmin}
                />
              </Group>
            )}

            {saveMutation.isError && (
              <Alert color="red" icon={<IconAlertTriangle size={16} />}>
                {isForbidden(saveMutation.error) ? 'Ação restrita a administradores.' : errorMessage(saveMutation.error, 'Não foi possível salvar o exercício.')}
              </Alert>
            )}

            {isAdmin ? (
              <Group justify="flex-end">
                <Button color="brand" onClick={submitForm} loading={saveMutation.isPending} disabled={!form.name.trim()}>
                  Salvar
                </Button>
              </Group>
            ) : (
              <Tooltip label="Ação restrita a administradores">
                <Group justify="flex-end">
                  <Button color="brand" disabled>
                    Salvar
                  </Button>
                </Group>
              </Tooltip>
            )}
          </Stack>

          {detail && detail.legacy_ids_merged.length > 0 && (
            <Alert color="blue" variant="light">
              Ids legados fundidos neste exercício: {detail.legacy_ids_merged.join(', ')}
            </Alert>
          )}

          {detail && (
            <>
              <Divider label="Descrição de execução (modo treino)" />
              <ExecutionDescriptionSection
                exerciseId={exerciseId}
                exercise={detail.exercise}
                isAdmin={isAdmin}
                detailQueryKey={detailQueryKey}
              />
            </>
          )}

          {detail && (
            <>
              <Divider label="Card ilustrado (modo treino)" />
              <ExerciseCardSection
                key={exerciseId}
                exerciseId={exerciseId}
                exerciseName={detail.exercise.name}
                isAdmin={isAdmin}
                enabled={exerciseCardsEnabled}
              />
            </>
          )}

          <Divider label="Apelidos (aliases)" />
          {detail && (
            <AliasesSection exerciseId={exerciseId} aliases={detail.aliases} isAdmin={isAdmin} detailQueryKey={detailQueryKey} />
          )}

          <Divider label="Mapeamentos externos" />
          {detail && (
            <ExternalMappingsSection
              exerciseId={exerciseId}
              mappings={detail.external_mappings}
              isAdmin={isAdmin}
              detailQueryKey={detailQueryKey}
            />
          )}

          {detail && (
            <>
              <Divider label="Contribuições musculares" />
              <MuscleContributionsSection
                exerciseId={exerciseId}
                contributions={detail.muscle_contributions}
                isAdmin={isAdmin}
                detailQueryKey={detailQueryKey}
              />
            </>
          )}

          {detail && (
            <>
              <Divider label="Equipamentos vinculados" />
              <EquipmentLinksSection
                exerciseId={exerciseId}
                links={detail.equipment_links}
                isAdmin={isAdmin}
                detailQueryKey={detailQueryKey}
              />
            </>
          )}
        </Stack>
      )}
    </Drawer>
  );
}
