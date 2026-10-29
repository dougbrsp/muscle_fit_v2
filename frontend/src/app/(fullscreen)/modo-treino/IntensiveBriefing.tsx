'use client';

import { IconPlayerPlay } from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { formatWeight, type WeightUnit } from '@/lib/weight-units';
import { intensiveRestSeconds, type IntensiveProfile } from '@/lib/workout-mode-intensive';
import type { WorkoutIconAction } from './ui/IconAction';
import { WorkoutActionBar } from './ui/WorkoutActionBar';
import styles from './intensive.module.css';

/** "FST-7 — 7 séries · 30s de descanso" (números do item/prescrição, nunca fixos). */
export function intensiveHeadline(profile: Pick<IntensiveProfile, 'techniqueName' | 'totalSets' | 'restBetween'>): string {
  const rest = intensiveRestSeconds(profile);
  const restText = rest > 0 ? `${rest}s de descanso` : 'sem descanso';
  return `${profile.techniqueName} — ${profile.totalSets} séries · ${restText}`;
}

/** RPE-alvo do protocolo: o número do backend; sem ele, só que o servidor define. */
export function intensiveTargetRpeText(targetRpe: number | null): string {
  return targetRpe != null ? `RPE ${targetRpe}` : 'Definido pelo servidor';
}

type Props = {
  profile: IntensiveProfile;
  exerciseName: string;
  equipmentName: string | null;
  plannedKg: number | null;
  weightUnit: WeightUnit;
  loadSuffix?: string | null;
  repsTarget: string | null;
  iconActions?: WorkoutIconAction[];
  /** Ajuste da carga inicial (o mesmo `QuickLoadAdjustment`). */
  adjustSlot?: ReactNode;
  onStart: () => void;
};

/** Briefing do bloco da técnica, antes da 1ª série de trabalho. */
export function IntensiveBriefing({
  profile,
  exerciseName,
  equipmentName,
  plannedKg,
  weightUnit,
  loadSuffix = null,
  repsTarget,
  iconActions = [],
  adjustSlot = null,
  onStart,
}: Props) {
  const rest = intensiveRestSeconds(profile);
  return (
    <>
      <section className={styles.card} aria-label={`Briefing ${profile.techniqueName}`} data-testid="intensive-briefing">
        <span className={styles.headline}>{intensiveHeadline(profile)}</span>
        <div className={styles.title}>{exerciseName}</div>
        {equipmentName && <span className={styles.muted}>{equipmentName}</span>}
        <div className={styles.tiles}>
          <div className={styles.tile}>
            <div className={styles.tileLabel}>Carga inicial</div>
            <div className={styles.tileValue} data-testid="intensive-initial-load">
              {plannedKg != null ? formatWeight(plannedKg, weightUnit) : '—'}
            </div>
            {plannedKg != null && loadSuffix && <div className={styles.tileHint}>{loadSuffix}</div>}
          </div>
          <div className={styles.tile}>
            <div className={styles.tileLabel}>Reps</div>
            <div className={styles.tileValue}>{repsTarget || '—'}</div>
          </div>
          <div className={styles.tile}>
            <div className={styles.tileLabel}>RPE-alvo</div>
            <div className={styles.tileValue} data-testid="intensive-target-rpe">
              {intensiveTargetRpeText(profile.targetRpe)}
            </div>
          </div>
        </div>
        <ul className={styles.steps}>
          <li>Cada série com boa técnica, até o RPE-alvo.</li>
          <li>
            {rest > 0
              ? `Descanso de ${rest}s entre as séries: o cronômetro começa quando você conclui a série.`
              : 'Sem descanso entre as etapas.'}
          </li>
          {profile.stretchGuidance && <li>{profile.stretchGuidance}</li>}
          <li>Se a carga pesar demais, reduza no ajuste rápido. A mudança vale para as próximas séries.</li>
        </ul>
        {adjustSlot}
      </section>
      <WorkoutActionBar
        primaryLabel={`Iniciar ${profile.techniqueName}`}
        primaryIcon={<IconPlayerPlay size={20} />}
        onPrimary={onStart}
        primaryTestId="intensive-start"
        iconActions={iconActions}
      />
    </>
  );
}
