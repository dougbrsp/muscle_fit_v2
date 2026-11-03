'use client';

import { Button, Group, SegmentedControl, Stack, Switch, Text } from '@mantine/core';
import { IconCalendarEvent } from '@tabler/icons-react';
import { useMutation } from '@tanstack/react-query';
import { NutritionLimitations } from './NutritionLimitations';
import { fetchCycleDaysFromTemplate, nutritionErrorInfo } from '@/lib/nutrition-api';
import { DAY_KIND_OPTIONS, WEEKDAY_OPTIONS, type NutritionCode } from '@/lib/nutrition-constants';
import { BRAND } from '@/theme';

/** Dia da semana ("0" = segunda) -> tipo do dia (código de `DAY_KIND_OPTIONS`). */
export type CycleDaysValue = Record<string, NutritionCode>;

type Props = {
  enabled: boolean;
  onEnabledChange: (enabled: boolean) => void;
  value: CycleDaysValue;
  onChange: (value: CycleDaysValue) => void;
  /** Aluno do plano (admin/PT); sem valor, o próprio usuário. */
  userId?: number | null;
};

/** Valor inicial: todos os dias com o tipo `normal` (o código vem do motor, `DAY_KIND_OPTIONS`). */
export function flatCycleDays(normalCode: NutritionCode): CycleDaysValue {
  return Object.fromEntries(WEEKDAY_OPTIONS.map((dia) => [dia.value, normalCode]));
}

/**
 * Dias de mais e menos gasto (D5): o usuário marca cada dia da semana como alto, normal ou
 * baixo, ou puxa a proposta do Modelo de treino e ajusta. A média semanal e os multiplicadores
 * são do motor; aqui só se escolhe o tipo de cada dia.
 */
export function PlanCycleDays({ enabled, onEnabledChange, value, onChange, userId }: Props) {
  const normal = DAY_KIND_OPTIONS.find((tipo) => tipo.value === 'normal')?.value ?? DAY_KIND_OPTIONS[0]?.value ?? '';
  const templateMutation = useMutation({
    mutationFn: () => fetchCycleDaysFromTemplate(userId ?? null),
    onSuccess: (proposta) => {
      if (proposta.cycle_days) onChange({ ...flatCycleDays(normal), ...proposta.cycle_days });
    },
  });
  const proposta = templateMutation.data;
  const erro = templateMutation.isError
    ? nutritionErrorInfo(templateMutation.error, 'Não foi possível ler o Modelo de treino.')
    : null;

  return (
    <Stack gap="xs">
      <Switch
        label="Dias de mais e menos gasto"
        description="Marque os dias da semana com mais ou menos carboidrato. A média da semana não muda."
        checked={enabled}
        onChange={(event) => onEnabledChange(event.currentTarget.checked)}
      />
      {enabled && (
        <Stack gap="xs">
          <Group>
            <Button
              variant="light"
              size="xs"
              leftSection={<IconCalendarEvent size={14} />}
              loading={templateMutation.isPending}
              onClick={() => templateMutation.mutate()}
            >
              Puxar do Modelo de treino
            </Button>
            <Text size="xs" c={BRAND.textMuted}>
              Dias de treino viram dia alto e os demais, baixo. Você ajusta antes de salvar.
            </Text>
          </Group>
          {proposta && <NutritionLimitations items={proposta.notes} title="Sobre o Modelo" tone="info" />}
          {erro && <NutritionLimitations items={erro.items} title={erro.message} tone="error" />}
          {erro && erro.items.length === 0 && (
            <Text c="red" size="sm">
              {erro.message}
            </Text>
          )}
          {WEEKDAY_OPTIONS.map((dia) => (
            <div key={dia.value}>
              <Text size="xs" c="dimmed">
                {dia.label}
              </Text>
              <SegmentedControl
                fullWidth
                size="xs"
                data={DAY_KIND_OPTIONS.map((tipo) => ({ value: tipo.value, label: tipo.label }))}
                value={value[dia.value] ?? normal}
                onChange={(tipo) => onChange({ ...value, [dia.value]: tipo })}
                aria-label={dia.label}
              />
            </div>
          ))}
        </Stack>
      )}
    </Stack>
  );
}
