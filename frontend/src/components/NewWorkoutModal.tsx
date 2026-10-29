'use client';

import { Alert, Button, Group, Modal, Select, Stack, Text, TextInput } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { apiFetch } from '@/lib/api';
import {
  conductedByOptions,
  GymResponse,
  TrainingTypeResponse,
  WorkoutHeaderForm,
  WorkoutPlanListResponse,
} from '@/lib/workout-log-types';

function defaultHeaderForm(): WorkoutHeaderForm {
  const today = new Date().toISOString().slice(0, 10);
  return {
    name: `Treino ${today.split('-').reverse().join('/')}`,
    local_date: today,
    started_at_time: '',
    gym_id: '',
    training_type_id: '',
    conducted_by: '',
    workout_plan_id: '',
  };
}

type Props = {
  opened: boolean;
  onClose: () => void;
  onSubmit: (form: WorkoutHeaderForm) => Promise<void>;
  isSubmitting?: boolean;
  errorMessage?: string | null;
  // Modo edição (cabeçalho de um treino existente, via PATCH
  // /workout-sessions/{id}/meta) — sem isso, o modal sempre abre em modo
  // criação (POST /workout-sessions/start).
  title?: string;
  submitLabel?: string;
  initialValues?: WorkoutHeaderForm;
};

/**
 * Modal de novo treino / edição de cabeçalho de um treino existente.
 * Reaproveita o padrão de
 * fitness-frontend/src/features/exercise-logs/NewWorkoutModal.tsx, trocando
 * o proxy `/api/*` pelo `apiFetch` direto na Fitness API real.
 *
 * Quem chama decide o verbo/endpoint via `onSubmit`: POST
 * /workout-sessions/start (criação, ver registro-exercicios/page.tsx) ou
 * PATCH /workout-sessions/{id}/meta (edição, ver
 * registro-exercicios/[activityId]/page.tsx, quando `initialValues` é
 * passado).
 */
export function NewWorkoutModal({
  opened,
  onClose,
  onSubmit,
  isSubmitting,
  errorMessage,
  title = 'Novo treino',
  submitLabel = 'Criar treino',
  initialValues,
}: Props) {
  const [form, setForm] = useState<WorkoutHeaderForm>(initialValues || defaultHeaderForm());

  useEffect(() => {
    if (opened) {
      setForm(initialValues || defaultHeaderForm());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened]);

  const gymsQuery = useQuery({
    queryKey: ['gyms', 'active'],
    queryFn: () => apiFetch<GymResponse>('/gyms?active_only=true'),
    enabled: opened,
  });

  const plansQuery = useQuery({
    queryKey: ['workout-plans', 'planned'],
    queryFn: () => apiFetch<WorkoutPlanListResponse>('/workout-plans?status=planned&limit=100'),
    enabled: opened,
  });

  const trainingTypesQuery = useQuery({
    queryKey: ['training-types', 'active'],
    queryFn: () => apiFetch<TrainingTypeResponse>('/training-types?is_active=true'),
    enabled: opened,
  });

  const gymOptions = useMemo(
    () => (gymsQuery.data?.items || []).map((g) => ({ value: String(g.id), label: g.name })),
    [gymsQuery.data],
  );

  const trainingTypeOptions = useMemo(
    () => (trainingTypesQuery.data?.items || []).map((t) => ({ value: String(t.id), label: t.name })),
    [trainingTypesQuery.data],
  );

  const planOptions = useMemo(
    () =>
      (plansQuery.data?.items || []).map((p) => ({
        value: String(p.id),
        label: p.plan_date ? `${p.name} (${p.plan_date})` : p.name,
      })),
    [plansQuery.data],
  );

  const canSubmit = Boolean(form.name && form.local_date);
  const isMobile = useMediaQuery('(max-width: 48em)');

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={title}
      size="lg"
      fullScreen={isMobile}
      radius={isMobile ? 0 : 'lg'}
    >
      <Stack gap="md">
        <TextInput
          label="Nome do treino"
          placeholder="Ex.: Treino A - Peito e Tríceps"
          value={form.name}
          onChange={(event) => {
            const nameVal = event.currentTarget.value;
            setForm((c) => ({ ...c, name: nameVal }));
          }}
          required
        />

        <Group grow>
          <TextInput
            type="date"
            label="Data"
            value={form.local_date}
            onChange={(event) => {
              const dateVal = event.currentTarget.value;
              setForm((c) => ({ ...c, local_date: dateVal }));
            }}
            required
          />
          <TextInput
            type="time"
            label="Hora"
            description="Opcional — melhora a precisão da recuperação muscular"
            value={form.started_at_time}
            onChange={(event) => {
              const timeVal = event.currentTarget.value;
              setForm((c) => ({ ...c, started_at_time: timeVal }));
            }}
          />
        </Group>

        <Group grow>
          <Select
            searchable
            label="Academia"
            placeholder="Opcional"
            data={gymOptions}
            value={form.gym_id || null}
            onChange={(value) => setForm((c) => ({ ...c, gym_id: value || '' }))}
            clearable
            nothingFoundMessage="Nenhuma academia encontrada"
          />
          <Select
            label="Conduzido por"
            placeholder="Opcional"
            data={conductedByOptions}
            value={form.conducted_by || null}
            onChange={(value) => setForm((c) => ({ ...c, conducted_by: value || '' }))}
            clearable
          />
        </Group>

        <Select
          searchable
          label="Meta de ativação"
          placeholder="Opcional"
          data={trainingTypeOptions}
          value={form.training_type_id || null}
          onChange={(value) => setForm((c) => ({ ...c, training_type_id: value || '' }))}
          clearable
          nothingFoundMessage="Nenhuma meta de ativação encontrada"
        />

        <Select
          searchable
          label="Ficha planejada"
          description="Opcional — vincula este treino a uma ficha ainda não executada"
          placeholder="Nenhuma"
          data={planOptions}
          value={form.workout_plan_id || null}
          onChange={(value) => setForm((c) => ({ ...c, workout_plan_id: value || '' }))}
          clearable
          nothingFoundMessage="Nenhuma ficha planejada encontrada"
        />

        {errorMessage && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            {errorMessage}
          </Alert>
        )}

        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancelar
          </Button>
          <Button loading={isSubmitting} disabled={!canSubmit} onClick={() => onSubmit(form)}>
            {submitLabel}
          </Button>
        </Group>

        {!canSubmit && (
          <Text size="xs" c="dimmed">
            Preencha ao menos nome e data para continuar.
          </Text>
        )}
      </Stack>
    </Modal>
  );
}
