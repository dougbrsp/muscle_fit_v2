'use client';

import { Alert, Anchor, Badge, Button, Group, Loader, Paper, Stack, Text, Title } from '@mantine/core';
import { IconAlertTriangle, IconClock, IconTemplate } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import type { ComponentProps } from 'react';
import { SafeMarkdownLazy as SafeMarkdown } from '@/components/LazyMarkdown';
import { apiFetch } from '@/lib/api';
import type {
  CardioApplyResponse,
  CardioRecommendationDetail,
  CardioRecommendationSessionView,
  CardioRecommendationView,
} from '@/lib/analytics-types';
import {
  applicableDates,
  applyErrorMessage,
  applyResultColor,
  planActionLabel,
  templateEditorPath,
} from '@/lib/cardio-recommendation-apply';
import { BRAND } from '@/theme';

type MarkdownComponents = ComponentProps<typeof SafeMarkdown>['components'];

type Props = {
  recommendationId: number;
  markdown: string;
  /** `prompt_version` da recomendação: sem ele (antiga), só o markdown, sem buscar o detalhe. */
  promptVersion?: string | null;
  markdownComponents?: MarkdownComponents;
};

/**
 * Recomendação de cardio (M98 C3b). Estruturada (`cardio-agent-v3`): sessões
 * com data, prescrição, tempo total, justificativa e sinais; ajustes e cortes
 * do motor com rótulo; e "Aplicar no Modelo" (por sessão ou todas), que grava
 * no RASCUNHO do Modelo, nunca publica. Antiga (JSON nulo): o markdown de
 * sempre. Tudo vem pronto do backend (`recommendation_view`).
 */
export function CardioRecommendationPanel({ recommendationId, markdown, promptVersion, markdownComponents }: Props) {
  const structured = Boolean(promptVersion);
  const detailQuery = useQuery({
    queryKey: ['cardio-recommendation', recommendationId],
    queryFn: () => apiFetch<CardioRecommendationDetail>(`/cardio-recommendations/${recommendationId}`),
    enabled: structured,
  });

  const fallback = <SafeMarkdown components={markdownComponents}>{markdown}</SafeMarkdown>;
  if (!structured) return fallback;
  if (detailQuery.isLoading) {
    return (
      <Group>
        <Loader size="sm" color="brand" />
        <Text c={BRAND.textMuted}>Carregando sessões...</Text>
      </Group>
    );
  }
  const view = detailQuery.data?.recommendation_view;
  if (!view) return fallback;
  return <StructuredRecommendation recommendationId={recommendationId} view={view} />;
}

export function StructuredRecommendation({ recommendationId, view }: { recommendationId: number; view: CardioRecommendationView }) {
  const qc = useQueryClient();
  const allDates = applicableDates(view);
  const applyMutation = useMutation({
    mutationFn: (dates: string[]) =>
      apiFetch<CardioApplyResponse>(`/cardio-recommendations/${recommendationId}/apply`, {
        method: 'POST',
        body: JSON.stringify({ dates }),
      }),
    onSuccess: async (result) => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['workout-plan-template', result.template_id] }),
        qc.invalidateQueries({ queryKey: ['workout-plan-template-draft', result.template_id] }),
        qc.invalidateQueries({ queryKey: ['workout-plan-templates'] }),
      ]);
    },
  });
  const result = applyMutation.data;

  return (
    <Stack gap="md">
      {view.summary && <Text c={BRAND.textPrimary}>{view.summary}</Text>}
      {view.objective && (
        <Text size="sm" c={BRAND.textMuted}>
          <Text component="span" fw={700} c={BRAND.textPrimary}>
            Objetivo:{' '}
          </Text>
          {view.objective}
        </Text>
      )}

      <Group justify="space-between" wrap="wrap">
        <Title order={4} c={BRAND.textPrimary}>
          Sessões recomendadas
        </Title>
        {view.can_apply && allDates.length > 1 && (
          <Button
            size="xs"
            color="brand"
            variant="light"
            leftSection={<IconTemplate size={14} />}
            loading={applyMutation.isPending}
            onClick={() => applyMutation.mutate(allDates)}
          >
            Aplicar todas no Modelo
          </Button>
        )}
      </Group>

      {view.sessions.length === 0 && (
        <Text size="sm" c={BRAND.textMuted}>
          Nenhum cardio adicional recomendado para o período.
        </Text>
      )}
      {view.sessions.map((session) => (
        <SessionCard
          key={session.date}
          session={session}
          applying={applyMutation.isPending}
          onApply={() => applyMutation.mutate([session.date])}
        />
      ))}

      {applyMutation.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} title="Não foi possível aplicar no Modelo">
          {applyErrorMessage(applyMutation.error)}
        </Alert>
      )}
      {result && (
        <Alert color={result.draft_saved ? 'green' : 'gray'} variant="light" title="Rascunho do Modelo">
          <Stack gap={4}>
            {result.results.map((item) => (
              <Text key={`${item.date}-${item.code}`} size="sm" c={applyResultColor(item.status)}>
                {item.message}
              </Text>
            ))}
            {result.has_draft && (
              <Text size="sm" c={BRAND.textPrimary}>
                Nada foi publicado.{' '}
                <Anchor component={Link} href={templateEditorPath(result.template_id)} c={BRAND.orange}>
                  Abrir o editor do Modelo
                </Anchor>{' '}
                para revisar e publicar.
              </Text>
            )}
          </Stack>
        </Alert>
      )}

      {view.notes.length > 0 && (
        <Stack gap={4}>
          <Title order={5} c={BRAND.textPrimary}>
            Ajustes do motor
          </Title>
          {view.notes.map((note, index) => (
            <Group key={`${note.code}-${note.date}-${index}`} gap={6} wrap="nowrap" align="flex-start">
              <Badge size="xs" variant="light" color={note.action === 'rejected' ? 'red' : 'yellow'}>
                {note.action === 'rejected' ? 'Cortada' : 'Ajustada'}
              </Badge>
              <Text size="sm" c={BRAND.textPrimary}>
                {note.date_label}: {note.label}
                {note.detail_label ? ` (${note.detail_label})` : ''}
              </Text>
            </Group>
          ))}
        </Stack>
      )}

      {view.confidence_label && (
        <Text size="sm" c={BRAND.textMuted}>
          <Text component="span" fw={700} c={BRAND.textPrimary}>
            Grau de confiança:{' '}
          </Text>
          {view.confidence_label}
          {view.confidence_reason ? `. ${view.confidence_reason}` : ''}
        </Text>
      )}
    </Stack>
  );
}

function SessionCard({
  session,
  applying,
  onApply,
}: {
  session: CardioRecommendationSessionView;
  applying: boolean;
  onApply: () => void;
}) {
  const link = planActionLabel(session.plan_action, session.slot_name);
  return (
    <Paper withBorder radius="md" p="md" style={{ background: BRAND.bg, borderColor: BRAND.border }}>
      <Stack gap={6}>
        <Group justify="space-between" wrap="wrap" gap="xs">
          <Group gap="xs" wrap="wrap">
            <Badge color="brand" variant="light">
              {session.date_label}
            </Badge>
            <Group gap={4} wrap="nowrap">
              <IconClock size={14} color={BRAND.textMuted} />
              <Text size="sm" c={BRAND.textMuted}>
                {session.total_minutes_label} no total
              </Text>
            </Group>
          </Group>
          {session.applicable && (
            <Button size="xs" variant="subtle" color="brand" loading={applying} onClick={onApply}>
              Aplicar no Modelo
            </Button>
          )}
        </Group>
        <Text fw={700} c={BRAND.textPrimary}>
          {session.prescription_text}
        </Text>
        {session.rationale && (
          <Text size="sm" c={BRAND.textMuted}>
            {session.rationale}
          </Text>
        )}
        {(session.signals.length > 0 || session.adjustment_labels.length > 0) && (
          <Group gap={4} wrap="wrap">
            {session.signals.map((signal) => (
              <Badge key={signal} size="sm" variant="outline" color="gray">
                {signal}
              </Badge>
            ))}
            {session.adjustment_labels.map((label) => (
              <Badge key={label} size="sm" variant="light" color="yellow">
                {label}
              </Badge>
            ))}
          </Group>
        )}
        {link && (
          <Text size="xs" c={BRAND.textMuted}>
            {link}
          </Text>
        )}
      </Stack>
    </Paper>
  );
}
