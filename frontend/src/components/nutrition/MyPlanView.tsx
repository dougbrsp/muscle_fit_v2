'use client';

import { Alert, Card, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { IconAlertTriangle, IconInfoCircle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { NutritionLimitations } from './NutritionLimitations';
import { GoalCard } from './PlanPreview';
import { PlanSummary } from './PlanSummary';
import { SubjectCard } from './SubjectCard';
import { WeekTargetsTable } from './WeekTargetsTable';
import { fetchMyNutritionPlan, isNoPublishedPlan, nutritionErrorInfo, nutritionKeys } from '@/lib/nutrition-api';
import { formatGrams, formatKcal } from '@/lib/nutrition-format';
import { BRAND } from '@/theme';

/** O próprio plano vigente, só leitura (`GET /nutrition/me/plan`). */
export function MyPlanView() {
  const myPlanQuery = useQuery({ queryKey: nutritionKeys.myPlan, queryFn: fetchMyNutritionPlan, retry: false });

  if (myPlanQuery.isLoading) {
    return <Text c={BRAND.textMuted}>Carregando...</Text>;
  }
  if (myPlanQuery.isError) {
    if (isNoPublishedPlan(myPlanQuery.error)) {
      return (
        <Alert color="blue" icon={<IconInfoCircle size={16} />} radius="md">
          Você ainda não tem um plano nutricional. Ele é definido pelo seu personal trainer ou por um administrador.
        </Alert>
      );
    }
    return (
      <Alert color="red" icon={<IconAlertTriangle size={16} />}>
        {nutritionErrorInfo(myPlanQuery.error, 'Não foi possível carregar o seu plano.').message}
      </Alert>
    );
  }
  const dados = myPlanQuery.data;
  if (!dados) return null;
  const hoje = dados.today_target;

  return (
    <Stack gap="md">
      <NutritionLimitations
        items={dados.missing}
        title="Faltam dados no seu perfil para calcular a meta"
        tone="error"
      />
      <SimpleGrid cols={{ base: 1, md: 3 }} spacing="md">
        <PlanSummary plan={dados.plan} goal={dados.goal} title="Seu plano" />
        <SubjectCard subject={dados.subject} title="Você hoje" />
        <Stack gap="md">
          <Card withBorder radius="md" bg={BRAND.bgSurface}>
            <Stack gap={4}>
              <Title order={4} c={BRAND.textPrimary}>
                Meta de hoje
              </Title>
              {hoje ? (
                <>
                  <Text size="xl" fw={700} c={BRAND.textPrimary}>
                    {formatKcal(hoje.kcal)}
                  </Text>
                  <Text size="sm" c={BRAND.textMuted}>
                    Proteína {formatGrams(hoje.protein_g)} · Carboidrato {formatGrams(hoje.carbs_g)} · Gordura{' '}
                    {formatGrams(hoje.fat_g)}
                  </Text>
                </>
              ) : (
                <Text size="sm" c="dimmed">
                  Sem meta para hoje.
                </Text>
              )}
            </Stack>
          </Card>
          <GoalCard goal={dados.goal} />
        </Stack>
      </SimpleGrid>
      <Title order={4} c={BRAND.textPrimary}>
        Semana atual
      </Title>
      <WeekTargetsTable rows={dados.week} today={dados.today} />
    </Stack>
  );
}
