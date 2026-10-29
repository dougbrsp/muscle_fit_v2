'use client';

import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import {
  Alert,
  Badge,
  Button,
  Divider,
  Group,
  Loader,
  Modal,
  NumberInput,
  Select,
  Stack,
  Text,
  Textarea,
  TextInput,
  Title,
} from '@mantine/core';
import { IconAlertTriangle, IconSparkles } from '@tabler/icons-react';
import { apiFetch, ApiError } from '@/lib/api';
import type { ApiListResponse, Gym } from '@/lib/gyms-types';
import type { AsyncJob } from '@/lib/analytics-types';
import type { AssignedTemplateSummary } from '@/lib/workout-plan-templates-types';
import {
  WORKOUT_PLAN_STATUS_OPTIONS,
  type WorkoutPlan,
  type WorkoutPlanDraft,
  type WorkoutPlanExercise,
  type WorkoutPlanGenerateInput,
  type WorkoutPlanInput,
} from '@/lib/workout-plans-types';
import { buildDraftRawPayload, toWorkoutPlanInput } from '@/lib/workout-plan-payload';
import { AUTO_SLOT_VALUE, buildGeneratePayload, canGenerateDraft } from '@/lib/workout-plan-generate-payload';
import { ExercisesTable } from './ExercisesTable';
import { MuscleActivationPreview } from './MuscleActivationPreview';
import { GenerationWarnings } from './GenerationWarnings';
import {
  DEFAULT_MAX_REST_SECONDS,
  DEFAULT_MIN_REST_SECONDS,
  REST_SECONDS_SANITY_MAX,
  REST_SECONDS_SANITY_MIN,
} from '@/lib/prescription-defaults';
import { BRAND } from '@/theme';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

type Props = {
  opened: boolean;
  onClose: () => void;
};

/**
 * Fluxo de geração de rascunho (`POST /workout-plans/generate`, 202 +
 * job_id + polling em `GET /jobs/{job_id}` — mesmo padrão assíncrono já
 * estabelecido em `/agente-cardio`) seguido de revisão/edição do rascunho e
 * `POST /workout-plans` para persistir de fato.
 *
 * Geração de cardio é MANUAL (paridade com o onpremise): este modal não
 * dispara `POST /cardio-recommendations/generate` sozinho — o usuário aciona
 * pelo botão "Gerar recomendação de cardio" no card do plano já salvo, em
 * `PlanDetailClient.tsx` (evita custo de chamada à IA a cada plano criado).
 */
export function GenerateDraftModal({ opened, onClose }: Props) {
  const qc = useQueryClient();
  const router = useRouter();

  const [gymId, setGymId] = useState<string | null>(null);
  const [planDate, setPlanDate] = useState<string>('');
  const [planTime, setPlanTime] = useState<string>('');
  const [goal, setGoal] = useState('');
  const [minRest, setMinRest] = useState<number>(DEFAULT_MIN_REST_SECONDS);
  const [maxRest, setMaxRest] = useState<number>(DEFAULT_MAX_REST_SECONDS);
  // Modelo de Treino: OBRIGATÓRIO desde o PR-18 ('' = nada escolhido ainda,
  // botão de gerar desabilitado). '__auto__' de slotSequence = próximo slot
  // automático do ciclo.
  const [templateId, setTemplateId] = useState<string>('');
  const [templateSlotSequence, setTemplateSlotSequence] = useState<string>(AUTO_SLOT_VALUE);

  const [jobId, setJobId] = useState<string | null>(null);
  const [draft, setDraft] = useState<WorkoutPlanDraft | null>(null);
  const [draftName, setDraftName] = useState('');
  const [draftExercises, setDraftExercises] = useState<WorkoutPlanExercise[]>([]);
  const [status, setStatus] = useState('planned');

  const gymsQuery = useQuery({
    queryKey: ['gyms'],
    queryFn: () => apiFetch<ApiListResponse<Gym>>('/gyms?active_only=true'),
    enabled: opened,
  });

  // Modelos de Ficha de Treino Personalizados atribuídos ao usuário logado —
  // lista pequena, aberta a qualquer usuário autenticado (me_router).
  const assignedTemplatesQuery = useQuery({
    queryKey: ['workout-plan-templates-assigned-to-me'],
    queryFn: () => apiFetch<ApiListResponse<AssignedTemplateSummary>>('/workout-plan-templates/assigned-to-me'),
    enabled: opened,
  });
  const assignedTemplates = assignedTemplatesQuery.data?.items || [];
  const selectedTemplate = assignedTemplates.find((t) => String(t.id) === templateId) || null;

  function resetForm() {
    setJobId(null);
    setDraft(null);
    setDraftName('');
    setDraftExercises([]);
  }

  function handleClose() {
    resetForm();
    onClose();
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
      handleClose();
      router.push(`/planos-treino/${plan.id}`);
    },
  });

  function handleGenerate() {
    if (!canGenerate) return;
    resetForm();
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
    const payload = toWorkoutPlanInput(
      {
        name: draftName,
        plan_type: draft.plan_type,
        split_day: draft.split_day || null,
        goal: draft.goal || null,
        status,
        plan_date: planDate || null,
        plan_time: planTime || null,
        gym_id: gymId ? Number(gymId) : null,
        // Persiste de qual modelo/slot este rascunho veio (null quando não
        // veio de nenhum modelo) — usado por resolve_next_slot para calcular
        // o próximo slot do ciclo em futuras gerações a partir do mesmo modelo.
        template_id: draft.template_id ?? null,
        template_slot_sequence: draft.template_slot_sequence ?? null,
        template_version_id: draft.template_version_id ?? null,
        // Resumo com tudo que a tela de detalhe reexibe depois de salvo.
        raw_payload: buildDraftRawPayload(draft),
      },
      draftExercises,
    );
    createMutation.mutate(payload);
  }

  const gymOptions = (gymsQuery.data?.items || []).map((g) => ({ value: String(g.id), label: g.name }));
  // Estado vazio: sem nenhum modelo atribuído não há o que gerar (PR-18) — o
  // formulário some e o usuário recebe a instrução do que fazer.
  const hasTemplates = assignedTemplates.length > 0;
  const templatesLoading = assignedTemplatesQuery.isLoading;
  const canGenerate = canGenerateDraft(templateId);

  return (
    <Modal opened={opened} onClose={handleClose} title="Gerar novo plano de treino" size="90%" centered>
      <Stack gap="md">
        {!draft && !hasTemplates && (
          <Stack gap="sm" py="md">
            {templatesLoading ? (
              <Group>
                <Loader size="sm" color="brand" />
                <Text c={BRAND.textMuted}>Carregando seus modelos de treino...</Text>
              </Group>
            ) : assignedTemplatesQuery.isError ? (
              <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao carregar seus modelos">
                {errorMessage(assignedTemplatesQuery.error, 'Não foi possível carregar seus modelos de treino.')}
              </Alert>
            ) : (
              <>
                <Alert color="yellow" icon={<IconAlertTriangle />} title="Nenhum modelo de treino atribuído">
                  Peça um modelo de treino ao seu treinador. A ficha é sempre gerada a partir de um Modelo de Treino
                  atribuído a você.
                </Alert>
                <Group justify="flex-end">
                  <Button variant="default" onClick={handleClose}>
                    Fechar
                  </Button>
                </Group>
              </>
            )}
          </Stack>
        )}

        {!draft && hasTemplates && (
          <>
            <Group grow align="flex-end">
              <Select
                label="Modelo de treino"
                description="Obrigatório — a ficha é gerada a partir da estrutura deste modelo"
                placeholder="Escolha um modelo atribuído a você"
                data={assignedTemplates.map((t) => ({ value: String(t.id), label: t.name }))}
                value={templateId || null}
                onChange={(v) => {
                  setTemplateId(v || '');
                  setTemplateSlotSequence(AUTO_SLOT_VALUE);
                }}
                searchable
                withAsterisk
              />
              {selectedTemplate && (
                <Select
                  label="Slot do modelo"
                  data={[
                    { value: AUTO_SLOT_VALUE, label: 'Automático (próximo do ciclo)' },
                    ...selectedTemplate.slots.map((s) => ({ value: String(s.sequence), label: s.name })),
                  ]}
                  value={templateSlotSequence}
                  onChange={(v) => setTemplateSlotSequence(v || AUTO_SLOT_VALUE)}
                />
              )}
            </Group>
            <Group grow>
              <Select
                label="Academia"
                placeholder="Nenhuma"
                data={gymOptions}
                value={gymId}
                onChange={setGymId}
                clearable
                searchable
              />
              <TextInput label="Data do plano" type="date" value={planDate} onChange={(e) => setPlanDate(e.currentTarget.value)} />
              <TextInput label="Hora pretendida" type="time" value={planTime} onChange={(e) => setPlanTime(e.currentTarget.value)} />
            </Group>
            <Textarea
              label="Objetivo (opcional)"
              placeholder="Ex.: foco em hipertrofia de membros superiores"
              value={goal}
              onChange={(e) => setGoal(e.currentTarget.value)}
              autosize
              minRows={2}
            />
            <Group grow>
              <NumberInput label="Descanso mínimo (s)" value={minRest} onChange={(v) => setMinRest(typeof v === 'number' ? v : DEFAULT_MIN_REST_SECONDS)} min={REST_SECONDS_SANITY_MIN} max={REST_SECONDS_SANITY_MAX} />
              <NumberInput label="Descanso máximo (s)" value={maxRest} onChange={(v) => setMaxRest(typeof v === 'number' ? v : DEFAULT_MAX_REST_SECONDS)} min={REST_SECONDS_SANITY_MIN} max={REST_SECONDS_SANITY_MAX} />
            </Group>
            <Text size="xs" c="dimmed">
              Exercícios com descanso definido no Modelo usam o valor do Modelo.
            </Text>

            {generateMutation.isError && (
              <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao iniciar a geração">
                {errorMessage(generateMutation.error, 'Não foi possível acionar a geração de plano.')}
              </Alert>
            )}
            {job?.status === 'failed' && (
              <Alert color="red" icon={<IconAlertTriangle />} title="A geração falhou">
                {job.error || 'Não foi possível gerar o rascunho. Tente novamente.'}
              </Alert>
            )}
            {isGenerating && (
              <Group>
                <Loader size="sm" color="brand" />
                <Text c={BRAND.textMuted}>Gerando ficha de treino...</Text>
              </Group>
            )}

            <Group justify="flex-end">
              <Button variant="default" onClick={handleClose}>
                Cancelar
              </Button>
              <Button
                leftSection={<IconSparkles size={16} />}
                color="brand"
                loading={isGenerating}
                onClick={handleGenerate}
                disabled={!canGenerate}
              >
                Gerar rascunho
              </Button>
            </Group>
          </>
        )}

        {draft && (
          <>
            <Group justify="space-between" align="flex-end" wrap="wrap">
              <TextInput label="Nome da ficha" value={draftName} onChange={(e) => setDraftName(e.currentTarget.value)} w={320} />
              <Group>
                {draft.split_day && <Badge color="brand">Treino {draft.split_day}</Badge>}
                <Select
                  label="Status"
                  data={WORKOUT_PLAN_STATUS_OPTIONS as unknown as { value: string; label: string }[]}
                  value={status}
                  onChange={(v) => setStatus(v || 'planned')}
                  w={160}
                />
              </Group>
            </Group>

            <GenerationWarnings draft={draft} />

            <Divider label="Exercícios" />
            <ExercisesTable exercises={draftExercises} onChange={setDraftExercises} />

            <Divider label="Prévia de ativação muscular" />
            <MuscleActivationPreview exercises={draftExercises} />

            {createMutation.isError && (
              <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao salvar">
                {errorMessage(createMutation.error, 'Não foi possível salvar o plano.')}
              </Alert>
            )}

            <Group justify="flex-end">
              <Button variant="default" onClick={resetForm}>
                Gerar outro rascunho
              </Button>
              <Button color="brand" loading={createMutation.isPending} onClick={handleSave} disabled={!draftName.trim()}>
                Salvar ficha
              </Button>
            </Group>
          </>
        )}
      </Stack>
    </Modal>
  );
}
