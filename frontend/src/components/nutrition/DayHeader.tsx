'use client';

import { ActionIcon, Button, Group, Stack, Text, TextInput, Title, Tooltip } from '@mantine/core';
import { IconChevronLeft, IconChevronRight } from '@tabler/icons-react';
import { UserPicker } from './UserPicker';
import { formatDayLong, shiftIsoDate } from '@/lib/nutrition-format';
import { BRAND } from '@/theme';

type Props = {
  /** Admin/PT escolhem o aluno (mesmo seletor do plano); os demais veem o próprio diário. */
  canManage: boolean;
  userId: number | null;
  onUserChange: (userId: number | null) => void;
  /** Nome do aluno mostrado (o da resposta da API). */
  studentName: string | null;
  date: string | null;
  /** "Hoje" do ALUNO, como a API devolve (fuso do perfil). */
  today: string | null;
  onDateChange: (date: string) => void;
};

/** Data do diário (dia anterior/seguinte, calendário, voltar para hoje) e, para admin/PT, o aluno. */
export function DayHeader({ canManage, userId, onUserChange, studentName, date, today, onDateChange }: Props) {
  return (
    <Stack gap="xs">
      {canManage && (
        <Group gap="sm" align="flex-end" wrap="wrap">
          <UserPicker value={userId} onChange={onUserChange} />
          {userId === null && (
            <Text size="sm" c={BRAND.textMuted} mb={6}>
              Sem aluno escolhido: mostrando o seu diário.
            </Text>
          )}
        </Group>
      )}
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
          {studentName ? ` · ${studentName}` : ''}
        </Title>
      )}
    </Stack>
  );
}
