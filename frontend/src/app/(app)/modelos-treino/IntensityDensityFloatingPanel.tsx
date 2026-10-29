'use client';

import { Group, Loader, Stack, Text, Tooltip } from '@mantine/core';
import type { IntensityDensityGroupRow } from '@/lib/intensity-density';
import { BRAND } from '@/theme';
import { FloatingPanel } from './FloatingPanel';
import { directionTextColor, IntensityDensityBar, IntensityDensityLegend } from './IntensityDensityBar';
import type { FloatingPanelControls } from './useFloatingPanels';

export const INTENSITY_DENSITY_PANEL_ID = 'intensity-density';
const TITLE = 'Intensidade × Densidade';

export const INTENSITY_DENSITY_UNIT_HINT =
  'Para onde o Modelo aponta em cada grupo. Intensidade: séries até a falha (RPE 10) ou perto dela (RPE 9); técnica de falha conta a última série como RPE 10. Densidade/Volume: mais repetições, pico de contração, descanso curto e mais séries. Média por exercício ponderada pelas séries equivalentes do grupo (principal + secundário; estabilizador fora) na janela de 7 dias. Aquecimento e ajuste ficam fora.';

export type IntensityDensityFloatingPanelProps = {
  rows: IntensityDensityGroupRow[];
  isPending: boolean;
  isError: boolean;
  controls: FloatingPanelControls;
  index: number;
  withinPortal?: boolean;
};

/**
 * Janela "Intensidade × Densidade" (Modos A e B). Recebe as linhas JÁ
 * calculadas pelo builder (`buildIntensityDensityByGroup`) — o mesmo resultado
 * da tabela "Intensidade × Densidade por grupo", para os números não divergirem.
 */
export function IntensityDensityFloatingPanel({
  rows,
  isPending,
  isError,
  controls,
  index,
  withinPortal,
}: IntensityDensityFloatingPanelProps) {
  let body;
  if (rows.length > 0) {
    body = (
      <Stack gap={6}>
        <IntensityDensityLegend />
        {rows.map((row) => (
          <div key={row.muscleGroup}>
            <Group justify="space-between" wrap="nowrap" gap={4}>
              <Text size="xs" c={BRAND.textPrimary} truncate>
                {row.muscleGroup}
              </Text>
              <Text size="xs" c={directionTextColor(row)} style={{ flexShrink: 0 }}>
                {row.label}
              </Text>
            </Group>
            <IntensityDensityBar row={row} />
          </div>
        ))}
      </Stack>
    );
  } else if (isPending) {
    body = (
      <Group justify="center" py="xs">
        <Loader size="xs" color="brand" />
      </Group>
    );
  } else {
    body = (
      <Text size="xs" c="dimmed">
        {isError
          ? 'Não foi possível calcular intensidade e densidade agora.'
          : 'Adicione exercícios aos slots para ver intensidade e densidade.'}
      </Text>
    );
  }

  return (
    <FloatingPanel title={TITLE} index={index} controls={controls} withinPortal={withinPortal}>
      <Tooltip label={INTENSITY_DENSITY_UNIT_HINT} multiline w={300} openDelay={400}>
        <Text size="xs" c="dimmed" mb={4} style={{ cursor: 'help' }}>
          Direção por grupo (ponderada por estímulo)
        </Text>
      </Tooltip>
      {body}
    </FloatingPanel>
  );
}
