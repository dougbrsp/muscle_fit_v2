'use client';

import { IconArrowsExchange, IconPlayerSkipForward, IconPlayerStop } from '@tabler/icons-react';
import type { ReactNode } from 'react';
import type { WeightUnit } from '@/lib/weight-units';
import {
  intensiveExtraRepsEnabled,
  type IntensiveBlockState,
  type IntensiveEvent,
  type IntensiveProfile,
  type IntensiveSetRecord,
} from '@/lib/workout-mode-intensive';
import { IntensiveBriefing } from './IntensiveBriefing';
import { IntensiveRest, IntensiveSyncNotice } from './IntensiveRest';
import { IntensiveReview, type IntensiveSetCorrection } from './IntensiveReview';
import { IntensiveSetEntry } from './IntensiveSetEntry';
import { QuickLoadAdjustment } from './QuickLoadAdjustment';
import type { WorkoutIconAction } from './ui/IconAction';
import styles from './intensive.module.css';

export type IntensiveBlockProps = {
  state: IntensiveBlockState;
  profile: IntensiveProfile;
  exerciseName: string;
  equipmentName: string | null;
  repsTarget: string | null;
  weightUnit: WeightUnit;
  loadIncrementKg: number | null | undefined;
  loadSuffix?: string | null;
  /** Linha "Próximo exercício" antes do rodapé da série. */
  nextExerciseLine?: ReactNode;
  dispatch: (event: IntensiveEvent) => void;
  onCompleteSet: (reps: number | null, rpe: number | null, extraReps: number | null) => void;
  onRetry: (clientKey: string) => void;
  onCorrect: (record: IntensiveSetRecord, correction: IntensiveSetCorrection) => Promise<void>;
  onFinish: () => void;
  /** Trocar exercício/equipamento (fluxo de sempre); ausente = sem a ação. */
  onSwap?: () => void;
  onSkipExercise: () => void;
};

/**
 * Orquestra o bloco do Modo Intensivo pela fase da máquina de estados
 * (`workout-mode-intensive.ts`): briefing, série, descanso/próxima pronta e
 * revisão. Não grava nada: a tela dona (`PlanExecutionScreen`) faz o POST/
 * PATCH e despacha os eventos de gravação.
 */
export function IntensiveBlock({
  state,
  profile,
  exerciseName,
  equipmentName,
  repsTarget,
  weightUnit,
  loadIncrementKg,
  loadSuffix = null,
  nextExerciseLine = null,
  dispatch,
  onCompleteSet,
  onRetry,
  onCorrect,
  onFinish,
  onSwap,
  onSkipExercise,
}: IntensiveBlockProps) {
  const swapAction: WorkoutIconAction[] = onSwap
    ? [
        {
          key: 'swap',
          label: 'Trocar exercício',
          caption: 'Trocar',
          icon: <IconArrowsExchange size={20} />,
          onClick: onSwap,
          testId: 'intensive-swap',
        },
      ]
    : [];
  const skipAction: WorkoutIconAction = {
    key: 'skip',
    label: 'Pular exercício',
    caption: 'Pular',
    icon: <IconPlayerSkipForward size={20} />,
    onClick: onSkipExercise,
    testId: 'intensive-skip-exercise',
  };
  const endAction: WorkoutIconAction = {
    key: 'end',
    label: 'Encerrar técnica',
    caption: 'Encerrar',
    icon: <IconPlayerStop size={20} />,
    onClick: () => dispatch({ type: 'END_TECHNIQUE' }),
    testId: 'intensive-end-technique',
  };

  const adjust = profile.allowsFatigueDrop ? (
    <QuickLoadAdjustment
      plannedKg={state.planned.kg}
      weightUnit={weightUnit}
      loadIncrementKg={loadIncrementKg}
      loadSuffix={loadSuffix}
      canUndo={state.undo.length > 0}
      onAdjust={(kg, source) => dispatch({ type: 'ADJUST_LOAD', kg, source })}
      onUndo={() => dispatch({ type: 'UNDO_ADJUST' })}
    />
  ) : null;

  async function confirmReps(record: IntensiveSetRecord, reps: number) {
    await onCorrect(record, { reps });
  }

  switch (state.phase) {
    case 'BRIEFING':
      return (
        <IntensiveBriefing
          profile={profile}
          exerciseName={exerciseName}
          equipmentName={equipmentName}
          plannedKg={state.planned.kg}
          weightUnit={weightUnit}
          loadSuffix={loadSuffix}
          repsTarget={repsTarget}
          iconActions={[...swapAction, skipAction]}
          adjustSlot={adjust}
          onStart={() => dispatch({ type: 'START' })}
        />
      );
    case 'SERIE_EM_EXECUCAO':
      return (
        <>
          <IntensiveSyncNotice records={state.records} onRetry={onRetry} />
          <IntensiveSetEntry
            // Série nova = stepper limpo (reps nunca herdadas da série anterior).
            key={state.currentSet}
            profile={profile}
            setIndex={state.currentSet}
            totalSets={state.totalSets}
            plannedKg={state.planned.kg}
            weightUnit={weightUnit}
            loadSuffix={loadSuffix}
            repsTarget={repsTarget}
            adjustSlot={adjust}
            beforeActions={nextExerciseLine}
            iconActions={[...swapAction, skipAction, endAction]}
            extraRepsEnabled={intensiveExtraRepsEnabled(profile, state.currentSet, state.totalSets)}
            onComplete={onCompleteSet}
            onSkipSet={() => dispatch({ type: 'SKIP_SET' })}
          />
        </>
      );
    case 'DESCANSO':
    case 'PROXIMA_SERIE_PRONTA':
      return (
        <IntensiveRest
          state={state}
          profile={profile}
          weightUnit={weightUnit}
          adjustSlot={adjust}
          iconActions={[...swapAction, endAction]}
          onRestAdjusted={(endsAt, totalSeconds) => dispatch({ type: 'REST_ADJUSTED', endsAt, totalSeconds })}
          onRestExpired={() => dispatch({ type: 'REST_EXPIRED' })}
          onContinue={() => dispatch({ type: state.phase === 'DESCANSO' ? 'SKIP_REST' : 'START_NEXT' })}
          onRetry={onRetry}
          onConfirmReps={confirmReps}
        />
      );
    case 'REGISTRO_PENDENTE':
      return (
        <section className={styles.card} aria-label="Gravando o bloco" data-testid="intensive-registering">
          <span className={styles.headline}>{profile.techniqueName}</span>
          <span className={styles.info}>Confirmando as séries com o servidor antes da revisão…</span>
          <IntensiveSyncNotice records={state.records} onRetry={onRetry} />
        </section>
      );
    case 'REVISAO_FINAL':
      return (
        <IntensiveReview
          state={state}
          profile={profile}
          weightUnit={weightUnit}
          loadSuffix={loadSuffix}
          onCorrect={onCorrect}
          onRetry={onRetry}
          onFinish={onFinish}
        />
      );
    default:
      return null;
  }
}
