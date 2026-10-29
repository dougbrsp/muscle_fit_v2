'use client';

import { IconCheck } from '@tabler/icons-react';
import { useState } from 'react';
import { formatWeight, fromKg, toKg, type WeightUnit } from '@/lib/weight-units';
import {
  hasUnsavedRecords,
  intensiveRecordStatus,
  pendingRepsRecords,
  type IntensiveBlockState,
  type IntensiveProfile,
  type IntensiveRecordStatus,
  type IntensiveSetRecord,
} from '@/lib/workout-mode-intensive';
import { IntensiveSyncNotice } from './IntensiveRest';
import { parseRepsInput } from './IntensiveSetEntry';
import { WorkoutActionBar } from './ui/WorkoutActionBar';
import styles from './intensive.module.css';

export const INTENSIVE_STATUS_LABEL: Record<IntensiveRecordStatus, string> = {
  done: 'Concluída',
  pending_reps: 'Pendente',
  saving: 'Salvando…',
  error: 'Não salva',
};

const STATUS_CLASS: Record<IntensiveRecordStatus, string> = {
  done: styles.statusDone,
  pending_reps: styles.statusPending,
  saving: styles.statusSaving,
  error: styles.statusError,
};

export type IntensiveSetCorrection = { reps?: number; weightKg?: number };

type Props = {
  state: IntensiveBlockState;
  profile: IntensiveProfile;
  weightUnit: WeightUnit;
  loadSuffix?: string | null;
  onCorrect: (record: IntensiveSetRecord, correction: IntensiveSetCorrection) => Promise<void>;
  onRetry: (clientKey: string) => void;
  onFinish: () => void;
};

function RowEditor({
  record,
  weightUnit,
  onSave,
  onCancel,
}: {
  record: IntensiveSetRecord;
  weightUnit: WeightUnit;
  onSave: (correction: IntensiveSetCorrection) => Promise<void>;
  onCancel: () => void;
}) {
  const [reps, setReps] = useState(record.reps != null ? String(record.reps) : '');
  const [weight, setWeight] = useState(record.weightKg != null ? String(fromKg(record.weightKg, weightUnit)) : '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    const correction: IntensiveSetCorrection = {};
    const repsValue = parseRepsInput(reps);
    if (repsValue != null && repsValue !== record.reps) correction.reps = repsValue;
    const weightValue = Number(weight.replace(',', '.'));
    if (weight.trim() !== '' && Number.isFinite(weightValue) && weightValue >= 0) {
      const kg = toKg(weightValue, weightUnit);
      if (kg !== record.weightKg) correction.weightKg = kg;
    }
    if (correction.reps == null && correction.weightKg == null) {
      onCancel();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onSave(correction);
      onCancel();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Não foi possível corrigir a série.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.editPanel} data-testid="intensive-review-editor">
      <div className={styles.inlineRow}>
        <input
          className={styles.smallInput}
          type="number"
          inputMode="numeric"
          min={1}
          placeholder="reps"
          aria-label={`Repetições da série ${record.index}`}
          value={reps}
          onChange={(event) => setReps(event.target.value)}
        />
        <input
          className={styles.smallInput}
          type="number"
          inputMode="decimal"
          min={0}
          aria-label={`Carga da série ${record.index} (${weightUnit})`}
          value={weight}
          onChange={(event) => setWeight(event.target.value)}
        />
        <span className={styles.muted}>{weightUnit}</span>
      </div>
      <div className={styles.inlineRow}>
        <button type="button" className={styles.editButton} disabled={busy} onClick={save}>
          {busy ? 'Salvando…' : 'Salvar'}
        </button>
        <button type="button" className={styles.linkButton} onClick={onCancel}>
          Cancelar
        </button>
      </div>
      {error && (
        <span className={styles.statusError} role="alert">
          {error}
        </span>
      )}
    </div>
  );
}

/**
 * Revisão do bloco: Série · Reps · Carga · Status (e as reps extras da família
 * além da falha), com correção rápida (PATCH da série) e as pendentes
 * destacadas antes de concluir a técnica.
 */
export function IntensiveReview({ state, profile, weightUnit, loadSuffix = null, onCorrect, onRetry, onFinish }: Props) {
  const [editing, setEditing] = useState<string | null>(null);
  const pending = pendingRepsRecords(state);
  const unsaved = hasUnsavedRecords(state);
  const ctaLabel =
    pending.length > 0
      ? `Concluir ${profile.techniqueName} com ${pending.length} pendente${pending.length > 1 ? 's' : ''}`
      : `Concluir ${profile.techniqueName}`;

  return (
    <>
      <IntensiveSyncNotice records={state.records} onRetry={onRetry} />
      <section className={styles.card} aria-label="Revisão do bloco" data-testid="intensive-review">
        <span className={styles.headline}>Revisão · {profile.techniqueName}</span>
        {pending.length > 0 && (
          <div className={styles.pendingChip} data-testid="intensive-review-pending">
            {pending.length === 1
              ? `A série ${pending[0].index} está sem repetições. Toque em "Corrigir" para confirmar.`
              : `As séries ${pending.map((r) => r.index).join(', ')} estão sem repetições. Toque em "Corrigir" em cada uma para confirmar.`}
          </div>
        )}
        {/* Lista (não tabela): cada série é um item com o botão "Corrigir"
            próprio, sem células dentro de um botão. */}
        <ul className={styles.reviewList} aria-label="Séries do bloco">
          {state.records.map((record) => {
            const status = intensiveRecordStatus(record);
            const load =
              record.weightKg != null
                ? `${formatWeight(record.weightKg, weightUnit)}${loadSuffix ? ` ${loadSuffix}` : ''}`
                : '—';
            return (
              <li
                key={record.clientKey}
                className={`${styles.reviewItem} ${status === 'pending_reps' ? styles.rowPending : ''}`}
                data-testid={`intensive-review-row-${record.index}`}
              >
                <div className={styles.tableRow}>
                  <span className={styles.cell}>Série {record.index}</span>
                  <span className={styles.cell}>
                    {record.reps ?? '—'}
                    {record.reps != null ? ' reps' : ''}
                  </span>
                  <span className={styles.cell}>{load}</span>
                  <span className={`${styles.cell} ${styles.status} ${STATUS_CLASS[status]}`}>{INTENSIVE_STATUS_LABEL[status]}</span>
                </div>
                {record.extraReps ? (
                  <span className={styles.muted} data-testid={`intensive-review-extra-${record.index}`}>
                    + {record.extraReps} {record.extraReps === 1 ? 'rep extra' : 'reps extras'} · {load}
                  </span>
                ) : null}
                <button
                  type="button"
                  className={styles.linkButton}
                  aria-label={`Corrigir série ${record.index}`}
                  aria-expanded={editing === record.clientKey}
                  disabled={status === 'saving'}
                  onClick={() => setEditing(editing === record.clientKey ? null : record.clientKey)}
                >
                  Corrigir
                </button>
                {editing === record.clientKey && (
                  <RowEditor
                    record={record}
                    weightUnit={weightUnit}
                    onSave={(correction) => onCorrect(record, correction)}
                    onCancel={() => setEditing(null)}
                  />
                )}
              </li>
            );
          })}
        </ul>
        {state.records.length === 0 && <span className={styles.muted}>Nenhuma série registrada neste bloco.</span>}
      </section>
      <WorkoutActionBar
        primaryLabel={ctaLabel}
        primaryIcon={<IconCheck size={20} />}
        onPrimary={onFinish}
        primaryDisabled={unsaved}
        primaryTestId="intensive-finish"
        notice={unsaved ? 'Aguarde a gravação de todas as séries (ou toque em "Tentar de novo").' : null}
      />
    </>
  );
}
