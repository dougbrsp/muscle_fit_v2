'use client';

import { ActionIcon, Button, Card, Group, Progress, Stack, Text } from '@mantine/core';
import { IconHourglass, IconMinus, IconPlayerSkipForward, IconPlus } from '@tabler/icons-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { DEFAULT_WEIGHT_UNIT, formatWeight, type WeightUnit } from '@/lib/weight-units';
import type { NextSetPreview } from '@/lib/workout-mode-next-set';
import {
  REST_ADJUST_STEP_SECONDS,
  createRestCountdown,
  formatRestClock,
  remainingRestSeconds,
  resolveRestStart,
  restClockAriaLabel,
  restElapsedPercent,
  restPrimaryLabel,
  type RestCountdown,
  type RestCountdownSnapshot,
  type StoredRestTimer,
} from '@/lib/workout-mode-rest-timer';
import { BRAND } from '@/theme';
import { signalTimerFinished } from './timer-feedback';
import type { WorkoutIconAction } from './ui/IconAction';
import { WorkoutActionBar } from './ui/WorkoutActionBar';
import styles from './workout-mode.module.css';

type CountdownInput = {
  // Identidade do descanso: muda a cada série/etapa que abre um descanso, e
  // reinicia a contagem.
  restKey: string;
  initialSeconds: number;
  // Descanso persistido na sessão local (lido só quando `restKey` muda): se
  // for o MESMO descanso, o relógio continua de onde estava (reload).
  getStored?: () => Pick<StoredRestTimer, 'rest_key' | 'rest_ends_at' | 'total_seconds'> | null;
  // Zerar encerra o descanso? (entre séries sim; transição de exercício não).
  autoAdvanceOnZero?: boolean;
  onFinish: (skipped: boolean) => void;
  // Fim recalculado (início e ±15 s), para persistir.
  onEndsAtChange?: (endsAt: number, totalSeconds: number) => void;
  // O relógio zerou (inclusive descanso restaurado já vencido). Só avisa:
  // quem decide avançar é `autoAdvanceOnZero`/`onFinish`.
  onExpired?: () => void;
};

/**
 * Estado do relógio do descanso, vivo SÓ no componente do timer: o tick de
 * 1 s re-renderiza o cronômetro, nunca a tela inteira. Bipe/vibração do fim
 * em `timer-feedback.ts`; recálculo no `visibilitychange` (tela bloqueada,
 * troca de app). Transição única e sem timer zumbi: `createRestCountdown`.
 */
function useRestCountdown({
  restKey,
  initialSeconds,
  getStored,
  autoAdvanceOnZero = true,
  onFinish,
  onEndsAtChange,
  onExpired,
}: CountdownInput) {
  const onExpiredRef = useRef(onExpired);
  onExpiredRef.current = onExpired;
  const onFinishRef = useRef(onFinish);
  onFinishRef.current = onFinish;
  const onEndsAtChangeRef = useRef(onEndsAtChange);
  onEndsAtChangeRef.current = onEndsAtChange;
  const autoAdvanceRef = useRef(autoAdvanceOnZero);
  autoAdvanceRef.current = autoAdvanceOnZero;
  const getStoredRef = useRef(getStored);
  getStoredRef.current = getStored;
  const countdownRef = useRef<RestCountdown | null>(null);

  const [snapshot, setSnapshot] = useState<RestCountdownSnapshot>(() => {
    const now = Date.now();
    const start = resolveRestStart({ restKey, initialSeconds, stored: getStored?.() ?? null, now });
    const remaining = remainingRestSeconds(start.endsAt, now);
    return { remaining, total: start.totalSeconds, expired: remaining <= 0 };
  });

  useEffect(() => {
    const now = Date.now();
    const start = resolveRestStart({ restKey, initialSeconds, stored: getStoredRef.current?.() ?? null, now });
    // Descanso restaurado JÁ vencido (reload/retomada depois do fim): a
    // transição de fim acontece uma vez, mas sem bipe/vibração surpresa na
    // abertura da tela — o aviso era para o momento em que zerou.
    const restoredExpired = start.restored && start.endsAt <= now;
    const countdown = createRestCountdown({
      endsAt: start.endsAt,
      totalSeconds: start.totalSeconds,
      autoAdvanceOnZero: () => autoAdvanceRef.current,
      onChange: setSnapshot,
      onZero: () => {
        if (!restoredExpired) signalTimerFinished();
        onExpiredRef.current?.();
      },
      onFinish: (skipped) => onFinishRef.current(skipped),
      onEndsAtChange: (endsAt, total) => onEndsAtChangeRef.current?.(endsAt, total),
    });
    countdownRef.current = countdown;
    onEndsAtChangeRef.current?.(start.endsAt, start.totalSeconds);
    countdown.start();
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') countdown.recalc();
    };
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => {
      countdown.dispose();
      if (countdownRef.current === countdown) countdownRef.current = null;
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
    // `initialSeconds` entra só no início de cada descanso (como antes).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restKey]);

  return {
    ...snapshot,
    adjust: (deltaSeconds: number) => countdownRef.current?.adjust(deltaSeconds),
    finish: () => countdownRef.current?.finish() ?? false,
  };
}

type HeroProps = Omit<CountdownInput, 'autoAdvanceOnZero'> & {
  // Transição de exercício (última série salva): zerar não avança; o CTA
  // vira "Continuar" quando o relógio zera.
  isExerciseTransition: boolean;
  hasNextExercise: boolean;
  // M22 — próxima série deste exercício, PRONTA de `resolveNextSetPreview`
  // (a mesma carga que o card de registro vai oferecer). `null` na transição.
  nextSet?: NextSetPreview | null;
  weightUnit?: WeightUnit;
  // Linha curta no lugar de "Próxima" na transição ("Exercício concluído").
  transitionNote?: string | null;
  // Ações de ícone do rodapé, ao lado do CTA (ex.: "Trocar"), só as válidas.
  iconActions?: WorkoutIconAction[];
  // Aviso junto do CTA (ex.: erro ao encerrar o treino).
  notice?: ReactNode;
  // Conteúdo entre o hero e o rodapé (série salva, recomendação, próximo
  // exercício, mapa). Elementos criados por quem chama: o tick do relógio
  // não os re-renderiza (mesma referência de elemento).
  children?: ReactNode;
  // Modo Intensivo: zerar NÃO avança; o hero mostra `note` ("Pronto para a
  // próxima série") e o CTA vira `ctaLabel` ("Iniciar próxima série").
  readyOnZero?: { note: string; ctaLabel: string } | null;
};

/**
 * Tela de descanso (novo visual, PR3/3; compacta no PR4): relógio `MM:SS`
 * de ~60 px entre −15 s / +15 s, barra de tempo e a próxima série com a
 * carga (a ÚNICA vez que ela aparece na tela); o conteúdo do descanso no
 * meio; e o rodapé no fim do fluxo com "Pular descanso" (+ "Trocar").
 */
export function RestTimerHero({
  isExerciseTransition,
  hasNextExercise,
  nextSet,
  weightUnit = DEFAULT_WEIGHT_UNIT,
  transitionNote,
  iconActions = [],
  notice,
  children,
  readyOnZero = null,
  ...countdownInput
}: HeroProps) {
  const { remaining, total, expired, adjust, finish } = useRestCountdown({
    ...countdownInput,
    autoAdvanceOnZero: !isExerciseTransition && readyOnZero == null,
  });
  const percent = restElapsedPercent(total, remaining);

  return (
    <>
      <section className={styles.restHero} aria-label="Descanso" data-testid="rest-timer-hero">
        <div className={styles.restClockRow}>
          <button
            type="button"
            className={styles.restAdjustButton}
            aria-label={`Menos ${REST_ADJUST_STEP_SECONDS} segundos`}
            onClick={() => adjust(-REST_ADJUST_STEP_SECONDS)}
            disabled={remaining <= 0}
          >
            <span aria-hidden="true">−{REST_ADJUST_STEP_SECONDS}s</span>
          </button>
          <div className={styles.restClockBox}>
            <span className={styles.eyebrow} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <IconHourglass size={14} color={BRAND.orange} aria-hidden="true" />
              Descanso
            </span>
            <div
              className={styles.restClock}
              role="timer"
              aria-label={restClockAriaLabel(remaining)}
              data-testid="rest-clock"
            >
              {formatRestClock(remaining)}
            </div>
          </div>
          <button
            type="button"
            className={styles.restAdjustButton}
            aria-label={`Mais ${REST_ADJUST_STEP_SECONDS} segundos`}
            onClick={() => adjust(REST_ADJUST_STEP_SECONDS)}
          >
            <span aria-hidden="true">+{REST_ADJUST_STEP_SECONDS}s</span>
          </button>
        </div>
        <div
          className={styles.restTrack}
          role="progressbar"
          aria-label="Tempo de descanso decorrido"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(percent)}
        >
          <div className={styles.restFill} style={{ width: `${percent}%` }} />
        </div>

        {readyOnZero && expired && (
          <div className={styles.restNext} data-testid="rest-ready">
            <strong>{readyOnZero.note}</strong>
          </div>
        )}

        {/* A carga da próxima série aparece UMA vez na tela, aqui (é a que o
            card de registro vai oferecer); o card do motor só diz a ação. */}
        {nextSet ? (
          <div className={styles.restNext} data-testid="rest-next-set">
            <span>
              Próxima: <strong>{nextSet.label}</strong>
            </span>
            {/* Série por tempo/máximo de reps: sem sugestão de carga (Lote T5). */}
            {nextSet.weightKg != null && (
              <span className={styles.restNextLoad} data-testid="rest-next-load">
                {formatWeight(nextSet.weightKg, weightUnit)}
              </span>
            )}
          </div>
        ) : (
          transitionNote && (
            <div className={styles.restNext}>
              <span>{transitionNote}</span>
            </div>
          )
        )}

        {/* Anúncio discreto só na mudança de estado, nunca a cada segundo. */}
        <span className={styles.srOnly} aria-live="polite">
          {expired ? 'Descanso concluído' : ''}
        </span>
      </section>

      {children}

      <WorkoutActionBar
        primaryLabel={
          readyOnZero && expired
            ? readyOnZero.ctaLabel
            : restPrimaryLabel({ expired, isExerciseTransition, hasNextExercise })
        }
        primaryIcon={<IconPlayerSkipForward size={20} />}
        onPrimary={() => {
          finish();
        }}
        primaryTestId="rest-primary-cta"
        notice={notice}
        iconActions={iconActions}
      />
    </>
  );
}

type Props = {
  // Muda a cada série salva (ex: `${exerciseId}-${setNumber}`) para forçar o
  // timer a reiniciar a contagem quando uma nova série é lançada.
  resetKey: string;
  initialSeconds: number;
  onFinish: (skipped: boolean) => void;
  // M22 — carga da PRÓXIMA série, para o usuário já montar o aparelho
  // enquanto descansa. Vem PRONTA de `resolveNextSetPreview` (a mesma carga
  // que o card de registro vai oferecer); este componente só exibe. `null` =
  // não há próxima série deste exercício.
  nextSet?: NextSetPreview | null;
  // Unidade de exibição (M3). O valor de `nextSet.weightKg` é sempre kg.
  weightUnit?: WeightUnit;
};

/**
 * Cronômetro de descanso do treino LIVRE (visual de antes do PR3). Mesmo
 * relógio do `RestTimerHero` (`useRestCountdown`), sem persistência.
 */
export function RestTimer({ resetKey, initialSeconds, onFinish, nextSet, weightUnit = DEFAULT_WEIGHT_UNIT }: Props) {
  const { remaining, total, adjust, finish } = useRestCountdown({ restKey: resetKey, initialSeconds, onFinish });
  const progressValue = restElapsedPercent(total, remaining);

  return (
    <Card withBorder radius="xl" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Stack gap="sm" align="center">
        <Text size="sm" c={BRAND.textMuted} fw={600}>
          Descanso
        </Text>
        <Text fz={56} fw={800} lh={1} c={BRAND.textPrimary}>
          {formatRestClock(remaining)}
        </Text>
        <Progress value={progressValue} w="100%" size="lg" radius="xl" color="brand" />
        {nextSet && (
          <Stack gap={2} align="center">
            <Text size="xs" c={BRAND.textMuted} fw={600}>
              Próxima: {nextSet.label}
            </Text>
            {nextSet.weightKg != null && (
              <Text fz={28} fw={800} lh={1.1} c={BRAND.textPrimary}>
                {formatWeight(nextSet.weightKg, weightUnit, { space: false })}
              </Text>
            )}
          </Stack>
        )}
        <Group gap="xs">
          <ActionIcon
            size={44}
            radius="xl"
            variant="light"
            color="brand"
            aria-label="Menos 15 segundos"
            onClick={() => adjust(-REST_ADJUST_STEP_SECONDS)}
          >
            <IconMinus size={20} />
          </ActionIcon>
          <Text size="sm" c={BRAND.textMuted} w={70} ta="center">
            ±15s
          </Text>
          <ActionIcon
            size={44}
            radius="xl"
            variant="light"
            color="brand"
            aria-label="Mais 15 segundos"
            onClick={() => adjust(REST_ADJUST_STEP_SECONDS)}
          >
            <IconPlus size={20} />
          </ActionIcon>
        </Group>
        <Button
          size="md"
          variant="default"
          radius="xl"
          fullWidth
          leftSection={<IconPlayerSkipForward size={18} />}
          onClick={() => finish()}
        >
          Pular descanso
        </Button>
      </Stack>
    </Card>
  );
}
