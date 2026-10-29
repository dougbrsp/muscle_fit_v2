'use client';

import { Alert, Button, Group, Loader, Stack, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconAlertTriangle, IconArrowLeft } from '@tabler/icons-react';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams, useRouter } from 'next/navigation';
import { useCallback, useMemo, useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import {
  DEFAULT_INTENT_DECLARATION,
  buildMethodologyVolumeQueryString,
  normalizeIntentDeclaration,
  sameIntentDeclaration,
} from '@/lib/methodology-volume';
import type {
  CountingModeComparison,
  MethodologyIntentDeclaration,
  MethodologyIntentDeclarationResponse,
  MethodologyVolumeEvaluation,
} from '@/lib/methodology-volume-types';
import type { ApiListResponse, MuscleGroupOption } from '@/lib/training-types-types';
import { useDynamicRouteParam } from '@/lib/use-dynamic-route-param';
import { BRAND } from '@/theme';
import { VolumeDiagnosticView } from './VolumeDiagnosticView';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

const INTENT_QUERY_KEY = 'workout-plan-template-methodology-intent';
const VOLUME_QUERY_KEY = 'workout-plan-template-methodology-volume';
const COUNTING_MODES_QUERY_KEY = 'workout-plan-template-methodology-volume-counting-modes';

/**
 * Dados e estado da tela `/diagnostico-volume/[templateId]` (Fase 4, PR G).
 *
 * - `useDynamicRouteParam`: em `output: 'export'` o `useParams()` devolve o
 *   slug do build (`placeholder`), nunca o id real — mesma armadilha de
 *   `/modelos-treino/[id]`.
 * - Intenção: a PERSISTIDA vem de `GET .../methodology-intent` (PR H). O
 *   rascunho local (`draft`) começa `null` = "seguir a persistida"; ao trocar o
 *   seletor a avaliação é refeita via `GET .../methodology-volume?intent=…`
 *   (o servidor ignora a persistida quando a query traz `intent`) — nada é
 *   gravado até "Salvar intenção no Modelo" (PUT).
 * - A tela é só leitura sobre o Modelo: nenhum slot é alterado aqui.
 */
export function VolumeDiagnosticClient() {
  const params = useParams<{ templateId: string }>();
  const routeId = useDynamicRouteParam(params.templateId);
  const router = useRouter();
  const qc = useQueryClient();

  const templateId = useMemo(() => {
    if (!routeId) return null;
    const id = Number(routeId);
    return Number.isInteger(id) && id > 0 ? id : null;
  }, [routeId]);
  const isRouteResolved = routeId !== null;

  const intentQuery = useQuery({
    queryKey: [INTENT_QUERY_KEY, templateId],
    queryFn: () => apiFetch<MethodologyIntentDeclarationResponse>(`/workout-plan-templates/${templateId}/methodology-intent`),
    enabled: templateId !== null,
    retry: false,
  });
  const persistedIntent: MethodologyIntentDeclaration | null = intentQuery.data
    ? normalizeIntentDeclaration(intentQuery.data.intent, intentQuery.data.target_group_ids)
    : null;

  const muscleGroupsQuery = useQuery({
    // Mesma chave do builder e do catálogo: o cache é compartilhado.
    queryKey: ['muscle-groups-catalog'],
    queryFn: () => apiFetch<ApiListResponse<MuscleGroupOption>>('/muscle-groups'),
  });
  const muscleGroupOptions = useMemo(
    () => (muscleGroupsQuery.data?.items ?? []).map((group) => ({ value: String(group.id), label: group.name })),
    [muscleGroupsQuery.data],
  );

  // null = seguir a intenção persistida (sem "e se").
  const [draft, setDraft] = useState<MethodologyIntentDeclaration | null>(null);
  const baseline = persistedIntent ?? DEFAULT_INTENT_DECLARATION;
  const effectiveDraft = draft ?? baseline;
  const isSimulating = draft !== null && !sameIntentDeclaration(draft, baseline);
  const simulated = isSimulating ? normalizeIntentDeclaration(effectiveDraft.intent, effectiveDraft.target_group_ids) : null;
  const queryString = buildMethodologyVolumeQueryString(simulated);

  const evaluationQuery = useQuery({
    queryKey: [VOLUME_QUERY_KEY, templateId, queryString],
    queryFn: () =>
      apiFetch<MethodologyVolumeEvaluation>(`/workout-plan-templates/${templateId}/methodology-volume${queryString}`),
    enabled: templateId !== null,
    retry: false,
    placeholderData: keepPreviousData,
  });

  // Lote 3: o Modelo salvo nos dois modos de contagem, lado a lado (só Modo B;
  // o backend calcula tudo). A intenção é a persistida: invalida ao salvá-la.
  const isModeB = evaluationQuery.data?.volume_mode === 'equipment_series';
  const comparisonQuery = useQuery({
    queryKey: [COUNTING_MODES_QUERY_KEY, templateId],
    queryFn: () =>
      apiFetch<CountingModeComparison>(`/workout-plan-templates/${templateId}/methodology-volume/counting-modes`),
    enabled: templateId !== null && isModeB,
    retry: false,
  });

  const saveMutation = useMutation({
    mutationFn: (declaration: MethodologyIntentDeclaration) =>
      apiFetch<MethodologyIntentDeclarationResponse>(`/workout-plan-templates/${templateId}/methodology-intent`, {
        method: 'PUT',
        body: JSON.stringify(declaration),
      }),
    onSuccess: (saved) => {
      qc.setQueryData([INTENT_QUERY_KEY, templateId], saved);
      setDraft(null);
      // A avaliação "sem query" passa a refletir a intenção nova; o builder
      // (avisos do GET do Modelo) também.
      void qc.invalidateQueries({ queryKey: [VOLUME_QUERY_KEY, templateId] });
      void qc.invalidateQueries({ queryKey: [COUNTING_MODES_QUERY_KEY, templateId] });
      void qc.invalidateQueries({ queryKey: ['workout-plan-template', templateId] });
      notifications.show({ color: 'green', message: 'Intenção declarada salva no Modelo.' });
    },
    onError: (error) => {
      notifications.show({
        color: 'red',
        title: 'Não foi possível salvar a intenção',
        message: errorMessage(error, 'Erro ao gravar a intenção declarada.'),
      });
    },
  });

  const [expandedKeys, setExpandedKeys] = useState<ReadonlySet<number>>(() => new Set());
  const toggleGroup = useCallback((key: number) => {
    setExpandedKeys((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  if (!isRouteResolved || (templateId !== null && evaluationQuery.isPending)) {
    return (
      <Group justify="center" py="xl">
        <Loader color="brand" />
      </Group>
    );
  }

  if (templateId === null || (evaluationQuery.isError && !evaluationQuery.data)) {
    const forbidden = evaluationQuery.error instanceof ApiError && evaluationQuery.error.status === 403;
    return (
      <Stack gap="md">
        <Title order={1} c={BRAND.textPrimary}>
          Diagnóstico de volume
        </Title>
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {templateId === null
            ? 'Endereço inválido: informe o id numérico de um Modelo de treino.'
            : forbidden
              ? 'Acesso restrito a administradores e personal trainers.'
              : errorMessage(evaluationQuery.error, 'Não foi possível avaliar este Modelo (talvez não exista mais).')}
        </Alert>
        <Button
          variant="light"
          color="brand"
          leftSection={<IconArrowLeft size={16} />}
          onClick={() => router.push('/modelos-treino')}
        >
          Voltar para modelos de treino
        </Button>
      </Stack>
    );
  }

  if (!evaluationQuery.data) return null;

  return (
    <VolumeDiagnosticView
      evaluation={evaluationQuery.data}
      persistedIntent={persistedIntent}
      persistedIntentError={
        intentQuery.isError ? errorMessage(intentQuery.error, 'intenção persistida indisponível') : null
      }
      draft={effectiveDraft}
      isSimulating={isSimulating}
      muscleGroupOptions={muscleGroupOptions}
      onDraftChange={setDraft}
      onSave={() => saveMutation.mutate(normalizeIntentDeclaration(effectiveDraft.intent, effectiveDraft.target_group_ids))}
      onDiscardSimulation={() => setDraft(null)}
      isSaving={saveMutation.isPending}
      saveError={saveMutation.isError ? errorMessage(saveMutation.error, 'Erro ao gravar a intenção declarada.') : null}
      isRefreshing={evaluationQuery.isFetching && evaluationQuery.isPlaceholderData}
      refreshError={evaluationQuery.isError ? errorMessage(evaluationQuery.error, 'erro de rede') : null}
      expandedKeys={expandedKeys}
      onToggleGroup={toggleGroup}
      comparison={comparisonQuery.data}
      comparisonPending={comparisonQuery.isPending}
      comparisonError={comparisonQuery.isError ? errorMessage(comparisonQuery.error, 'erro de rede') : null}
    />
  );
}
