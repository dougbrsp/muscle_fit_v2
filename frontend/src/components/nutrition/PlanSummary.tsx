'use client';

import { Badge, Card, Group, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { formatNumberOrDash } from '@/lib/format';
import {
  ACTIVITY_LEVEL_OPTIONS,
  BMR_FORMULA_OPTIONS,
  PLAN_STATE_OPTIONS,
  PROTEIN_BASIS_OPTIONS,
  STRATEGY_OPTIONS,
  TDEE_MODE_OPTIONS,
  nutritionLabel,
} from '@/lib/nutrition-constants';
import { formatShareAsPercent } from '@/lib/nutrition-format';
import type { NutritionGoal, NutritionPlan } from '@/lib/nutrition-types';
import { formatDateBR } from '@/lib/physical-indicators-types';
import { BRAND } from '@/theme';

const STATE_COLORS: Record<string, string> = { draft: 'gray', current: 'green', superseded: 'dark' };

export function PlanStateBadge({ state }: { state: string }) {
  return (
    <Badge color={STATE_COLORS[state] ?? 'gray'} variant="light">
      {nutritionLabel(PLAN_STATE_OPTIONS, state)}
    </Badge>
  );
}

function goalBodyFatText(plan: NutritionPlan, goal: NutritionGoal | null | undefined): string {
  // Com o resumo da API (`plan.goal_summary` do motor), mostra o valor efetivo e se é o default;
  // sem ele, só o que o plano fixou.
  const valor = goal ? goal.goal_body_fat_percent : plan.goal_body_fat_percent;
  if (valor == null) return '-';
  const texto = `${formatNumberOrDash(valor, 1)}%`;
  return goal?.goal_body_fat_is_default ? `${texto} (padrão do motor)` : texto;
}

/** Parâmetros do plano (só leitura), com os rótulos do motor. `goal` (opcional)
 * é o resumo da meta de composição que a API calculou para o aluno. */
export function PlanSummary({
  plan,
  goal,
  title = 'Plano',
}: {
  plan: NutritionPlan;
  goal?: NutritionGoal | null;
  title?: string;
}) {
  const proteinBasis = nutritionLabel(PROTEIN_BASIS_OPTIONS, plan.protein_basis);
  const linhas: { label: string; value: string }[] = [
    { label: 'Estratégia', value: nutritionLabel(STRATEGY_OPTIONS, plan.strategy) ?? '-' },
    {
      label: 'Proteína',
      value: `${formatNumberOrDash(plan.protein_g_per_kg, 2)}${proteinBasis ? ` ${proteinBasis}` : ''}`,
    },
    { label: 'Gordura', value: `${formatShareAsPercent(plan.fat_kcal_share)} das kcal` },
  ];
  if (plan.weekly_rate_share != null) {
    linhas.push({ label: 'Ritmo de perda', value: `${formatShareAsPercent(plan.weekly_rate_share, 2)} do peso por semana` });
  }
  if (plan.surplus_share != null) {
    linhas.push({ label: 'Superávit', value: `${formatShareAsPercent(plan.surplus_share)} do gasto${plan.advanced ? ' (avançado)' : ''}` });
  }
  if (plan.carb_min_g_per_kg != null) {
    linhas.push({ label: 'Carboidrato mínimo', value: `${formatNumberOrDash(plan.carb_min_g_per_kg, 2)} g por kg de peso` });
  }
  linhas.push(
    { label: 'Fórmula da TMB', value: nutritionLabel(BMR_FORMULA_OPTIONS, plan.bmr_formula) ?? '-' },
    { label: 'Gasto do treino', value: nutritionLabel(TDEE_MODE_OPTIONS, plan.tdee_mode) ?? '-' },
    {
      label: 'Nível de atividade',
      value: nutritionLabel(ACTIVITY_LEVEL_OPTIONS, plan.activity_level) ?? 'O do perfil',
    },
    { label: 'Refeições por dia', value: plan.meals_per_day == null ? '-' : String(plan.meals_per_day) },
    { label: '% de gordura alvo', value: goalBodyFatText(plan, goal) },
    { label: 'Peso-alvo', value: plan.goal_weight_kg == null ? '-' : `${formatNumberOrDash(plan.goal_weight_kg, 1)} kg` },
  );

  return (
    <Card withBorder radius="md" bg={BRAND.bgSurface}>
      <Stack gap="xs">
        <Group justify="space-between" wrap="wrap">
          <Title order={4} c={BRAND.textPrimary}>
            {title}
          </Title>
          <Group gap="xs">
            <PlanStateBadge state={plan.state} />
            {plan.effective_from && (
              <Text size="xs" c="dimmed">
                Vale desde {formatDateBR(plan.effective_from)}
              </Text>
            )}
          </Group>
        </Group>
        <SimpleGrid cols={{ base: 1, xs: 2 }} spacing="xs" verticalSpacing={4}>
          {linhas.map((linha) => (
            <div key={linha.label}>
              <Text size="xs" c="dimmed">
                {linha.label}
              </Text>
              <Text size="sm" c={BRAND.textPrimary}>
                {linha.value}
              </Text>
            </div>
          ))}
        </SimpleGrid>
        {plan.notes && (
          <Text size="sm" c={BRAND.textMuted}>
            {plan.notes}
          </Text>
        )}
      </Stack>
    </Card>
  );
}
