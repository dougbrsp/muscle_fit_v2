'use client';

import { useState } from 'react';
import { formatWeight, fromKg, toKg, type WeightUnit } from '@/lib/weight-units';
import { quickAdjustOptions, quickAdjustPlannedKg } from '@/lib/workout-mode-intensive';
import type { LoadAdjustSource } from '@/lib/workout-mode-load-suggestions';
import styles from './intensive.module.css';

type Props = {
  /** Carga planejada das próximas séries (kg); null = sem carga (nada a ajustar). */
  plannedKg: number | null;
  weightUnit: WeightUnit;
  /** Passo do equipamento (`load_increment_kg`, via `resolveSessionPairLimits`). */
  loadIncrementKg: number | null | undefined;
  /** "por halter" / "por lado" quando a convenção do par pede. */
  loadSuffix?: string | null;
  canUndo: boolean;
  disabled?: boolean;
  /** Rótulo da carga ajustada (default: a das próximas séries do bloco). */
  label?: string;
  onAdjust: (kg: number, source: LoadAdjustSource) => void;
  onUndo: () => void;
};

/**
 * Ajuste rápido de carga do Modo Intensivo, sem modal: −1 incremento, −5%,
 * −10%, Editar e Desfazer. Muda só a carga PLANEJADA das próximas séries do
 * bloco (o histórico e o cronômetro não mudam). A conta é o espelho do dono
 * (`session_loads.quick_load_adjust`) na unidade exibida.
 */
export function QuickLoadAdjustment({
  plannedKg,
  weightUnit,
  loadIncrementKg,
  loadSuffix = null,
  canUndo,
  disabled = false,
  label = 'Carga das próximas séries',
  onAdjust,
  onUndo,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const options = quickAdjustOptions(weightUnit, loadIncrementKg);
  const noLoad = plannedKg == null;

  function startEdit() {
    setDraft(plannedKg != null ? String(fromKg(plannedKg, weightUnit)) : '');
    setEditing(true);
  }

  function confirmEdit() {
    const value = Number(draft.replace(',', '.'));
    if (draft.trim() === '' || !Number.isFinite(value) || value < 0) return;
    onAdjust(toKg(value, weightUnit), 'manual');
    setEditing(false);
  }

  return (
    <div className={styles.adjust} data-testid="quick-load-adjustment">
      <span className={styles.info}>
        {label}:{' '}
        <strong data-testid="quick-load-planned">
          {plannedKg != null ? formatWeight(plannedKg, weightUnit) : '—'}
          {plannedKg != null && loadSuffix ? ` ${loadSuffix}` : ''}
        </strong>
      </span>
      <div className={styles.adjustButtons}>
        {options.map((option) => (
          <button
            key={option.action}
            type="button"
            className={styles.adjustButton}
            aria-label={option.accessibleLabel}
            disabled={disabled || noLoad}
            data-testid={`quick-adjust-${option.action}`}
            onClick={() => {
              if (plannedKg == null) return;
              onAdjust(
                quickAdjustPlannedKg({ plannedKg, unit: weightUnit, loadIncrementKg, action: option.action }),
                'fatigue',
              );
            }}
          >
            {option.label}
          </button>
        ))}
        <button
          type="button"
          className={`${styles.adjustButton} ${styles.adjustNeutral}`}
          disabled={disabled}
          onClick={startEdit}
          data-testid="quick-adjust-edit"
        >
          Editar
        </button>
        <button
          type="button"
          className={`${styles.adjustButton} ${styles.adjustNeutral}`}
          disabled={disabled || !canUndo}
          onClick={onUndo}
          data-testid="quick-adjust-undo"
        >
          Desfazer
        </button>
      </div>
      {editing && (
        <div className={styles.inlineRow}>
          <input
            className={styles.smallInput}
            type="number"
            inputMode="decimal"
            min={0}
            aria-label={`Carga planejada (${weightUnit})`}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            data-testid="quick-adjust-edit-input"
          />
          <span className={styles.muted}>{weightUnit}</span>
          <button type="button" className={styles.editButton} onClick={confirmEdit}>
            OK
          </button>
          <button type="button" className={styles.linkButton} onClick={() => setEditing(false)}>
            Cancelar
          </button>
        </div>
      )}
    </div>
  );
}
