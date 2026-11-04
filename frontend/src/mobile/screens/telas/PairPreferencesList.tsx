'use client';

import { UnstyledButton } from '@mantine/core';
import { IconStar, IconStarFilled, IconX } from '@tabler/icons-react';
import { MAX_PREFERENCE_STARS, starsLabel, type TrainingPreferenceGroup, type TrainingPreferenceItem } from '@/lib/equipment-recommend';
import { ListBlock, ListGroup } from '../../ui';
import styles from './PairPreferencesList.module.css';

const STAR_VALUES = Array.from({ length: MAX_PREFERENCE_STARS }, (_, i) => i + 1);

/**
 * Notas por par exercício+equipamento (slot `pairPreferences`): uma seção por
 * grupo muscular, o exercício como título e uma linha por equipamento com as
 * estrelas em alvos de 44 pt. A nota exibida e o envio vêm do cliente da tela
 * (mesmo hook do desktop); aqui só se apresenta. Foto só quando existe.
 */
export function PairPreferencesList({
  groups,
  starsOf,
  onChange,
  onOpenPhoto,
}: {
  groups: TrainingPreferenceGroup[];
  starsOf: (pair: TrainingPreferenceItem) => number | null;
  onChange: (pair: TrainingPreferenceItem, stars: number | null) => void;
  onOpenPhoto: (photo: { src: string; alt: string }) => void;
}) {
  return (
    <>
      {groups.map((group) => (
        <ListGroup key={group.group} title={group.group}>
          <ListBlock>
          {group.exercises.map((exercise) => (
            <div key={exercise.exerciseId} className={styles.block}>
              <p className={styles.exercise}>{exercise.exerciseName}</p>
              {exercise.pairs.map((pair) => {
                const equipmentName = pair.equipment_name || `Equipamento #${pair.equipment_id}`;
                const label = `${exercise.exerciseName} em ${equipmentName}`;
                const value = starsOf(pair);
                return (
                  <div key={pair.equipment_exercise_id} className={styles.pair}>
                    <div className={styles.pairHead}>
                      {pair.photo_url ? (
                        <UnstyledButton
                          className={styles.thumb}
                          aria-label={`Ampliar foto de ${equipmentName}`}
                          onClick={() => onOpenPhoto({ src: pair.photo_url as string, alt: equipmentName })}
                        >
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img src={pair.photo_url} alt="" width={40} height={40} />
                        </UnstyledButton>
                      ) : null}
                      <span className={styles.equipment}>{equipmentName}</span>
                    </div>
                    <div className={styles.stars} role="group" aria-label={`Sua nota para ${label}: ${starsLabel(value)}`}>
                      {STAR_VALUES.map((n) => {
                        const on = value != null && n <= value;
                        return (
                          <button
                            key={n}
                            type="button"
                            className={styles.star}
                            aria-label={`${n} ${n === 1 ? 'estrela' : 'estrelas'} para ${label}`}
                            aria-pressed={value === n}
                            onClick={() => onChange(pair, n)}
                          >
                            {on ? <IconStarFilled size={24} className={styles.starOn} /> : <IconStar size={24} className={styles.starOff} />}
                          </button>
                        );
                      })}
                      <button
                        type="button"
                        className={styles.clear}
                        aria-label={`Limpar minha nota para ${label}`}
                        disabled={value == null}
                        onClick={() => onChange(pair, null)}
                      >
                        <IconX size={18} />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          ))}
          </ListBlock>
        </ListGroup>
      ))}
    </>
  );
}
