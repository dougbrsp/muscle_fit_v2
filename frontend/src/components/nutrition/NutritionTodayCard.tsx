'use client';

import { Button, Group, Skeleton, Stack, Text } from '@mantine/core';
import { IconPlus } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import type { ReactNode } from 'react';
import { nutrientField } from '@/lib/nutrition-constants';
import { fetchNutritionToday, macroRows, todayKeys, type NutritionToday } from '@/lib/nutrition-dashboard';
import { formatTotal, formatWithUnit } from '@/lib/nutrition-format';
import type { BalanceRow } from '@/lib/nutrition-types';
import { MOBILE_TOKENS } from '@/mobile/tokens';

export type TodayCardState = 'loading' | 'offline' | 'error' | 'no_target' | 'ready';

/** Estado do card a partir da consulta: pausada (sem rede) não é erro, e sem dado nem erro ainda é carga. */
export function todayCardState(query: {
  data: NutritionToday | undefined;
  isError: boolean;
  fetchStatus: 'fetching' | 'paused' | 'idle';
}): TodayCardState {
  if (query.data) return query.data.target == null ? 'no_target' : 'ready';
  if (query.fetchStatus === 'paused') return 'offline';
  if (query.isError) return 'error';
  return 'loading';
}

type ViewProps = {
  state: TodayCardState;
  today?: NutritionToday;
  /**
   * PONTO DE EXTENSÃO do lote N7: o card de check-in semanal entra aqui, acima dos macros do dia,
   * e a home só passa o nó pronto (a avaliação e as metas propostas são do motor; este card não
   * calcula nada). Sem check-in a mostrar, o `CheckinCard` não renderiza e o slot fica vazio.
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
        {formatTotal(row.intake ?? row.within_target, unidade, row.intake == null || row.intake_complete)} de{' '}
        {formatWithUnit(row.target, unidade, 0)}
        {' · '}
        {sobra ? `acima ${formatWithUnit(row.excess, unidade, 0)}` : `falta ${formatWithUnit(row.to_consume, unidade, 0)}`}
      </Text>
    </Group>
  );
}

const MESSAGES: Record<Exclude<TodayCardState, 'loading' | 'ready'>, string> = {
  offline: 'Sem conexão: os macros de hoje aparecem quando a rede voltar. Você ainda pode abrir o diário.',
  error: 'Não foi possível carregar a nutrição agora. Você ainda pode registrar uma refeição.',
  no_target: 'Sem meta para hoje: crie e publique o seu plano de nutrição para ver o que falta comer.',
};

/** Apresentação do card (sem consulta): testável por SSR em cada estado. */
export function NutritionTodayView({ state, today, checkinSlot, className }: ViewProps) {
  const linhas = state === 'ready' && today ? macroRows(today.balance.rows) : [];
  return (
    <section className={className} aria-label="Nutrição de hoje">
      <Text size="xs" fw={700} tt="uppercase" c={MOBILE_TOKENS.textMuted} mb={8}>
        Nutrição de hoje
      </Text>
      {checkinSlot}
      {state === 'loading' ? (
        <Stack gap={6} aria-busy="true" aria-label="Carregando a nutrição de hoje">
          <Skeleton height={16} radius="sm" />
          <Skeleton height={16} radius="sm" />
          <Skeleton height={16} radius="sm" />
        </Stack>
      ) : state === 'ready' ? (
        <Stack gap={6}>
          {linhas.map((linha) => (
            <MacroLine key={linha.field} row={linha} />
          ))}
        </Stack>
      ) : (
        <Text size="sm" c={MOBILE_TOKENS.textMuted} role="status">
          {MESSAGES[state]}
        </Text>
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

/**
 * Macros do dia na home mobile (D12 da reformulação da nutrição): energia, proteína, carboidrato e
 * gordura (consumido, meta e quanto falta) e o atalho para registrar a refeição. Os números vêm de
 * `GET /nutrition/today` (só hoje); nenhum cálculo na tela.
 */
export function NutritionTodayCard({ checkinSlot, className }: Pick<ViewProps, 'checkinSlot' | 'className'>) {
  const consulta = useQuery({ queryKey: todayKeys.one(), queryFn: () => fetchNutritionToday(), retry: false });
  return <NutritionTodayView state={todayCardState(consulta)} today={consulta.data} checkinSlot={checkinSlot} className={className} />;
}
