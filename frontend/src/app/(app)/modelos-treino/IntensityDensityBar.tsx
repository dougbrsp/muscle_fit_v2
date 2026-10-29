'use client';

import { Stack, Text, Tooltip } from '@mantine/core';
import { formatIndexPercent, type IntensityDensityGroupRow } from '@/lib/intensity-density';

const BAR_HEIGHT = 8;
const MARKER_SIZE = 5;
/** Cores do tema Mantine: vermelho = Densidade/Volume (esquerda), verde = Intensidade (direita). */
export const INTENSITY_DENSITY_GRADIENT =
  'linear-gradient(90deg, var(--mantine-color-red-7), var(--mantine-color-green-7))';

/** Cor do texto da direção (mesmas famílias do degradê). */
export function directionTextColor(row: IntensityDensityGroupRow): string {
  if (row.direction === 'intensity') return 'green.5';
  if (row.direction === 'density') return 'red.5';
  return 'dimmed';
}

function TooltipBody({ row }: { row: IntensityDensityGroupRow }) {
  const { subindices } = row;
  return (
    <Stack gap={0}>
      <Text size="xs" fw={600}>
        {row.muscleGroup} — {row.label}
      </Text>
      <Text size="xs">Intensidade (RPE): {formatIndexPercent(row.intensity)}</Text>
      <Text size="xs">Densidade/Volume: {formatIndexPercent(row.density)}</Text>
      <Text size="xs" c="dimmed">
        {[
          `reps: ${formatIndexPercent(subindices.reps)}`,
          `pico: ${formatIndexPercent(subindices.peak)}`,
          `descanso: ${formatIndexPercent(subindices.rest)}`,
          `séries: ${formatIndexPercent(subindices.sets)}`,
        ].join(' · ')}
      </Text>
    </Stack>
  );
}

export type IntensityDensityBarProps = {
  row: IntensityDensityGroupRow;
  /** Mostra "Densidade/Volume" e "Intensidade" nos extremos, abaixo da barra. */
  withLegend?: boolean;
};

/**
 * Barra em degradê vermelho (Densidade/Volume) → verde (Intensidade) com um
 * marcador em seta na posição do grupo. Usada pela janela flutuante e pela
 * tabela do builder, com o MESMO `IntensityDensityGroupRow`.
 */
export function IntensityDensityBar({ row, withLegend = false }: IntensityDensityBarProps) {
  const pct = row.position == null ? null : Math.round(row.position * 1000) / 10;
  const ariaLabel =
    pct == null
      ? `${row.muscleGroup}: sem dados`
      : `${row.muscleGroup}: ${row.label} (intensidade ${formatIndexPercent(row.intensity)}, densidade/volume ${formatIndexPercent(row.density)})`;

  return (
    <Tooltip label={<TooltipBody row={row} />} multiline w={260} openDelay={300} disabled={pct == null}>
      <div role="img" aria-label={ariaLabel} style={{ minWidth: 80, cursor: pct == null ? undefined : 'help' }}>
        <div style={{ position: 'relative', paddingTop: MARKER_SIZE + 1 }}>
          {pct != null && (
            <div
              data-marker
              data-position={pct}
              style={{
                position: 'absolute',
                top: 0,
                left: `${pct}%`,
                transform: 'translateX(-50%)',
                width: 0,
                height: 0,
                borderLeft: `${MARKER_SIZE}px solid transparent`,
                borderRight: `${MARKER_SIZE}px solid transparent`,
                borderTop: `${MARKER_SIZE + 1}px solid var(--mantine-color-text)`,
              }}
            />
          )}
          <div
            style={{
              position: 'relative',
              height: BAR_HEIGHT,
              borderRadius: BAR_HEIGHT / 2,
              background: pct == null ? 'var(--mantine-color-default-border)' : INTENSITY_DENSITY_GRADIENT,
              opacity: pct == null ? 1 : 0.85,
            }}
          >
            {pct != null && (
              <div
                style={{
                  position: 'absolute',
                  top: -1,
                  bottom: -1,
                  left: `${pct}%`,
                  width: 2,
                  transform: 'translateX(-50%)',
                  background: 'var(--mantine-color-text)',
                  borderRadius: 1,
                }}
              />
            )}
          </div>
        </div>
        {withLegend && <IntensityDensityLegend size="10px" />}
      </div>
    </Tooltip>
  );
}

/** Legenda dos extremos: Densidade/Volume à esquerda, Intensidade à direita. */
export function IntensityDensityLegend({ size = 'xs' }: { size?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginTop: 2 }}>
      <Text size={size} c="red.5" fw={600}>
        ← Densidade/Volume
      </Text>
      <Text size={size} c="green.5" fw={600}>
        Intensidade →
      </Text>
    </div>
  );
}
