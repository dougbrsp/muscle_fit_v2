'use client';

import { Alert, Badge, Button, Card, Group, Stack, Table, Text, Title } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { formatNumberOrDash } from '@/lib/format';
import {
  formatAdjustment,
  formatChangeValue,
  shortDay,
  verdictTone,
  weekRangeLabel,
  type Checkin,
  type CheckinDayMacros,
  type CheckinSummary,
} from '@/lib/nutrition-checkin';
import { formatKcal, formatShareAsPercent } from '@/lib/nutrition-format';
import { MOBILE_TOKENS } from '@/mobile/tokens';
import { BRAND } from '@/theme';
import { NutritionLimitations } from './NutritionLimitations';

type Props = {
  checkin: Checkin;
  busy?: boolean;
  error?: string | null;
  onAccept?: () => void;
  onDecline?: () => void;
};

const NUMBER = (value: number | null | undefined, digits = 1) => formatNumberOrDash(value, digits);

function signed(value: number | null | undefined, unit: string, digits = 2): string {
  if (value == null || !Number.isFinite(value)) return '-';
  const texto = formatNumberOrDash(Math.abs(value), digits);
  return `${value > 0 ? '+' : value < 0 ? '-' : ''}${texto} ${unit}`;
}

type Row = { label: string; week: string; reference: string; note?: string };

/** Semana × alvo: os valores são os do motor (`summary`); a tela só os coloca lado a lado. */
function weekRows(summary: Partial<CheckinSummary>, goal: Checkin['goal']): Row[] {
  const lean = summary.lean_trend;
  return [
    {
      label: 'Gasto (kcal por dia)',
      week: formatKcal(summary.mean_tdee_kcal),
      reference: summary.observed_tdee?.kcal != null ? `observado: ${formatKcal(summary.observed_tdee.kcal)}` : 'observado: sem dados',
    },
    {
      label: 'Consumo (kcal por dia)',
      week: `${formatKcal(summary.mean_intake_kcal)} em ${summary.complete_days ?? 0} dia(s) completos`,
      reference: `meta: ${formatKcal(summary.mean_target_kcal)}`,
      note: summary.kcal_share != null ? `aderência ${formatShareAsPercent(summary.kcal_share, 0)} da meta` : undefined,
    },
    {
      label: 'Peso',
      week: `${NUMBER(summary.weight_week_mean_kg)} kg (média da semana)`,
      reference: `esperado: ${signed(summary.expected_weekly_change_kg, 'kg/sem')}`,
      note: `tendência: ${signed(summary.weight_weekly_change_kg, 'kg/sem')}`,
    },
    {
      label: '% de gordura (bioimpedância)',
      week: summary.body_fat_percent != null ? `${NUMBER(summary.body_fat_percent)}%` : '-',
      reference: goal?.goal_body_fat_percent != null ? `alvo: ${NUMBER(goal.goal_body_fat_percent)}%` : 'sem alvo',
    },
    {
      label: 'Massa muscular (bioimpedância)',
      week: summary.muscle_mass_kg != null ? `${NUMBER(summary.muscle_mass_kg)} kg` : '-',
      reference: lean?.valid_for_bia ? `tendência: ${signed(lean.weekly_change, 'kg/sem')}` : 'sem tendência (veja os motivos)',
    },
  ];
}

function MacroCell({ value, unit }: { value: number | null; unit: string }) {
  return <>{value == null ? '-' : `${Math.round(value)} ${unit}`}</>;
}

function MacrosTable({ before, after, scopeLabel }: { before: CheckinDayMacros; after: CheckinDayMacros; scopeLabel: string }) {
  const linhas: Array<{ label: string; antes: number | null; depois: number | null; unit: string }> = [
    { label: 'Energia', antes: before.kcal, depois: after.kcal, unit: 'kcal' },
    { label: 'Proteína', antes: before.protein_g, depois: after.protein_g, unit: 'g' },
    { label: 'Carboidrato', antes: before.carb_g, depois: after.carb_g, unit: 'g' },
    { label: 'Gordura', antes: before.fat_g, depois: after.fat_g, unit: 'g' },
  ];
  return (
    <Table.ScrollContainer minWidth={280}>
      <Table withRowBorders={false} verticalSpacing={6} aria-label="Metas de um dia sem treino, antes e depois">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{scopeLabel}</Table.Th>
            <Table.Th>Agora</Table.Th>
            <Table.Th>Proposta</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {linhas.map((linha) => (
            <Table.Tr key={linha.label}>
              <Table.Td>{linha.label}</Table.Td>
              <Table.Td>
                <MacroCell value={linha.antes} unit={linha.unit} />
              </Table.Td>
              <Table.Td fw={700}>
                <MacroCell value={linha.depois} unit={linha.unit} />
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

/**
 * Tela de revisão do check-in (N7): a semana contra o alvo, o que muda nas metas e por quê, e aceitar ou
 * recusar. Apresentação pura do que a API devolve (`GET /nutrition/checkin`); nenhum cálculo aqui. Os botões
 * ficam numa barra fixa embaixo com a área segura do aparelho (`env(safe-area-inset-bottom)`).
 */
export function CheckinReview({ checkin, busy = false, error = null, onAccept, onDecline }: Props) {
  const resumo = checkin.summary ?? {};
  const proposta = checkin.proposal;
  const ajuste = formatAdjustment(proposta?.delta_kcal ?? checkin.adjustment_kcal);
  return (
    <Stack gap="md">
      <Stack gap={4}>
        <Group gap="xs" align="center">
          <Title order={2} c={BRAND.textPrimary}>
            Check-in da semana de {weekRangeLabel(checkin)}
          </Title>
          <Badge color={verdictTone(checkin.verdict)} variant="light" size="lg">
            {checkin.verdict_label}
          </Badge>
        </Group>
        <Text size="sm" c={BRAND.textMuted}>
          {checkin.status_label}
          {checkin.can_respond && checkin.expires_on ? ` · responda até ${shortDay(checkin.expires_on)}` : ''}
        </Text>
      </Stack>

      <NutritionLimitations items={checkin.missing_items} title="O que faltou para avaliar a semana" tone="info" />

      <Card withBorder radius="md" bg={BRAND.bgSurface}>
        <Stack gap="xs">
          <Title order={4} c={BRAND.textPrimary}>
            Semana × alvo
          </Title>
          <Table.ScrollContainer minWidth={300}>
            <Table verticalSpacing={6} aria-label="A semana contra o alvo">
              <Table.Tbody>
                {weekRows(resumo, checkin.goal).map((linha) => (
                  <Table.Tr key={linha.label}>
                    <Table.Td>
                      <Text size="sm" fw={600}>
                        {linha.label}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm">{linha.week}</Text>
                      {linha.note && (
                        <Text size="xs" c={BRAND.textMuted}>
                          {linha.note}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" c={BRAND.textMuted}>
                        {linha.reference}
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Stack>
      </Card>

      <NutritionLimitations items={checkin.reason_items} title="Por que o motor decidiu assim" tone="info" />

      {proposta ? (
        <Card withBorder radius="md" bg={BRAND.bgSurface}>
          <Stack gap="sm">
            <Title order={4} c={BRAND.textPrimary}>
              O que muda{ajuste ? `: ${ajuste} por dia` : ''}
            </Title>
            {proposta.change_items.map((item) => (
              <Group key={item.field} justify="space-between" wrap="nowrap" gap="xs">
                <Text size="sm">{item.label}</Text>
                <Text size="sm" fw={700}>
                  {formatChangeValue(item, item.before)} → {formatChangeValue(item, item.after)}
                </Text>
              </Group>
            ))}
            <MacrosTable before={proposta.before} after={proposta.after} scopeLabel={proposta.scope_label ?? 'Dia sem treino'} />
            <Text size="xs" c={BRAND.textMuted}>
              O ciclo de dias, a prioridade de macros e o modo do treino do seu plano continuam como estão. Ao aceitar, uma nova
              versão do plano é publicada a partir de hoje; os dias já passados não mudam.
            </Text>
          </Stack>
        </Card>
      ) : (
        <Text size="sm" c={BRAND.textMuted}>
          Nenhuma mudança nas metas nesta semana.
        </Text>
      )}

      {error && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} role="alert">
          {error}
        </Alert>
      )}

      {checkin.can_respond && (
        <div
          style={{
            position: 'sticky',
            // Acima da barra de navegação do celular e da área segura (mesmo padrão de PlanForm e PlanPreview).
            bottom: 'calc(var(--m-bottom-nav, 0px) + env(safe-area-inset-bottom))',
            zIndex: 5,
            background: MOBILE_TOKENS.bg,
            borderTop: `1px solid ${MOBILE_TOKENS.border}`,
            padding: `12px ${MOBILE_TOKENS.contentPadding}px 12px`,
            margin: `0 -${MOBILE_TOKENS.contentPadding}px`,
          }}
        >
          {/* Empilhados: dois botões lado a lado ficam apertados em 360 px. */}
          <Stack gap="xs">
            <Button color="brand" fullWidth loading={busy} onClick={onAccept} style={{ minHeight: MOBILE_TOKENS.touchTarget }}>
              Aceitar as novas metas
            </Button>
            <Button variant="light" color="gray" fullWidth disabled={busy} onClick={onDecline} style={{ minHeight: MOBILE_TOKENS.touchTarget }}>
              Manter as atuais
            </Button>
          </Stack>
        </div>
      )}
    </Stack>
  );
}
