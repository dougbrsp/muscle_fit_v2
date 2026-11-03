'use client';

import { Alert, Select, Stack, Text } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { nutritionErrorInfo } from '@/lib/nutrition-api';
import { checkinKeys, fetchCheckinSettings, saveCheckinWeekday } from '@/lib/nutrition-checkin';
import { BRAND } from '@/theme';

/**
 * Dia da semana do check-in semanal (D8: o usuário escolhe; padrão segunda). Mora ao lado do formulário do plano,
 * mas é uma preferência própria: salva na hora e não publica versão do plano. As opções e o dia vêm da API.
 */
export function CheckinDayField({ userId }: { userId: number | null }) {
  // O pai usa `key={userId}`: trocar de aluno remonta o campo e zera `salvar.data` (senão mostraria o dia do aluno anterior).
  const qc = useQueryClient();
  const consulta = useQuery({ queryKey: checkinKeys.settings(userId), queryFn: () => fetchCheckinSettings(userId), retry: false });
  const salvar = useMutation({
    mutationFn: (dia: number) => saveCheckinWeekday(dia, userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: checkinKeys.all }),
  });
  const atual = salvar.data ?? consulta.data;
  if (consulta.isError) return null;
  return (
    <Stack gap={4}>
      <Select
        label="Dia do check-in semanal"
        description="A semana que acabou é avaliada neste dia, quando você abrir o app."
        w={260}
        allowDeselect={false}
        disabled={!atual || salvar.isPending}
        data={(atual?.weekday_options ?? []).map((opcao) => ({ value: String(opcao.value), label: opcao.label }))}
        value={atual ? String(atual.weekday) : null}
        onChange={(valor) => valor !== null && salvar.mutate(Number(valor))}
      />
      {atual?.is_default && !salvar.data && (
        <Text size="xs" c={BRAND.textMuted}>
          Padrão: {atual.weekday_label}.
        </Text>
      )}
      {salvar.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} role="alert">
          {nutritionErrorInfo(salvar.error, 'Não foi possível salvar o dia do check-in.').message}
        </Alert>
      )}
    </Stack>
  );
}
