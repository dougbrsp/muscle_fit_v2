'use client';

import { IconCheck } from '@tabler/icons-react';
import { useEffect, useState, type ReactNode } from 'react';
import { formatWeight, type WeightUnit } from '@/lib/weight-units';
import {
  intensivePauseHoldsCompletion,
  pauseRemainingSeconds,
  type IntensivePause,
  type IntensivePlannedLoad,
  type IntensiveProfile,
} from '@/lib/workout-mode-intensive';
import type { LoadAdjustSource } from '@/lib/workout-mode-load-suggestions';
import { intensiveTargetRpeText } from './IntensiveBriefing';
import { parseRepsInput } from './IntensiveSetEntry';
import { QuickLoadAdjustment } from './QuickLoadAdjustment';
import type { WorkoutIconAction } from './ui/IconAction';
import { WorkoutActionBar } from './ui/WorkoutActionBar';
import styles from './intensive.module.css';

/** Rótulo da origem da carga da etapa (o que vai em `load_adjust_source`). */
export function applicationLoadSourceText(source: LoadAdjustSource | null): string | null {
  if (source === 'protocol') return 'redução prevista pelo protocolo';
  if (source === 'fatigue') return 'redução extra por fadiga';
  if (source === 'manual') return 'carga editada';
  return null;
}

/** Relógio de parede só enquanto a pausa corre (o tempo vem de `endsAt`, nunca de um contador). */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

/** Pausa curta do rest-pause (a da prescrição): cronômetro sem início automático do próximo mini-bloco. */
export function IntensivePauseBanner({
  pause,
  nextLabel,
  skipped = false,
}: {
  pause: IntensivePause;
  nextLabel: string;
  /** "Pular pausa" tocado: mostra pronto sem esperar o cronômetro. */
  skipped?: boolean;
}) {
  const [running, setRunning] = useState(!skipped);
  const now = useNow(running && !skipped);
  const remaining = skipped ? 0 : pauseRemainingSeconds(pause.endsAt, now);
  useEffect(() => {
    if (remaining <= 0) setRunning(false);
  }, [remaining]);
  return (
    <div className={styles.stretch} role="timer" aria-live="polite" data-testid="intensive-pause">
      {remaining > 0 ? (
        <>
          <span className={styles.tileLabel}>Pausa · {pause.totalSeconds}s</span>
          <span className={styles.load} data-testid="intensive-pause-remaining">
            {remaining}s
          </span>
          <span className={styles.info}>Respire. Mesma carga no {nextLabel.toLowerCase()}.</span>
        </>
      ) : (
        <span className={styles.headline} data-testid="intensive-pause-ready">
          Pronto para o {nextLabel.toLowerCase()}
        </span>
      )}
    </div>
  );
}

type Props = {
  profile: IntensiveProfile;
  /** "Drop 2 de 3" / "Mini-bloco 1 de 4". */
  stageLabel: string;
  /** Nome curto da etapa ("Drop", "Mini-bloco") para o CTA. */
  stageNoun: string;
  planned: IntensivePlannedLoad;
  weightUnit: WeightUnit;
  loadIncrementKg: number | null | undefined;
  loadSuffix?: string | null;
  /** Etapas já feitas neste bloco ("Drop 1: 8 reps · 40 kg"). */
  appliedItems?: string[];
  /** Pausa do rest-pause em curso (null = sem pausa: drop set não tem descanso). */
  pause?: IntensivePause | null;
  saving: boolean;
  error?: string | null;
  canUndo: boolean;
  onAdjust: (kg: number, source: LoadAdjustSource) => void;
  onUndo: () => void;
  onComplete: (reps: number | null) => void;
  onSkipApplication?: () => void;
  iconActions?: WorkoutIconAction[];
  beforeActions?: ReactNode;
};

/**
 * Etapa da técnica no Modo Intensivo (drop set / rest-pause): a etapa atual em
 * destaque, a carga da etapa (a do protocolo, pronta do dono
 * `session_loads`), o ajuste imediato por fadiga, reps com um toque e o
 * RPE-alvo automático (sem RPE individual). Drop set não tem descanso; o
 * rest-pause mostra a pausa da prescrição. Não grava nada: a tela dona faz o
 * POST (`intensiveApplicationPayload`).
 */
export function IntensiveApplicationCard({
  profile,
  stageLabel,
  stageNoun,
  planned,
  weightUnit,
  loadIncrementKg,
  loadSuffix = null,
  appliedItems = [],
  pause = null,
  saving,
  error = null,
  canUndo,
  onAdjust,
  onUndo,
  onComplete,
  onSkipApplication,
  iconActions = [],
  beforeActions = null,
}: Props) {
  const [repsText, setRepsText] = useState('');
  const reps = parseRepsInput(repsText);
  const sourceText = applicationLoadSourceText(planned.source);
  // "Concluir" só depois da pausa ou com "Pular pausa" explícito (fim da pausa
  // marcado por `endsAt`: uma pausa nova não herda o pulo da anterior).
  const [skippedPauseEndsAt, setSkippedPauseEndsAt] = useState<number | null>(null);
  const pauseSkipped = pause != null && skippedPauseEndsAt === pause.endsAt;
  const [pauseNow, setPauseNow] = useState(() => Date.now());
  const pauseHolds = intensivePauseHoldsCompletion(pause, pauseNow, pauseSkipped);
  useEffect(() => {
    if (!pauseHolds || !pause) return undefined;
    const id = window.setTimeout(() => setPauseNow(Date.now()), Math.max(0, pause.endsAt - Date.now()) + 50);
    return () => window.clearTimeout(id);
  }, [pauseHolds, pause]);

  function step(delta: number) {
    if (reps == null) {
      if (delta > 0) setRepsText('1');
      return;
    }
    const next = reps + delta;
    setRepsText(next > 0 ? String(next) : '');
  }

  return (
    <>
      {pause && <IntensivePauseBanner key={pause.endsAt} pause={pause} nextLabel={stageNoun} skipped={pauseSkipped} />}
      <section className={styles.card} aria-label={stageLabel} data-testid="intensive-application">
        <div className={styles.setHeader}>
          <span className={styles.setLabel} data-testid="intensive-application-label">
            {stageLabel}
          </span>
          <span className={styles.headline}>{profile.techniqueName}</span>
        </div>
        {appliedItems.length > 0 && (
          <ul className={styles.steps} data-testid="intensive-application-done">
            {appliedItems.map((item) => (
              <li key={item} className={styles.muted}>
                {item}
              </li>
            ))}
          </ul>
        )}
        <div>
          <span className={styles.tileLabel}>Carga {profile.loadConstant ? '(constante)' : 'desta etapa'}</span>
          <div className={styles.load} data-testid="intensive-application-load">
            {planned.kg != null ? formatWeight(planned.kg, weightUnit) : '—'}
            {planned.kg != null && loadSuffix && <span className={styles.loadSuffix}>{loadSuffix}</span>}
          </div>
          {sourceText && (
            <span className={styles.info} data-testid="intensive-application-source">
              {sourceText}
            </span>
          )}
        </div>
        <div>
          <span className={styles.tileLabel}>Repetições</span>
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
              data-testid="intensive-application-reps"
            />
            <button type="button" className={styles.stepButton} aria-label="Aumentar repetições" onClick={() => step(1)}>
              +
            </button>
          </div>
        </div>
        <span className={styles.info} data-testid="intensive-application-rpe">
          RPE-alvo automático: <strong>{intensiveTargetRpeText(profile.targetRpe)}</strong> · sem RPE por etapa
          {reps == null && ' · sem reps, a etapa fica pendente para confirmar depois'}
        </span>
        {profile.allowsFatigueDrop && (
          <QuickLoadAdjustment
            plannedKg={planned.kg}
            weightUnit={weightUnit}
            loadIncrementKg={loadIncrementKg}
            loadSuffix={loadSuffix}
            label={`Carga do ${stageNoun.toLowerCase()}`}
            canUndo={canUndo}
            disabled={saving}
            onAdjust={onAdjust}
            onUndo={onUndo}
          />
        )}
      </section>
      {beforeActions}
      <WorkoutActionBar
        primaryLabel={`Concluir ${stageNoun.toLowerCase()}`}
        primaryIcon={<IconCheck size={20} />}
        onPrimary={() => {
          if (!pauseHolds) onComplete(reps);
        }}
        primaryLoading={saving}
        primaryDisabled={pauseHolds}
        primaryTestId="intensive-application-complete"
        notice={
          error ? (
            <span role="alert" data-testid="intensive-application-error">
              {error}
            </span>
          ) : pauseHolds ? (
            <span data-testid="intensive-pause-hold">Aguarde o fim da pausa ou toque em &quot;Pular pausa&quot;.</span>
          ) : null
        }
        iconActions={iconActions}
        secondary={[
          ...(pauseHolds && pause
            ? [{ key: 'skip-pause', label: 'Pular pausa', onClick: () => setSkippedPauseEndsAt(pause.endsAt), testId: 'intensive-skip-pause' }]
            : []),
          ...(onSkipApplication
            ? [{ key: 'skip-application', label: `Pular ${stageNoun.toLowerCase()}`, onClick: onSkipApplication, testId: 'intensive-skip-application' }]
            : []),
        ]}
      />
    </>
  );
}
