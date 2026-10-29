'use client';

import { Box, Group, Text, Tooltip } from '@mantine/core';
import { fmtSets } from '@/lib/dashboard-format';
import { plannedVsRealizedScale } from '@/lib/dashboard-muscles';
import { statusVisual } from '@/lib/dashboard-status';

/**
 * Barra `planejado x realizado` para grupos cuja meta vem do PRÓPRIO Modelo
 * por equipamento (`target_source = 'template_plan'`).
 *
 * Difere da `MinTargetMaxBar` de propósito: um plano fixo não tem mínimo nem
 * máximo, então há UMA marca (o planejado) e o realizado sólido — nada de
 * inventar ±X% para preencher três marcas. Grupo fora do Modelo (planejado
 * zero) não recebe marca nenhuma e diz isso por escrito: o realizado nele é
 * trabalho extra, não déficit. Cor nunca é o único sinal (§14): o valor vai
 * escrito ao lado e no `aria-label`.
 */
export function PlannedVsRealizedBar({
  planned,
  actual,
  status,
  height = 10,
  unit = 'séries/sem',
}: {
  planned: number | null;
  actual: number | null;
  status: string;
  height?: number;
  unit?: string;
}) {
  const scale = plannedVsRealizedScale({ planned, actual });
  const visual = statusVisual('plannedVolume', status);

  const descricao = [
    `Realizado ${fmtSets(actual, 1)} ${unit}`,
    scale.outsideModel ? 'grupo fora do seu Modelo' : `planejado ${fmtSets(planned, 1)}`,
  ].join(' · ');

  return (
    <Box>
      <Box
        role="img"
        aria-label={descricao}
        title={descricao}
        style={{
          position: 'relative',
          height,
          borderRadius: height,
          background: 'var(--mantine-color-dark-6)',
          overflow: 'visible',
        }}
      >
        {scale.actualPct != null && (
          <Box
            aria-hidden
            style={{
              position: 'absolute',
              left: 0,
              width: `${scale.actualPct}%`,
              height,
              borderRadius: height,
              background: visual.hex,
            }}
          />
        )}
        {scale.plannedPct != null && (
          <Tooltip label={`Planejado pelo modelo: ${fmtSets(planned, 1)} ${unit}`} withArrow>
            <Box
              aria-hidden
              style={{
                position: 'absolute',
                left: `${scale.plannedPct}%`,
                top: -3,
                bottom: -3,
                width: 2,
                background: 'var(--mantine-color-dark-1)',
                opacity: 0.85,
              }}
            />
          </Tooltip>
        )}
      </Box>
      <Group gap={6} justify="space-between" mt={2} wrap="nowrap">
        <Text size="xs" c="dimmed">
          {scale.outsideModel ? 'fora do modelo' : `planejado ${fmtSets(planned, 1)}`}
        </Text>
        {scale.overflow && !scale.outsideModel && (
          <Text size="xs" c={visual.color} fw={600}>
            acima do planejado
          </Text>
        )}
      </Group>
    </Box>
  );
}
