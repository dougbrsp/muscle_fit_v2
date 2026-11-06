'use client';

import { Badge, Button, Group, Stack, Text } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { fmtDate, fmtDateTime, fmtDecimal } from '@/lib/dashboard-format';
import { deleteActivityCorrection, diaryKeys, nutritionErrorInfo, putActivityCorrection } from '@/lib/nutrition-api';
import { adaptiveLabel, ADAPTIVE_CODES, dashboardKeys, type AdaptiveActivity, type AdaptiveEnergyDay } from '@/lib/nutrition-dashboard';
import { formatKcal } from '@/lib/nutrition-format';
import { BRAND } from '@/theme';

/** Códigos do estado do dia (`sync_status` do motor); único lugar com os literais. */
const DAY_SYNC = { confirmed: 'confirmed', pending: 'pending' } as const;

/** Ações de correção manual (Lote G): o backend aplica e devolve o dia; aqui só dispara e invalida as queries. */
function ActivityActions({ activity }: { activity: AdaptiveActivity }) {
  const qc = useQueryClient();
  const activityId = activity.id ?? null;
  const mutation = useMutation({
    // `undo` = DELETE da correção; os demais são o código da natureza ou da ação (`ADAPTIVE_CODES`).
    mutationFn: (acao: string) => {
      if (activityId == null) return Promise.reject(new Error('Atividade sem identificador.'));
      if (acao === 'undo') return deleteActivityCorrection(activityId);
      return putActivityCorrection(
        activityId,
        acao === ADAPTIVE_CODES.exclude
          ? { action: ADAPTIVE_CODES.exclude }
          : { action: ADAPTIVE_CODES.reclassify, energy_kind: acao },
      );
    },
    onSuccess: () =>
      Promise.all([qc.invalidateQueries({ queryKey: diaryKeys.all }), qc.invalidateQueries({ queryKey: dashboardKeys.all })]),
  });
  if (activityId == null) return null;
  const corrigida = activity.correction != null || activity.excluded === true;
  const busy = mutation.isPending;
  return (
    <Stack gap={4}>
      <Group gap={6} wrap="wrap" data-testid="atividade-acoes">
        {corrigida ? (
          <Button size="compact-xs" variant="default" loading={busy} onClick={() => mutation.mutate('undo')} data-testid="atividade-desfazer">
            Desfazer
          </Button>
        ) : (
          <>
            <Button size="compact-xs" variant="light" color="red" disabled={busy} onClick={() => mutation.mutate(ADAPTIVE_CODES.exclude)} data-testid="atividade-excluir">
              Excluir (duplicada)
            </Button>
            <Button size="compact-xs" variant="light" disabled={busy} onClick={() => mutation.mutate(ADAPTIVE_CODES.kindActive)} data-testid="atividade-reclassificar-ativa">
              Reclassificar: {adaptiveLabel('activity_correction_energy_kind', ADAPTIVE_CODES.kindActive)}
            </Button>
            <Button size="compact-xs" variant="light" disabled={busy} onClick={() => mutation.mutate(ADAPTIVE_CODES.kindGross)} data-testid="atividade-reclassificar-total">
              Reclassificar: {adaptiveLabel('activity_correction_energy_kind', ADAPTIVE_CODES.kindGross)}
            </Button>
          </>
        )}
      </Group>
      {mutation.isError && (
        <Text size="xs" c="red" role="alert" data-testid="atividade-erro">
          {nutritionErrorInfo(mutation.error, 'Não foi possível corrigir a atividade.').message}
        </Text>
      )}
    </Stack>
  );
}

function ActivityRow({ activity, editable }: { activity: AdaptiveActivity; editable: boolean }) {
  const duplicata = (activity.possible_duplicate_of?.length ?? 0) > 0 || activity.duplicate_of != null;
  const excluida = activity.excluded === true || activity.correction?.action === ADAPTIVE_CODES.exclude;
  const reclassificada = activity.correction?.action === ADAPTIVE_CODES.reclassify;
  const foraDoTotal = activity.counts_in_total === false && !excluida;
  const duracao = activity.duration_minutes == null ? null : `${fmtDecimal(activity.duration_minutes, 0)} min`;
  const horario = activity.start ? fmtDateTime(activity.start) : null;
  return (
    <Stack gap={2} data-testid="adaptativo-atividade" data-excluida={excluida ? 'true' : undefined}>
      <Group gap="xs" wrap="wrap">
        <Text
          size="sm"
          fw={500}
          c={excluida ? 'dimmed' : BRAND.textPrimary}
          td={excluida ? 'line-through' : undefined}
          data-testid="adaptativo-atividade-nome"
        >
          {activity.name || activity.kind || 'Atividade'}
        </Text>
        {reclassificada && (
          <Badge size="xs" color="blue" variant="light" data-testid="adaptativo-reclassificada">
            {adaptiveLabel('activity_correction_action', ADAPTIVE_CODES.reclassify)}
            {activity.correction?.energy_kind ? `: ${adaptiveLabel('activity_correction_energy_kind', String(activity.correction.energy_kind))}` : ''}
          </Badge>
        )}
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
      <Text size="xs" c="dimmed" td={excluida ? 'line-through' : undefined}>
        Informada: {formatKcal(activity.reported_kcal)} · Ativa normalizada: {formatKcal(activity.active_kcal)}
        {activity.calorie_mode ? ` · ${adaptiveLabel('calorie_mode', activity.calorie_mode)}` : ''}
      </Text>
      {editable && <ActivityActions activity={activity} />}
    </Stack>
  );
}

/**
 * Atividades importadas de um dia (nome, horário, duração, kcal informada e ativa, modo, selos) e o estado do dia
 * (confirmado ou pendente de sincronização). Peça comum ao cartão do dashboard e ao diário: só exibe o que o motor
 * mandou. `showDate={false}` omite a data quando a tela já é do dia.
 */
export function ActivityDayList({
  day,
  showDate = true,
  editable = false,
}: {
  day: AdaptiveEnergyDay;
  showDate?: boolean;
  /** Mostra as ações de correção manual (excluir, reclassificar, desfazer); só no diário. */
  editable?: boolean;
}) {
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
        atividades.map((a, i) => <ActivityRow key={String(a.id ?? a.external_id ?? i)} activity={a} editable={editable} />)
      )}
    </Stack>
  );
}
