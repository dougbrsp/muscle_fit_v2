'use client';

import { COUNTING_MODE_SELECT_DATA, countingModeOption, normalizeCountingMode } from '@/lib/volume-counting-mode';
import {
  Alert,
  Badge,
  Button,
  Divider,
  Group,
  Modal,
  MultiSelect,
  NumberInput,
  Progress,
  Select,
  Stack,
  Text,
  Textarea,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { IconAlertTriangle, IconSparkles } from '@tabler/icons-react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import { pollAsyncJob } from '@/lib/async-job-poll';
import type { ApiListResponse as GymsApiListResponse, Gym } from '@/lib/gyms-types';
import {
  PERIODIZATION_DAYS_MAX,
  PERIODIZATION_DAYS_MIN,
  PERIODIZATION_INSTRUCTIONS_MAX_LENGTH,
  PERIODIZATION_POLL_TIMEOUT_MS,
  buildPeriodizationRequest,
  countPeriodizationReport,
  describePeriodizationEntry,
  newPeriodizationForm,
  parsePeriodizationJobError,
  periodizationDayGoals,
  periodizationJobResultToSeed,
  periodizationOriginQuery,
  storePeriodizationSeed,
  validatePeriodizationForm,
} from '@/lib/periodization';
import {
  defaultGoal,
  goalOption,
  goalSelectData,
  goalValue,
  itemGoalFromSelect,
  itemGoalSelectData,
  itemGoalSelectValue,
  trainingGoalDescription,
  useTrainingGoals,
} from '@/lib/training-goals';
import {
  PERIODIZATION_CARDIO_OPTIONS,
  PERIODIZATION_VOLUME_MODE_HINT,
  type PeriodizationCardioChoice,
  type PeriodizationDraftAccepted,
  type PeriodizationFailure,
  type PeriodizationFormState,
  type PeriodizationJobResult,
} from '@/lib/periodization-types';
import type {
  ApiListResponse as TrainingTypesApiListResponse,
  TrainingTypeListItem,
} from '@/lib/training-types-types';
import { VOLUME_MODE_OPTIONS, type VolumeMode } from '@/lib/workout-plan-templates-types';
import { BRAND } from '@/theme';

// Expectativa honesta de duração (§7.1: RAG + chamada à OpenAI com timeout de
// 90 s dentro de uma Lambda de 120 s). Serve só para a barra de progresso não
// mentir: ela satura e continua "em andamento", nunca finge ter acabado.
const EXPECTED_DURATION_SECONDS = 60;

type Phase = 'form' | 'generating' | 'failed';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : error instanceof Error ? error.message : fallback;
}

/**
 * Modal "Gerar modelo automaticamente" (PR-17, PLANO_20260912.md §7.1 passos
 * 1-3 e 9). Dispara `POST /workout-plan-templates/periodization-drafts`
 * (202 + `job_id`), faz polling de `GET /jobs/{job_id}` e, no sucesso, guarda o
 * rascunho na sessão e abre `/modelos-treino/novo` em modo "rascunho não
 * salvo".
 *
 * **Nada é gravado em modelo aqui.** Este componente não tem nenhum caminho de
 * POST/PUT para `/workout-plan-templates` — quem grava é o "Salvar" do builder,
 * depois da edição humana, levando o `generation_log_id` junto.
 */
export function PeriodizationModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const router = useRouter();
  const isMobile = useMediaQuery('(max-width: 48em)');

  const [form, setForm] = useState<PeriodizationFormState>(newPeriodizationForm);
  const [phase, setPhase] = useState<Phase>('form');
  const [failure, setFailure] = useState<PeriodizationFailure | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  // Só para exibição: o id da auditoria já existe no 202, antes de o job
  // terminar — útil quando a geração falha e o usuário precisa reportar.
  const [generationLogId, setGenerationLogId] = useState<number | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function patch(next: Partial<PeriodizationFormState>) {
    setForm((prev) => ({ ...prev, ...next }));
  }

  const ragFoldersQuery = useQuery({
    queryKey: ['training-type-rag-folders'],
    queryFn: () => apiFetch<{ items: string[] }>('/training-types/rag-folders'),
    enabled: opened,
    staleTime: 300_000,
    retry: false,
  });

  const trainingTypesQuery = useQuery({
    queryKey: ['training-types-for-templates'],
    queryFn: () => apiFetch<TrainingTypesApiListResponse<TrainingTypeListItem>>('/training-types'),
    enabled: opened,
    retry: false,
  });

  const gymsQuery = useQuery({
    queryKey: ['gyms'],
    queryFn: () => apiFetch<GymsApiListResponse<Gym>>('/gyms?active_only=true'),
    enabled: opened,
    retry: false,
  });

  // M97 F5: objetivos (rótulos e default) vêm de `GET /training-goals`; a tela
  // não escreve nome nem número de objetivo.
  const trainingGoalsQuery = useTrainingGoals();
  const trainingGoals = trainingGoalsQuery.data;

  function setDayGoal(index: number, goal: string | null) {
    setForm((prev) => {
      const next = periodizationDayGoals(prev);
      next[index] = goal;
      return { ...prev, dayGoals: next };
    });
  }

  function stopTimer() {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }

  useEffect(() => stopTimer, []);

  const generateMutation = useMutation({
    mutationFn: async (): Promise<PeriodizationJobResult> => {
      const accepted = await apiFetch<PeriodizationDraftAccepted>(
        '/workout-plan-templates/periodization-drafts',
        {
          method: 'POST',
          body: JSON.stringify(buildPeriodizationRequest(form, defaultGoal(trainingGoals)?.value ?? null)),
        },
      );
      setGenerationLogId(accepted.generation_log_id ?? null);
      return pollAsyncJob<PeriodizationJobResult>(accepted.job_id, {
        timeoutMs: PERIODIZATION_POLL_TIMEOUT_MS,
        failedFallbackMessage:
          'A geração automática do modelo falhou no backend. Nenhum modelo foi criado.',
        timeoutMessage:
          'A geração passou do tempo esperado. Nenhum modelo foi criado — tente novamente em instantes.',
      });
    },
    onSuccess: (result) => {
      stopTimer();
      // O rascunho vai para a sessão (não para o banco) e o builder o lê ao
      // abrir com a marca `?origem=periodizacao`.
      storePeriodizationSeed(periodizationJobResultToSeed(result));
      setPhase('form');
      onClose();
      router.push(`/modelos-treino/novo${periodizationOriginQuery()}`);
    },
    onError: (error: unknown) => {
      stopTimer();
      // 429 = limite de 10 gerações por usuário/dia (§7.3): chega como
      // `ApiError` do próprio POST, antes de existir job.
      if (error instanceof ApiError) {
        setFailure({
          message:
            error.status === 429
              ? error.message ||
                'Limite diário de gerações automáticas de modelo atingido. Tente novamente amanhã.'
              : errorMessage(error, 'Não foi possível iniciar a geração automática do modelo.'),
          validationReport: null,
          generationLogId: null,
          model: null,
          promptVersion: null,
        });
      } else {
        // Job `failed`: o relatório do validador vem serializado em JSON dentro
        // de `job.error`, e é ele que diz ao usuário o que foi recusado.
        setFailure(parsePeriodizationJobError(errorMessage(error, '')));
      }
      setPhase('failed');
    },
  });

  function handleGenerate() {
    const validationError = validatePeriodizationForm(form);
    setFormError(validationError);
    if (validationError) return;
    setFailure(null);
    setElapsedSeconds(0);
    setGenerationLogId(null);
    setPhase('generating');
    stopTimer();
    timerRef.current = setInterval(() => setElapsedSeconds((prev) => prev + 1), 1000);
    generateMutation.mutate();
  }

  function handleClose() {
    if (phase === 'generating') return; // fechar não cancela a Lambda: não fingir que cancela.
    stopTimer();
    setPhase('form');
    setFailure(null);
    setFormError(null);
    onClose();
  }

  const ragOptions = (ragFoldersQuery.data?.items || []).map((folder) => ({ value: folder, label: folder }));
  const trainingTypeOptions = (trainingTypesQuery.data?.items || [])
    .filter((item) => item.is_active)
    .map((item) => ({ value: String(item.id), label: item.name }));
  const gymOptions = (gymsQuery.data?.items || []).map((gym) => ({ value: String(gym.id), label: gym.name }));
  const gymRequired = form.volumeMode === 'equipment_series';
  const instructionsLength = form.instructions.length;
  const progressValue = Math.min(99, Math.round((elapsedSeconds / EXPECTED_DURATION_SECONDS) * 100));

  return (
    <Modal
      opened={opened}
      onClose={handleClose}
      title="Gerar modelo automaticamente"
      size="lg"
      fullScreen={isMobile}
      radius={isMobile ? 0 : undefined}
      closeOnClickOutside={phase !== 'generating'}
      closeOnEscape={phase !== 'generating'}
      withCloseButton={phase !== 'generating'}
    >
      <Stack gap="md">
        {phase === 'generating' ? (
          <Stack gap="sm">
            <Text c={BRAND.textPrimary} fw={600}>
              Gerando o rascunho do modelo...
            </Text>
            <Progress value={progressValue} animated color="brand" />
            <Text size="sm" c={BRAND.textMuted}>
              {elapsedSeconds}s decorridos. A geração costuma levar algumas dezenas de segundos: o backend lê as
              fontes de RAG, consulta o modelo de linguagem e roda a validação determinística antes de devolver o
              rascunho. Nenhum modelo é criado nesta etapa.
            </Text>
            <Text size="xs" c={BRAND.textMuted}>
              Não feche esta janela: ela é quem abre o rascunho para edição quando a geração terminar.
            </Text>
          </Stack>
        ) : (
          <>
            <Text size="sm" c={BRAND.textMuted}>
              O resultado é um <b>rascunho não salvo</b>: ele abre no builder pré-preenchido, para você revisar e
              editar. Nada é gravado até você clicar em &quot;Salvar modelo&quot;.
            </Text>

            <MultiSelect
              label="Fontes de metodologia"
              description="Pastas de rag/markdown/TIPOS_TREINO que entram no contexto do modelo — divisões de treino (Full Body, ABC) e metodologias de volume."
              placeholder={ragOptions.length ? 'Selecione uma ou mais fontes' : 'Nenhuma fonte disponível'}
              data={ragOptions}
              value={form.ragSources}
              onChange={(value) => patch({ ragSources: value })}
              searchable
              clearable
              disabled={ragFoldersQuery.isLoading}
            />
            {ragFoldersQuery.isError && (
              <Alert color="yellow" icon={<IconAlertTriangle size={16} />}>
                {errorMessage(ragFoldersQuery.error, 'Não foi possível carregar as fontes de RAG.')} Você pode gerar
                sem fonte selecionada.
              </Alert>
            )}

            <Select
              label="Perfil de Metas de Ativação"
              description="Metas semanais de volume por grupo muscular usadas para projetar a ativação do rascunho. Opcional."
              placeholder="Nenhum"
              data={trainingTypeOptions}
              value={form.trainingTypeId}
              onChange={(value) => patch({ trainingTypeId: value })}
              searchable
              clearable
              disabled={trainingTypesQuery.isLoading}
            />

            <Select
              label="Modo de volume"
              description={PERIODIZATION_VOLUME_MODE_HINT[form.volumeMode]}
              data={VOLUME_MODE_OPTIONS}
              value={form.volumeMode}
              onChange={(value) => patch({ volumeMode: (value as VolumeMode) || 'percent_budget' })}
              allowDeselect={false}
            />

            {form.volumeMode === 'equipment_series' && (
              <Select
                label="Contagem do volume"
                description={countingModeOption(form.countingMode).description}
                data={COUNTING_MODE_SELECT_DATA as { value: string; label: string }[]}
                value={form.countingMode}
                onChange={(value) => patch({ countingMode: normalizeCountingMode(value) })}
                allowDeselect={false}
              />
            )}

            <Select
              label={gymRequired ? 'Academia (obrigatória neste modo)' : 'Academia (opcional)'}
              description={
                gymRequired
                  ? 'O rascunho só pode prescrever equipamentos ativos nesta academia.'
                  : 'Informe para o modelo considerar o catálogo da academia; sem ela o rascunho fica só em grupos musculares.'
              }
              placeholder={gymRequired ? 'Selecione a academia' : 'Nenhuma'}
              data={gymOptions}
              value={form.gymId}
              onChange={(value) => patch({ gymId: value })}
              searchable
              clearable={!gymRequired}
              withAsterisk={gymRequired}
              disabled={gymsQuery.isLoading}
            />

            <NumberInput
              label="Dias (slots) por ciclo"
              description={`Entre ${PERIODIZATION_DAYS_MIN} e ${PERIODIZATION_DAYS_MAX}.`}
              min={PERIODIZATION_DAYS_MIN}
              max={PERIODIZATION_DAYS_MAX}
              clampBehavior="strict"
              allowDecimal={false}
              value={form.daysPerCycle}
              onChange={(value) => patch({ daysPerCycle: typeof value === 'number' ? value : form.daysPerCycle })}
            />

            <Select
              label="Objetivo do modelo"
              description={trainingGoalDescription(goalOption(trainingGoals, form.trainingGoal))}
              data={goalSelectData(trainingGoals)}
              value={goalValue(trainingGoals, form.trainingGoal)}
              onChange={(value) => value && patch({ trainingGoal: value })}
              allowDeselect={false}
              disabled={!trainingGoals}
              placeholder={trainingGoalsQuery.isError ? 'Não foi possível carregar os objetivos' : undefined}
            />

            {trainingGoals && (
              <Stack gap={6}>
                <Text size="sm" fw={500} c={BRAND.textPrimary}>
                  Objetivo por dia
                </Text>
                <Text size="xs" c={BRAND.textMuted}>
                  Para um modelo híbrido, escolha o objetivo de cada dia. O validador aplica o objetivo do dia item a
                  item (os isoladores podem ficar em faixa moderada) e registra cada ajuste no rascunho.
                </Text>
                <Group gap="xs" wrap="wrap">
                  {periodizationDayGoals(form).map((dayGoal, index) => (
                    <Select
                      key={`day-goal-${index}`}
                      size="xs"
                      w={isMobile ? '100%' : 180}
                      label={`Dia ${index + 1}`}
                      data={itemGoalSelectData(trainingGoals, form.trainingGoal)}
                      value={itemGoalSelectValue(trainingGoals, dayGoal)}
                      onChange={(value) => setDayGoal(index, itemGoalFromSelect(trainingGoals, value))}
                      allowDeselect={false}
                    />
                  ))}
                </Group>
              </Stack>
            )}

            <Select
              label="Cardio no Modelo"
              description={
                PERIODIZATION_CARDIO_OPTIONS.find((option) => option.value === form.cardio)?.description
              }
              data={PERIODIZATION_CARDIO_OPTIONS.map(({ value, label }) => ({ value, label }))}
              value={form.cardio}
              onChange={(value) => value && patch({ cardio: value as PeriodizationCardioChoice })}
              allowDeselect={false}
            />

            <Textarea
              label="Instruções livres"
              description="Entram no prompt como dado, nunca como instrução ao modelo. Ex.: ênfase em costas, joelho sensível."
              placeholder="Opcional"
              autosize
              minRows={3}
              maxLength={PERIODIZATION_INSTRUCTIONS_MAX_LENGTH}
              value={form.instructions}
              onChange={(event) => patch({ instructions: event.currentTarget.value })}
            />
            <Text
              size="xs"
              ta="right"
              c={instructionsLength >= PERIODIZATION_INSTRUCTIONS_MAX_LENGTH ? 'orange' : BRAND.textMuted}
            >
              {instructionsLength} / {PERIODIZATION_INSTRUCTIONS_MAX_LENGTH} caracteres
            </Text>

            {formError && (
              <Alert color="red" icon={<IconAlertTriangle size={16} />}>
                {formError}
              </Alert>
            )}

            {phase === 'failed' && failure && (
              <Alert color="red" icon={<IconAlertTriangle size={16} />} title="A geração não foi concluída">
                <Stack gap="xs">
                  <Text size="sm" c={BRAND.textPrimary}>
                    {failure.message}
                  </Text>
                  {countPeriodizationReport(failure.validationReport) > 0 && (
                    <>
                      <Divider />
                      <Text size="xs" fw={600} c={BRAND.textPrimary}>
                        O validador determinístico recusou o rascunho por:
                      </Text>
                      <Stack gap={4}>
                        {(failure.validationReport?.errors || []).map((entry, index) => (
                          <Group key={`err-${index}`} gap="xs" align="flex-start" wrap="nowrap">
                            <Badge size="sm" color="red" variant="light">
                              {entry.code}
                            </Badge>
                            <Text size="xs" c={BRAND.textPrimary}>
                              {describePeriodizationEntry(entry)}
                            </Text>
                          </Group>
                        ))}
                      </Stack>
                    </>
                  )}
                  <Text size="xs" c={BRAND.textMuted}>
                    Nenhum modelo de treino foi criado ou alterado
                    {(failure.generationLogId ?? generationLogId) != null
                      ? ` (geração #${failure.generationLogId ?? generationLogId} registrada na auditoria).`
                      : '.'}
                  </Text>
                </Stack>
              </Alert>
            )}

            <Group justify="flex-end">
              <Button variant="default" onClick={handleClose}>
                Cancelar
              </Button>
              <Button
                color="brand"
                leftSection={<IconSparkles size={16} />}
                onClick={handleGenerate}
                loading={generateMutation.isPending}
              >
                {phase === 'failed' ? 'Tentar novamente' : 'Gerar rascunho'}
              </Button>
            </Group>
          </>
        )}
      </Stack>
    </Modal>
  );
}
