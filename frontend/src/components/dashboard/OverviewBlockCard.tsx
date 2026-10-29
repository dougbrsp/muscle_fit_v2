'use client';

import { Anchor, Group, Paper, Stack, Text, Title } from '@mantine/core';
import { IconArrowRight } from '@tabler/icons-react';
import Link from 'next/link';
import type { ReactNode } from 'react';
import type { DataQuality } from '@/lib/dashboard-v3-types';
import { BRAND } from '@/theme';
import { PartialState } from './states';
import { StatusBadge } from './StatusBadge';

/**
 * Cartão de UM bloco da visão geral (F1.6): título, a PERGUNTA que o bloco
 * responde, a RESPOSTA em uma frase (vinda do backend), os 2–3 números que a
 * sustentam (`children`) e o link de drill-down.
 *
 * Regras do brief que este componente materializa:
 * - nenhum cartão só repete número: a frase de resposta é obrigatória;
 * - cada bloco carrega a PRÓPRIA confiança (`data_quality`) e limitações,
 *   mostradas aqui, não escondidas na do envelope;
 * - mobile-first: empilha em coluna, o link fica no rodapé.
 */
export function OverviewBlockCard({
  title,
  question,
  answer,
  quality,
  limitations = [],
  drilldownHref,
  drilldownLabel = 'Ver detalhes',
  children,
}: {
  title: string;
  question: string;
  answer: string;
  quality: DataQuality;
  limitations?: string[];
  drilldownHref: string;
  drilldownLabel?: string;
  children?: ReactNode;
}) {
  return (
    <Paper
      withBorder
      radius="lg"
      p="md"
      component="section"
      aria-label={title}
      style={{ background: BRAND.bgElevated, borderColor: BRAND.border, height: '100%' }}
    >
      <Stack gap="sm" justify="space-between" style={{ height: '100%' }}>
        <Stack gap="xs">
          <Group justify="space-between" align="flex-start" wrap="nowrap">
            <div>
              <Title order={3} size="h5" c={BRAND.textPrimary}>
                {title}
              </Title>
              <Text size="xs" c={BRAND.textMuted}>
                {question}
              </Text>
            </div>
            <StatusBadge domain="dataQuality" code={quality} size="xs" />
          </Group>

          <Text size="md" fw={600}>
            {answer}
          </Text>

          {children}

          {quality !== 'HIGH' && limitations.length > 0 && (
            <PartialState quality={quality} limitations={limitations.slice(0, 2)} title="Ressalva" fixHref={null} />
          )}
        </Stack>

        <Anchor component={Link} href={drilldownHref} size="sm" fw={600} c={BRAND.orange}>
          <Group gap={4} wrap="nowrap">
            {drilldownLabel}
            <IconArrowRight size={14} aria-hidden />
          </Group>
        </Anchor>
      </Stack>
    </Paper>
  );
}
