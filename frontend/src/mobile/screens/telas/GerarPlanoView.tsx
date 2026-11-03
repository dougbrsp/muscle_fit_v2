'use client';

import { Alert, Badge, Button, Group, NumberInput, Select, Stack, Text, Textarea, TextInput } from '@mantine/core';
import { IconAlertTriangle, IconSparkles } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { GenerationWarnings } from '@/app/(app)/planos-treino/GenerationWarnings';
import { ApiError, apiFetch } from '@/lib/api';
import type { AsyncJob } from '@/lib/analytics-types';
import type { ApiListResponse, Gym } from '@/lib/gyms-types';
import {
  DEFAULT_MAX_REST_SECONDS,
  DEFAULT_MIN_REST_SECONDS,
  REST_SECONDS_SANITY_MAX,
  REST_SECONDS_SANITY_MIN,
} from '@/lib/prescription-defaults';
import { AUTO_SLOT_VALUE, buildGeneratePayload, canGenerateDraft } from '@/lib/workout-plan-generate-payload';
import { buildDraftCreatePayload } from '@/lib/workout-plan-payload';
import type { AssignedTemplateSummary } from '@/lib/workout-plan-templates-types';
import {
  WORKOUT_PLAN_STATUS_OPTIONS,
  type WorkoutPlan,
  type WorkoutPlanDraft,
  type WorkoutPlanExercise,
  type WorkoutPlanGenerateInput,
  type WorkoutPlanInput,
} from '@/lib/workout-plans-types';
import { ErrorState, LoadingState, ScreenHeader, StickyAction } from '../../ui';
import { PlanActivationPreviewList } from './PlanActivationPreviewList';
import { PlanExercisesEditor } from './PlanExercisesEditor';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/**
 * Geração de rascunho de plano no celular (mesmos endpoints e payload builders
 * do `GenerateDraftModal`): escolher Modelo -> gerar (job assíncrono) ->
 * revisar exercícios -> salvar. É uma visão de página inteira, não um modal.
 */
export function GerarPlanoView({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const router = useRouter();
  const [gymId, setGymId] = useState<string | null>(null);
  const [planDate, setPlanDate] = useState('');
  const [planTime, setPlanTime] = useState('');
  const [goal, setGoal] = useState('');
  const [minRest, setMinRest] = useState<number>(DEFAULT_MIN_REST_SECONDS);
  const [maxRest, setMaxRest] = useState<number>(DEFAULT_MAX_REST_SECONDS);
  const [templateId, setTemplateId] = useState('');
  const [templateSlotSequence, setTemplateSlotSequence] = useState<string>(AUTO_SLOT_VALUE);
  const [jobId, setJobId] = useState<string | null>(null);
  const [draft, setDraft] = useState<WorkoutPlanDraft | null>(null);
  const [draftName, setDraftName] = useState('');
  const [draftExercises, setDraftExercises] = useState<WorkoutPlanExercise[]>([]);
  const [status, setStatus] = useState('planned');
  const [editingRow, setEditingRow] = useState(false);

  const gymsQuery = useQuery({
    queryKey: ['gyms'],
    queryFn: () => apiFetch<ApiListResponse<Gym>>('/gyms?active_only=true'),
  });
  const assignedQuery = useQuery({
    queryKey: ['workout-plan-templates-assigned-to-me'],
    queryFn: () => apiFetch<ApiListResponse<AssignedTemplateSummary>>('/workout-plan-templates/assigned-to-me'),
  });
  const templates = assignedQuery.data?.items || [];
  const selectedTemplate = templates.find((t) => String(t.id) === templateId) || null;

  function resetDraft() {
    setJobId(null);
    setDraft(null);
    setDraftName('');
    setDraftExercises([]);
  }

  const generateMutation = useMutation({
    mutationFn: (payload: WorkoutPlanGenerateInput) =>
      apiFetch<{ job_id: string; status: string }>('/workout-plans/generate', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    onSuccess: (data) => setJobId(data.job_id),
  });
  const jobQuery = useQuery({
    queryKey: ['plan-generation-job', jobId],
    queryFn: () => apiFetch<AsyncJob<WorkoutPlanDraft>>(`/jobs/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (query) => (query.state.data?.status === 'pending' ? 2000 : false),
  });
  const job = jobQuery.data;
  const isGenerating = generateMutation.isPending || (!!jobId && (job?.status === 'pending' || jobQuery.isLoading));

  useEffect(() => {
    if (job?.status === 'done' && job.result && !draft) {
      setDraft(job.result);
      setDraftName(job.result.name);
      setDraftExercises(job.result.exercises || []);
    }
  }, [job, draft]);

  const createMutation = useMutation({
    mutationFn: (payload: WorkoutPlanInput) => apiFetch<WorkoutPlan>('/workout-plans', { method: 'POST', body: JSON.stringify(payload) }),
    onSuccess: (plan) => {
      qc.invalidateQueries({ queryKey: ['workout-plans'] });
      onClose();
      router.push(`/planos-treino/${plan.id}`);
    },
  });

  const canGenerate = canGenerateDraft(templateId);

  function handleGenerate() {
    if (!canGenerate) return;
    resetDraft();
    generateMutation.mutate(
      buildGeneratePayload({
        templateId,
        templateSlotSequence,
        gymId,
        planDate,
        planTime,
        goal,
        minRestSeconds: minRest,
        maxRestSeconds: maxRest,
      }),
    );
  }

  function handleSave() {
    if (!draft) return;
    createMutation.mutate(
      buildDraftCreatePayload(
        draft,
        { name: draftName, status, plan_date: planDate, plan_time: planTime, gym_id: gymId },
        draftExercises,
      ),
    );
  }

  const gymOptions = (gymsQuery.data?.items || []).map((g) => ({ value: String(g.id), label: g.name }));

  // Revisão do rascunho
  if (draft) {
    return (
      <>
        {editingRow ? null : <ScreenHeader title="Revisar rascunho" subtitle="Ajuste e salve a ficha" />}
        <Stack gap="md" pb="md">
          {editingRow ? null : (
            <>
              <TextInput size="md" label="Nome da ficha" value={draftName} onChange={(e) => setDraftName(e.currentTarget.value)} />
              <Group gap="xs">{draft.split_day ? <Badge color="brand">Treino {draft.split_day}</Badge> : null}</Group>
              <Select
                size="md"
                label="Status"
                data={WORKOUT_PLAN_STATUS_OPTIONS as unknown as { value: string; label: string }[]}
                value={status}
                onChange={(v) => setStatus(v || 'planned')}
                allowDeselect={false}
              />
              <GenerationWarnings draft={draft} />
            </>
          )}
          <PlanExercisesEditor exercises={draftExercises} onChange={setDraftExercises} onEditingChange={setEditingRow} />
          {editingRow ? null : <PlanActivationPreviewList exercises={draftExercises} />}
          {createMutation.isError ? (
            <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao salvar" role="alert">
              {errorMessage(createMutation.error, 'Não foi possível salvar o plano.')}
            </Alert>
          ) : null}
        </Stack>
        {editingRow ? null : (
          <StickyAction>
            <Group wrap="nowrap" gap="sm">
              <Button variant="default" size="md" mih={48} onClick={resetDraft}>
                Outro
              </Button>
              <Button
                size="md"
                color="brand"
                mih={48}
                style={{ flex: 1 }}
                loading={createMutation.isPending}
                disabled={!draftName.trim()}
                onClick={handleSave}
              >
                Salvar ficha
              </Button>
            </Group>
          </StickyAction>
        )}
      </>
    );
  }

  // Formulário de geração
  return (
    <>
      <ScreenHeader title="Gerar plano" subtitle="A ficha nasce de um Modelo de Treino atribuído a você" />
      {assignedQuery.isLoading ? (
        <LoadingState label="Carregando seus modelos de treino..." />
      ) : assignedQuery.isError ? (
        <ErrorState
          title="Erro ao carregar seus modelos"
          description={errorMessage(assignedQuery.error, 'Não foi possível carregar seus modelos de treino.')}
          onRetry={() => void assignedQuery.refetch()}
        />
      ) : templates.length === 0 ? (
        <Stack>
          <Alert color="yellow" icon={<IconAlertTriangle />} title="Nenhum modelo de treino atribuído">
            Peça um modelo de treino ao seu treinador. A ficha é sempre gerada a partir de um Modelo de Treino atribuído a você.
          </Alert>
          <Button variant="default" mih={48} onClick={onClose}>
            Fechar
          </Button>
        </Stack>
      ) : (
        <>
          <Stack gap="md" pb="md">
            <Select
              size="md"
              label="Modelo de treino"
              description="Obrigatório: a ficha é gerada a partir da estrutura deste modelo"
              placeholder="Escolha um modelo atribuído a você"
              data={templates.map((t) => ({ value: String(t.id), label: t.name }))}
              value={templateId || null}
              onChange={(v) => {
                setTemplateId(v || '');
                setTemplateSlotSequence(AUTO_SLOT_VALUE);
              }}
              searchable
              withAsterisk
            />
            {selectedTemplate ? (
              <Select
                size="md"
                label="Slot do modelo"
                data={[
                  { value: AUTO_SLOT_VALUE, label: 'Automático (próximo do ciclo)' },
                  ...selectedTemplate.slots.map((s) => ({ value: String(s.sequence), label: s.name })),
                ]}
                value={templateSlotSequence}
                onChange={(v) => setTemplateSlotSequence(v || AUTO_SLOT_VALUE)}
                allowDeselect={false}
              />
            ) : null}
            <Select size="md" label="Academia" placeholder="Nenhuma" data={gymOptions} value={gymId} onChange={setGymId} clearable searchable />
            <Group grow align="flex-start">
              <TextInput size="md" label="Data do plano" type="date" value={planDate} onChange={(e) => setPlanDate(e.currentTarget.value)} />
              <TextInput size="md" label="Hora pretendida" type="time" value={planTime} onChange={(e) => setPlanTime(e.currentTarget.value)} />
            </Group>
            <Textarea
              size="md"
              label="Objetivo (opcional)"
              placeholder="Ex.: foco em hipertrofia de membros superiores"
              value={goal}
              onChange={(e) => setGoal(e.currentTarget.value)}
              autosize
              minRows={2}
            />
            <Group grow align="flex-start">
              <NumberInput
                size="md"
                label="Descanso mín. (s)"
                inputMode="numeric"
                value={minRest}
                onChange={(v) => setMinRest(typeof v === 'number' ? v : DEFAULT_MIN_REST_SECONDS)}
                min={REST_SECONDS_SANITY_MIN}
                max={REST_SECONDS_SANITY_MAX}
              />
              <NumberInput
                size="md"
                label="Descanso máx. (s)"
                inputMode="numeric"
                value={maxRest}
                onChange={(v) => setMaxRest(typeof v === 'number' ? v : DEFAULT_MAX_REST_SECONDS)}
                min={REST_SECONDS_SANITY_MIN}
                max={REST_SECONDS_SANITY_MAX}
              />
            </Group>
            <Text size="xs" c="dimmed">
              Exercícios com descanso definido no Modelo usam o valor do Modelo.
            </Text>
            {generateMutation.isError ? (
              <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao iniciar a geração" role="alert">
                {errorMessage(generateMutation.error, 'Não foi possível acionar a geração de plano.')}
              </Alert>
            ) : null}
            {job?.status === 'failed' ? (
              <Alert color="red" icon={<IconAlertTriangle />} title="A geração falhou" role="alert">
                {job.error || 'Não foi possível gerar o rascunho. Tente novamente.'}
              </Alert>
            ) : null}
            {isGenerating ? <LoadingState label="Gerando ficha de treino..." /> : null}
          </Stack>
          <StickyAction>
            <Group wrap="nowrap" gap="sm">
              <Button variant="default" size="md" mih={48} onClick={onClose}>
                Cancelar
              </Button>
              <Button
                size="md"
                color="brand"
                mih={48}
                style={{ flex: 1 }}
                leftSection={<IconSparkles size={20} />}
                loading={isGenerating}
                disabled={!canGenerate}
                onClick={handleGenerate}
              >
                Gerar rascunho
              </Button>
            </Group>
          </StickyAction>
        </>
      )}
    </>
  );
}
