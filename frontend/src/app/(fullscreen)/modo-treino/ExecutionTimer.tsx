'use client';

import { Button, Group, Progress, Stack, Text } from '@mantine/core';
import { IconPlayerPause, IconPlayerPlay, IconPlayerStop, IconRotate } from '@tabler/icons-react';
import { useEffect, useRef, useState } from 'react';
import type { ExecutionTimerSpec } from '@/lib/workout-mode-measures';
import { BRAND } from '@/theme';
import { signalTimerFinished } from './timer-feedback';

type Status = 'idle' | 'running' | 'paused' | 'done';

type Props = {
  /** Modo e alvo do cronômetro (`executionTimerSpec`, núcleo do Modo Treino). */
  spec: ExecutionTimerSpec;
  /** Muda a cada série (ex.: `${exercício}-${série}`) para zerar o cronômetro. */
  resetKey: string;
  /**
   * Fim da execução: o countdown chegou a zero (devolve o alvo) ou o usuário
   * tocou "Encerrar" (devolve os segundos reais executados, arredondados).
   */
  onStop: (seconds: number) => void;
};

// Intervalo da tela. Só redesenha: o tempo vem SEMPRE de `Date.now()` contra
// o instante em que a contagem começou (a tela pode apagar e o navegador
// congelar o `setInterval`; ao voltar, o valor certo é recalculado).
const TICK_MS = 250;
const MS_PER_SECOND = 1000;

function formatClock(totalSeconds: number): string {
  const safe = Math.max(0, totalSeconds);
  const minutes = Math.floor(safe / 60);
  const seconds = safe % 60;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

/**
 * Cronômetro de EXECUÇÃO da série (Lote T5): regressivo até o alvo (série por
 * tempo, janela do máximo de reps) ou livre (máximo de reps sem janela).
 * Iniciar / pausar / retomar / encerrar antes; bipe + vibração quando o
 * regressivo chega a zero. O tempo decorrido é `acumulado + (agora − início
 * do trecho atual)`, recalculado a cada tick e ao voltar para a aba.
 */
export function ExecutionTimer({ spec, resetKey, onStop }: Props) {
  const [status, setStatus] = useState<Status>('idle');
  const [elapsedMs, setElapsedMs] = useState(0);
  const accumulatedRef = useRef(0);
  const runningSinceRef = useRef<number | null>(null);
  const statusRef = useRef<Status>('idle');
  const onStopRef = useRef(onStop);
  onStopRef.current = onStop;

  const targetMs = spec.mode === 'countdown' && spec.targetSeconds != null ? spec.targetSeconds * MS_PER_SECOND : null;

  function currentElapsed(): number {
    const since = runningSinceRef.current;
    return accumulatedRef.current + (since != null ? Date.now() - since : 0);
  }

  function setStatusBoth(next: Status) {
    statusRef.current = next;
    setStatus(next);
  }

  function finish(elapsed: number, reachedTarget: boolean) {
    runningSinceRef.current = null;
    accumulatedRef.current = elapsed;
    setElapsedMs(elapsed);
    setStatusBoth('done');
    if (reachedTarget) signalTimerFinished();
    onStopRef.current(Math.round(elapsed / MS_PER_SECOND));
  }

  function tick() {
    if (statusRef.current !== 'running') return;
    const elapsed = currentElapsed();
    if (targetMs != null && elapsed >= targetMs) {
      finish(targetMs, true);
      return;
    }
    setElapsedMs(elapsed);
  }

  // Série nova: cronômetro zerado e parado.
  useEffect(() => {
    accumulatedRef.current = 0;
    runningSinceRef.current = null;
    setElapsedMs(0);
    setStatusBoth('idle');
  }, [resetKey, spec.mode, spec.targetSeconds]);

  useEffect(() => {
    if (status !== 'running') return undefined;
    const interval = setInterval(tick, TICK_MS);
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') tick();
    };
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, targetMs]);

  if (spec.mode === 'none') return null;

  function start() {
    runningSinceRef.current = Date.now();
    setStatusBoth('running');
  }

  function pause() {
    accumulatedRef.current = currentElapsed();
    runningSinceRef.current = null;
    setElapsedMs(accumulatedRef.current);
    setStatusBoth('paused');
  }

  function endEarly() {
    finish(currentElapsed(), false);
  }

  function restart() {
    accumulatedRef.current = 0;
    runningSinceRef.current = Date.now();
    setElapsedMs(0);
    setStatusBoth('running');
  }

  const elapsedSeconds = Math.floor(elapsedMs / MS_PER_SECOND);
  const shownSeconds =
    targetMs != null ? Math.ceil(Math.max(0, targetMs - elapsedMs) / MS_PER_SECOND) : elapsedSeconds;
  const progressValue = targetMs != null && targetMs > 0 ? Math.min(100, (elapsedMs / targetMs) * 100) : null;

  return (
    <Stack gap="xs" align="center" data-testid="execution-timer">
      <Text size="xs" c={BRAND.textMuted} fw={600}>
        {targetMs != null ? 'Tempo restante' : 'Cronômetro'}
      </Text>
      <Text fz={48} fw={800} lh={1} c={status === 'done' ? 'green.6' : BRAND.textPrimary} aria-live="polite">
        {formatClock(shownSeconds)}
      </Text>
      {progressValue != null && <Progress value={progressValue} w="100%" size="md" radius="xl" color="brand" />}
      {status === 'done' && (
        <Text size="xs" c={BRAND.textMuted}>
          Executado: {formatClock(Math.round(elapsedMs / MS_PER_SECOND))}
        </Text>
      )}
      <Group gap="xs" grow w="100%">
        {status === 'idle' && (
          <Button size="lg" h={56} radius="xl" color="brand" leftSection={<IconPlayerPlay size={20} />} onClick={start}>
            Iniciar
          </Button>
        )}
        {status === 'running' && (
          <Button size="lg" h={56} radius="xl" variant="light" leftSection={<IconPlayerPause size={20} />} onClick={pause}>
            Pausar
          </Button>
        )}
        {status === 'paused' && (
          <Button size="lg" h={56} radius="xl" color="brand" leftSection={<IconPlayerPlay size={20} />} onClick={start}>
            Retomar
          </Button>
        )}
        {(status === 'running' || status === 'paused') && (
          <Button
            size="lg"
            h={56}
            radius="xl"
            color="orange"
            variant="light"
            leftSection={<IconPlayerStop size={20} />}
            onClick={endEarly}
          >
            {targetMs != null ? 'Encerrar antes' : 'Encerrar'}
          </Button>
        )}
        {status === 'done' && (
          <Button size="md" radius="xl" variant="subtle" leftSection={<IconRotate size={18} />} onClick={restart}>
            Refazer
          </Button>
        )}
      </Group>
    </Stack>
  );
}
