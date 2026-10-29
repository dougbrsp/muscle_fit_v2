'use client';

import { Group, Text, Tooltip } from '@mantine/core';
import type { MantineSize } from '@mantine/core';
import { EM_DASH, fmtDecimal } from '@/lib/dashboard-format';
import type { Metric } from '@/lib/dashboard-v3-types';
import { StatusBadge } from './StatusBadge';
import { statusVisual } from '@/lib/dashboard-status';

/**
 * Renderiza o envelope semântico `Metric` do backend (plano §8.4,
 * `contracts.Metric`). É aqui que a regra "`null` nunca vira `0`" vira pixel.
 *
 * O que a UI mostra, e por quê:
 * - `value: null` → `—` (não calculável). `0` continua sendo `0`, porque zero
 *   observado é uma informação real e diferente de ausência de dado;
 * - `nature` só ganha selo quando NÃO é `OBSERVED`/`DERIVED`: estimativa,
 *   projeção, planejado e inferência precisam ser lidos como tal; observado e
 *   derivado são o caso normal e não merecem ruído visual;
 * - `data_quality` só ganha selo quando não é `HIGH`;
 * - `limitations[]` vão para o tooltip/`title` — é o que o número NÃO permite
 *   concluir, e o texto vem pronto do backend (nunca escrito na tela).
 *
 * Nenhum cálculo acontece aqui: valor, unidade, natureza, qualidade e
 * limitações chegam prontos do `response_model`.
 */
export function MetricValue({
  metric,
  digits = 1,
  size = 'sm',
  fw,
  showNature = true,
  showQuality = true,
  emptyHint = 'Não calculável com os dados registrados no período.',
}: {
  metric: Metric | null | undefined;
  digits?: number;
  size?: MantineSize;
  fw?: number;
  /** Desliga o selo de natureza quando a coluna inteira já é de um só tipo. */
  showNature?: boolean;
  showQuality?: boolean;
  emptyHint?: string;
}) {
  if (!metric) {
    return (
      <Tooltip label={emptyHint} withArrow multiline w={280}>
        <Text size={size} c="dimmed" style={{ cursor: 'help' }}>
          {EM_DASH}
        </Text>
      </Tooltip>
    );
  }

  const isAbsent = metric.value == null;
  const text = isAbsent
    ? EM_DASH
    : `${fmtDecimal(metric.value, digits)}${metric.unit ? ` ${metric.unit}` : ''}`;

  const natureVisual = statusVisual('dataNature', metric.nature);
  const showNatureBadge =
    showNature && metric.nature !== 'OBSERVED' && metric.nature !== 'DERIVED';
  const showQualityBadge = showQuality && metric.data_quality !== 'HIGH';

  // `title` além do Tooltip: cobre leitura por teclado/leitor de tela e a
  // exportação estática, onde o hover do Mantine pode não estar disponível.
  const tooltipLines = [
    isAbsent ? emptyHint : null,
    `${natureVisual.label}: ${natureVisual.description ?? ''}`.trim(),
    ...metric.limitations,
  ].filter(Boolean) as string[];
  const title = tooltipLines.join('\n');

  return (
    <Tooltip label={title} withArrow multiline w={320}>
      <Group gap={6} wrap="nowrap" title={title} style={{ cursor: 'help' }}>
        <Text size={size} fw={fw} c={isAbsent ? 'dimmed' : undefined}>
          {text}
        </Text>
        {showNatureBadge && <StatusBadge domain="dataNature" code={metric.nature} size="xs" />}
        {showQualityBadge && (
          <StatusBadge domain="dataQuality" code={metric.data_quality} size="xs" />
        )}
      </Group>
    </Tooltip>
  );
}
