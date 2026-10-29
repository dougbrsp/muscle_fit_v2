'use client';

import { Alert, Badge, Button, Card, Group, Stack, Switch, Text } from '@mantine/core';
import { IconFeather } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch, ApiError } from '@/lib/api';
import {
  DELOAD_CURRENT_QUERY_KEY,
  DELOAD_LOCKED_MESSAGE,
  deloadBannerTitle,
  deloadRuleText,
  periodRangeLabel,
  sessionDeloadQueryKey,
  startPeriodLabel,
  PAIR_SUGGESTION_QUERY_ROOT,
  WORKOUT_MODE_PLAN_QUERY_ROOT,
  type DeloadCurrentResponse,
  type DeloadPeriod,
  type SessionDeloadStatus,
} from '@/lib/deload';
import { BRAND } from '@/theme';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/** `GET /deload-periods/current`: deload de hoje (fuso do usuário) e os números do dono. */
export function useCurrentDeload() {
  return useQuery({
    queryKey: DELOAD_CURRENT_QUERY_KEY,
    queryFn: () => apiFetch<DeloadCurrentResponse>('/deload-periods/current'),
    staleTime: 60_000,
  });
}

/**
 * Início do Modo Treino (M97 F3): toggle "Sessão de deload" (vai como
 * `deload: true` no `start`) e a semana de deload por período
 * (`/deload-periods`: criar com a duração default do dono, ver a atual,
 * encerrar). Com período ativo hoje, toda sessão já nasce de deload; o
 * toggle fica ligado e travado.
 */
export function DeloadStartPanel({ manual, onManualChange }: { manual: boolean; onManualChange: (value: boolean) => void }) {
  const qc = useQueryClient();
  const currentQuery = useCurrentDeload();
  const current = currentQuery.data;
  const period = current?.period ?? null;

  const createMutation = useMutation({
    mutationFn: () => apiFetch<{ item: DeloadPeriod }>('/deload-periods', { method: 'POST', body: JSON.stringify({}) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: DELOAD_CURRENT_QUERY_KEY }),
  });
  const cancelMutation = useMutation({
    mutationFn: (id: number) => apiFetch(`/deload-periods/${id}/cancel`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: DELOAD_CURRENT_QUERY_KEY }),
  });

  return (
    <Card withBorder radius="xl" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }} data-testid="deload-start-panel">
      <Stack gap="sm">
        <Group gap="sm" wrap="nowrap" align="flex-start">
          <IconFeather size={28} color="var(--mantine-color-cyan-6)" />
          <div style={{ minWidth: 0 }}>
            <Group gap={6} wrap="wrap">
              <Text fw={800} c={BRAND.textPrimary}>
                Deload
              </Text>
              {period && (
                <Badge color="cyan" variant="light" data-testid="deload-period-badge">
                  Semana de deload: {periodRangeLabel(period)}
                </Badge>
              )}
            </Group>
            <Text size="sm" c={BRAND.textMuted}>
              {deloadRuleText(current?.constants)}
            </Text>
          </div>
        </Group>
        <Switch
          color="cyan"
          label={period ? 'Sessão de deload (semana de deload ativa)' : 'Sessão de deload'}
          checked={manual || Boolean(period)}
          disabled={Boolean(period)}
          onChange={(e) => onManualChange(e.currentTarget.checked)}
          data-testid="deload-session-toggle"
        />
        <Group gap="xs" wrap="wrap">
          {period ? (
            <Button
              size="xs"
              variant="light"
              color="gray"
              loading={cancelMutation.isPending}
              onClick={() => cancelMutation.mutate(period.id)}
              data-testid="deload-period-cancel"
            >
              Encerrar semana de deload
            </Button>
          ) : (
            <Button
              size="xs"
              variant="light"
              color="cyan"
              loading={createMutation.isPending}
              disabled={!current}
              onClick={() => createMutation.mutate()}
              data-testid="deload-period-start"
            >
              {startPeriodLabel(current?.constants)}
            </Button>
          )}
        </Group>
        {(createMutation.isError || cancelMutation.isError) && (
          <Alert color="red" variant="light" p="xs">
            {errorMessage(createMutation.error ?? cancelMutation.error, 'Não foi possível atualizar a semana de deload.')}
          </Alert>
        )}
      </Stack>
    </Card>
  );
}

/**
 * Banner da sessão em andamento (M97 F3): mostra quando a sessão é de deload
 * (por período ou avulsa) e permite ligar/desligar só enquanto não há série
 * gravada (`PATCH /workout-sessions/{id}/meta` com `deload`; o servidor
 * confere e devolve 409 depois da 1ª série).
 */
export function DeloadSessionBanner({ sessionId }: { sessionId: number }) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: sessionDeloadQueryKey(sessionId),
    queryFn: () => apiFetch<{ session_id: number; deload: SessionDeloadStatus }>(`/workout-sessions/${sessionId}/deload`),
    staleTime: 30_000,
  });
  const constantsQuery = useCurrentDeload();
  const toggleMutation = useMutation({
    mutationFn: (value: boolean) =>
      apiFetch<{ deload?: SessionDeloadStatus }>(`/workout-sessions/${sessionId}/meta`, {
        method: 'PATCH',
        body: JSON.stringify({ deload: value }),
      }),
    onSuccess: (data) => {
      if (data.deload) qc.setQueryData(sessionDeloadQueryKey(sessionId), { session_id: sessionId, deload: data.deload });
      // O briefing traz a prescrição de deload da sessão.
      qc.invalidateQueries({ queryKey: ['exercise-briefing'] });
      // A ficha da sessão muda (prescrição de deload desde a 1ª série).
      qc.invalidateQueries({ queryKey: [WORKOUT_MODE_PLAN_QUERY_ROOT] });
      // E a sugestão do par trocado nesta sessão (M97-D2).
      qc.invalidateQueries({ queryKey: [PAIR_SUGGESTION_QUERY_ROOT] });
    },
    onError: () => qc.invalidateQueries({ queryKey: sessionDeloadQueryKey(sessionId) }),
  });

  const status = query.data?.deload;
  if (!status) return null;
  const title = deloadBannerTitle(status);
  const canToggle = Boolean(status.can_toggle);
  // Sessão normal com série já gravada: nada a mostrar.
  if (!title && !canToggle) return null;

  const lockedError =
    toggleMutation.error instanceof ApiError && toggleMutation.error.status === 409 ? DELOAD_LOCKED_MESSAGE : null;

  return (
    <Alert
      color="cyan"
      variant={title ? 'light' : 'outline'}
      icon={<IconFeather size={16} />}
      py={6}
      data-testid="deload-session-banner"
      title={title ?? undefined}
    >
      <Stack gap={4}>
        {title && (
          <Text size="xs" c={BRAND.textMuted}>
            {deloadRuleText(constantsQuery.data?.constants)}
          </Text>
        )}
        {canToggle && (
          <Switch
            size="xs"
            color="cyan"
            label={title ? 'Sessão de deload (até a 1ª série)' : 'Marcar como sessão de deload (até a 1ª série)'}
            checked={status.active}
            disabled={toggleMutation.isPending}
            onChange={(e) => toggleMutation.mutate(e.currentTarget.checked)}
            data-testid="deload-session-switch"
          />
        )}
        {toggleMutation.isError && (
          <Text size="xs" c="red">
            {lockedError ?? errorMessage(toggleMutation.error, 'Não foi possível mudar o deload da sessão.')}
          </Text>
        )}
      </Stack>
    </Alert>
  );
}
