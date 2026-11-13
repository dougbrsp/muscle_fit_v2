'use client';

import { Alert, Button, Group, Loader, Modal, Stack, Text } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconSparkles } from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';
import { RecoverableError } from '@/components/RecoverableError';
import { apiFetch } from '@/lib/api';
import { useOfflineUser } from '@/lib/offline/use-offline-user';
import type { PlanCardioResponse } from '@/lib/analytics-types';
import { type StoredWorkoutModeSession, type WorkoutAiSummaryResult } from '@/lib/workout-mode-types';
import { useIaControls } from '@/lib/screen-permissions';
import { fetchWorkoutSummary } from '@/lib/workout-mode-ai';
import {
  clearParkedSession,
  clearStoredSession,
  loadPerSetAiPreference,
  loadStoredSession,
  parkStoredSession,
  saveStoredSession,
  savePerSetAiPreference,
} from '@/lib/workout-mode-storage';
import { BRAND } from '@/theme';
import { CardioAfterStrength } from './CardioAfterStrength';
import { FreeformScreen } from './FreeformScreen';
import { ModeSelectScreen } from './ModeSelectScreen';
import { PlanExecutionScreen } from './PlanExecutionScreen';
import { StorageStatusNotice } from './StorageStatusNotice';
import { SyncIndicator } from './SyncIndicator';
import { WakeLockNotice } from './WakeLockNotice';
import { WorkoutModeShell } from './WorkoutModeShell';
import { preloadMuscleBodyMap } from '@/components/MuscleBodyMapLazy';

type CompletionState = {
  open: boolean;
  loading: boolean;
  error: boolean;
  summary: string | null;
  // Ficha da sessão encerrada (M98 C3b): o modal mostra o cardio do slot.
  workoutPlanId: number | null;
};

const initialCompletionState: CompletionState = {
  open: false,
  loading: false,
  error: false,
  summary: null,
  workoutPlanId: null,
};

/**
 * Orquestrador da tela `/modo-treino` — shell full-screen imersivo (SEM
 * `AppShellLayout`, ver `src/app/(fullscreen)/layout.tsx`) para execução
 * guiada de treino no celular. Decide entre `ModeSelectScreen` (nenhuma
 * sessão local ativa) e a tela de execução correspondente ao `source` da
 * sessão (`PlanExecutionScreen`/`FreeformScreen`, ambas placeholders nesta
 * etapa — ver Parte B).
 *
 * Contrato entre este componente e as telas de execução (estável para a
 * Parte B substituir os placeholders sem tocar aqui):
 * - `PlanExecutionScreen({ session, perSetAiEnabled, onFinish })`
 * - `FreeformScreen({ sessionId, gymId, perSetAiEnabled, onFinish })`
 * - `onFinish(sessionId: number)` é chamado quando a tela de execução
 *   confirma que `POST /workout-sessions/{id}/finish` teve sucesso — este
 *   orquestrador cuida do resto (limpar sessão local, gerar+persistir o
 *   resumo de IA do treino via `POST /activities/{id}/ai-summary/generate`,
 *   mostrar o modal de conclusão e navegar de volta).
 */
export function WorkoutModeClient() {
  // COD-14: o mapa corporal da execução vem sob demanda; começa a baixar já na montagem.
  useEffect(preloadMuscleBodyMap, []);
  const router = useRouter();
  const queryClient = useQueryClient();
  const ia = useIaControls();
  const [session, setSession] = useState<StoredWorkoutModeSession | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const [exitOpen, { open: openExit, close: closeExit }] = useDisclosure(false);
  const [perSetAiEnabled, setPerSetAiEnabled] = useState(false);
  const [completion, setCompletion] = useState<CompletionState>(initialCompletionState);
  // Número da conclusão em curso; sair do modal o invalida, para o resumo que
  // chegar depois não atualizar estado de um modal já fechado.
  const completionRunRef = useRef(0);
  // Vaga na barra do topo onde a execução desenha a linha de progresso.
  const [headerSlot, setHeaderSlot] = useState<HTMLDivElement | null>(null);

  // Partição offline do usuário (IndexedDB) hidratada ANTES de ler a sessão local.
  const offlineUser = useOfflineUser();

  useEffect(() => {
    if (offlineUser.state !== 'ready') return;
    setSession(loadStoredSession());
    setPerSetAiEnabled(loadPerSetAiPreference());
    setHydrated(true);
  }, [offlineUser.state, offlineUser.userId]);

  function handleSessionStarted(next: StoredWorkoutModeSession) {
    // A retomada já mesclou o snapshot guardado ao sair (ver
    // `ModeSelectScreen.handleResume`); iniciar outra sessão o torna obsoleto.
    clearParkedSession();
    saveStoredSession(next);
    setSession(next);
  }

  function handlePerSetAiToggle(enabled: boolean) {
    setPerSetAiEnabled(enabled);
    savePerSetAiPreference(enabled);
  }

  // Disparado quando `POST /workout-sessions/{id}/finish` responde com
  // sucesso (ver `PlanExecutionScreen`/`FreeformScreen`). Encerra a sessão
  // local imediatamente e, em seguida, gera E JÁ PERSISTE o resumo de IA do
  // treino inteiro (`POST /activities/{id}/ai-summary/generate`) — não
  // bloqueante: um erro aqui não impede o usuário de fechar o modal e voltar
  // à lista (o treino já foi salvo normalmente de qualquer forma).
  //
  // M98 C3b: se o slot da ficha tiver cardio prescrito, o mesmo modal mostra
  // o cardio (só exibição; `CardioAfterStrength`).
  function handleWorkoutFinished(sessionId: number) {
    const workoutPlanId = session?.source === 'plan' ? (session.workout_plan_id ?? null) : null;
    clearStoredSession();
    clearParkedSession();
    setSession(null);
    // Sem ia.modo_treino/editar o resumo de IA nem é pedido (o POST daria 403):
    // o modal mostra só "Treino concluído" e o cardio.
    if (!ia.canModoTreino) {
      setCompletion({ open: true, loading: false, error: false, summary: null, workoutPlanId });
      return;
    }
    setCompletion({ open: true, loading: true, error: false, summary: null, workoutPlanId });
    const run = ++completionRunRef.current;
    fetchWorkoutSummary(sessionId)
      .then((result) => {
        // O resumo já está salvo no servidor: quem saiu do modal e abriu o
        // treino antes de ele ficar pronto (cache de 60s) passa a vê-lo.
        queryClient.invalidateQueries({ queryKey: ['activity-detail', sessionId] });
        if (run !== completionRunRef.current) return;
        setCompletion((prev) => ({ ...prev, loading: false, error: false, summary: result.summary }));
      })
      .catch(() => {
        if (run !== completionRunRef.current) return;
        setCompletion((prev) => ({ ...prev, loading: false, error: true, summary: null }));
      });
  }

  // Cardio do slot da ficha encerrada. Falha = o bloco só não aparece (o
  // treino de força já foi salvo; o resumo segue normal).
  const cardioQuery = useQuery({
    queryKey: ['workout-plan-cardio', completion.workoutPlanId],
    queryFn: () => apiFetch<PlanCardioResponse>(`/workout-plans/${completion.workoutPlanId}/cardio`),
    enabled: completion.open && completion.workoutPlanId != null,
    retry: false,
  });

  function closeCompletionAndReturn() {
    completionRunRef.current += 1;
    setCompletion(initialCompletionState);
    router.push('/registro-exercicios');
  }

  function handleExitConfirmed() {
    closeExit();
    // A sessão continua `in_progress` no backend: guarda o estado local
    // (modo avançado, aquecimento/ajuste, skips, overrides) para "Retomar"
    // voltar exatamente onde parou, em vez de apagá-lo.
    parkStoredSession();
    setSession(null);
    router.push('/registro-exercicios');
  }

  if (offlineUser.state === 'locked') {
    return (
      <Stack align="center" gap="sm" p="lg" data-testid="tab-locked-notice" role="status">
        <Text fw={600}>Treino aberto em outra aba</Text>
        <Text size="sm" c={BRAND.textMuted} ta="center">
          Para não gravar por cima do mesmo treino, só uma aba por vez escreve. Feche a outra aba ou use o botão abaixo
          quando ela for fechada; esta aba continua automaticamente quando a outra sair.
        </Text>
        <Button variant="default" onClick={offlineUser.retry}>
          Tentar de novo
        </Button>
      </Stack>
    );
  }

  if (offlineUser.state === 'error') {
    return (
      <RecoverableError
        title="Não foi possível abrir o treino"
        description="Não conseguimos confirmar sua conta para carregar os dados salvos neste aparelho. Verifique a conexão e tente de novo."
        onRetry={offlineUser.retry}
      />
    );
  }

  if (!hydrated) {
    return null;
  }

  return (
    <WorkoutModeShell
      aiToggle={session ? { checked: perSetAiEnabled, onChange: handlePerSetAiToggle } : null}
      onExit={openExit}
      onHeaderSlot={setHeaderSlot}
      inSession={Boolean(session)}
    >
      <StorageStatusNotice />
      <SyncIndicator />
      {session && <WakeLockNotice />}
      {!session && <ModeSelectScreen onSessionStarted={handleSessionStarted} />}
      {session && session.source === 'plan' && (
        <PlanExecutionScreen
          session={session}
          perSetAiEnabled={perSetAiEnabled}
          onFinish={handleWorkoutFinished}
          headerSlot={headerSlot}
        />
      )}
      {session && session.source === 'freeform' && (
        <FreeformScreen
          sessionId={session.session_id}
          gymId={session.gym_id}
          perSetAiEnabled={perSetAiEnabled}
          onFinish={handleWorkoutFinished}
        />
      )}

      <Modal opened={exitOpen} onClose={closeExit} title="Sair do modo de treino" size="sm" centered>
        <Stack gap="sm">
          <Text size="sm" c={BRAND.textMuted}>
            A sessão de treino continua salva e você pode retomá-la depois pela tela de registro de exercícios. Deseja
            sair mesmo assim?
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={closeExit}>
              Cancelar
            </Button>
            <Button color="red" onClick={handleExitConfirmed}>
              Sair
            </Button>
          </Group>
        </Stack>
      </Modal>

      <Modal
        opened={completion.open}
        onClose={closeCompletionAndReturn}
        title="Treino concluído"
        size="sm"
        centered
        closeOnClickOutside={!completion.loading}
      >
        <Stack gap="sm">
          {completion.loading && (
            <Group gap="xs">
              <Loader size="sm" color="brand" />
              <Text size="sm" c={BRAND.textMuted}>
                Gerando resumo do treino com IA...
              </Text>
            </Group>
          )}
          {!completion.loading && completion.error && (
            <Alert color="gray" variant="light" icon={<IconAlertTriangle size={16} />}>
              <Text size="sm" c={BRAND.textMuted}>
                Não foi possível gerar o resumo de IA deste treino agora. O treino já foi salvo normalmente.
              </Text>
            </Alert>
          )}
          {!completion.loading && !completion.error && completion.summary && (
            <Group align="flex-start" gap="sm" wrap="nowrap">
              <IconSparkles size={20} color={BRAND.orange} style={{ flexShrink: 0, marginTop: 2 }} />
              <Text size="sm" c={BRAND.textPrimary}>
                {completion.summary}
              </Text>
            </Group>
          )}
          <CardioAfterStrength cardio={cardioQuery.data} />
          <Button fullWidth radius="xl" onClick={closeCompletionAndReturn}>
            Voltar aos treinos
          </Button>
        </Stack>
      </Modal>
    </WorkoutModeShell>
  );
}
