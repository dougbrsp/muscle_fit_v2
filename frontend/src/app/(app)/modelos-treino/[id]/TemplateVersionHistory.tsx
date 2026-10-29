'use client';

import { Alert, Badge, Button, Group, List, Modal, Paper, Stack, Text, Title } from '@mantine/core';
import { IconAlertTriangle, IconEye, IconHistory, IconRotate } from '@tabler/icons-react';
import { useMediaQuery } from '@mantine/hooks';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { apiFetch } from '@/lib/api';
import {
  readRevertError,
  versionHistoryRows,
  type RevertError,
  type TemplateVersionListResponse,
  type VersionHistoryRow,
} from '@/lib/template-versions';
import { REVERT_DISCARDS_DRAFT_WARNING } from '@/lib/template-drafts';
import type { WorkoutPlanTemplateDetail, WorkoutPlanTemplateVersionDetail } from '@/lib/workout-plan-templates-types';
import { goalBadgeColor, goalOption, isDefaultGoal, useTrainingGoals, type TrainingGoalsResponse } from '@/lib/training-goals';
import { BRAND } from '@/theme';
import { TemplateVersionSummary } from './TemplateVersionSummary';

/**
 * Histórico de versões do Modelo (PR4 do versionamento). Cada edição
 * estrutural gera uma versão nova; aqui a pessoa vê a lista, abre uma versão
 * em modo somente leitura e reverte para ela (o que cria a versão n+1 com o
 * conteúdo da escolhida — nada é apagado).
 *
 * `TemplateVersionHistoryList` e `RevertErrorAlert` são só apresentação
 * (testados por SSR em TemplateVersionHistory.test.ts); o painel cuida das
 * consultas, da confirmação e da reversão.
 */

type ListProps = {
  rows: VersionHistoryRow[];
  canManage: boolean;
  onView: (versionId: number) => void;
  onRevert: (row: VersionHistoryRow) => void;
  /** M97 F3: objetivos de `GET /training-goals` para o badge (ausente = sem badge). */
  trainingGoals?: TrainingGoalsResponse;
};

export function TemplateVersionHistoryList({ rows, canManage, onView, onRevert, trainingGoals }: ListProps) {
  if (rows.length === 0) {
    return (
      <Text size="sm" c={BRAND.textMuted}>
        Nenhuma versão registrada ainda.
      </Text>
    );
  }
  return (
    <Stack gap="xs">
      {rows.map((row) => (
        <Paper
          key={row.id}
          withBorder
          radius="md"
          p="sm"
          style={row.isCurrent ? { borderColor: BRAND.orange, borderWidth: 2 } : undefined}
          data-current={row.isCurrent ? 'true' : undefined}
        >
          <Stack gap={6}>
            <Group gap={8} wrap="wrap">
              <Badge variant={row.isCurrent ? 'filled' : 'light'} color="brand" size="lg">
                {row.label}
              </Badge>
              {row.isCurrent && (
                <Badge variant="light" color="green">
                  Vigente
                </Badge>
              )}
              {row.revertedFrom && (
                <Badge variant="light" color="grape">
                  {row.revertedFrom}
                </Badge>
              )}
              {trainingGoals && row.trainingGoal && !isDefaultGoal(trainingGoals, row.trainingGoal) && (
                <Badge
                  variant="light"
                  color={goalBadgeColor(goalOption(trainingGoals, row.trainingGoal))}
                  data-testid="version-goal-badge"
                >
                  {goalOption(trainingGoals, row.trainingGoal)?.label}
                </Badge>
              )}
            </Group>
            <Text size="sm" c={BRAND.textMuted} style={{ overflowWrap: 'anywhere' }}>
              {row.when} · {row.author}
            </Text>
            {row.summary && (
              <Text size="sm" style={{ overflowWrap: 'anywhere' }}>
                {row.summary}
              </Text>
            )}
            <Group gap="xs" wrap="wrap">
              <Button
                size="xs"
                variant="light"
                color="brand"
                leftSection={<IconEye size={14} />}
                onClick={() => onView(row.id)}
              >
                Ver
              </Button>
              {canManage && !row.isCurrent && (
                <Button
                  size="xs"
                  variant="light"
                  color="orange"
                  leftSection={<IconRotate size={14} />}
                  onClick={() => onRevert(row)}
                >
                  Reverter para esta versão
                </Button>
              )}
            </Group>
          </Stack>
        </Paper>
      ))}
    </Stack>
  );
}

export function RevertErrorAlert({ error }: { error: RevertError }) {
  return (
    <Alert color="red" icon={<IconAlertTriangle size={16} />} title="Reversão bloqueada">
      <Stack gap={4}>
        <Text size="sm">{error.message}</Text>
        {error.errors.length > 0 && (
          <List size="sm" spacing={2}>
            {error.errors.map((message, index) => (
              <List.Item key={index} style={{ overflowWrap: 'anywhere' }}>
                {message}
              </List.Item>
            ))}
          </List>
        )}
      </Stack>
    </Alert>
  );
}

type PanelProps = {
  templateId: number;
  canManage: boolean;
  /** Há rascunho não publicado (067): reverter o descarta, e a confirmação avisa. */
  hasDraft?: boolean;
  /** Depois de reverter: o builder recarrega o Modelo (e este painel recarrega a lista). */
  onReverted: (detail: WorkoutPlanTemplateDetail) => void | Promise<void>;
};

export function versionsQueryKey(templateId: number) {
  return ['workout-plan-template-versions', templateId] as const;
}

export function TemplateVersionHistoryPanel({ templateId, canManage, hasDraft = false, onReverted }: PanelProps) {
  // No iPhone o modal ocupa a tela inteira (sem scroll horizontal da grade).
  const isMobile = useMediaQuery('(max-width: 48em)');
  const [viewingId, setViewingId] = useState<number | null>(null);
  const [confirming, setConfirming] = useState<VersionHistoryRow | null>(null);
  const [revertError, setRevertError] = useState<RevertError | null>(null);

  const trainingGoalsQuery = useTrainingGoals();
  const versionsQuery = useQuery({
    queryKey: versionsQueryKey(templateId),
    queryFn: () => apiFetch<TemplateVersionListResponse>(`/workout-plan-templates/${templateId}/versions`),
  });

  const versionQuery = useQuery({
    queryKey: ['workout-plan-template-version', templateId, viewingId],
    queryFn: () =>
      apiFetch<WorkoutPlanTemplateVersionDetail>(`/workout-plan-templates/${templateId}/versions/${viewingId}`),
    enabled: viewingId != null,
  });

  const revertMutation = useMutation({
    mutationFn: (versionId: number) =>
      apiFetch<WorkoutPlanTemplateDetail>(`/workout-plan-templates/${templateId}/versions/${versionId}/revert`, {
        method: 'POST',
      }),
    onSuccess: async (detail) => {
      setConfirming(null);
      setRevertError(null);
      await onReverted(detail);
      await versionsQuery.refetch();
    },
    onError: (error: unknown) => setRevertError(readRevertError(error)),
  });

  const rows = versionHistoryRows(versionsQuery.data?.items ?? []);

  return (
    <Paper withBorder radius="md" p="md">
      <Stack gap="sm">
        <Group gap={8}>
          <IconHistory size={18} />
          <Title order={3} c={BRAND.textPrimary}>
            Histórico de versões
          </Title>
        </Group>
        <Text size="sm" c={BRAND.textMuted}>
          Cada mudança estrutural (slots, itens, modo, tipo de treino, grade ou intenção) gera uma versão nova; as
          anteriores ficam guardadas e o histórico do aluno continua sendo lido com a versão da época.
        </Text>
        {versionsQuery.isLoading && <Text size="sm">Carregando histórico...</Text>}
        {versionsQuery.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            Não foi possível carregar o histórico de versões.
          </Alert>
        )}
        {versionsQuery.isSuccess && (
          <TemplateVersionHistoryList
            rows={rows}
            trainingGoals={trainingGoalsQuery.data}
            canManage={canManage}
            onView={setViewingId}
            onRevert={(row) => {
              setRevertError(null);
              setConfirming(row);
            }}
          />
        )}
      </Stack>

      <Modal
        opened={viewingId != null}
        onClose={() => setViewingId(null)}
        title={
          versionQuery.data
            ? `Versão ${versionQuery.data.version.number} (somente leitura)`
            : 'Versão (somente leitura)'
        }
        size="xl"
        fullScreen={Boolean(isMobile)}
      >
        {versionQuery.isLoading && <Text size="sm">Carregando versão...</Text>}
        {versionQuery.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            Não foi possível carregar esta versão.
          </Alert>
        )}
        {versionQuery.data && <TemplateVersionSummary detail={versionQuery.data} />}
      </Modal>

      <Modal
        opened={confirming != null}
        onClose={() => {
          setConfirming(null);
          setRevertError(null);
        }}
        title="Reverter o modelo"
      >
        <Stack gap="sm">
          <Text size="sm">
            O modelo voltará ao conteúdo da <b>{confirming?.label}</b>. Isso cria uma versão nova — nada é apagado, e a
            versão atual continua no histórico.
          </Text>
          {hasDraft && (
            <Alert color="yellow" icon={<IconAlertTriangle size={16} />} data-testid="revert-draft-warning">
              {REVERT_DISCARDS_DRAFT_WARNING}
            </Alert>
          )}
          {revertError && <RevertErrorAlert error={revertError} />}
          <Group justify="flex-end" gap="xs" wrap="wrap">
            <Button
              variant="default"
              onClick={() => {
                setConfirming(null);
                setRevertError(null);
              }}
            >
              Cancelar
            </Button>
            <Button
              color="orange"
              loading={revertMutation.isPending}
              onClick={() => confirming && revertMutation.mutate(confirming.id)}
            >
              Reverter para {confirming?.label}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Paper>
  );
}
