'use client';

import { Modal } from '@mantine/core';
import { useState } from 'react';
import { formatWeight, type WeightUnit } from '@/lib/weight-units';
import { pendingRepsKey, type PendingRepsSet } from '@/lib/workout-mode-pending-reps';
import { parseRepsInput } from './IntensiveSetEntry';
import styles from './intensive.module.css';
import { WORKOUT_MODE_CSS_VARS } from './ui/workout-mode-css-vars';

type PanelProps = {
  items: ReadonlyArray<PendingRepsSet>;
  weightUnit: WeightUnit;
  finishing: boolean;
  /** PATCH da série (rejeita com a mensagem a mostrar). */
  onConfirm: (item: PendingRepsSet, reps: number) => Promise<void>;
  onFinish: () => void;
  onCancel: () => void;
  /** Erro do "Finalizar" (visível dentro da confirmação). */
  error?: string | null;
};

/**
 * Lista compacta das séries com repetições pendentes, mostrada ao tocar em
 * "Finalizar treino". Cada uma é confirmada ali (PATCH da série) porque,
 * depois de finalizado, o servidor recusa a correção (409). "Finalizar mesmo
 * assim" segue com as pendentes (elas continuam fora dos motores).
 */
export function PendingRepsFinishPanel({
  items,
  weightUnit,
  finishing,
  onConfirm,
  onFinish,
  onCancel,
  error = null,
}: PanelProps) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const allConfirmed = items.length === 0;

  async function confirm(item: PendingRepsSet) {
    const key = pendingRepsKey(item);
    const reps = parseRepsInput(drafts[key] ?? '');
    if (reps == null || busy) return;
    setBusy(key);
    setErrors((prev) => ({ ...prev, [key]: '' }));
    try {
      await onConfirm(item, reps);
    } catch (error) {
      setErrors((prev) => ({ ...prev, [key]: error instanceof Error ? error.message : 'Não foi possível confirmar.' }));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className={styles.card} data-testid="pending-reps-finish">
      <span className={styles.headline}>
        {allConfirmed ? 'Repetições confirmadas' : `${items.length} série${items.length > 1 ? 's' : ''} com repetições pendentes`}
      </span>
      <span className={styles.info}>
        {allConfirmed
          ? 'Tudo certo para finalizar o treino.'
          : 'Confirme as repetições antes de finalizar: depois do fim do treino a série não pode mais ser corrigida aqui.'}
      </span>
      {items.map((item) => {
        const key = pendingRepsKey(item);
        const draft = drafts[key] ?? '';
        return (
          <div key={key} className={styles.editPanel} data-testid="pending-reps-row">
            <span className={styles.muted}>
              {item.exerciseName} · {item.label}
              {item.weightKg != null ? ` · ${formatWeight(item.weightKg, weightUnit)}` : ''}
            </span>
            <div className={styles.inlineRow}>
              <input
                className={styles.smallInput}
                type="number"
                inputMode="numeric"
                min={1}
                placeholder="reps"
                aria-label={`Repetições de ${item.exerciseName}, ${item.label}`}
                value={draft}
                onChange={(event) => setDrafts((prev) => ({ ...prev, [key]: event.target.value }))}
              />
              <button
                type="button"
                className={styles.editButton}
                disabled={busy != null || parseRepsInput(draft) == null}
                onClick={() => void confirm(item)}
                data-testid="pending-reps-confirm"
              >
                {busy === key ? 'Salvando…' : 'Confirmar'}
              </button>
            </div>
            {errors[key] && (
              <span className={styles.errorBox} role="alert">
                {errors[key]}
              </span>
            )}
          </div>
        );
      })}
      {error && (
        <span className={styles.errorBox} role="alert" data-testid="pending-reps-finish-error">
          {error}
        </span>
      )}
      <div className={styles.inlineRow}>
        <button
          type="button"
          className={styles.editButton}
          disabled={finishing || busy != null}
          onClick={onFinish}
          data-testid={allConfirmed ? 'pending-reps-finish-now' : 'pending-reps-finish-anyway'}
        >
          {finishing ? 'Finalizando…' : allConfirmed ? 'Finalizar treino' : 'Finalizar mesmo assim'}
        </button>
        <button type="button" className={styles.linkButton} onClick={onCancel} disabled={finishing}>
          Voltar ao treino
        </button>
      </div>
    </div>
  );
}

export function PendingRepsFinishModal({ opened, ...panel }: PanelProps & { opened: boolean }) {
  return (
    <Modal
      opened={opened}
      onClose={panel.onCancel}
      title="Finalizar treino"
      centered
      radius="lg"
      closeButtonProps={{ 'aria-label': 'Fechar' }}
    >
      <div style={WORKOUT_MODE_CSS_VARS}>
        <PendingRepsFinishPanel {...panel} />
      </div>
    </Modal>
  );
}
