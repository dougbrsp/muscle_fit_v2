import type { ReactNode } from 'react';
import styles from '../workout-mode.module.css';
import { ExerciseThumbnail } from './ExerciseThumbnail';

/** Nome do equipamento como botão que abre a troca (mesmo handler dos botões "Trocar"). */
export function EquipmentSwapButton({
  equipmentName,
  onClick,
  className,
  testId,
}: {
  equipmentName: string;
  onClick: () => void;
  className?: string;
  testId: string;
}) {
  return (
    <button
      type="button"
      className={className}
      title={equipmentName}
      onClick={onClick}
      aria-label={`Trocar equipamento: ${equipmentName}`}
      data-testid={testId}
    >
      {equipmentName}
    </button>
  );
}

/**
 * Descanso ENTRE séries: nome do equipamento atual tocável. Na transição de
 * exercício (o card é o do PRÓXIMO) ou sem equipamento definido, não mostra nada.
 */
export function RestEquipmentSwap({
  equipmentName,
  isExerciseTransition,
  onSwap,
}: {
  equipmentName?: string | null;
  isExerciseTransition: boolean;
  onSwap: () => void;
}) {
  if (isExerciseTransition || !equipmentName) return null;
  return (
    <EquipmentSwapButton
      equipmentName={equipmentName}
      onClick={onSwap}
      className={styles.restEquipmentSwap}
      testId="rest-equipment-swap"
    />
  );
}

/** Tamanho da foto no título compacto (PR4: ~48–56 px). */
export const TITLE_THUMB_SIZE = 52;

/**
 * Título compacto do exercício (briefing e execução), ~60–80 px de altura:
 * foto à esquerda; nome em até 2 linhas (o nome COMPLETO fica no DOM, no
 * `title` e no rótulo da seção — o corte é só visual, no fim da 2ª linha, sem
 * quebrar palavra no meio); e numa linha só: equipamento · chips (a fadiga
 * do grupo, uma única vez por tela). `trailing` é um botão de ícone de 44 px
 * (ex.: Info). Sem card de borda laranja nem glow.
 */
export function ExerciseTitleRow({
  name,
  equipmentName,
  photoUrl,
  photoAlt,
  onPhotoClick,
  onEquipmentClick,
  meta,
  trailing,
  testId,
}: {
  name: string;
  equipmentName?: string | null;
  photoUrl?: string | null;
  photoAlt?: string;
  // Toque na foto (ex.: ampliar). Ausente ou sem foto: foto estática.
  onPhotoClick?: () => void;
  // Toque no nome do equipamento = abrir a troca (mesmo handler dos botões
  // "Trocar"). Ausente: nome só texto (troca não permitida neste ponto).
  onEquipmentClick?: () => void;
  // Chips depois do equipamento, na mesma linha (fadiga, "Unilateral").
  meta?: ReactNode;
  trailing?: ReactNode;
  testId?: string;
}) {
  const alt = photoAlt || equipmentName || name;
  const thumb = <ExerciseThumbnail photoUrl={photoUrl} alt={alt} size={TITLE_THUMB_SIZE} />;
  return (
    <section className={styles.titleRow} aria-label={`Exercício: ${name}`} data-testid={testId}>
      {photoUrl && onPhotoClick ? (
        <button type="button" className={styles.thumbButton} onClick={onPhotoClick} aria-label="Ampliar foto do equipamento">
          {thumb}
        </button>
      ) : (
        thumb
      )}
      <div className={styles.titleText}>
        <h2 className={styles.titleName} title={name} data-testid="exercise-title">
          {name}
        </h2>
        {(equipmentName || meta) && (
          <div className={styles.titleMeta}>
            {equipmentName && (
              onEquipmentClick ? (
                <EquipmentSwapButton
                  equipmentName={equipmentName}
                  onClick={onEquipmentClick}
                  className={styles.titleMetaButton}
                  testId="title-equipment-swap"
                />
              ) : (
                <span className={styles.titleMetaText} title={equipmentName}>
                  {equipmentName}
                </span>
              )
            )}
            {meta}
          </div>
        )}
      </div>
      {trailing}
    </section>
  );
}
