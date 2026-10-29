'use client';

import { IconCheck } from '@tabler/icons-react';
import { useState, type ReactNode } from 'react';
import { formatWeight, type WeightUnit } from '@/lib/weight-units';
import { parseNumericTarget } from '@/lib/workout-mode-types';
import type { IntensiveProfile } from '@/lib/workout-mode-intensive';
import { intensiveTargetRpeText } from './IntensiveBriefing';
import type { WorkoutIconAction } from './ui/IconAction';
import { WorkoutActionBar } from './ui/WorkoutActionBar';
import styles from './intensive.module.css';

/** Texto digitado -> reps válidas (inteiro > 0) ou null (= pendente). Nunca 0 nem a meta. */
export function parseRepsInput(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  return Number.isInteger(value) && value > 0 ? value : null;
}

/**
 * Valor inicial do campo "RPE registrado": o RPE-alvo do protocolo, pronto do
 * servidor (`advanced_technique_parameters.target_rpe`, dono
 * `training_safety.protocol_target_rpe`). Sem alvo (snapshot antigo) o campo
 * começa vazio: o frontend não inventa número.
 */
export function intensiveRpeInitialText(targetRpe: number | null): string {
  return targetRpe != null ? String(targetRpe) : '';
}

/** Texto do campo de RPE -> número enviado como `rpe`; vazio ou inválido = null. */
export function parseRpeInput(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const value = Number(trimmed.replace(',', '.'));
  return Number.isFinite(value) ? value : null;
}

type Props = {
  profile: IntensiveProfile;
  setIndex: number;
  totalSets: number;
  plannedKg: number | null;
  weightUnit: WeightUnit;
  loadSuffix?: string | null;
  repsTarget: string | null;
  /** `QuickLoadAdjustment` (a série atual ainda pode ter a carga ajustada). */
  adjustSlot?: ReactNode;
  /** Linha discreta antes do rodapé (ex.: próximo exercício). */
  beforeActions?: ReactNode;
  iconActions?: WorkoutIconAction[];
  notice?: ReactNode;
  /** Além da falha: "reps extras" de um toque nesta série (só na última do bloco). */
  extraRepsEnabled?: boolean;
  onComplete: (reps: number | null, rpe: number | null, extraReps: number | null) => void;
  onSkipSet?: () => void;
};

/** Contador de "reps extras" (além da falha): cada toque soma uma; sem RPE. */
export function ExtraRepsTapper({ value, onChange }: { value: number; onChange: (next: number) => void }) {
  return (
    <div data-testid="intensive-extra-reps">
      <span className={styles.tileLabel}>Reps extras (além da falha)</span>
      <div className={styles.repsStepper}>
        <button
          type="button"
          className={styles.stepButton}
          aria-label="Remover rep extra"
          onClick={() => onChange(Math.max(0, value - 1))}
          disabled={value <= 0}
        >
          −
        </button>
        <span className={styles.repsInput} aria-live="polite" data-testid="intensive-extra-reps-value">
          {value}
        </span>
        <button
          type="button"
          className={styles.stepButton}
          aria-label="Somar rep extra"
          onClick={() => onChange(value + 1)}
          data-testid="intensive-extra-reps-add"
        >
          +1
        </button>
      </div>
    </div>
  );
}

/**
 * Série do bloco: "Série 4 de 7", a carga planejada, um stepper grande de
 * reps e "Concluir série" com um toque. Sem reps a série é gravada como
 * PENDENTE (confirmada depois). O campo "RPE registrado" fica sempre
 * visível, já preenchido com o RPE-alvo (editável; apagado = sem RPE), como no
 * fluxo normal do Modo Treino. Série nova = componente remontado
 * (`key={state.currentSet}` em `IntensiveBlock`) = campo de novo com o alvo.
 */
export function IntensiveSetEntry({
  profile,
  setIndex,
  totalSets,
  plannedKg,
  weightUnit,
  loadSuffix = null,
  repsTarget,
  adjustSlot = null,
  beforeActions = null,
  iconActions = [],
  notice = null,
  extraRepsEnabled = false,
  onComplete,
  onSkipSet,
}: Props) {
  const [repsText, setRepsText] = useState('');
  const [extraReps, setExtraReps] = useState(0);
  const [rpeText, setRpeText] = useState(() => intensiveRpeInitialText(profile.targetRpe));
  const reps = parseRepsInput(repsText);
  const firstTap = parseNumericTarget(repsTarget);

  function step(delta: number) {
    if (reps == null) {
      // Primeiro toque: parte do início da faixa da ficha (visível antes de concluir).
      if (delta > 0) setRepsText(String(firstTap != null && firstTap > 0 ? Math.floor(firstTap) : 1));
      return;
    }
    const next = reps + delta;
    setRepsText(next > 0 ? String(next) : '');
  }

  function complete() {
    onComplete(
      reps,
      parseRpeInput(rpeText),
      extraRepsEnabled && extraReps > 0 ? extraReps : null,
    );
  }

  return (
    <>
      <section className={styles.card} aria-label={`Série ${setIndex} de ${totalSets}`} data-testid="intensive-set-entry">
        <div className={styles.setHeader}>
          <span className={styles.setLabel} data-testid="intensive-set-label">
            Série {setIndex} de {totalSets}
          </span>
          <span className={styles.headline}>{profile.techniqueName}</span>
        </div>
        <div>
          <span className={styles.tileLabel}>Carga planejada</span>
          <div className={styles.load} data-testid="intensive-planned-load">
            {plannedKg != null ? formatWeight(plannedKg, weightUnit) : '—'}
            {plannedKg != null && loadSuffix && <span className={styles.loadSuffix}>{loadSuffix}</span>}
          </div>
        </div>
        <div>
          <span className={styles.tileLabel}>Repetições{repsTarget ? ` · meta ${repsTarget}` : ''}</span>
          <div className={styles.repsStepper}>
            <button
              type="button"
              className={styles.stepButton}
              aria-label="Diminuir repetições"
              onClick={() => step(-1)}
              disabled={reps == null}
            >
              −
            </button>
            <input
              className={styles.repsInput}
              type="number"
              inputMode="numeric"
              min={1}
              placeholder="—"
              aria-label="Repetições feitas"
              value={repsText}
              onChange={(event) => setRepsText(event.target.value)}
              data-testid="intensive-reps-input"
            />
            <button type="button" className={styles.stepButton} aria-label="Aumentar repetições" onClick={() => step(1)}>
              +
            </button>
          </div>
        </div>
        {extraRepsEnabled && <ExtraRepsTapper value={extraReps} onChange={setExtraReps} />}
        <span className={styles.info}>
          RPE-alvo: <strong>{intensiveTargetRpeText(profile.targetRpe)}</strong>
          {reps == null && ' · sem reps, a série fica pendente para confirmar depois'}
        </span>
        <div className={styles.inlineRow}>
          <label className={styles.muted} htmlFor="intensive-rpe-input">
            RPE registrado nesta série
          </label>
          <input
            id="intensive-rpe-input"
            className={styles.smallInput}
            type="number"
            inputMode="decimal"
            step={0.5}
            min={0}
            placeholder="—"
            aria-label="RPE registrado nesta série"
            value={rpeText}
            onChange={(event) => setRpeText(event.target.value)}
            data-testid="intensive-rpe-input"
          />
        </div>
        {adjustSlot}
      </section>
      {beforeActions}
      <WorkoutActionBar
        primaryLabel="Concluir série"
        primaryIcon={<IconCheck size={20} />}
        onPrimary={complete}
        primaryTestId="intensive-complete-set"
        notice={notice}
        iconActions={iconActions}
        secondary={onSkipSet ? [{ key: 'skip-set', label: 'Pular série', onClick: onSkipSet, testId: 'intensive-skip-set' }] : []}
      />
    </>
  );
}
