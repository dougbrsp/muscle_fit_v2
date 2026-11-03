'use client';

import { Alert, Badge, Button, Group, Stack, Text } from '@mantine/core';
import { IconAlertTriangle, IconCalendarCheck } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import {
  acceptCheckin,
  checkinKeys,
  declineCheckin,
  fetchCheckin,
  formatAdjustment,
  shortDay,
  verdictTone,
  weekRangeLabel,
  type Checkin,
} from '@/lib/nutrition-checkin';
import { nutritionErrorInfo } from '@/lib/nutrition-api';
import { targetsChangedKeys } from '@/lib/nutrition-plan-form';
import { MOBILE_TOKENS } from '@/mobile/tokens';

type ViewProps = {
  checkin: Checkin | null | undefined;
  busy?: boolean;
  error?: string | null;
  onAccept?: () => void;
  onDecline?: () => void;
};

/** A linha curta do card: o que mudaria (proposta) ou o primeiro motivo/o que faltou (sem mudança). Tudo vem do motor. */
function summaryLine(checkin: Checkin): string {
  const ajuste = formatAdjustment(checkin.proposal?.delta_kcal ?? checkin.adjustment_kcal);
  if (checkin.can_respond && ajuste) {
    return `Proposta: ${ajuste} por dia nas suas metas. Vale até ${shortDay(checkin.expires_on)}; sem resposta, as metas atuais seguem.`;
  }
  const motivo = checkin.missing_items[0] ?? checkin.reason_items[0];
  return motivo ? motivo.label : 'As metas continuam as mesmas.';
}

/** Apresentação do card (sem consulta): testável por SSR. Some quando o motor diz que não há o que mostrar. */
export function CheckinCardView({ checkin, busy = false, error = null, onAccept, onDecline }: ViewProps) {
  if (!checkin || !checkin.show_card) return null;
  return (
    <section
      aria-label="Check-in semanal"
      style={{
        border: `1px solid ${MOBILE_TOKENS.accent}`,
        borderRadius: 12,
        background: MOBILE_TOKENS.accentSoft + '1A',
        padding: 12,
        marginBottom: 12,
      }}
    >
      <Stack gap={8}>
        <Group justify="space-between" wrap="nowrap" gap="xs">
          <Group gap={6} wrap="nowrap">
            <IconCalendarCheck size={18} color={MOBILE_TOKENS.accent} aria-hidden />
            <Text size="sm" fw={700} c={MOBILE_TOKENS.text}>
              Check-in semanal
            </Text>
          </Group>
          <Badge color={verdictTone(checkin.verdict)} variant="light">
            {checkin.verdict_label}
          </Badge>
        </Group>
        <Text size="xs" c={MOBILE_TOKENS.textMuted}>
          Semana de {weekRangeLabel(checkin)}
        </Text>
        <Text size="sm" c={MOBILE_TOKENS.text}>
          {summaryLine(checkin)}
        </Text>
        {error && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />} role="alert">
            {error}
          </Alert>
        )}
        {checkin.can_respond ? (
          <Stack gap={6}>
            <Button color="brand" fullWidth loading={busy} onClick={onAccept} style={{ minHeight: MOBILE_TOKENS.touchTarget }}>
              Aceitar as novas metas
            </Button>
            <Group grow gap="xs">
              <Button
                component={Link}
                href="/nutricao/checkin"
                variant="light"
                color="brand"
                style={{ minHeight: MOBILE_TOKENS.touchTarget }}
              >
                Ver a semana
              </Button>
              <Button variant="subtle" color="gray" disabled={busy} onClick={onDecline} style={{ minHeight: MOBILE_TOKENS.touchTarget }}>
                Manter as atuais
              </Button>
            </Group>
          </Stack>
        ) : (
          <Button
            component={Link}
            href="/nutricao/checkin"
            variant="light"
            color="brand"
            fullWidth
            style={{ minHeight: MOBILE_TOKENS.touchTarget }}
          >
            Ver a semana
          </Button>
        )}
      </Stack>
    </section>
  );
}

/**
 * Card do check-in semanal (N7) para o `checkinSlot` da home mobile e para o topo do diário. Lê
 * `GET /nutrition/checkin`: a primeira abertura depois do dia do check-in gera a semana no servidor; as
 * seguintes são leituras leves. Aceitar publica a nova versão do plano e invalida o plano, o diário e o painel.
 * Sem check-in a mostrar (ou sem rede) o card não aparece.
 */
export function CheckinCard() {
  const qc = useQueryClient();
  const consulta = useQuery({
    queryKey: checkinKeys.current(),
    queryFn: () => fetchCheckin(),
    staleTime: 60_000,
    retry: false,
  });
  const checkin = consulta.data?.checkin ?? null;
  const depois = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: checkinKeys.all }),
      ...targetsChangedKeys().map((queryKey) => qc.invalidateQueries({ queryKey })),
    ]);
  };
  const aceitar = useMutation({ mutationFn: (id: number) => acceptCheckin(id), onSuccess: depois, onError: depois });
  const recusar = useMutation({ mutationFn: (id: number) => declineCheckin(id), onSuccess: depois, onError: depois });
  const erro = aceitar.isError
    ? nutritionErrorInfo(aceitar.error, 'Não foi possível aceitar agora.').message
    : recusar.isError
      ? nutritionErrorInfo(recusar.error, 'Não foi possível responder agora.').message
      : null;
  return (
    <CheckinCardView
      checkin={checkin}
      busy={aceitar.isPending || recusar.isPending}
      error={erro}
      onAccept={() => checkin && aceitar.mutate(checkin.id)}
      onDecline={() => checkin && recusar.mutate(checkin.id)}
    />
  );
}
