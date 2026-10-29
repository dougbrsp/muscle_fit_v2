import { IconClock, IconHeartbeat } from '@tabler/icons-react';
import type { PlanCardioItem, PlanCardioResponse, PlanCardioZoneRange } from '@/lib/analytics-types';
import { WORKOUT_MODE_CSS_VARS } from './ui/workout-mode-css-vars';
import styles from './workout-mode.module.css';

/** "Z2 · 115–129 bpm" ou só "Z2" quando o atleta não tem zonas. */
export function zoneRangeText(zone: PlanCardioZoneRange): string {
  if (zone.lower_bpm == null || zone.upper_bpm == null) return zone.label;
  return `${zone.label} · ${zone.lower_bpm}–${zone.upper_bpm} bpm`;
}

function CardioItem({ item }: { item: PlanCardioItem }) {
  return (
    <div className={styles.raised} style={{ padding: 10 }} data-testid="cardio-after-strength-item">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }}>
        <strong style={{ color: 'var(--wm-text)', fontSize: 15 }}>{item.mode_label}</strong>
        <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center', color: 'var(--wm-text-muted)', fontSize: 13 }}>
          <IconClock size={14} aria-hidden="true" />
          {item.total_minutes_label} no total
        </span>
      </div>
      <ul className={styles.stepList} style={{ margin: '8px 0 0', paddingLeft: 0, listStyle: 'none' }}>
        {item.steps.map((step) => (
          <li key={step.kind}>
            <div className={styles.eyebrow}>{step.label}</div>
            <div style={{ color: 'var(--wm-text)', fontWeight: 700 }}>{step.text}</div>
            {step.zones.map((zone) => (
              <div key={zone.zone} style={{ color: 'var(--wm-text-muted)', fontSize: 13 }}>
                {zoneRangeText(zone)}
              </div>
            ))}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Cardio prescrito no slot do dia, mostrado ao ENCERRAR a força (M98 C3b).
 * Só exibe: sem registro nem timer (o realizado vem do Intervals; a execução
 * guiada é o backlog M98-R). Etapas, textos, tempo total e faixas de bpm vêm
 * prontos de `GET /workout-plans/{id}/cardio`. Sem cardio, não desenha nada.
 */
export function CardioAfterStrength({ cardio }: { cardio: PlanCardioResponse | null | undefined }) {
  if (!cardio || cardio.items.length === 0) return null;
  return (
    <div className={styles.root} style={WORKOUT_MODE_CSS_VARS} data-testid="cardio-after-strength">
      <div className={styles.card} style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', color: 'var(--wm-orange)' }}>
          <IconHeartbeat size={18} aria-hidden="true" />
          <span className={styles.eyebrow} style={{ color: 'var(--wm-orange)' }}>
            Cardio de hoje{cardio.slot_name ? ` · ${cardio.slot_name}` : ''}
          </span>
        </div>
        {cardio.items.map((item, index) => (
          <CardioItem key={`${item.text}-${index}`} item={item} />
        ))}
        <div style={{ color: 'var(--wm-text-muted)', fontSize: 12 }}>
          O realizado entra pelo Intervals.icu; não é preciso registrar aqui.
        </div>
      </div>
    </div>
  );
}
