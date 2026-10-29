'use client';

import { Group, Select, Text, TextInput } from '@mantine/core';
import {
  DASHBOARD_WINDOW_OPTIONS,
  dashboardWindowSelectData,
  type DashboardFilters,
  type DashboardWindow,
} from '@/lib/dashboard-filters';

/**
 * Seletor de período ÚNICO do hub `/dashboard/*` — escreve `?window=` (e
 * `?date_from=`/`?date_to=` na janela personalizada) via `useDashboardFilters`,
 * de modo que trocar o período em uma sub-aba e navegar para outra preserva a
 * escolha.
 *
 * Substitui os 6 conjuntos de opções de período que existiam no app. As rotas
 * v2 continuam recebendo `days`, derivado por `dashboardWindowDays()` — o
 * rótulo e o número enviado são sempre o mesmo valor (28 dias é `days=28`).
 *
 * `allowed` restringe as janelas quando a tela só faz sentido em períodos
 * maiores (ex.: `/dashboard/treinos`, que compara sessões do mesmo treino ao
 * longo do tempo).
 */
export function DashboardPeriodSelect({
  filters,
  onChange,
  allowed = DASHBOARD_WINDOW_OPTIONS,
  rangeError,
  label = 'Período',
  width = 200,
}: {
  filters: DashboardFilters;
  onChange: (patch: Partial<DashboardFilters>) => void;
  allowed?: readonly DashboardWindow[];
  rangeError?: string | null;
  label?: string;
  width?: number;
}) {
  const value = (allowed as readonly string[]).includes(filters.window)
    ? filters.window
    : allowed[0];

  return (
    <Group gap="sm" align="flex-end" wrap="wrap">
      <Select
        label={label}
        data={dashboardWindowSelectData(allowed)}
        value={value}
        onChange={(next) => next && onChange({ window: next as DashboardWindow })}
        allowDeselect={false}
        w={width}
      />

      {filters.window === 'custom' && (
        <>
          {/* `type="date"` nativo: o app é export estático e o Mantine
              DatePickerInput exigiria @mantine/dates, hoje fora das deps. */}
          <TextInput
            label="De"
            type="date"
            value={filters.dateFrom ?? ''}
            onChange={(event) => onChange({ dateFrom: event.currentTarget.value || null })}
            w={160}
            error={Boolean(rangeError)}
          />
          <TextInput
            label="Até"
            type="date"
            value={filters.dateTo ?? ''}
            onChange={(event) => onChange({ dateTo: event.currentTarget.value || null })}
            w={160}
            error={Boolean(rangeError)}
          />
        </>
      )}

      {rangeError && (
        <Text size="xs" c="red" style={{ flexBasis: '100%' }}>
          {rangeError} Enquanto isso, a consulta usa o período padrão.
        </Text>
      )}
    </Group>
  );
}
