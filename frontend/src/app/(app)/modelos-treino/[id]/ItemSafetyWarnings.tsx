'use client';

import { Alert, Badge, Group, Stack, Text } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { safetyWarningColor } from '@/lib/workout-plan-template-form';
import { TEMPLATE_SAFETY_WARNING_LABEL, type TemplateSafetyWarning } from '@/lib/workout-plan-templates-types';
import { BRAND } from '@/theme';

// Bloco de avisos exibido DENTRO do card/linha do item correspondente. Fica
// fora de qualquer `Collapse` — um item colapsado continua mostrando o
// aviso, como exige o PR-12.
export function ItemSafetyWarnings({ warnings }: { warnings: TemplateSafetyWarning[] }) {
  if (warnings.length === 0) return null;
  const hasBlocking = warnings.some((w) => w.code !== 'RPE_ABOVE_SAFE_CEILING');
  return (
    <Alert
      color={hasBlocking ? 'red' : 'orange'}
      variant="light"
      icon={<IconAlertTriangle size={16} />}
      mt="xs"
      p="xs"
      title="Revisar por segurança"
    >
      <Stack gap={4}>
        {warnings.map((w, index) => (
          <Group key={`${w.code}-${index}`} gap="xs" align="flex-start" wrap="nowrap">
            <Badge size="sm" variant="light" color={safetyWarningColor(w.code)}>
              {TEMPLATE_SAFETY_WARNING_LABEL[w.code] || w.code}
            </Badge>
            <Text size="xs" c={BRAND.textPrimary}>
              {w.message}
            </Text>
          </Group>
        ))}
      </Stack>
    </Alert>
  );
}
