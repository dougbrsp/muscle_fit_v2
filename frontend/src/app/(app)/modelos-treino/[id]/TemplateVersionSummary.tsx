'use client';

import { Badge, Group, Paper, SimpleGrid, Stack, Text } from '@mantine/core';
import { buildVersionSummary } from '@/lib/template-version-summary';
import type { WorkoutPlanTemplateVersionDetail } from '@/lib/workout-plan-templates-types';
import { BRAND } from '@/theme';

/**
 * Resumo SOMENTE LEITURA de uma versão do Modelo: o que cada slot pedia, sem
 * nenhum controle do editor (nada de campos, setas, lixeira ou avisos de
 * edição). Cartões largos — 1 coluna no celular, até 3 no desktop — e texto
 * que quebra por palavra, sem scroll horizontal.
 */
export function TemplateVersionSummary({ detail }: { detail: WorkoutPlanTemplateVersionDetail }) {
  const summary = buildVersionSummary(detail);
  const wrap = { overflowWrap: 'break-word', wordBreak: 'normal' } as const;

  return (
    <Stack gap="md">
      <Stack gap={4}>
        <Text fw={700} size="lg" style={wrap}>
          {summary.title}
        </Text>
        <Group gap={6} wrap="wrap">
          <Badge variant="light" color="brand">
            {summary.modeLabel}
          </Badge>
          {detail.version.is_current && (
            <Badge variant="light" color="green">
              Vigente
            </Badge>
          )}
        </Group>
        <Text size="sm" c={BRAND.textMuted} style={wrap}>
          Início: {summary.startLabel} · Autor: {summary.authorLabel}
        </Text>
        <Text size="sm" style={wrap}>
          {summary.changeLabel}
        </Text>
      </Stack>

      {summary.slots.length === 0 ? (
        <Text size="sm" c={BRAND.textMuted}>
          Esta versão não tem slots.
        </Text>
      ) : (
        <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }} spacing="md" verticalSpacing="md">
          {summary.slots.map((slot) => (
            <Paper key={slot.key} withBorder radius="md" p="md" data-slot-kind={slot.kindLabel}>
              <Stack gap="xs">
                <Group gap={6} wrap="wrap" justify="space-between">
                  <Text fw={600} style={wrap}>
                    {slot.title}
                  </Text>
                  <Badge variant="light" color={slot.isRest ? 'gray' : 'brand'}>
                    {slot.kindLabel}
                  </Badge>
                </Group>
                {slot.weekdayLabel && (
                  <Text size="xs" c={BRAND.textMuted}>
                    {slot.weekdayLabel}
                  </Text>
                )}
                {slot.isRest && slot.items.length === 0 && (
                  <Text size="sm" c={BRAND.textMuted}>
                    Descanso
                  </Text>
                )}
                {!slot.isRest && slot.items.length === 0 && (
                  <Text size="sm" c={BRAND.textMuted}>
                    Sem itens
                  </Text>
                )}
                {slot.items.map((item) => (
                  <Stack key={item.key} gap={2} data-item>
                    <Text size="sm" fw={600} style={wrap}>
                      {item.title}
                    </Text>
                    {item.subtitle && (
                      <Text size="xs" c={BRAND.textMuted} style={wrap}>
                        {item.subtitle}
                      </Text>
                    )}
                    {item.details.length > 0 && (
                      <Text size="xs" style={wrap}>
                        {item.details.join(' · ')}
                      </Text>
                    )}
                  </Stack>
                ))}
              </Stack>
            </Paper>
          ))}
        </SimpleGrid>
      )}
    </Stack>
  );
}
