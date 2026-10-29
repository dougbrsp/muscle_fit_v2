'use client';

import { Alert, Button, Card, Group, NumberInput, SegmentedControl, Stack, Text, Textarea, Title } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { HUNGER_OPTIONS, WATER_ML_LIMITS } from '@/lib/nutrition-constants';
import { nutritionErrorInfo, saveNutritionDayLog } from '@/lib/nutrition-api';
import { inputNumberOrNull } from '@/lib/nutrition-format';
import type { DayLog } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

const NAO_INFORMADO = '';

type Props = {
  date: string;
  userId: number | null;
  dayLog: DayLog | null;
  onSaved: () => void;
};

/** Água, fome (escala do motor) e anotação do dia; a API confere as faixas (400 com o código do motor). */
export function WaterHungerInputs({ date, userId, dayLog, onSaved }: Props) {
  const [agua, setAgua] = useState<number | string>(dayLog?.water_ml ?? '');
  const [fome, setFome] = useState<string>(dayLog?.hunger != null ? String(dayLog.hunger) : NAO_INFORMADO);
  const [notas, setNotas] = useState(dayLog?.notes ?? '');
  const [minimo, maximo] = WATER_ML_LIMITS;

  const mutation = useMutation({
    mutationFn: () =>
      saveNutritionDayLog(
        date,
        { water_ml: inputNumberOrNull(agua), hunger: fome === NAO_INFORMADO ? null : Number(fome), notes: notas.trim() || null },
        userId,
      ),
    onSuccess: onSaved,
  });
  const erro = mutation.isError ? nutritionErrorInfo(mutation.error, 'Não foi possível salvar o dia.') : null;

  return (
    <Card withBorder radius="md" bg={BRAND.bgSurface}>
      <Stack gap="sm">
        <Title order={4} c={BRAND.textPrimary}>
          Água e fome
        </Title>
        <Group gap="md" align="flex-end" wrap="wrap">
          <NumberInput label="Água (ml)" value={agua} onChange={setAgua} min={minimo} max={maximo} step={250} w={160} />
          <Stack gap={4}>
            <Text size="sm" fw={500}>
              Fome
            </Text>
            <SegmentedControl
              value={fome}
              onChange={setFome}
              data={[{ value: NAO_INFORMADO, label: '—' }, ...HUNGER_OPTIONS]}
              size="xs"
              aria-label="Fome"
            />
          </Stack>
        </Group>
        <Textarea label="Anotação do dia" value={notas} onChange={(e) => setNotas(e.currentTarget.value)} maxLength={1000} autosize minRows={1} />
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
