'use client';

import { Alert, List, Text } from '@mantine/core';
import { IconAlertTriangle, IconInfoCircle } from '@tabler/icons-react';
import type { NutritionLimitation } from '@/lib/nutrition-types';

type Props = {
  items: NutritionLimitation[];
  title: string;
  /** `warning` (amarelo, aviso) ou `error` (vermelho, bloqueia). */
  tone?: 'warning' | 'error' | 'info';
};

/** Limitações/erros do motor, com o texto que a API já devolve rotulado. */
export function NutritionLimitations({ items, title, tone = 'warning' }: Props) {
  if (items.length === 0) return null;
  const color = tone === 'error' ? 'red' : tone === 'info' ? 'blue' : 'yellow';
  const Icon = tone === 'info' ? IconInfoCircle : IconAlertTriangle;
  return (
    <Alert color={color} icon={<Icon size={16} />} title={title} radius="md">
      <List size="sm" spacing={2}>
        {items.map((item) => (
          <List.Item key={item.code}>
            <Text size="sm" component="span">
              {item.label}
            </Text>
          </List.Item>
        ))}
      </List>
    </Alert>
  );
}
