'use client';

import { Alert, Button, Card, Group, Modal, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconCheck, IconRefresh } from '@tabler/icons-react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { NutritionLimitations } from './NutritionLimitations';
import { SubjectCard } from './SubjectCard';
import { WeekTargetsTable } from './WeekTargetsTable';
import { formatNumberOrDash } from '@/lib/format';
import {
  nutritionErrorCode,
  nutritionErrorInfo,
  nutritionKeys,
  previewNutritionPlan,
  publishNutritionPlan,
  recomputeNutritionTargets,
} from '@/lib/nutrition-api';
import {
  BMR_FORMULA_OPTIONS,
  TDEE_MODE_OPTIONS,
  TRAINING_KCAL_NATURE_OPTIONS,
  nutritionLabel,
  nutritionNotice,
} from '@/lib/nutrition-constants';
import { formatGrams, formatKcal } from '@/lib/nutrition-format';
import type { NutritionGoal, NutritionPlan, NutritionPublishResponse, NutritionTargetDetail } from '@/lib/nutrition-types';
import { formatDateBR } from '@/lib/physical-indicators-types';
import { BRAND } from '@/theme';

function Line({ label, value }: { label: string; value: string }) {
  return (
    <Group justify="space-between" wrap="nowrap" gap="xs">
      <Text size="sm" c="dimmed">
        {label}
      </Text>
      <Text size="sm" c={BRAND.textPrimary} ta="right">
        {value}
      </Text>
    </Group>
  );
}

/** Meta de hoje com cada etapa do motor: TMB -> treino -> TDEE -> alvo -> macros. */
export function TodayTargetCard({ target }: { target: NutritionTargetDetail }) {
  const natureza = nutritionLabel(TRAINING_KCAL_NATURE_OPTIONS, target.training.kcal_nature);
  const fator = formatNumberOrDash(target.tdee.activity_factor, 3);
  const delta = target.kcal_target.delta_kcal;
  return (
    <Card withBorder radius="md" bg={BRAND.bgSurface}>
      <Stack gap={6}>
        <Title order={4} c={BRAND.textPrimary}>
          Meta de hoje
        </Title>
        <Line
          label={`TMB (${nutritionLabel(BMR_FORMULA_OPTIONS, target.bmr.formula) ?? '-'})`}
          value={formatKcal(target.bmr.kcal)}
        />
        <Line
          label={`Gasto do treino${natureza ? ` (${natureza})` : ''}`}
          value={target.training_known ? formatKcal(target.training.kcal) : 'A definir'}
        />
        <Line
          label={`Gasto total (${nutritionLabel(TDEE_MODE_OPTIONS, target.tdee.mode) ?? '-'} · fator ${fator})`}
          value={formatKcal(target.tdee.kcal)}
        />
        <Line
          label="Alvo calórico"
          value={`${formatKcal(target.kcal)}${delta == null ? '' : ` (${delta > 0 ? '+' : ''}${formatNumberOrDash(delta, 0)})`}`}
        />
        <Line label="Proteína" value={formatGrams(target.protein_g)} />
        <Line label="Carboidrato" value={formatGrams(target.carbs_g)} />
        <Line label="Gordura" value={formatGrams(target.fat_g)} />
        <Line label="Fibra" value={formatGrams(target.fiber_g)} />
        <Line label="Proteína por refeição" value={formatGrams(target.protein_per_meal_g)} />
        {target.scale_comparison?.warn && (
          <Text size="xs" c="yellow">
            {nutritionNotice('scale_bmr_deviation')} ({formatKcal(target.scale_comparison.scale_kcal)})
          </Text>
        )}
        <NutritionLimitations items={target.kcal_target.clamped_by} title="Ajustes do motor" tone="info" />
      </Stack>
    </Card>
  );
}

export function GoalCard({ goal }: { goal: NutritionGoal }) {
  return (
    <Card withBorder radius="md" bg={BRAND.bgSurface}>
      <Stack gap={6}>
        <Title order={4} c={BRAND.textPrimary}>
          Meta de composição
        </Title>
        <Line
          label={`% de gordura alvo${goal.goal_body_fat_is_default ? ' (padrão do motor)' : ''}`}
          value={goal.goal_body_fat_percent == null ? '-' : `${formatNumberOrDash(goal.goal_body_fat_percent, 1)}%`}
        />
        <Line
          label="Peso com a massa magra atual"
          value={goal.target_weight_for_goal_kg == null ? '-' : `${formatNumberOrDash(goal.target_weight_for_goal_kg, 1)} kg`}
        />
        <Line label="Peso-alvo informado" value={goal.goal_weight_kg == null ? '-' : `${formatNumberOrDash(goal.goal_weight_kg, 1)} kg`} />
        {goal.should_stop && (
          <Text size="xs" c="yellow">
            {nutritionNotice('phase_should_stop')}
          </Text>
        )}
      </Stack>
    </Card>
  );
}

type Props = {
  plan: NutritionPlan;
  /** Id da vigente do aluno que a tela mostra (null = nenhuma): vai na publicação (409 se mudou). */
  currentPlanId: number | null;
  onPublished: (result: NutritionPublishResponse) => void;
  /** Outra publicação mudou a vigente (409 `publish_conflict`): a tela recarrega a lista. */
  onConflict?: () => void;
};

/**
 * Preview do plano (POST, sem gravar): sujeito, meta de hoje e os 7 dias da
 * semana atual, calculados pelo motor. 422 = faltam dados do aluno (lista
 * vinda da API). Rascunho pode ser publicado; o vigente pode ter as metas
 * recalculadas de hoje em diante.
 */
export function PlanPreview({ plan, currentPlanId, onPublished, onConflict }: Props) {
  const [confirmOpened, { open: openConfirm, close: closeConfirm }] = useDisclosure(false);
  const isDraft = plan.state === 'draft';
  const isCurrent = plan.state === 'current';

  const previewQuery = useQuery({
    queryKey: [...nutritionKeys.preview(plan.id), plan.updated_at, plan.state],
    queryFn: () => previewNutritionPlan(plan.id),
    retry: false,
  });

  const publishMutation = useMutation({
    mutationFn: () => publishNutritionPlan(plan.id, currentPlanId),
    onSuccess: (result) => {
      closeConfirm();
      onPublished(result);
    },
    onError: (erro) => {
      if (nutritionErrorCode(erro) === 'publish_conflict') onConflict?.();
    },
  });

  const recomputeMutation = useMutation({
    mutationFn: (today: string) =>
      recomputeNutritionTargets({ user_id: plan.user_id, date_from: today, date_to: null }),
  });

  if (previewQuery.isLoading) {
    return <Text c={BRAND.textMuted}>Calculando...</Text>;
  }

  if (previewQuery.isError) {
    const info = nutritionErrorInfo(previewQuery.error, 'Não foi possível calcular o preview.');
    const faltamDados = nutritionErrorCode(previewQuery.error) === 'nutrition_subject_incomplete';
    return faltamDados ? (
      <NutritionLimitations items={info.items} title={info.message} tone="error" />
    ) : (
      <Alert color="red" icon={<IconAlertTriangle size={16} />}>
        {info.message}
      </Alert>
    );
  }

  const preview = previewQuery.data;
  if (!preview) return null;
  const publishError = publishMutation.isError ? nutritionErrorInfo(publishMutation.error, 'Não foi possível publicar.') : null;
  const recomputeError = recomputeMutation.isError
    ? nutritionErrorInfo(recomputeMutation.error, 'Não foi possível recalcular.')
    : null;

  return (
    <Stack gap="md">
      <SimpleGrid cols={{ base: 1, md: 3 }} spacing="md">
        <SubjectCard subject={preview.subject} />
        <TodayTargetCard target={preview.target} />
        <GoalCard goal={preview.goal} />
      </SimpleGrid>

      <Title order={4} c={BRAND.textPrimary}>
        Semana atual
      </Title>
      <WeekTargetsTable
        rows={preview.week.map((target) => ({ local_date: target.local_date, target }))}
        today={preview.today}
      />
      <NutritionLimitations items={preview.limitations} title="Avisos do motor nesta semana" />

      {isDraft && (
        <Group justify="flex-end">
          <Button color="brand" leftSection={<IconCheck size={16} />} onClick={openConfirm}>
            Publicar plano
          </Button>
        </Group>
      )}
      {isCurrent && (
        <Group justify="flex-end">
          <Button
            variant="light"
            color="brand"
            leftSection={<IconRefresh size={16} />}
            loading={recomputeMutation.isPending}
            onClick={() => recomputeMutation.mutate(preview.today)}
          >
            Recalcular metas a partir de hoje
          </Button>
        </Group>
      )}
      {recomputeMutation.isSuccess && (
        <Text size="sm" c="green">
          {recomputeMutation.data.targets.length} meta(s) recalculada(s) até {formatDateBR(recomputeMutation.data.date_to)}.
        </Text>
      )}
      {recomputeError && <NutritionLimitations items={recomputeError.items} title={recomputeError.message} tone="error" />}
      {recomputeError && recomputeError.items.length === 0 && (
        <Text c="red" size="sm">
          {recomputeError.message}
        </Text>
      )}

      <Modal opened={confirmOpened} onClose={closeConfirm} title="Publicar plano" centered>
        <Stack gap="md">
          <Text size="sm">
            O plano passa a valer a partir de hoje ({formatDateBR(preview.today)}) e substitui o plano vigente do aluno.
            As metas de dias anteriores ficam como estão.
          </Text>
          {publishError && <NutritionLimitations items={publishError.items} title={publishError.message} tone="error" />}
          {publishError && publishError.items.length === 0 && (
            <Text c="red" size="sm">
              {publishError.message}
            </Text>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={closeConfirm}>
              Cancelar
            </Button>
            <Button color="brand" loading={publishMutation.isPending} onClick={() => publishMutation.mutate()}>
              Publicar
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
