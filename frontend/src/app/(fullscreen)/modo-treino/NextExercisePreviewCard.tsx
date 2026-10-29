'use client';

import { IconFlag, IconFlagOff, IconPlayerSkipForward, IconPlayerStop } from '@tabler/icons-react';
import { measureTargetText, setMeasureOf } from '@/lib/workout-mode-measures';
import type { GroupFatigueReading, WorkoutModePlanExercise } from '@/lib/workout-mode-types';
import { BRAND } from '@/theme';
import { ExerciseThumbnail } from './ui/ExerciseThumbnail';
import { IconAction } from './ui/IconAction';
import { MuscleFatigueChip } from './ui/MuscleFatigueChip';
import { StatusChip } from './ui/StatusChip';
import styles from './workout-mode.module.css';

/** Foto do próximo exercício na linha compacta do descanso (PR4). */
const NEXT_THUMB_SIZE = 40;

type Actions = {
  onSkip: () => void;
  onCancel: () => void;
  onFinish: () => void;
  finishing?: boolean;
};

type Props = {
  planExercise: WorkoutModePlanExercise;
  // Foto do equipamento do PRÓXIMO item (nunca a do exercício atual). Sem
  // foto: placeholder do mesmo tamanho.
  equipmentPhotoUrl?: string | null;
  equipmentName?: string | null;
  // Fadiga atual do grupo do PRÓXIMO exercício, só quando ela foi lida para
  // ele (ver `PlanExecutionScreen`). Sem leitura: o chip some.
  fatigue?: GroupFatigueReading | null;
  // Ações sobre o próximo exercício, as mesmas de antes na transição de
  // exercício (o "Continuar" é o CTA do rodapé). Ausente = card só informativo
  // (descanso entre séries).
  actions?: Actions | null;
};

/**
 * Prévia do próximo exercício da ficha no descanso, numa linha compacta
 * (PR4): foto de 40 px, "Próximo: <nome completo>" (até 2 linhas), o plano
 * e o equipamento numa linha curta, e o chip do grupo (ou a fadiga atual,
 * quando lida para ele). A transição para o próximo exercício não é
 * automática ao zerar o cronômetro: o usuário confirma no CTA do rodapé.
 */
export function NextExercisePreviewCard({ planExercise, equipmentPhotoUrl, equipmentName, fatigue, actions }: Props) {
  const measureTarget = measureTargetText(setMeasureOf(planExercise));
  const planned = [
    planExercise.sets_count ? `${planExercise.sets_count} séries` : null,
    measureTarget ?? (planExercise.reps_target ? `${planExercise.reps_target} reps` : null),
    planExercise.load_target || null,
    planExercise.rpe_target ? `RPE ${planExercise.rpe_target}` : null,
  ].filter((part): part is string => Boolean(part));
  const details = [equipmentName || null, planned.length > 0 ? planned.join(' · ') : null].filter(Boolean).join(' · ');

  return (
    <section className={styles.nextCard} aria-label="Próximo exercício" data-testid="next-exercise-card">
      <div className={styles.nextCardHead}>
        <ExerciseThumbnail
          photoUrl={equipmentPhotoUrl}
          alt={equipmentName || planExercise.exercise_name}
          size={NEXT_THUMB_SIZE}
        />
        <div className={styles.nextCardText}>
          <div className={styles.nextCardTitle} data-testid="next-exercise-name" title={planExercise.exercise_name}>
            <span>Próximo:</span> {planExercise.exercise_name}
          </div>
          {details && (
            <div className={styles.nextCardPlanned} title={details}>
              {details}
            </div>
          )}
        </div>
        {(planExercise.muscle_primary || fatigue) && (
          <div className={styles.nextCardChips}>
            {planExercise.muscle_primary && (
              <StatusChip label={planExercise.muscle_primary} color={BRAND.orange} size="sm" />
            )}
            {fatigue && <MuscleFatigueChip reading={fatigue} hideWhenUnavailable />}
          </div>
        )}
      </div>

      {actions && (
        <div className={styles.iconActionRow}>
          <IconAction
            action={{
              key: 'skip',
              label: 'Pular (fazer depois)',
              caption: 'Fazer depois',
              icon: <IconPlayerSkipForward size={18} />,
              onClick: actions.onSkip,
            }}
          />
          <IconAction
            action={{
              key: 'cancel',
              label: 'Remover da sessão',
              caption: 'Remover',
              icon: <IconFlagOff size={18} />,
              onClick: actions.onCancel,
            }}
          />
          <IconAction
            action={{
              key: 'finish',
              label: 'Encerrar treino',
              caption: 'Encerrar treino',
              icon: <IconPlayerStop size={18} />,
              onClick: actions.onFinish,
              loading: actions.finishing,
            }}
          />
        </div>
      )}
    </section>
  );
}

/**
 * Fim da ficha no descanso: não há próximo exercício conhecido. Nunca um
 * card de "próximo" vazio.
 */
export function LastExerciseNotice({ isExerciseTransition }: { isExerciseTransition: boolean }) {
  return (
    <section className={styles.linkRow} style={{ cursor: 'default' }} data-testid="last-exercise-notice">
      <span className={styles.linkRowIcon} aria-hidden="true">
        <IconFlag size={18} />
      </span>
      <span style={{ flex: '1 1 auto', minWidth: 0, fontWeight: 600 }}>
        {isExerciseTransition
          ? 'Era o último exercício da ficha. Continue para encerrar o treino.'
          : 'Último exercício da ficha: depois das séries dele, o treino termina.'}
      </span>
    </section>
  );
}
