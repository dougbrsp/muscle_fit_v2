'use client';

import { Alert, Button, Group, Modal, Select, Stack, Text, Textarea } from '@mantine/core';
import { IconAlertTriangle, IconSparkles } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { MEAL_PARSE_QUOTA_EXCEEDED_LABEL, MEAL_PARSE_TEXT_MAX_CHARS, MEAL_SLOT_OPTIONS } from '@/lib/nutrition-constants';
import { fetchMealParseQuota, mealParseKeys, nutritionErrorInfo, parseMealText } from '@/lib/nutrition-api';
import type { MealEditorForm } from '@/lib/nutrition-meal-form';
import { editorFormFromParse } from '@/lib/nutrition-meal-parse';
import { BRAND } from '@/theme';

type Props = {
  opened: boolean;
  onClose: () => void;
  /** Aluno do diário (admin/PT) ou `null` (o próprio usuário). */
  userId: number | null;
  date: string;
  /** A leitura terminou: o editor da refeição abre com os itens para confirmar. */
  onParsed: (form: MealEditorForm) => void;
};

/**
 * Leitura da refeição por IA (Lote L5b): a pessoa descreve o que comeu, a API
 * dispara o job (`POST /nutrition/meals/parse`, 202) e a tela espera o
 * resultado (`dispatchAndPollJob`). Os itens, a escolha do alimento no
 * catálogo e a quantidade proposta vêm prontos do motor; a gravação é a do
 * editor de refeição de sempre. A cota do dia (de quem pede) vem da API.
 */
export function MealParseModal({ opened, onClose, userId, date, onParsed }: Props) {
  const qc = useQueryClient();
  const [texto, setTexto] = useState('');
  const [slot, setSlot] = useState<string | null>(null);

  const cota = useQuery({
    queryKey: mealParseKeys.quota,
    queryFn: fetchMealParseQuota,
    enabled: opened,
    retry: false,
    staleTime: 30_000,
  });

  const ler = useMutation({
    mutationFn: () =>
      parseMealText({
        text: texto,
        local_date: date,
        meal_slot: slot,
        ...(userId != null ? { user_id: userId } : {}),
      }),
    onSuccess: (resultado) => {
      onParsed(editorFormFromParse(resultado, { date, slot, text: texto }));
      setTexto('');
    },
    onSettled: () => qc.invalidateQueries({ queryKey: mealParseKeys.quota }),
  });

  const esgotada = cota.data ? !cota.data.allowed : false;
  const erro = ler.isError ? nutritionErrorInfo(ler.error, 'Não foi possível ler a refeição.') : null;

  return (
    <Modal opened={opened} onClose={onClose} title="Descrever a refeição" size="lg">
      <Stack gap="sm">
        <Text size="sm" c={BRAND.textMuted}>
          Escreva o que você comeu, com as quantidades se souber. A IA separa os alimentos e o diário procura cada um
          no catálogo; você confere tudo antes de salvar.
        </Text>
        <Textarea
          label="O que você comeu?"
          placeholder="Ex.: arroz, feijão, um bife grelhado e salada"
          value={texto}
          onChange={(e) => setTexto(e.currentTarget.value)}
          maxLength={MEAL_PARSE_TEXT_MAX_CHARS}
          autosize
          minRows={3}
          disabled={ler.isPending}
        />
        <Select
          label="Refeição (opcional)"
          placeholder="Escolha"
          data={[...MEAL_SLOT_OPTIONS]}
          value={slot}
          onChange={setSlot}
          clearable
          w={260}
          disabled={ler.isPending}
        />
        {cota.data && (
          <Text size="xs" c={BRAND.textMuted}>
            Leituras por IA hoje: {cota.data.used} de {cota.data.limit}.
          </Text>
        )}
        {esgotada && (
          <Alert color="yellow" icon={<IconAlertTriangle size={16} />}>
            {MEAL_PARSE_QUOTA_EXCEEDED_LABEL}
          </Alert>
        )}
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
        <Group justify="flex-end">
          <Button variant="subtle" color="gray" onClick={onClose} disabled={ler.isPending}>
            Cancelar
          </Button>
          <Button
            color="brand"
            leftSection={<IconSparkles size={16} />}
            onClick={() => ler.mutate()}
            loading={ler.isPending}
            disabled={!texto.trim() || esgotada}
          >
            Ler com IA
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
