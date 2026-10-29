'use client';

import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Textarea,
  Title,
} from '@mantine/core';
import { IconAlertTriangle, IconArrowLeft, IconBarbell, IconClipboardList, IconSparkles } from '@tabler/icons-react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import type { AsyncJob } from '@/lib/analytics-types';
import { conductedByOptions, type GymResponse, type WorkoutListItem } from '@/lib/workout-log-types';
import {
  type ApiListResponse,
  type WorkoutPlan,
  type WorkoutPlanDraft,
  type WorkoutPlanExercise,
  type WorkoutPlanGenerateInput,
  type WorkoutPlanListItem,
} from '@/lib/workout-plans-types';
import { type StoredWorkoutModeSession, type WorkoutPlanDetail } from '@/lib/workout-mode-types';
import { loadParkedSession } from '@/lib/workout-mode-storage';
import { mergeResumedSession } from '@/lib/workout-mode-plan-flow';
import { resolveWorkoutModeFromPlan } from '@/lib/workout-mode-advanced-stage';
import { DeloadStartPanel } from './DeloadPanels';
import { WorkoutModeNotice } from './WorkoutModeNotice';
import { buildDraftRawPayload, toWorkoutPlanInput } from '@/lib/workout-plan-payload';
import { AUTO_SLOT_VALUE, buildGeneratePayload, canGenerateDraft, localPlanTime } from '@/lib/workout-plan-generate-payload';
import type { AssignedTemplateSummary } from '@/lib/workout-plan-templates-types';
import {
  DEFAULT_MAX_REST_SECONDS,
  DEFAULT_MIN_REST_SECONDS,
  REST_SECONDS_SANITY_MAX,
  REST_SECONDS_SANITY_MIN,
} from '@/lib/prescription-defaults';
import { BRAND } from '@/theme';

type Props = {
  onSessionStarted: (session: StoredWorkoutModeSession) => void;
};

type SubMode = 'select' | 'ai-form' | 'ai-draft' | 'existing-list' | 'freeform-setup';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

type StartSessionResult = { session_id: number; workout_plan_id?: number | null; gym_id?: number | null };

async function startWorkoutSession(payload: {
  name: string;
  workout_plan_id?: number | null;
  gym_id?: number | null;
  gym_name?: string | null;
  conducted_by?: string | null;
  // M97 F3: marca avulsa de deload (só vai quando ligada; período ativo o
  // servidor aplica sozinho).
  deload?: boolean;
}): Promise<StartSessionResult> {
  return apiFetch<{ status: string; session_id: number }>('/workout-sessions/start', {
    method: 'POST',
    body: JSON.stringify(payload),
  }).then((data) => ({ session_id: data.session_id, workout_plan_id: payload.workout_plan_id, gym_id: payload.gym_id }));
}

/**
 * Modo do treino (PR B4 da Evolução ZEPP): decidido pela ficha, nunca por uma
 * escolha "normal/avançado" do usuário. Regra no núcleo
 * (`resolveWorkoutModeFromPlan`, a mesma do relógio); aqui só se lê a ficha.
 */
function isAdvancedPlan(exercises: WorkoutPlanDetail['exercises'] | null | undefined): boolean {
  return resolveWorkoutModeFromPlan(exercises) === 'advanced';
}

function fetchPlanDetail(planId: number): Promise<WorkoutPlanDetail> {
  return apiFetch<WorkoutPlanDetail>(`/workout-plans/${planId}`);
}

/**
 * Tela inicial do modo treino: escolher COMO começar (ficha nova via IA,
 * ficha já planejada, ou treino avulso) — reaproveita `POST
 * /workout-sessions/start` (mesmo endpoint que `WorkoutDetailClient`/o app
 * Zepp usam para abrir uma sessão ao vivo) e o fluxo assíncrono de geração
 * (202 + `job_id` + polling em `GET /jobs/{job_id}`, mesmo padrão de
 * `GenerateDraftModal.tsx` em `/planos-treino`).
 *
 * Sem escolha "normal/avançado" (PR B4 da Evolução ZEPP, decisão do usuário
 * de 2026-10-19): ao iniciar uma sessão com ficha, o modo vem da ficha
 * (`resolveWorkoutModeFromPlan`) e é gravado UMA vez em `advanced_mode`
 * (sempre `true`/`false` em sessão nova). Retomada com snapshot da mesma
 * sessão mantém o modo salvo (nunca muda no meio do treino); sem snapshot
 * (storage perdido, sessão aberta no relógio) aplica a mesma regra à ficha.
 */
export function ModeSelectScreen({ onSessionStarted }: Props) {
  const [subMode, setSubMode] = useState<SubMode>('select');
  // M97 F3: "Sessão de deload" escolhida no início (vale para qualquer forma de começar).
  const [deloadManual, setDeloadManual] = useState(false);
  const deloadField = deloadManual ? { deload: true } : {};

  const gymsQuery = useQuery({
    queryKey: ['workout-mode-gyms'],
    queryFn: () => apiFetch<GymResponse>('/gyms?active_only=true'),
  });

  // Retomada de treino em andamento: a sessão local (localStorage) pode se
  // perder (Safari limpando storage, troca de navegador, saída pelo "X" do
  // header) mesmo com a activity ainda 'in_progress' no backend. Consulta só
  // roda na tela inicial (onde WorkoutModeClient cai sem sessão local).
  const inProgressQuery = useQuery({
    queryKey: ['workout-mode-resume-candidate'],
    queryFn: () => apiFetch<{ items: WorkoutListItem[] }>('/activities?session_status=in_progress&limit=1'),
    enabled: subMode === 'select',
  });
  const inProgressActivity = inProgressQuery.data?.items?.[0] ?? null;

  const resumeMutation = useMutation({
    mutationFn: async (activity: WorkoutListItem): Promise<StoredWorkoutModeSession> => {
      // O servidor só conhece os campos da activity; o estado local da execução
      // (modo avançado, aquecimento/ajuste, skips, overrides) vem do snapshot
      // guardado ao sair pelo "X", quando é da mesma sessão.
      const base: StoredWorkoutModeSession = {
        session_id: activity.id,
        source: activity.workout_plan_id ? 'plan' : 'freeform',
        workout_plan_id: activity.workout_plan_id ?? null,
        gym_id: activity.gym_id ?? null,
        conducted_by: activity.conducted_by ?? null,
      };
      const snapshot = loadParkedSession();
      const merged = mergeResumedSession(base, snapshot);
      // Snapshot da mesma sessão: o modo salvo vale (inclusive o escolhido à
      // mão antes do PR B4). Sem ele, o modo segue a ficha, como numa sessão
      // nova. Se a ficha não carregar, a retomada falha com aviso e "Retomar"
      // tenta de novo: retomar no normal em silêncio gravaria o modo errado
      // até o fim do treino (o modo não muda no meio).
      if (snapshot?.session_id === base.session_id || !activity.workout_plan_id) return merged;
      const plan = await fetchPlanDetail(activity.workout_plan_id);
      return { ...merged, advanced_mode: isAdvancedPlan(plan.exercises) };
    },
    onSuccess: onSessionStarted,
  });

  // ── Gerar ficha do meu modelo ────────────────────────────────────────
  // PR-18: esta tela nunca mandava `template_id` e caía no fluxo Full Body.
  // Agora ela gera a partir de um Modelo de Treino atribuído, como a tela
  // /planos-treino — e sem modelo não há formulário.
  const [genGymId, setGenGymId] = useState<string | null>(null);
  const [genTemplateId, setGenTemplateId] = useState<string>('');
  const [genTemplateSlot, setGenTemplateSlot] = useState<string>(AUTO_SLOT_VALUE);
  const [genGoal, setGenGoal] = useState('');
  const [genPlanDate, setGenPlanDate] = useState('');
  // Hora local capturada no clique de gerar (não na montagem) e reaproveitada
  // no salvamento do mesmo fluxo gerar→salvar. Não há campo de hora na tela.
  const [genPlanTime, setGenPlanTime] = useState<string | null>(null);
  const [genMinRestSeconds, setGenMinRestSeconds] = useState<number | null>(DEFAULT_MIN_REST_SECONDS);
  const [genMaxRestSeconds, setGenMaxRestSeconds] = useState<number | null>(DEFAULT_MAX_REST_SECONDS);
  const [jobId, setJobId] = useState<string | null>(null);
  const [draft, setDraft] = useState<WorkoutPlanDraft | null>(null);

  // Modelos atribuídos ao usuário logado (mesma rota aberta a qualquer
  // usuário autenticado usada em /planos-treino).
  const assignedTemplatesQuery = useQuery({
    queryKey: ['workout-plan-templates-assigned-to-me'],
    queryFn: () => apiFetch<ApiListResponse<AssignedTemplateSummary>>('/workout-plan-templates/assigned-to-me'),
    enabled: subMode === 'ai-form',
  });
  const assignedTemplates = assignedTemplatesQuery.data?.items || [];
  const selectedTemplate = assignedTemplates.find((t) => String(t.id) === genTemplateId) || null;
  const canGenerate = canGenerateDraft(genTemplateId);

  const generateMutation = useMutation({
    mutationFn: (payload: WorkoutPlanGenerateInput) =>
      apiFetch<{ job_id: string; status: string }>('/workout-plans/generate', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    onSuccess: (data) => setJobId(data.job_id),
  });

  const jobQuery = useQuery({
    queryKey: ['workout-mode-plan-generation-job', jobId],
    queryFn: () => apiFetch<AsyncJob<WorkoutPlanDraft>>(`/jobs/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (query) => (query.state.data?.status === 'pending' ? 2000 : false),
  });

  const job = jobQuery.data;
  const isGenerating = generateMutation.isPending || (!!jobId && (job?.status === 'pending' || jobQuery.isLoading));

  useEffect(() => {
    if (job?.status === 'done' && job.result && !draft) {
      setDraft(job.result);
      setSubMode('ai-draft');
    }
  }, [job, draft]);

  function handleGenerate() {
    if (!canGenerate) return;
    setJobId(null);
    setDraft(null);
    const planTime = localPlanTime();
    setGenPlanTime(planTime);
    generateMutation.mutate(
      buildGeneratePayload({
        templateId: genTemplateId,
        templateSlotSequence: genTemplateSlot,
        gymId: genGymId,
        gymName: genGymId ? gymsQuery.data?.items.find((g) => String(g.id) === genGymId)?.name : null,
        goal: genGoal,
        planDate: genPlanDate,
        planTime,
        minRestSeconds: genMinRestSeconds,
        maxRestSeconds: genMaxRestSeconds,
      }),
    );
  }

  const confirmDraftMutation = useMutation({
    mutationFn: async () => {
      if (!draft) throw new Error('Nenhum rascunho gerado.');
      const savePayload = toWorkoutPlanInput(
        {
          name: draft.name,
          plan_type: draft.plan_type,
          split_day: draft.plan_type === 'abc' ? draft.split_day || null : null,
          goal: draft.goal || null,
          plan_date: draft.plan_date || genPlanDate || null,
          plan_time: genPlanTime || localPlanTime(),
          status: 'planned',
          gym_id: draft.gym_id ?? (genGymId ? Number(genGymId) : null),
          gym_name: draft.gym_name ?? null,
          template_id: draft.template_id ?? null,
          template_slot_sequence: draft.template_slot_sequence ?? null,
          template_version_id: draft.template_version_id ?? null,
          raw_payload: buildDraftRawPayload(draft),
        },
        draft.exercises,
        { sequenceFromIndex: false },
      );
      const savedPlan = await apiFetch<WorkoutPlan>('/workout-plans', {
        method: 'POST',
        body: JSON.stringify(savePayload),
      });

      const result = await startWorkoutSession({
        name: savedPlan.name,
        workout_plan_id: savedPlan.id,
        gym_id: savedPlan.gym_id,
        gym_name: savedPlan.gym_name,
        ...deloadField,
      });
      // A ficha GRAVADA (pós-segurança) decide o modo, não o rascunho.
      return { ...result, advanced_mode: isAdvancedPlan(savedPlan.exercises) };
    },
    onSuccess: (result) => {
      onSessionStarted({
        session_id: result.session_id,
        source: 'plan',
        workout_plan_id: result.workout_plan_id ?? null,
        gym_id: result.gym_id ?? null,
        advanced_mode: result.advanced_mode,
      });
    },
  });

  // ── Ficha existente ──────────────────────────────────────────────────
  const existingPlansQuery = useQuery({
    queryKey: ['workout-mode-existing-plans'],
    queryFn: () => apiFetch<ApiListResponse<WorkoutPlanListItem>>('/workout-plans?status=planned&limit=50'),
    enabled: subMode === 'existing-list',
  });

  const startFromExistingMutation = useMutation({
    mutationFn: async (plan: WorkoutPlanListItem) => {
      // Lê a ficha ANTES de abrir a sessão: o modo sai dela, e uma falha aqui
      // não deixa uma sessão `in_progress` órfã no backend.
      const detail = await fetchPlanDetail(plan.id);
      const result = await startWorkoutSession({
        name: plan.name,
        workout_plan_id: plan.id,
        gym_id: plan.gym_id,
        ...deloadField,
      });
      return { ...result, advanced_mode: isAdvancedPlan(detail.exercises) };
    },
    onSuccess: (result) => {
      onSessionStarted({
        session_id: result.session_id,
        source: 'plan',
        workout_plan_id: result.workout_plan_id ?? null,
        gym_id: result.gym_id ?? null,
        advanced_mode: result.advanced_mode,
      });
    },
  });

  // ── Treino avulso ────────────────────────────────────────────────────
  const [freeformGymId, setFreeformGymId] = useState<string | null>(null);
  const [freeformConductedBy, setFreeformConductedBy] = useState<string | null>(null);

  const startFreeformMutation = useMutation({
    mutationFn: () => {
      const gym = gymsQuery.data?.items.find((g) => String(g.id) === freeformGymId);
      return startWorkoutSession({
        name: 'Treino avulso',
        gym_id: freeformGymId ? Number(freeformGymId) : null,
        gym_name: gym?.name || null,
        conducted_by: freeformConductedBy || null,
        ...deloadField,
      });
    },
    onSuccess: (result) => {
      onSessionStarted({
        session_id: result.session_id,
        source: 'freeform',
        gym_id: freeformGymId ? Number(freeformGymId) : null,
        conducted_by: freeformConductedBy || null,
      });
    },
  });

  const gymOptions = (gymsQuery.data?.items || []).map((g) => ({ value: String(g.id), label: g.name }));

  if (subMode === 'select') {
    return (
      <Stack gap="xl" py="md">
        <div>
          <Title order={2} c={BRAND.textPrimary}>
            Modo de treino
          </Title>
          <Text c={BRAND.textMuted}>Escolha como começar seu treino de hoje.</Text>
        </div>
        {inProgressActivity && (
          <Card withBorder radius="xl" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.orange }}>
            <Group justify="space-between" wrap="nowrap" align="flex-start" gap="md">
              <Group gap="md" wrap="nowrap">
                <IconAlertTriangle size={32} color={BRAND.orange} />
                <div>
                  <Text fw={800} c={BRAND.textPrimary}>
                    Treino em andamento encontrado
                  </Text>
                  <Text size="sm" c={BRAND.textMuted}>
                    {inProgressActivity.workout_plan_name || inProgressActivity.name} — {inProgressActivity.local_date}
                  </Text>
                </div>
              </Group>
              <Button
                radius="xl"
                loading={resumeMutation.isPending}
                onClick={() => inProgressActivity && resumeMutation.mutate(inProgressActivity)}
              >
                Retomar
              </Button>
            </Group>
          </Card>
        )}
        {resumeMutation.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            {errorMessage(resumeMutation.error, 'Não foi possível carregar a ficha do treino em andamento.')} Toque em
            &quot;Retomar&quot; para tentar de novo.
          </Alert>
        )}
        <DeloadStartPanel manual={deloadManual} onManualChange={setDeloadManual} />
        <SimpleGrid cols={1} spacing="md">
          <Card
            component="button"
            withBorder
            radius="xl"
            p="xl"
            onClick={() => setSubMode('ai-form')}
            style={{ cursor: 'pointer', textAlign: 'left', background: BRAND.bgElevated, borderColor: BRAND.border }}
          >
            <Group gap="md" wrap="nowrap">
              <IconSparkles size={36} color={BRAND.orange} />
              <div>
                <Text fw={800} fz="lg" c={BRAND.textPrimary}>
                  Gerar ficha do meu modelo
                </Text>
                <Text size="sm" c={BRAND.textMuted}>
                  Gera a ficha do dia a partir do seu Modelo de Treino, considerando recuperação muscular, histórico e
                  equipamento da academia.
                </Text>
              </div>
            </Group>
          </Card>
          <Card
            component="button"
            withBorder
            radius="xl"
            p="xl"
            onClick={() => setSubMode('existing-list')}
            style={{ cursor: 'pointer', textAlign: 'left', background: BRAND.bgElevated, borderColor: BRAND.border }}
          >
            <Group gap="md" wrap="nowrap">
              <IconClipboardList size={36} color="var(--mantine-color-grape-6)" />
              <div>
                <Text fw={800} fz="lg" c={BRAND.textPrimary}>
                  Ficha existente
                </Text>
                <Text size="sm" c={BRAND.textMuted}>
                  Escolha uma ficha já planejada e comece a executar. Se ela tiver aquecimento, série de ajuste ou
                  técnica avançada configurados, o treino já abre no avançado.
                </Text>
              </div>
            </Group>
          </Card>
          <Card
            component="button"
            withBorder
            radius="xl"
            p="xl"
            onClick={() => setSubMode('freeform-setup')}
            style={{ cursor: 'pointer', textAlign: 'left', background: BRAND.bgElevated, borderColor: BRAND.border }}
          >
            <Group gap="md" wrap="nowrap">
              <IconBarbell size={36} color="var(--mantine-color-teal-6)" />
              <div>
                <Text fw={800} fz="lg" c={BRAND.textPrimary}>
                  Treino avulso
                </Text>
                <Text size="sm" c={BRAND.textMuted}>
                  Sem ficha — escolha equipamento e exercício na hora, um de cada vez.
                </Text>
              </div>
            </Group>
          </Card>
        </SimpleGrid>
      </Stack>
    );
  }

  if (subMode === 'ai-form') {
    return (
      <Stack gap="md" py="md">
        <Button variant="subtle" leftSection={<IconArrowLeft size={16} />} onClick={() => setSubMode('select')}>
          Voltar
        </Button>
        <Title order={3} c={BRAND.textPrimary}>
          Gerar ficha do meu modelo
        </Title>
        {assignedTemplatesQuery.isLoading && (
          <Group>
            <Loader size="sm" />
            <Text c={BRAND.textMuted}>Carregando seus modelos de treino...</Text>
          </Group>
        )}
        {assignedTemplatesQuery.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            {errorMessage(assignedTemplatesQuery.error, 'Não foi possível carregar seus modelos de treino.')}
          </Alert>
        )}
        {!assignedTemplatesQuery.isLoading && !assignedTemplatesQuery.isError && assignedTemplates.length === 0 && (
          <Alert color="yellow" icon={<IconAlertTriangle size={16} />} title="Nenhum modelo de treino atribuído">
            Peça um modelo de treino ao seu treinador. A ficha é sempre gerada a partir de um Modelo de Treino
            atribuído a você.
          </Alert>
        )}
        {assignedTemplates.length > 0 && (
          <>
            <Select
              label="Modelo de treino"
              placeholder="Escolha um modelo atribuído a você"
              data={assignedTemplates.map((t) => ({ value: String(t.id), label: t.name }))}
              value={genTemplateId || null}
              onChange={(v) => {
                setGenTemplateId(v || '');
                setGenTemplateSlot(AUTO_SLOT_VALUE);
              }}
              withAsterisk
              searchable
            />
            {selectedTemplate && (
              <Select
                label="Slot do modelo"
                data={[
                  { value: AUTO_SLOT_VALUE, label: 'Automático (próximo do ciclo)' },
                  ...selectedTemplate.slots.map((s) => ({ value: String(s.sequence), label: s.name })),
                ]}
                value={genTemplateSlot}
                onChange={(v) => setGenTemplateSlot(v || AUTO_SLOT_VALUE)}
              />
            )}
            <Select label="Academia" placeholder="Selecione" data={gymOptions} value={genGymId} onChange={setGenGymId} clearable />
            <TextInput label="Data" type="date" value={genPlanDate} onChange={(e) => setGenPlanDate(e.currentTarget.value)} />
            <Textarea label="Objetivo (opcional)" value={genGoal} onChange={(e) => setGenGoal(e.currentTarget.value)} autosize minRows={2} />
            <Group grow>
              <NumberInput
                label="Descanso mínimo (s)"
                value={genMinRestSeconds ?? undefined}
                onChange={(v) => setGenMinRestSeconds(v === '' ? null : Number(v))}
                min={REST_SECONDS_SANITY_MIN}
                max={REST_SECONDS_SANITY_MAX}
                step={5}
              />
              <NumberInput
                label="Descanso máximo (s)"
                value={genMaxRestSeconds ?? undefined}
                onChange={(v) => setGenMaxRestSeconds(v === '' ? null : Number(v))}
                min={REST_SECONDS_SANITY_MIN}
                max={REST_SECONDS_SANITY_MAX}
                step={5}
              />
            </Group>
            <Text size="xs" c="dimmed">
              Exercícios com descanso definido no Modelo usam o valor do Modelo.
            </Text>
            {generateMutation.isError && (
              <Alert color="red" icon={<IconAlertTriangle size={16} />}>
                {errorMessage(generateMutation.error, 'Não foi possível acionar a geração de plano.')}
              </Alert>
            )}
            {job?.status === 'failed' && (
              <Alert color="red" icon={<IconAlertTriangle size={16} />}>
                {job.error || 'Não foi possível gerar o rascunho. Tente novamente.'}
              </Alert>
            )}
            <Button
              size="lg"
              radius="xl"
              h={56}
              leftSection={<IconSparkles size={18} />}
              loading={isGenerating}
              onClick={handleGenerate}
              disabled={!canGenerate}
            >
              Gerar ficha
            </Button>
            <Text size="xs" c={BRAND.textMuted} ta="center">
              Pode levar até 1 minuto.
            </Text>
          </>
        )}
      </Stack>
    );
  }

  if (subMode === 'ai-draft' && draft) {
    return (
      <Stack gap="md" py="md">
        <Button
          variant="subtle"
          leftSection={<IconArrowLeft size={16} />}
          onClick={() => {
            setDraft(null);
            setJobId(null);
            setSubMode('ai-form');
          }}
        >
          Voltar
        </Button>
        <Title order={3} c={BRAND.textPrimary}>
          {draft.name}
        </Title>
        <Text size="sm" c={BRAND.textMuted}>
          Revise rapidamente abaixo. Para ajustes detalhados, use /planos-treino no desktop.
        </Text>
        {/* Prévia: quem decide o modo é a ficha GRAVADA (ver confirmDraftMutation). */}
        <WorkoutModeNotice advanced={isAdvancedPlan(draft.exercises)} />
        <Stack gap="xs">
          {(draft.exercises || []).map((ex: WorkoutPlanExercise) => (
            <Card key={ex.sequence} withBorder radius="lg" p="sm" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
              <Group justify="space-between" wrap="nowrap">
                <Text size="sm" fw={600} c={BRAND.textPrimary}>
                  {ex.sequence}. {ex.exercise_name}
                </Text>
                <Text size="xs" c={BRAND.textMuted}>
                  {ex.sets_count}x{ex.reps_target} · {ex.load_target || '-'}
                </Text>
              </Group>
            </Card>
          ))}
        </Stack>
        {confirmDraftMutation.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            {errorMessage(confirmDraftMutation.error, 'Não foi possível salvar/iniciar a ficha.')}
          </Alert>
        )}
        <Button size="lg" radius="xl" h={56} loading={confirmDraftMutation.isPending} onClick={() => confirmDraftMutation.mutate()}>
          Confirmar e começar treino
        </Button>
      </Stack>
    );
  }

  if (subMode === 'existing-list') {
    return (
      <Stack gap="md" py="md">
        <Button variant="subtle" leftSection={<IconArrowLeft size={16} />} onClick={() => setSubMode('select')}>
          Voltar
        </Button>
        <Title order={3} c={BRAND.textPrimary}>
          Ficha existente
        </Title>
        {existingPlansQuery.isLoading && <Loader size="sm" color="brand" />}
        {existingPlansQuery.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            Não foi possível carregar as fichas planejadas.
          </Alert>
        )}
        {startFromExistingMutation.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            {errorMessage(startFromExistingMutation.error, 'Não foi possível iniciar a sessão de treino.')}
          </Alert>
        )}
        <Stack gap="xs">
          {(existingPlansQuery.data?.items || []).map((plan) => (
            <Card
              key={plan.id}
              component="button"
              withBorder
              radius="xl"
              p="md"
              onClick={() => startFromExistingMutation.mutate(plan)}
              style={{ cursor: 'pointer', textAlign: 'left', background: BRAND.bgElevated, borderColor: BRAND.border }}
            >
              <Group justify="space-between" wrap="nowrap">
                <div>
                  <Text fw={700} c={BRAND.textPrimary}>
                    {plan.name}
                  </Text>
                  <Text size="xs" c={BRAND.textMuted}>
                    {plan.plan_date || 'Sem data'} {plan.gym_name ? `· ${plan.gym_name}` : ''}
                  </Text>
                </div>
                <Group gap={6}>
                  <Badge variant="light" color="brand">
                    {plan.exercises_count ?? 0} exercícios
                  </Badge>
                  {startFromExistingMutation.isPending && startFromExistingMutation.variables?.id === plan.id && (
                    <Loader size="xs" color="brand" />
                  )}
                </Group>
              </Group>
            </Card>
          ))}
          {!existingPlansQuery.isLoading && (existingPlansQuery.data?.items || []).length === 0 && (
            <Text c={BRAND.textMuted} size="sm" ta="center" py="md">
              Nenhuma ficha planejada encontrada.
            </Text>
          )}
        </Stack>
      </Stack>
    );
  }

  if (subMode === 'freeform-setup') {
    return (
      <Stack gap="md" py="md">
        <Button variant="subtle" leftSection={<IconArrowLeft size={16} />} onClick={() => setSubMode('select')}>
          Voltar
        </Button>
        <Title order={3} c={BRAND.textPrimary}>
          Treino avulso
        </Title>
        <Select label="Academia" placeholder="Selecione (opcional)" data={gymOptions} value={freeformGymId} onChange={setFreeformGymId} clearable />
        <Select
          label="Conduzido por"
          placeholder="Selecione (opcional)"
          data={conductedByOptions}
          value={freeformConductedBy}
          onChange={setFreeformConductedBy}
          clearable
        />
        {startFreeformMutation.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            {errorMessage(startFreeformMutation.error, 'Não foi possível iniciar a sessão de treino.')}
          </Alert>
        )}
        <Button size="lg" radius="xl" h={56} loading={startFreeformMutation.isPending} onClick={() => startFreeformMutation.mutate()}>
          Começar treino avulso
        </Button>
      </Stack>
    );
  }

  return null;
}
