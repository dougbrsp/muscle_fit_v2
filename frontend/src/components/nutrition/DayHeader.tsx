'use client';

import { ActionIcon, Button, Group, Stack, Text, TextInput, Title, Tooltip } from '@mantine/core';
import { IconChevronLeft, IconChevronRight } from '@tabler/icons-react';
import type { NutritionDayTarget } from '@/lib/nutrition-types';
import { DayPriorityBadge } from './DayPriorityBadge';
import { formatDayLong, shiftIsoDate } from '@/lib/nutrition-format';
import { usePresentationSlot } from '@/lib/presentation-slots';
import { BRAND } from '@/theme';

type Props = {
  date: string | null;
  /** "Hoje" do ALUNO, como a API devolve (fuso do perfil). */
  today: string | null;
  onDateChange: (date: string) => void;
  /** Meta do dia, só para o selo de prioridade; ausente/carregando = sem selo. */
  priority?: NutritionDayTarget | null;
};

/** Data do diário (dia anterior/seguinte, calendário, voltar para hoje); o diário é sempre do usuário logado. */
export function DayHeader({ date, today, onDateChange, priority }: Props) {
  const Alternative = usePresentationSlot('dayHeader');
  if (Alternative) {
    // O selo fica fora do slot do celular (o contrato do slot não muda).
    return (
      <Stack gap="xs" align="flex-start">
        <div style={{ width: '100%' }}>
          <Alternative date={date} today={today} onDateChange={onDateChange} />
        </div>
        <DayPriorityBadge target={priority} />
      </Stack>
    );
  }
  return (
    <Stack gap="xs" align="flex-start">
      <Group gap="xs" align="flex-end" wrap="wrap">
        <Tooltip label="Dia anterior">
          <ActionIcon
            variant="light"
            color="brand"
            size="lg"
            aria-label="Dia anterior"
            disabled={!date}
            onClick={() => date && onDateChange(shiftIsoDate(date, -1))}
          >
            <IconChevronLeft size={18} />
          </ActionIcon>
        </Tooltip>
        <TextInput
          type="date"
          aria-label="Data do diário"
          value={date ?? ''}
          onChange={(event) => {
            const valor = event.currentTarget.value;
            if (valor) onDateChange(valor);
          }}
          w={170}
        />
        <Tooltip label="Dia seguinte">
          <ActionIcon
            variant="light"
            color="brand"
            size="lg"
            aria-label="Dia seguinte"
            disabled={!date}
            onClick={() => date && onDateChange(shiftIsoDate(date, 1))}
          >
            <IconChevronRight size={18} />
          </ActionIcon>
        </Tooltip>
        {today && date !== today && (
          <Button variant="subtle" color="brand" size="sm" onClick={() => onDateChange(today)}>
            Hoje
          </Button>
        )}
      </Group>
      {date && (
        <Title order={3} c={BRAND.textPrimary} tt="capitalize">
          {formatDayLong(date)}
        </Title>
      )}
      <DayPriorityBadge target={priority} />
    </Stack>
  );
}
