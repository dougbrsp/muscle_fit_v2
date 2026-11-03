'use client';

import { Button, Group, Skeleton, Stack, Text } from '@mantine/core';
import { IconPlus } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import type { ReactNode } from 'react';
import { diaryKeys, fetchNutritionDay, fetchNutritionWeek } from '@/lib/nutrition-api';
import { nutrientField } from '@/lib/nutrition-constants';
import { macroRows } from '@/lib/nutrition-dashboard';
import { formatTotal, formatWithUnit } from '@/lib/nutrition-format';
import type { BalanceRow } from '@/lib/nutrition-types';
import { MOBILE_TOKENS } from '@/mobile/tokens';

type Props = {
  /**
   * PONTO DE EXTENSÃO do lote N7: o card de check-in semanal entra aqui, acima dos macros do dia,
   * e a home só passa o nó pronto (a avaliação e as metas propostas são do motor; este card não
   * calcula nada). Sem N7 fica vazio.
   */
  checkinSlot?: ReactNode;
  /** Classe do contêiner (a home usa o mesmo cartão das outras seções). */
  className?: string;
};

function MacroLine({ row }: { row: BalanceRow }) {
  const campo = nutrientField(row.field);
  const unidade = campo?.unit ?? '';
  const sobra = row.excess != null && row.excess > 0;
  return (
    <Group justify="space-between" wrap="nowrap" gap="xs">
      <Text size="sm" fw={600} c={MOBILE_TOKENS.text}>
        {campo?.label ?? row.field}
      </Text>
      <Text size="sm" c={MOBILE_TOKENS.textMuted} ta="right">
        {formatTotal(row.intake, unidade, row.intake_complete)} de {formatWithUnit(row.target, unidade, 0)}
        {' · '}
        {sobra ? `acima ${formatWithUnit(row.excess, unidade, 0)}` : `falta ${formatWithUnit(row.to_consume, unidade, 0)}`}
      </Text>
    </Group>
  );
}

/**
 * Macros do dia na home mobile (D12 da reformulação da nutrição): energia, proteína, carboidrato e
 * gordura (consumido, meta e quanto falta) e o atalho para registrar a refeição. Os números vêm do
 * mesmo `GET /nutrition/days/{date}` do diário (mesma chave de cache); nenhum cálculo na tela.
 */
export function NutritionTodayCard({ checkinSlot, className }: Props) {
  // "Hoje" do ALUNO é o da API (fuso do perfil), nunca o do aparelho.
  const semana = useQuery({ queryKey: diaryKeys.week(null, null), queryFn: () => fetchNutritionWeek(null, null), retry: false });
  const hoje = semana.data?.today ?? null;
  const dia = useQuery({
    queryKey: diaryKeys.day(null, hoje ?? ''),
    queryFn: () => fetchNutritionDay(hoje as string, null),
    enabled: hoje !== null,
    retry: false,
  });

  const linhas = dia.data ? macroRows(dia.data.balance.rows) : [];
  const carregando = semana.isLoading || (hoje !== null && dia.isLoading);
  const semMeta = dia.data != null && dia.data.target == null;

  return (
    <section className={className} aria-label="Nutrição de hoje">
      <Text size="xs" fw={700} tt="uppercase" c={MOBILE_TOKENS.textMuted} mb={8}>
        Nutrição de hoje
      </Text>
      {checkinSlot}
      {carregando ? (
        <Stack gap={6} aria-busy="true" aria-label="Carregando a nutrição de hoje">
          <Skeleton height={16} radius="sm" />
          <Skeleton height={16} radius="sm" />
          <Skeleton height={16} radius="sm" />
        </Stack>
      ) : semana.isError || dia.isError ? (
        <Text size="sm" c={MOBILE_TOKENS.textMuted}>
          Não foi possível carregar a nutrição agora. Você ainda pode registrar uma refeição.
        </Text>
      ) : semMeta ? (
        <Text size="sm" c={MOBILE_TOKENS.textMuted}>
          Sem meta para hoje: publique um plano de nutrição para ver o que falta comer.
        </Text>
      ) : (
        <Stack gap={6}>
          {linhas.map((linha) => (
            <MacroLine key={linha.field} row={linha} />
          ))}
        </Stack>
      )}
      <Button
        component={Link}
        href="/nutricao"
        variant="light"
        color="brand"
        fullWidth
        mt="sm"
        leftSection={<IconPlus size={16} />}
        style={{ minHeight: MOBILE_TOKENS.touchTarget }}
      >
        Registrar refeição
      </Button>
    </section>
  );
}
