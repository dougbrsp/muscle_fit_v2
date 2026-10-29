'use client';

import { ActionIcon, Badge, Group, Paper, SimpleGrid, Stack, Text, Tooltip, UnstyledButton } from '@mantine/core';
import { IconChevronLeft, IconChevronRight, IconHistory } from '@tabler/icons-react';
import { NutritionLimitations } from './NutritionLimitations';
import { ENERGY_NUTRIENT, nutritionNotice } from '@/lib/nutrition-constants';
import { formatKcal, formatShareAsPercent, formatSignedKcal, formatTotal, shiftIsoDate, weekdayShort } from '@/lib/nutrition-format';
import type { NutritionWeek, WeekDay } from '@/lib/nutrition-types';
import { formatDateBR } from '@/lib/physical-indicators-types';
import { BRAND } from '@/theme';

type Props = {
  week: NutritionWeek | undefined;
  selected: string | null;
  onSelect: (date: string) => void;
  /** Recebe uma data da semana anterior/seguinte (a API devolve a semana que a contém). */
  onWeekChange: (anchor: string) => void;
};

function DayPill({ day, selected, today, onSelect }: { day: WeekDay; selected: boolean; today: boolean; onSelect: () => void }) {
  const total = day.totals;
  const ingestao = total
    ? formatTotal(total.nutrients[ENERGY_NUTRIENT], 'kcal', !total.incomplete_fields.includes(ENERGY_NUTRIENT))
    : '-';
  return (
    <UnstyledButton onClick={onSelect} aria-pressed={selected} aria-label={`Abrir ${formatDateBR(day.local_date)}`}>
      <Paper
        withBorder
        radius="md"
        p="xs"
        bg={selected ? 'var(--mantine-color-brand-light)' : BRAND.bgElevated}
        style={{ borderColor: today ? 'var(--mantine-color-brand-5)' : undefined }}
      >
        <Stack gap={2} align="center">
          <Text size="xs" c={BRAND.textMuted} tt="capitalize">
            {weekdayShort(day.local_date)} {formatDateBR(day.local_date).slice(0, 5)}
          </Text>
          <Text size="sm" fw={700} c={BRAND.textPrimary}>
            {ingestao}
          </Text>
          <Text size="xs" c="dimmed">
            meta {formatKcal(day.target?.kcal)}
          </Text>
          <Group gap={4} justify="center" wrap="nowrap">
            {day.logged && (
              <Badge size="xs" color="green" variant="light">
                registrado
              </Badge>
            )}
            {day.intake_incomplete && (
              <Tooltip label={nutritionNotice('intake_incomplete_days')} multiline w={300} withArrow>
                <Badge size="xs" color="yellow" variant="light">
                  incompleto
                </Badge>
              </Tooltip>
            )}
            {day.target?.backfilled && (
              <Tooltip label={nutritionNotice('target_backfilled')} multiline w={300} withArrow>
                <IconHistory size={14} color={BRAND.textMuted} aria-label="Meta retroativa" />
              </Tooltip>
            )}
          </Group>
        </Stack>
      </Paper>
    </UnstyledButton>
  );
}

/** A semana do diário (segunda a domingo): ingestão × meta por dia e o resumo do motor (`weekly_budget`). */
export function WeekStrip({ week, selected, onSelect, onWeekChange }: Props) {
  if (!week) return null;
  const { report } = week;
  return (
    <Stack gap="xs">
      <Group justify="space-between" wrap="nowrap">
        <ActionIcon variant="subtle" color="brand" aria-label="Semana anterior" onClick={() => onWeekChange(shiftIsoDate(week.start, -1))}>
          <IconChevronLeft size={18} />
        </ActionIcon>
        <Text size="sm" c={BRAND.textMuted}>
          Semana de {formatDateBR(week.start)} a {formatDateBR(week.end)}
        </Text>
        <ActionIcon variant="subtle" color="brand" aria-label="Semana seguinte" onClick={() => onWeekChange(shiftIsoDate(week.end, 1))}>
          <IconChevronRight size={18} />
        </ActionIcon>
      </Group>
      <SimpleGrid cols={{ base: 2, xs: 4, md: 7 }} spacing="xs">
        {week.days.map((dia) => (
          <DayPill
            key={dia.local_date}
            day={dia}
            selected={dia.local_date === selected}
            today={dia.local_date === week.today}
            onSelect={() => onSelect(dia.local_date)}
          />
        ))}
      </SimpleGrid>
      <Group gap="lg" wrap="wrap">
        <Text size="sm" c={BRAND.textMuted}>
          Orçamento da semana: <b>{formatKcal(report.budget.kcal)}</b>
        </Text>
        <Text size="sm" c={BRAND.textMuted}>
          Aderência (kcal): <b>{formatShareAsPercent(report.adherence.kcal_share)}</b>
          {report.adherence.kcal_within_tolerance === true && ' ✓'}
        </Text>
        <Text size="sm" c={BRAND.textMuted}>
          Proteína: <b>{formatShareAsPercent(report.adherence.protein_share)}</b>
          {report.adherence.protein_ok === true && ' ✓'}
        </Text>
        <Text size="sm" c={BRAND.textMuted}>
          Banco: <b>{formatSignedKcal(report.bank.balance_kcal)}</b> (transferência {formatSignedKcal(report.bank.carryover_kcal)})
        </Text>
        <Badge color={report.adherence.valid_week ? 'green' : 'gray'} variant="light">
          {report.adherence.logged_days} dia(s) registrado(s){report.adherence.valid_week ? ' · semana válida' : ''}
        </Badge>
      </Group>
      <NutritionLimitations items={report.limitations} title="Sobre a semana" tone="info" />
    </Stack>
  );
}
