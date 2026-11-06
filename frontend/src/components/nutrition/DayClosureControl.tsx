'use client';

import { Alert, Badge, Button, Group, Stack, Text } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { closeNutritionDay, diaryKeys, nutritionErrorInfo, reopenNutritionDay } from '@/lib/nutrition-api';
import { adaptiveLabel, ADAPTIVE_CODES, dashboardKeys } from '@/lib/nutrition-dashboard';
import type { DayClosure } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

/** Rótulo pronto do backend (JSON gerado); sem o grupo/código no JSON, não há selo. */
export function completenessText(source: string | null | undefined): string | null {
  if (!source) return null;
  const rotulo = adaptiveLabel('completeness_source', source);
  return rotulo !== source ? rotulo : null;
}

/**
 * "Fechei o dia" / "Reabrir o dia" do diário e a origem da completude do dia. Só aparece para dias até hoje
 * (comparação de datas ISO, apenas para esconder o botão); a validação final é do backend, e o rótulo vem pronto dele.
 */
export function DayClosureControl({
  date,
  today,
  closure,
  completenessSource,
  minHeight,
}: {
  date: string;
  today: string | null;
  closure: DayClosure | null | undefined;
  completenessSource: string | null | undefined;
  minHeight?: number;
}) {
  const qc = useQueryClient();
  const fechado = closure?.closed === true;
  const mutation = useMutation({
    mutationFn: () => (fechado ? reopenNutritionDay(date) : closeNutritionDay(date)),
    onSuccess: () =>
      Promise.all([qc.invalidateQueries({ queryKey: diaryKeys.all }), qc.invalidateQueries({ queryKey: dashboardKeys.all })]),
  });
  const origem = completenessText(completenessSource);
  const podeFechar = today !== null && date <= today;
  if (!podeFechar && !origem) return null;
  return (
    <Stack gap="xs" data-testid="fechar-dia">
      <Group gap="xs" wrap="wrap">
        {origem && (
          <Badge
            size="sm"
            variant="light"
            color={completenessSource === ADAPTIVE_CODES.manualClose ? 'brand' : 'green'}
            data-testid="fechar-dia-origem"
          >
            {origem}
          </Badge>
        )}
        {podeFechar && (
          <Button
            size="xs"
            variant={fechado ? 'default' : 'light'}
            color="brand"
            mih={minHeight}
            loading={mutation.isPending}
            onClick={() => mutation.mutate()}
            data-testid="fechar-dia-botao"
          >
            {fechado ? 'Reabrir o dia' : 'Fechei o dia'}
          </Button>
        )}
      </Group>
      {podeFechar && !fechado && (
        <Text size="xs" c={BRAND.textMuted}>
          Fechar o dia o conta como completo para o gasto adaptativo; você pode reabrir quando quiser.
        </Text>
      )}
      {mutation.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} role="alert" data-testid="fechar-dia-erro">
          {nutritionErrorInfo(mutation.error, 'Não foi possível atualizar o fechamento do dia.').message}
        </Alert>
      )}
    </Stack>
  );
}
