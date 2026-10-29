import { BRAND } from '@/theme';
import styles from '../workout-mode.module.css';
import { StatusChip } from './StatusChip';

/**
 * "Próximo: <nome> · GRUPO" numa linha só (briefing e execução). `name`
 * nulo = este é o último exercício da ficha. O nome completo fica no `title`.
 */
export function NextExerciseLine({
  name,
  muscle,
  testId,
}: {
  name: string | null;
  muscle?: string | null;
  testId?: string;
}) {
  return (
    <div className={styles.nextLine} data-testid={testId}>
      {name ? (
        <>
          <span className={styles.nextLineText} title={name}>
            <span>Próximo:</span> {name}
          </span>
          {muscle && <StatusChip label={muscle} color={BRAND.textMuted} size="sm" />}
        </>
      ) : (
        <span className={styles.nextLineText} style={{ color: BRAND.textMuted }}>
          Último exercício da ficha
        </span>
      )}
    </div>
  );
}
