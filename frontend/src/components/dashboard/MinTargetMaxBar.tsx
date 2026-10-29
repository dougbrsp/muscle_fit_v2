'use client';

import { Box, Group, Text, Tooltip } from '@mantine/core';
import { fmtSets } from '@/lib/dashboard-format';
import { minTargetMaxScale } from '@/lib/dashboard-muscles';
import { statusVisual } from '@/lib/dashboard-status';

/**
 * Barra `mínimo → alvo → máximo` com a posição do REALIZADO (plano §7).
 *
 * Duas decisões que a barra materializa:
 *
 * 1. **Projetado não é realizado.** A projeção aparece como um trecho
 *    HACHURADO depois do realizado, com marcador próprio — nunca somada à
 *    mesma barra sólida. Somar as duas faria um grupo que ainda não foi
 *    treinado parecer dentro da meta porque o modelo promete rodá-lo depois.
 * 2. **Cor nunca é o único sinal** (§14): as três metas têm marcas verticais
 *    rotuladas e o valor vai escrito ao lado; quem não distingue as cores lê
 *    os números do mesmo jeito.
 */
export function MinTargetMaxBar({
  min,
  target,
  max,
  actual,
  projected = null,
  status,
  height = 10,
  unit = 'séries/sem',
}: {
  min: number | null;
  target: number | null;
  max: number | null;
  actual: number | null;
  projected?: number | null;
  status: string;
  height?: number;
  unit?: string;
}) {
  const scale = minTargetMaxScale({ min, target, max, actual, projected });
  const visual = statusVisual('weeklyVolume', status);

  const marca = (pct: number, label: string, valor: number | null) => (
    <Tooltip key={label} label={`${label}: ${fmtSets(valor, 1)} ${unit}`} withArrow>
      <Box
        aria-hidden
        style={{
          position: 'absolute',
          left: `${pct}%`,
          top: -3,
          bottom: -3,
          width: 2,
          background: 'var(--mantine-color-dark-1)',
          opacity: 0.85,
        }}
      />
    </Tooltip>
  );

  const descricao = [
    `Realizado ${fmtSets(actual, 1)} ${unit}`,
    `mínimo ${fmtSets(min, 1)}`,
    `alvo ${fmtSets(target, 1)}`,
    `máximo ${fmtSets(max, 1)}`,
    projected != null && projected > 0
      ? `projeção do ciclo +${fmtSets(projected, 1)} séries (não entra no status)`
      : null,
  ]
    .filter(Boolean)
    .join(' · ');

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
        {/* Projetado: hachurado, DEPOIS do realizado e visualmente distinto. */}
        {scale.projectedPct != null && (
          <Box
            aria-hidden
            style={{
              position: 'absolute',
              left: 0,
              width: `${scale.projectedPct}%`,
              height,
              borderRadius: height,
              backgroundImage:
                'repeating-linear-gradient(45deg, var(--mantine-color-blue-8) 0 4px, transparent 4px 8px)',
              opacity: 0.8,
            }}
          />
        )}
        {/* Realizado: sólido, é o único que o status classifica. */}
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
        {marca(scale.minPct, 'Mínimo', min)}
        {marca(scale.targetPct, 'Alvo', target)}
        {marca(scale.maxPct, 'Máximo', max)}
      </Box>
      <Group gap={6} justify="space-between" mt={2} wrap="nowrap">
        <Text size="xs" c="dimmed">
          mín {fmtSets(min, 1)} · alvo {fmtSets(target, 1)} · máx {fmtSets(max, 1)}
        </Text>
        {scale.overflow && (
          <Text size="xs" c={visual.color} fw={600}>
            acima do máximo
          </Text>
        )}
      </Group>
    </Box>
  );
}
