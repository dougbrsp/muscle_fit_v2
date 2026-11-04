'use client';

import type { ReactElement, ReactNode } from 'react';
import { ResponsiveContainer } from 'recharts';
import { MOBILE_TOKENS } from '../tokens';
import styles from './ui.module.css';

/**
 * Receita de eixos para gráficos no celular (MB-3): poucos ticks, sem hover.
 * Espalhe nos `XAxis`/`YAxis` do Recharts: `<XAxis {...MOBILE_CHART_AXIS} />`.
 * Os dados e a unidade continuam os do desktop; só a densidade muda.
 */
export const MOBILE_CHART_AXIS = {
  tickCount: 4,
  interval: 'preserveStartEnd',
  minTickGap: 24,
  tick: { fontSize: 11, fill: 'rgba(245, 245, 245, 0.64)' },
  axisLine: false,
  tickLine: false,
} as const;

/** Margens enxutas para o gráfico ocupar a largura (eixo Y curto). */
export const MOBILE_CHART_MARGIN = { top: 8, right: 8, bottom: 0, left: -12 } as const;

/**
 * Moldura de gráfico do celular: resumo textual acima (a leitura principal,
 * vinda pronta da API), gráfico compacto e interação por toque. `children` é
 * UM gráfico do Recharts (LineChart, BarChart...) sem `ResponsiveContainer`:
 * a moldura já o envolve. Altura padrão 180, máximo 240. Texto alternativo em
 * `aria-label` (o gráfico não é lido por leitor de tela, o resumo é).
 */
export function MobileChart({
  summary,
  label,
  height = MOBILE_TOKENS.chartHeight,
  children,
}: {
  summary: ReactNode;
  label: string;
  height?: number;
  children: ReactElement;
}) {
  const h = Math.min(Math.max(height, 120), MOBILE_TOKENS.chartHeightMax);
  return (
    <figure className={styles.chart} style={{ marginInline: 0 }}>
      <figcaption className={styles.chartSummary}>{summary}</figcaption>
      <div className={styles.chartCanvas} role="img" aria-label={label} style={{ height: h }}>
        <ResponsiveContainer width="100%" height="100%">
          {children}
        </ResponsiveContainer>
      </div>
    </figure>
  );
}
