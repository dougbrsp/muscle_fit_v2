'use client';

import { useState, type ReactNode } from 'react';
import type { WeightUnit } from '@/lib/weight-units';
import type { IntensiveBlockState, IntensiveProfile, IntensiveSetRecord } from '@/lib/workout-mode-intensive';
import { pendingRepsRecords } from '@/lib/workout-mode-intensive';
import { parseRepsInput } from './IntensiveSetEntry';
import { RestTimerHero } from './RestTimer';
import type { WorkoutIconAction } from './ui/IconAction';
import styles from './intensive.module.css';

export const INTENSIVE_READY_NOTE = 'Pronto para a próxima série';
export const INTENSIVE_READY_CTA = 'Iniciar próxima série';

/**
 * Séries que o servidor ainda não confirmou: gravando (discreto) ou com erro
 * ("Tentar de novo" reenvia a MESMA `client_key`). Nunca some em silêncio.
 */
export function IntensiveSyncNotice({
  records,
  onRetry,
}: {
  records: ReadonlyArray<IntensiveSetRecord>;
  onRetry: (clientKey: string) => void;
}) {
  const failed = records.filter((record) => record.sync === 'error');
  const saving = records.filter((record) => record.sync === 'saving');
  if (failed.length === 0 && saving.length === 0) return null;
  return (
    <>
      {failed.map((record) => (
        <div key={record.clientKey} className={styles.errorBox} role="alert" data-testid="intensive-sync-error">
          <span>
            Série {record.index} não foi salva{record.error ? `: ${record.error}` : '.'}
          </span>
          <button type="button" className={styles.editButton} onClick={() => onRetry(record.clientKey)}>
            Tentar de novo
          </button>
        </div>
      ))}
      {saving.length > 0 && (
        <span className={styles.muted} aria-live="polite" data-testid="intensive-sync-saving">
          Salvando série {saving.map((record) => record.index).join(', ')}…
        </span>
      )}
    </>
  );
}

/** Chip "Repetições pendentes": confirma as reps inline (PATCH), sem pausar o cronômetro. */
export function PendingRepsChip({
  records,
  onConfirm,
}: {
  records: ReadonlyArray<IntensiveSetRecord>;
  onConfirm: (record: IntensiveSetRecord, reps: number) => Promise<void>;
}) {
  const pending = records.filter((record) => record.reps == null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (pending.length === 0) return null;

  async function confirm(record: IntensiveSetRecord) {
    const reps = parseRepsInput(drafts[record.clientKey] ?? '');
    if (reps == null) return;
    setBusy(record.clientKey);
    setError(null);
    try {
      await onConfirm(record, reps);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Não foi possível confirmar as repetições.');
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className={styles.pendingChip} data-testid="intensive-pending-chip">
      <span>Repetições pendentes ({pending.length})</span>
      {pending.map((record) => (
        <div key={record.clientKey} className={styles.inlineRow}>
          <span>Série {record.index}</span>
          <input
            className={styles.smallInput}
            type="number"
            inputMode="numeric"
            min={1}
            placeholder="reps"
            aria-label={`Repetições da série ${record.index}`}
            value={drafts[record.clientKey] ?? ''}
            onChange={(event) => setDrafts((prev) => ({ ...prev, [record.clientKey]: event.target.value }))}
          />
          <button
            type="button"
            className={styles.editButton}
            disabled={busy != null || record.sync === 'saving'}
            onClick={() => confirm(record)}
          >
            {busy === record.clientKey ? 'Salvando…' : 'OK'}
          </button>
        </div>
      ))}
      {error && (
        <span className={styles.statusError} role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

type Props = {
  state: IntensiveBlockState;
  profile: IntensiveProfile;
  weightUnit: WeightUnit;
  /** Ajuste rápido (não mexe no cronômetro). */
  adjustSlot?: ReactNode;
  iconActions?: WorkoutIconAction[];
  onRestAdjusted: (endsAt: number, totalSeconds: number) => void;
  onRestExpired: () => void;
  /** Pular o descanso (contando) ou iniciar a próxima série (zerado). */
  onContinue: () => void;
  onRetry: (clientKey: string) => void;
  onConfirmReps: (record: IntensiveSetRecord, reps: number) => Promise<void>;
};

/**
 * Descanso do bloco: o `RestTimerHero` de sempre (fim absoluto `endsAt`,
 * ±15 s, bipe/vibração) sobre o descanso do PRÓPRIO bloco. Zerar não inicia a
 * série: mostra "Pronto para a próxima série" e o CTA "Iniciar próxima série".
 */
export function IntensiveRest({
  state,
  profile,
  weightUnit,
  adjustSlot = null,
  iconActions = [],
  onRestAdjusted,
  onRestExpired,
  onContinue,
  onRetry,
  onConfirmReps,
}: Props) {
  const rest = state.rest;
  if (!rest) return null;
  return (
    <>
      <IntensiveSyncNotice records={state.records} onRetry={onRetry} />
      <RestTimerHero
        restKey={rest.key}
        initialSeconds={rest.totalSeconds}
        getStored={() => ({ rest_key: rest.key, rest_ends_at: rest.endsAt, total_seconds: rest.totalSeconds })}
        onEndsAtChange={onRestAdjusted}
        onExpired={onRestExpired}
        onFinish={() => onContinue()}
        isExerciseTransition={false}
        hasNextExercise
        readyOnZero={{ note: INTENSIVE_READY_NOTE, ctaLabel: INTENSIVE_READY_CTA }}
        nextSet={{ label: `Série ${state.currentSet} de ${state.totalSets}`, weightKg: state.planned.kg }}
        weightUnit={weightUnit}
        iconActions={iconActions}
      >
        <section className={styles.card} aria-label="Durante o descanso" data-testid="intensive-rest-panel">
          {profile.stretchGuidance && (
            <span className={styles.stretch} data-testid="intensive-stretch">
              {profile.stretchGuidance}
            </span>
          )}
          {adjustSlot}
          <PendingRepsChip records={pendingRepsRecords(state)} onConfirm={onConfirmReps} />
        </section>
      </RestTimerHero>
    </>
  );
}
