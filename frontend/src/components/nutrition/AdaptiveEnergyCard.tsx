'use client';

import { useId } from 'react';
import { Badge, Button, Card, Group, Stack, Text, Title } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { fmtDate, fmtDateTime, fmtDecimal, fmtInt } from '@/lib/dashboard-format';
import { adaptiveLabel, type AdaptiveActivity, type AdaptiveEnergy, type AdaptiveEnergyDay, type AdaptiveEnergyState } from '@/lib/nutrition-dashboard';
import { formatKcal } from '@/lib/nutrition-format';
import { BRAND } from '@/theme';

export function Linha({ label, value, testId }: { label: string; value: string; testId?: string }) {
  return (
    <Group justify="space-between" wrap="nowrap" gap="xs" align="flex-start">
      <Text size="sm" c="dimmed">
        {label}
      </Text>
      <Text size="sm" c={BRAND.textPrimary} ta="right" data-testid={testId}>
        {value}
      </Text>
    </Group>
  );
}

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

function DayBlock({ day }: { day: AdaptiveEnergyDay }) {
  const atividades = day.activities ?? [];
  return (
    <Stack gap={6} data-testid="adaptativo-dia">
      <Group gap="xs" justify="space-between" wrap="wrap">
        <Text size="sm" fw={600} c={BRAND.textPrimary}>
          {fmtDate(day.day)}
        </Text>
        <Group gap={4}>
          {day.confirmed === false && (
            <Badge size="xs" color="yellow" variant="light" data-testid="adaptativo-dia-pendente">
              Pendente de sincronização
            </Badge>
          )}
          {day.confirmed === true && (
            <Badge size="xs" color="green" variant="light">
              Confirmado
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

/**
 * Confiança, última atualização e "O que falta" do gasto adaptativo (peça comum ao cartão do dashboard e à prévia do
 * plano). Só exibe o que o motor mandou.
 */
export function AdaptiveEnergyStatus({ state: s }: { state: AdaptiveEnergyState }) {
  const pendencias = s.suspension_reasons ?? [];
  const avisos = s.data_quality_flags ?? [];
  return (
    <>
        <Linha
          label="Confiança"
          value={`${adaptiveLabel('confidence_level', s.confidence_level)} (${fmtInt(s.valid_days)} de ${fmtInt(s.window_days)} dias válidos)`}
          testId="adaptativo-confianca"
        />
        <Linha label="Última atualização" value={fmtDate(s.last_update_date)} />
      {(pendencias.length > 0 || avisos.length > 0) && (
          <Stack gap={2} data-testid="adaptativo-falta">
            <Text size="sm" fw={600} c={BRAND.textPrimary}>
              O que falta
            </Text>
            {pendencias.map((p) => (
              <Text key={p.code} size="xs" c="yellow">
                {adaptiveLabel('suspension_reason', p.code)}
                {typeof p.have === 'number' && typeof p.need === 'number' ? ` (${fmtDecimal(p.have, 1)} de ${fmtDecimal(p.need, 1)})` : ''}
              </Text>
            ))}
            {avisos.map((f) => (
              <Text key={f} size="xs" c="dimmed">
                {adaptiveLabel('data_quality_flag', f)}
              </Text>
            ))}
          </Stack>
        )}
    </>
  );
}

/**
 * Gasto adaptativo em sombra: só exibe o que o motor calculou (`adaptive_energy` do dashboard).
 * Nenhuma meta usa este valor. Sem o bloco, nada aparece; indisponível, só um aviso curto.
 */
export function AdaptiveEnergyCard({ energy }: { energy: AdaptiveEnergy | null | undefined }) {
  const [aberto, { toggle }] = useDisclosure(false);
  const painelId = useId();
  if (!energy) return null;
  const titulo = (
    <Group justify="space-between" gap="xs" wrap="wrap">
      <Title order={4} c={BRAND.textPrimary}>
        Gasto adaptativo (em sombra)
      </Title>
      <Badge color="gray" variant="light" data-testid="adaptativo-selo">
        {energy.applied ? 'Em uso no seu plano' : 'Em sombra: ainda não altera suas metas'}
      </Badge>
    </Group>
  );
  const s = energy.state;
  if (!energy.available || !s) {
    return (
      <Card withBorder radius="md" bg={BRAND.bgSurface} data-testid="adaptativo-indisponivel">
        <Stack gap={6}>
          {titulo}
          <Text size="sm" c="dimmed">
            {energy.unavailable_reason_label ?? (energy.unavailable_reason ? adaptiveLabel('unavailable_reason', energy.unavailable_reason) : 'Ainda não há dados suficientes para o cálculo.')}
          </Text>
        </Stack>
      </Card>
    );
  }
  const dias = energy.days ?? [];
  return (
    <Card withBorder radius="md" bg={BRAND.bgSurface} data-testid="adaptativo-cartao">
      <Stack gap={6}>
        {titulo}
        <Linha label="Gasto adaptativo médio" value={formatKcal(s.base_tdee)} />
        <Linha label="Atividade hoje" value={formatKcal(s.activity_calories_today)} />
        <Linha label="Média de atividade" value={formatKcal(s.average_activity_calories)} />
        <Linha label="Ajuste relativo aplicado" value={formatKcal(s.activity_adjustment)} />
        <Linha label="Gasto estimado hoje" value={formatKcal(s.estimated_tdee_today)} />
        <AdaptiveEnergyStatus state={s} />
        <Linha
          label="Peso de tendência"
          value={`${s.trend_weight_kg == null ? '-' : `${fmtDecimal(s.trend_weight_kg, 1)} kg`} · ${
            s.weight_slope_kg_per_week == null ? '-' : `${fmtDecimal(s.weight_slope_kg_per_week, 2)} kg/semana`
          }`}
        />
        {dias.length > 0 && (
          <>
            <Button variant="subtle" size="compact-xs" onClick={toggle} style={{ alignSelf: 'flex-start' }} data-testid="adaptativo-atividades-toggle" aria-expanded={aberto} aria-controls={painelId}>
              {aberto ? 'Ocultar atividades importadas' : 'Ver atividades importadas'}
            </Button>
            <div id={painelId} hidden={!aberto}>
              {aberto && (
                <Stack gap="sm" data-testid="adaptativo-atividades">
                  {dias.map((d) => (
                    <DayBlock key={d.day} day={d} />
                  ))}
                </Stack>
              )}
            </div>
          </>
        )}
      </Stack>
    </Card>
  );
}
