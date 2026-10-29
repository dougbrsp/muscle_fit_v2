'use client';

import { Anchor, Badge, Group, List, Paper, Stack, Text, Tooltip } from '@mantine/core';
import { IconArrowRight } from '@tabler/icons-react';
import Link from 'next/link';
import { cooldownBadge } from '@/lib/dashboard-checkin';
import type { DashboardFilters } from '@/lib/dashboard-filters';
import { fmtDateTime } from '@/lib/dashboard-format';
import { insightHref, insightSections } from '@/lib/dashboard-overview';
import type { Insight } from '@/lib/dashboard-overview-types';
import { BRAND } from '@/theme';
import { StatusBadge } from './StatusBadge';

/**
 * Um insight explicável (contrato §12), com os sete campos SEMPRE visíveis:
 * Observação / Evidência / Comparação / Confiança / Explicações alternativas /
 * Ação sugerida / Quando reavaliar.
 *
 * Tom informativo, sem alarmismo: a severidade aparece como badge do
 * registro único (`INFO | ATTENTION | ACTION`) e o texto vem pronto do
 * backend. Nada aqui muda ficha, meta ou prescrição — a "ação" é uma
 * sugestão em texto, e o link leva à tela de origem para a pessoa decidir.
 *
 * F2.2 — quando o backend decidiu o cooldown (`insight.cooldown`), o selo diz em
 * que pé este alerta está: "Novo", "Piorou", "Já visto"... Um cartão que aparece
 * (ou desaparece) sem explicação é indistinguível de defeito, e o selo é o que
 * torna a decisão legível para quem usa.
 */
export function InsightCard({
  insight,
  filters,
  compact = false,
}: {
  insight: Insight;
  filters: DashboardFilters;
  /** Na home mostra Observação + Ação abertos e o resto em lista curta. */
  compact?: boolean;
}) {
  const sections = insightSections(insight);
  const href = insightHref(insight, filters);
  const cooldown = insight.cooldown;
  const selo = cooldownBadge(cooldown);

  return (
    <Paper
      withBorder
      radius="md"
      p="md"
      component="article"
      aria-label={insight.question}
      style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}
    >
      <Stack gap="xs">
        <Group justify="space-between" align="flex-start" wrap="wrap" gap="xs">
          <Text size="sm" c={BRAND.textMuted} fw={600}>
            {insight.question}
          </Text>
          <Group gap={6}>
            <StatusBadge domain="severity" code={insight.severity} size="xs" />
            <StatusBadge domain="dataQuality" code={insight.data_quality} size="xs" />
            {selo && cooldown && (
              <Tooltip
                label={
                  cooldown.suppressed
                    ? `Você já viu este alerta${
                        cooldown.last_emitted_at ? ` em ${fmtDateTime(cooldown.last_emitted_at)}` : ''
                      }. Ele volta a aparecer ${
                        cooldown.snoozed_until ? `depois de ${fmtDateTime(cooldown.snoozed_until)}` : 'na próxima mudança'
                      }, ou antes se piorar.`
                    : `Mostrado ${cooldown.emitted_count}x; silenciado ${cooldown.suppressed_count}x.`
                }
                withArrow
                multiline
                w={280}
              >
                <Badge size="xs" variant="light" color={selo.color} style={{ cursor: 'help' }}>
                  {selo.label}
                </Badge>
              </Tooltip>
            )}
          </Group>
        </Group>

        {sections.map((section) => (
          <div key={section.key}>
            <Text size="xs" c={BRAND.textMuted} tt="uppercase" fw={600}>
              {section.label}
            </Text>
            {section.lines.length === 1 ? (
              <Text size={section.key === 'observation' ? 'md' : 'sm'} fw={section.key === 'observation' ? 600 : undefined}>
                {section.lines[0]}
              </Text>
            ) : (
              <List size="sm" spacing={2}>
                {section.lines.map((line) => (
                  <List.Item key={line}>{line}</List.Item>
                ))}
              </List>
            )}
          </div>
        ))}

        <Anchor component={Link} href={href} size="sm" fw={600} c={BRAND.orange}>
          <Group gap={4} wrap="nowrap">
            {compact ? 'Ver na tela de origem' : `Abrir ${insight.source_screen}`}
            <IconArrowRight size={14} aria-hidden />
          </Group>
        </Anchor>
      </Stack>
    </Paper>
  );
}
