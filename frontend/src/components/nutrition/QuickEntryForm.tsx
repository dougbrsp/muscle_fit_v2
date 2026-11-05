'use client';

import { Alert, Button, Collapse, Group, NumberInput, Select, Stack, Text, TextInput } from '@mantine/core';
import { IconAlertTriangle, IconChevronDown } from '@tabler/icons-react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { MEAL_SLOT_OPTIONS, nutritionLabel } from '@/lib/nutrition-constants';
import { useQueryClient } from '@tanstack/react-query';
import { createMeal, lastMealTimeKey } from '@/lib/nutrition-api';
import {
  QUICK_ENTRY_NAME_MAX_CHARS,
  QUICK_ENTRY_NUMERIC_FIELDS,
  clockTime,
  emptyQuickDraft,
  fetchSlotForTime,
  quickEntryErrors,
  quickMealPayload,
  slotForTimeKeys,
  type QuickEntryDraft,
} from '@/lib/nutrition-quick-entry';
import type { Meal } from '@/lib/nutrition-types';

type Props = {
  /** Dia selecionado no diário (`YYYY-MM-DD`). */
  date: string;
  /** "Hoje" do aluno, como a API o calcula; `null` enquanto não carregou. */
  today: string | null;
  /** Aumenta os alvos de toque (celular). */
  touch?: boolean;
  onSaved: (meal: Meal) => void;
  onCancel: () => void;
};

/**
 * Lançamento rápido: 6 campos obrigatórios (nome, peso, kcal, proteína, carbo, gordura).
 * Hora (atual) e refeição ficam num bloco recolhido; a refeição da hora vem da API
 * (`/nutrition/meals/slot-for-time`) e muda ao trocar a hora, salvo escolha manual.
 * Sem cálculo na tela: os valores vão como digitados e o motor valida e grava.
 */
export function QuickEntryForm({ date, today, touch = false, onSaved, onCancel }: Props) {
  const [draft, setDraft] = useState<QuickEntryDraft>(emptyQuickDraft);
  const [eatenAt, setEatenAt] = useState<string>(() => clockTime());
  const [manualSlot, setManualSlot] = useState<string | null>(null);
  const [aberto, setAberto] = useState(false);
  const alt = touch ? 48 : undefined;

  const sugestao = useQuery({
    queryKey: slotForTimeKeys.time(eatenAt),
    queryFn: () => fetchSlotForTime(eatenAt),
    enabled: eatenAt !== '' && manualSlot === null,
    retry: false,
    staleTime: 60_000,
  });
  const slot = manualSlot ?? sugestao.data?.meal_slot ?? null;
  const falhouSugestao = manualSlot === null && (sugestao.isError || eatenAt === '');
  // Dia diferente de hoje, sugestão que falhou ou refeição ainda sem valor: o bloco abre para conferir/escolher.
  const forcado = (today !== null && date !== today) || falhouSugestao;
  const visivel = aberto || forcado;

  const queryClient = useQueryClient();
  const corpo = quickMealPayload({ date, slot, eatenAt, draft });
  const salvar = useMutation({
    mutationFn: () => {
      if (!corpo) throw new Error('Preencha os 6 campos e escolha a refeição.');
      return createMeal(corpo);
    },
    onSuccess: (salva) => {
      void queryClient.invalidateQueries({ queryKey: lastMealTimeKey });
      onSaved(salva);
    },
  });
  const erros = salvar.isError ? quickEntryErrors(salvar.error, 'Não foi possível salvar o lançamento.') : null;

  const rotuloSlot = slot ? (nutritionLabel(MEAL_SLOT_OPTIONS, slot) ?? slot) : null;

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (corpo) salvar.mutate();
      }}
      noValidate
    >
      <Stack gap="sm">
        <TextInput
          label="Nome"
          value={draft.name}
          onChange={(event) => {
            const valor = event.currentTarget.value;
            setDraft((atual) => ({ ...atual, name: valor }));
          }}
          maxLength={QUICK_ENTRY_NAME_MAX_CHARS}
          required
          data-autofocus
          error={erros?.byField.name}
          styles={alt ? { input: { minHeight: alt } } : undefined}
        />
        {QUICK_ENTRY_NUMERIC_FIELDS.map((campo) => (
          <NumberInput
            key={campo.code}
            label={`${campo.label} (${campo.unit})`}
            value={draft.values[campo.code] ?? ''}
            onChange={(valor) => setDraft((atual) => ({ ...atual, values: { ...atual.values, [campo.code]: valor } }))}
            required
            min={0}
            decimalScale={2}
            decimalSeparator=","
            hideControls
            inputMode="decimal"
            error={erros?.byField[campo.code]}
            styles={alt ? { input: { minHeight: alt } } : undefined}
          />
        ))}

        <Stack gap={4}>
          <Button
            variant="subtle"
            color="gray"
            justify="space-between"
            fullWidth
            mih={alt}
            rightSection={<IconChevronDown size={16} style={{ transform: visivel ? 'rotate(180deg)' : 'none' }} aria-hidden />}
            aria-expanded={visivel}
            onClick={() => setAberto((atual) => !atual)}
            disabled={forcado && !aberto}
          >
            Hora e refeição: {eatenAt || 'sem hora'} · {rotuloSlot ?? 'escolha a refeição'}
          </Button>
          <Collapse in={visivel}>
            <Stack gap="xs">
              {today !== null && date !== today && (
                <Text size="sm" c="dimmed">
                  Este não é o dia de hoje ({date}). Confira a hora.
                </Text>
              )}
              <Group gap="sm" align="flex-start" grow>
                <TextInput
                  type="time"
                  label="Hora"
                  value={eatenAt}
                  onChange={(event) => setEatenAt(event.currentTarget.value)}
                  styles={alt ? { input: { minHeight: alt } } : undefined}
                />
                <Select
                  label="Refeição"
                  placeholder="Escolha"
                  data={[...MEAL_SLOT_OPTIONS]}
                  value={slot}
                  onChange={(valor) => setManualSlot(valor)}
                  allowDeselect={false}
                  error={
                    falhouSugestao && slot === null
                      ? 'Não foi possível sugerir a refeição para esta hora. Escolha uma.'
                      : undefined
                  }
                  comboboxProps={{ withinPortal: true }}
                  styles={alt ? { input: { minHeight: alt } } : undefined}
                />
              </Group>
            </Stack>
          </Collapse>
        </Stack>

        {erros && erros.general.length > 0 && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />} role="alert">
            {erros.general.map((texto) => (
              <Text key={texto} size="sm">
                {texto}
              </Text>
            ))}
          </Alert>
        )}
        <Group grow={touch}>
          <Button type="submit" color="brand" loading={salvar.isPending} disabled={corpo === null} mih={alt}>
            Lançar
          </Button>
          <Button variant="default" onClick={onCancel} mih={alt}>
            Cancelar
          </Button>
        </Group>
      </Stack>
    </form>
  );
}
