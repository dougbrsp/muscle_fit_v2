'use client';

import { IconBolt } from '@tabler/icons-react';
import { BRAND } from '@/theme';
import styles from './workout-mode.module.css';

/**
 * Aviso (informação, não botão) de que o treino roda no modo avançado. Desde
 * o PR B4 da Evolução ZEPP o modo vem da ficha (`resolveWorkoutModeFromPlan`
 * no núcleo do Modo Treino), sem escolha "normal/avançado" antes de começar.
 * No modo normal não mostra nada. Novo visual (PR1/3): faixa compacta, com
 * estilo inline a partir de `BRAND` porque também aparece na seleção de
 * modo (`ModeSelectScreen`), fora da raiz que define as variáveis `--wm-*`.
 */
export const ADVANCED_MODE_NOTICE = 'Treino avançado: aquecimento, ajuste e técnica da ficha';

export function WorkoutModeNotice({ advanced, compact = false }: { advanced: boolean; compact?: boolean }) {
  if (!advanced) return null;
  // PR4 ("uma tela"): na execução o aviso vira um selo curto na linha de
  // progresso do topo; o texto completo fica no rótulo acessível e no tooltip.
  if (compact) {
    return (
      <span role="note" aria-label={ADVANCED_MODE_NOTICE} title={ADVANCED_MODE_NOTICE} className={styles.modeChip}>
        <IconBolt size={14} aria-hidden="true" />
        <span aria-hidden="true">Avançado</span>
      </span>
    );
  }
  return (
    <div
      role="note"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        minHeight: 44,
        padding: '10px 14px',
        borderRadius: 16,
        border: `1px solid ${BRAND.border}`,
        background: BRAND.bgElevated,
      }}
    >
      <IconBolt size={18} color={BRAND.orange} aria-hidden="true" style={{ flexShrink: 0 }} />
      <span style={{ color: BRAND.textPrimary, fontSize: 14, minWidth: 0, overflowWrap: 'anywhere' }}>
        {ADVANCED_MODE_NOTICE}
      </span>
    </div>
  );
}
