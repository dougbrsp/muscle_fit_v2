'use client';

import { Alert, Button, Card, Group, Stack, Text, Textarea, Title } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { nutritionErrorInfo, saveNutritionDayLog } from '@/lib/nutrition-api';
import type { DayLog } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

type Props = {
  date: string;
  dayLog: DayLog | null;
  onSaved: () => void;
};

/** Anotação do dia (água e fome saíram do produto em 2026-11-03). */
export function DayNotesInput({ date, dayLog, onSaved }: Props) {
  const [notas, setNotas] = useState(dayLog?.notes ?? '');

  const mutation = useMutation({
    mutationFn: () => saveNutritionDayLog(date, { notes: notas.trim() || null }),
    onSuccess: onSaved,
  });
  const erro = mutation.isError ? nutritionErrorInfo(mutation.error, 'Não foi possível salvar o dia.') : null;

  return (
    <Card withBorder radius="md" bg={BRAND.bgSurface}>
      <Stack gap="sm">
        <Title order={4} c={BRAND.textPrimary}>
          Anotação do dia
        </Title>
        <Textarea
          aria-label="Anotação do dia"
          value={notas}
          onChange={(e) => setNotas(e.currentTarget.value)}
          maxLength={1000}
          autosize
          minRows={1}
        />
        {erro && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            {erro.message}
            {erro.items.map((item) => (
              <Text key={item.code} size="sm">
                {item.label}
              </Text>
            ))}
          </Alert>
        )}
        <Group>
          <Button color="brand" size="sm" loading={mutation.isPending} onClick={() => mutation.mutate()}>
            Salvar dia
          </Button>
          {mutation.isSuccess && (
            <Text size="sm" c="green">
              Salvo.
            </Text>
          )}
        </Group>
      </Stack>
    </Card>
  );
}
