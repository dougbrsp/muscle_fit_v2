'use client';

import { Loader } from '@mantine/core';
import {
  IconAlertTriangle,
  IconCircleCheck,
  IconEqual,
  IconSparkles,
  IconTrendingDown,
  IconTrendingUp,
} from '@tabler/icons-react';
import { useId, useState } from 'react';
import { loadAdjustmentText } from '@/lib/set-engine-status';
import { aiTipView } from '@/lib/workout-mode-execution-view';
import type { RestSavedSetView } from '@/lib/workout-mode-rest-view';
import type { SetEvaluationResult } from '@/lib/workout-mode-types';
import { BRAND, WORKOUT_MODE_TOKENS } from '@/theme';
import { ClampText } from './ui/ClampText';
import { StatusChip } from './ui/StatusChip';
import styles from './workout-mode.module.css';

/** Cor CSS de um selo do motor (nome de cor do Mantine vindo de `set-engine-status`). */
function mantineColor(name: string): string {
  return `var(--mantine-color-${name}-4)`;
}

const DIRECTION_ICONS = { UP: IconTrendingUp, HOLD: IconEqual, DOWN: IconTrendingDown } as const;

/**
 * "SÉRIE SALVA" + selos do motor + a regra do motor para a próxima série
 * (`next_set_load`) num card só (PR4: compacto). A ação ("Aumentar carga")
 * e o motivo ficam numa linha com "ver mais", que abre a explicação da
 * regra no lugar. Tudo pronto em `restSavedSetView`; sem série gravada ou
 * sem recomendação, a parte correspondente some (nunca card vazio).
 *
 * `loadShownElsewhere`: a carga da próxima série JÁ está no relógio do
 * descanso (`RestTimerHero`) — aqui ela não se repete. Sem ela no relógio
 * (ex.: série medida), a carga do motor aparece junto da ação.
 */
export function RestSavedSetPanel({
  view,
  loadShownElsewhere = false,
}: {
  view: RestSavedSetView;
  loadShownElsewhere?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const explanationId = useId();
  if (!view.showSavedChip && view.badges.length === 0 && !view.engine) return null;
  const engine = view.engine;
  const DirectionIcon = engine?.direction ? DIRECTION_ICONS[engine.direction] : IconEqual;
  const reason = engine?.reason || '';

  return (
    <section className={styles.engineCard} aria-label="Série salva e recomendação do motor" data-testid="rest-saved-set">
      {(view.showSavedChip || view.badges.length > 0) && (
        <div className={styles.chipRow}>
          {view.showSavedChip && (
            <StatusChip
              icon={<IconCircleCheck size={14} />}
              label="Série salva"
              color={WORKOUT_MODE_TOKENS.success}
              testId="set-saved-chip"
              size="sm"
            />
          )}
          {view.badges.map((badge) => (
            <StatusChip key={badge.label} label={badge.label} color={mantineColor(badge.color)} size="sm" />
          ))}
        </div>
      )}

      {engine && (
        <div data-testid="engine-next-set">
          <div className={styles.engineLine}>
            <span
              className={styles.engineAction}
              style={{ color: mantineColor(engine.color) }}
              data-testid="engine-next-set-summary"
            >
              <DirectionIcon size={18} aria-hidden="true" />
              <span>
                <span className={styles.srOnly}>Regra do motor para a próxima série: </span>
                {engine.actionLabel}
                {!loadShownElsewhere && engine.loadText ? ` → ${engine.loadText}` : ''}
              </span>
            </span>
            {(reason || engine.explanation) && (
              <>
                <p className={styles.clampText} style={{ color: BRAND.textMuted, lineHeight: 1.6 }}>
                  {reason}
                </p>
                {engine.explanation && (
                  <button
                    type="button"
                    className={styles.textToggle}
                    style={{ lineHeight: 1.6 }}
                    aria-expanded={open}
                    aria-controls={explanationId}
                    onClick={() => setOpen((value) => !value)}
                  >
                    {open ? 'ver menos' : 'ver mais'}
                  </button>
                )}
              </>
            )}
          </div>
          {engine.explanation && (
            <p id={explanationId} className={styles.engineExplanation} hidden={!open}>
              {engine.explanation}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

/**
 * Card da IA por série, separado do motor: só com o toggle "IA por série"
 * ligado e com resposta real (carregando, indisponível ou o texto). Toggle
 * desligado ou avaliação sem texto: nada (ver `aiTipView`). PR4: uma linha
 * com "ver mais"; as notas de corte (RPE/carga) abrem junto.
 */
export function RestAiEvaluationCard({
  enabled,
  loading,
  error,
  result,
}: {
  enabled: boolean;
  loading: boolean;
  error: boolean;
  result: SetEvaluationResult | null;
}) {
  if (!enabled) return null;
  const tip = aiTipView(enabled, result);
  if (!loading && !error && !tip) return null;
  const loadNotes = (result?.load_adjustments || [])
    .map((adjustment) => loadAdjustmentText(adjustment))
    .filter((text): text is string => Boolean(text));
  const lead = <span className={styles.tipTitle}>IA:</span>;

  return (
    <section
      className={`${styles.tipRow} ${styles.tipRowAi}`}
      aria-label="Avaliação da IA"
      data-testid="rest-ai-card"
      aria-busy={loading}
    >
      <span className={styles.tipIcon} aria-hidden="true">
        <IconSparkles size={18} />
      </span>
      <div className={styles.tipBody}>
        {loading && (
          <div className={styles.aiTipStatus}>
            <Loader size="xs" color="brand" /> Avaliando série com IA...
          </div>
        )}
        {!loading && error && (
          <div className={styles.aiTipStatus}>
            <IconAlertTriangle size={16} aria-hidden="true" /> Avaliação de IA indisponível para esta série.
          </div>
        )}
        {!loading && !error && tip && (
          <ClampText lead={lead} text={tip.note}>
            {tip.realignedByEngine && (
              <span className={styles.aiTipFootnote} style={{ display: 'block' }} data-testid="rest-ai-engine-note">
                A carga da próxima série segue a regra do motor.
              </span>
            )}
            {/* PR-14 (§5.7): cortes do servidor na sugestão da IA, com o porquê. */}
            {(result?.clamps || []).map((clamp) => (
              <span
                key={`${clamp.field}-${clamp.from}-${clamp.to}`}
                className={styles.aiTipFootnote}
                style={{ display: 'block' }}
              >
                RPE sugerido limitado por segurança: {clamp.from} → {clamp.to}
              </span>
            ))}
            {loadNotes.map((text) => (
              <span key={text} className={styles.aiTipFootnote} style={{ display: 'block' }}>
                {text}
              </span>
            ))}
          </ClampText>
        )}
      </div>
    </section>
  );
}
