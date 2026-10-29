'use client';

import { Alert, Button, Group, Paper, Stack, Text, Title } from '@mantine/core';
import { IconAlertTriangle, IconPlus } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { MyPlanView } from '@/components/nutrition/MyPlanView';
import { NutritionSubnav } from '@/components/nutrition/NutritionSubnav';
import { PlanForm } from '@/components/nutrition/PlanForm';
import { PlanHistory } from '@/components/nutrition/PlanHistory';
import { PlanPreview } from '@/components/nutrition/PlanPreview';
import { PlanSummary } from '@/components/nutrition/PlanSummary';
import { UserPicker } from '@/components/nutrition/UserPicker';
import { apiFetch } from '@/lib/api';
import type { SessionUser } from '@/lib/auth';
import { deleteNutritionPlan, fetchNutritionPlans, nutritionErrorInfo, nutritionKeys } from '@/lib/nutrition-api';
import type { NutritionPlan } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

async function fetchCurrentUser(): Promise<SessionUser> {
  return apiFetch<SessionUser>('/auth/me');
}

type Editing = { mode: 'new' } | { mode: 'edit'; plan: NutritionPlan } | null;

/** Admin/PT: escolhe o aluno, cria/edita o rascunho, vê o preview e publica. */
function PlanManager() {
  const qc = useQueryClient();
  const [userId, setUserId] = useState<number | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [editing, setEditing] = useState<Editing>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const plansQuery = useQuery({
    queryKey: nutritionKeys.plans(userId ?? 0),
    queryFn: () => fetchNutritionPlans(userId as number),
    enabled: userId !== null,
    retry: false,
  });

  const deleteMutation = useMutation({
    mutationFn: (plan: NutritionPlan) => deleteNutritionPlan(plan.id),
    onSuccess: async (_resultado, plan) => {
      if (selectedId === plan.id) setSelectedId(null);
      setNotice('Rascunho excluído.');
      await qc.invalidateQueries({ queryKey: nutritionKeys.plans(plan.user_id) });
    },
  });

  function chooseUser(id: number | null) {
    setUserId(id);
    setSelectedId(null);
    setEditing(null);
    setNotice(null);
  }

  async function afterSave(plan: NutritionPlan) {
    setEditing(null);
    setSelectedId(plan.id);
    setNotice('Rascunho salvo. Confira o preview antes de publicar.');
    await qc.invalidateQueries({ queryKey: nutritionKeys.preview(plan.id) });
    await qc.invalidateQueries({ queryKey: nutritionKeys.plans(plan.user_id) });
  }

  const plans = plansQuery.data?.items ?? [];
  const selected = plans.find((plan) => plan.id === selectedId) ?? null;
  // A vigente que a tela mostra: vai na publicação (concorrência otimista, 409 se mudou).
  const currentPlanId = plans.find((plan) => plan.state === 'current')?.id ?? null;

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <UserPicker value={userId} onChange={chooseUser} />
        {userId !== null && (
          <Button
            color="brand"
            leftSection={<IconPlus size={16} />}
            onClick={() => {
              setEditing({ mode: 'new' });
              setNotice(null);
            }}
          >
            Novo rascunho
          </Button>
        )}
      </Group>

      {notice && (
        <Text size="sm" c="green">
          {notice}
        </Text>
      )}
      {deleteMutation.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {nutritionErrorInfo(deleteMutation.error, 'Não foi possível excluir o rascunho.').message}
        </Alert>
      )}

      {userId !== null && plansQuery.isLoading && <Text c={BRAND.textMuted}>Carregando...</Text>}
      {plansQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {nutritionErrorInfo(plansQuery.error, 'Não foi possível carregar os planos do aluno.').message}
        </Alert>
      )}

      {editing && userId !== null && (
        <Paper withBorder radius="md" p="md" bg={BRAND.bgElevated}>
          <Stack gap="sm">
            <Title order={3} c={BRAND.textPrimary}>
              {editing.mode === 'new' ? 'Novo rascunho' : 'Editar rascunho'}
            </Title>
            <PlanForm
              key={editing.mode === 'new' ? 'novo' : editing.plan.id}
              userId={userId}
              plan={editing.mode === 'edit' ? editing.plan : null}
              onSaved={afterSave}
              onCancel={() => setEditing(null)}
            />
          </Stack>
        </Paper>
      )}

      {plansQuery.isSuccess && (
        <Stack gap="xs">
          <Title order={3} c={BRAND.textPrimary}>
            Planos de {plansQuery.data.user.full_name || plansQuery.data.user.username}
          </Title>
          <PlanHistory
            plans={plans}
            selectedId={selectedId}
            onSelect={(plan) => {
              setSelectedId(plan.id);
              setNotice(null);
            }}
            onEdit={(plan) => {
              setEditing({ mode: 'edit', plan });
              setNotice(null);
            }}
            onDelete={(plan) => deleteMutation.mutate(plan)}
            deletingId={deleteMutation.isPending ? (deleteMutation.variables?.id ?? null) : null}
          />
        </Stack>
      )}

      {selected && (
        <Stack gap="md">
          <PlanSummary plan={selected} />
          <PlanPreview
            plan={selected}
            currentPlanId={currentPlanId}
            onConflict={() => qc.invalidateQueries({ queryKey: nutritionKeys.plans(selected.user_id) })}
            onPublished={async (result) => {
              setSelectedId(result.plan.id);
              setNotice(`Plano publicado. ${result.targets.length} meta(s) diária(s) calculada(s) a partir de hoje.`);
              await qc.invalidateQueries({ queryKey: nutritionKeys.plans(result.plan.user_id) });
            }}
          />
        </Stack>
      )}
    </Stack>
  );
}

/**
 * `/nutricao/plano` — plano nutricional (Lote L2b). Admin e personal trainer
 * definem o plano de qualquer aluno; os demais veem o próprio, só leitura. O
 * backend decide o acesso (`nutrition_access.py`) e calcula tudo (motor
 * `domain/nutrition`); a tela só exibe.
 */
export function PlanoClient() {
  const currentUserQuery = useQuery({
    queryKey: ['current-user'],
    queryFn: fetchCurrentUser,
    staleTime: 60_000,
    retry: false,
  });
  const role = currentUserQuery.data?.role;
  const canManage = role === 'admin' || role === 'personal_trainer';

  return (
    <Stack gap="xl">
      <div>
        <NutritionSubnav active="/nutricao/plano" />
        <Title order={1} c={BRAND.textPrimary} mt="xs">
          Plano nutricional
        </Title>
        <Text c={BRAND.textMuted} size="md">
          {canManage
            ? 'Defina a estratégia e os parâmetros do aluno, confira a semana calculada pelo motor e publique.'
            : 'Metas calculadas pelo motor a partir do plano definido para você.'}
        </Text>
      </div>
      {currentUserQuery.isLoading && <Text c={BRAND.textMuted}>Carregando...</Text>}
      {currentUserQuery.isSuccess && (canManage ? <PlanManager /> : <MyPlanView />)}
    </Stack>
  );
}
