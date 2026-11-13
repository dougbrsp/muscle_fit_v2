'use client';

import { Button, Text } from '@mantine/core';
import { IconPlus } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { ApiError, apiFetch } from '@/lib/api';
import { formatDateOrDash } from '@/lib/format';
import { useIaControls } from '@/lib/screen-permissions';
import { planTypeDisplayLabel, type ApiListResponse, type WorkoutPlanListItem } from '@/lib/workout-plans-types';
import { BottomSheet, ListGroup, ListRow, ResponsiveDataList, ScreenHeader, StickyAction } from '../../ui';
import { SheetButton, useBackToClose } from './common';
import { GerarPlanoView } from './GerarPlanoView';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/** Planos de treino (celular): mesma query `['workout-plans']` e exclusão do desktop. */
export function PlanosScreen() {
  const qc = useQueryClient();
  const router = useRouter();
  const ia = useIaControls();
  const [generating, setGenerating] = useState(false);
  // Depois de aberta, a visão de geração fica montada (escondida) para voltar não perder o rascunho.
  const [generationStarted, setGenerationStarted] = useState(false);
  const [selected, setSelected] = useState<WorkoutPlanListItem | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const plansQuery = useQuery({
    queryKey: ['workout-plans'],
    queryFn: () => apiFetch<ApiListResponse<WorkoutPlanListItem>>('/workout-plans?limit=100'),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => apiFetch(`/workout-plans/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workout-plans'] });
      closeSheet();
    },
  });

  function closeSheet() {
    setSelected(null);
    setConfirmDelete(false);
    deleteMutation.reset();
  }

  useBackToClose(generating, () => setGenerating(false));

  const items = plansQuery.data?.items;

  return (
    <>
      {generationStarted && ia.canGerarPlano ? (
        <div hidden={!generating}>
          <GerarPlanoView onClose={() => setGenerating(false)} />
        </div>
      ) : null}
      <div hidden={generating}>
      <ScreenHeader title="Planos de treino" subtitle="Fichas geradas dos seus Modelos" />
      <ResponsiveDataList
        aria-label="Planos de treino"
        items={items}
        getKey={(p) => p.id}
        isLoading={plansQuery.isLoading}
        isError={plansQuery.isError}
        onRetry={() => void plansQuery.refetch()}
        errorTitle="Erro ao carregar planos"
        emptyTitle="Nenhum plano de treino gerado ainda"
        renderItem={(plan) => (
          <ListRow
            title={plan.name}
            subtitle={[
              formatDateOrDash(plan.plan_date) + (plan.plan_time ? ` ${plan.plan_time}` : ''),
              planTypeDisplayLabel(plan),
              plan.split_day ? `Treino ${plan.split_day}` : null,
            ]
              .filter(Boolean)
              .join(' · ')}
            value={`${plan.exercises_count} ${plan.exercises_count === 1 ? 'exercício' : 'exercícios'}`}
            onClick={() => setSelected(plan)}
          />
        )}
      />

      {ia.canGerarPlano ? (
        <StickyAction>
          <Button fullWidth size="lg" color="brand" mih={48} leftSection={<IconPlus size={20} />} onClick={() => {
              setGenerationStarted(true);
              setGenerating(true);
            }}>
            Gerar novo plano
          </Button>
        </StickyAction>
      ) : null}

      <BottomSheet
        opened={selected !== null}
        onClose={closeSheet}
        title={confirmDelete ? 'Confirmar exclusão' : (selected?.name ?? 'Plano')}
        footer={
          selected ? (
            confirmDelete ? (
              <>
                <SheetButton variant="default" disabled={deleteMutation.isPending} onClick={() => setConfirmDelete(false)}>
                  Cancelar
                </SheetButton>
                <SheetButton color="red" loading={deleteMutation.isPending} onClick={() => deleteMutation.mutate(selected.id)}>
                  Excluir
                </SheetButton>
              </>
            ) : (
              <>
                <SheetButton variant="default" color="red" onClick={() => setConfirmDelete(true)}>
                  Excluir
                </SheetButton>
                <SheetButton color="brand" onClick={() => router.push(`/planos-treino/${selected.id}`)}>
                  Abrir plano
                </SheetButton>
              </>
            )
          ) : undefined
        }
      >
        {selected && confirmDelete ? (
          <>
            <Text>
              Excluir o plano <b>{selected.name}</b>? Esta ação não pode ser desfeita.
            </Text>
            {deleteMutation.isError ? (
              <Text size="sm" c="red" role="alert">
                {errorMessage(deleteMutation.error, 'Não foi possível excluir o plano.')}
              </Text>
            ) : null}
          </>
        ) : selected ? (
          <ListGroup>
            <ListRow title="Data" value={formatDateOrDash(selected.plan_date)} />
            <ListRow title="Hora" value={selected.plan_time || '-'} />
            <ListRow title="Tipo" value={planTypeDisplayLabel(selected)} />
            <ListRow title="Academia" value={selected.gym_name || '-'} />
            <ListRow title="Exercícios" value={String(selected.exercises_count)} />
            <ListRow title="Status" value={selected.status} />
            {selected.goal ? <ListRow title="Objetivo" subtitle={selected.goal} /> : null}
          </ListGroup>
        ) : null}
      </BottomSheet>
      </div>
    </>
  );
}
