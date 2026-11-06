'use client';

import { Badge, Group, Stack, Text } from '@mantine/core';
import { fmtDate, fmtDateTime, fmtDecimal } from '@/lib/dashboard-format';
import { adaptiveLabel, type AdaptiveActivity, type AdaptiveEnergyDay } from '@/lib/nutrition-dashboard';
import { formatKcal } from '@/lib/nutrition-format';
import { BRAND } from '@/theme';

/** Códigos do estado do dia (`sync_status` do motor); único lugar com os literais. */
const DAY_SYNC = { confirmed: 'confirmed', pending: 'pending' } as const;

function ActivityRow({ activity }: { activity: AdaptiveActivity }) {
  const duplicata = (activity.possible_duplicate_of?.length ?? 0) > 0 || activity.duplicate_of != null;
  const foraDoTotal = activity.counts_in_total === false;
  const duracao = activity.duration_minutes == null ? null : `${fmtDecimal(activity.duration_minutes, 0)} min`;
  const horario = activity.start ? fmtDateTime(activity.start) : null;
  return (
    <Stack gap={2} data-testid="adaptativo-atividade">
      <Group gap="xs" wrap="wrap">
        <Text size="sm" fw={500} c={BRAND.textPrimary}>
          {activity.name || activity.kind || 'Atividade'}
        </Text>
        {duplicata && (
          <Badge size="xs" color="yellow" variant="light" data-testid="adaptativo-duplicata">
            Possível duplicata
          </Badge>
        )}
        {foraDoTotal && (
          <Badge size="xs" color="gray" variant="light" data-testid="adaptativo-fora-total">
            Fora do total
          </Badge>
        )}
        {(activity.flags ?? []).map((flag) => (
          <Badge key={flag} size="xs" color="gray" variant="outline">
            {adaptiveLabel('activity_flag', flag)}
          </Badge>
        ))}
      </Group>
      <Text size="xs" c="dimmed">
        {[horario, duracao].filter(Boolean).join(' · ') || '-'}
      </Text>
      <Text size="xs" c="dimmed">
        Informada: {formatKcal(activity.reported_kcal)} · Ativa normalizada: {formatKcal(activity.active_kcal)}
        {activity.calorie_mode ? ` · ${adaptiveLabel('calorie_mode', activity.calorie_mode)}` : ''}
      </Text>
    </Stack>
  );
}

/**
 * Atividades importadas de um dia (nome, horário, duração, kcal informada e ativa, modo, selos) e o estado do dia
 * (confirmado ou pendente de sincronização). Peça comum ao cartão do dashboard e ao diário: só exibe o que o motor
 * mandou. `showDate={false}` omite a data quando a tela já é do dia.
 */
export function ActivityDayList({ day, showDate = true }: { day: AdaptiveEnergyDay; showDate?: boolean }) {
  const atividades = day.activities ?? [];
  // Exibição apenas: o estado vem do motor (`sync_status`); sem ele (payload antigo) usa o `confirmed` recebido.
  const estado = day.sync_status ?? (day.confirmed === true ? DAY_SYNC.confirmed : day.confirmed === false ? DAY_SYNC.pending : null);
  return (
    <Stack gap={6} data-testid="adaptativo-dia">
      <Group gap="xs" justify={showDate ? 'space-between' : 'flex-start'} wrap="wrap">
        {showDate && (
          <Text size="sm" fw={600} c={BRAND.textPrimary}>
            {fmtDate(day.day)}
          </Text>
        )}
        <Group gap={4}>
          {estado === DAY_SYNC.pending && (
            <Badge size="xs" color="yellow" variant="light" data-testid="adaptativo-dia-pendente">
              {adaptiveLabel('day_sync_status', estado)}
            </Badge>
          )}
          {estado === DAY_SYNC.confirmed && (
            <Badge
              size="xs"
              color="green"
              variant="light"
              data-testid="adaptativo-dia-confirmado"
              title={day.confirmation_source ? adaptiveLabel('day_confirmation_source', day.confirmation_source) : undefined}
            >
              {adaptiveLabel('day_sync_status', estado)}
            </Badge>
          )}
          {day.reduced_precision && (
            <Badge size="xs" color="gray" variant="light">
              Precisão reduzida
            </Badge>
          )}
        </Group>
      </Group>
      {atividades.length === 0 ? (
        <Text size="xs" c="dimmed">
          Nenhuma atividade importada.
        </Text>
      ) : (
        atividades.map((a, i) => <ActivityRow key={String(a.id ?? a.external_id ?? i)} activity={a} />)
      )}
    </Stack>
  );
}
