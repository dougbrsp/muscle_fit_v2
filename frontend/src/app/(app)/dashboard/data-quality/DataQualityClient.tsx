'use client';

import { Alert, Badge, Button, Group, List, Paper, Stack, Text, Title } from '@mantine/core';
import { IconArrowRight, IconInfoCircle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { useMemo } from 'react';
import { DashboardPeriodSelect } from '@/components/dashboard/DashboardPeriodSelect';
import {
  DataQualityBadge,
  SEVERITY_DESCRIPTIONS,
  SEVERITY_ORDER,
  SeverityBadge,
} from '@/components/dashboard/SeverityBadge';
import { AllClearState, DashboardCardSkeleton, ErrorState } from '@/components/dashboard/states';
import { dashboardKeys, fetchDataQuality } from '@/lib/dashboard-api';
import { useDashboardFilters } from '@/lib/dashboard-filters';
import { fmtDate, fmtInt } from '@/lib/dashboard-format';
import type { DataQualityItem, Severity } from '@/lib/dashboard-v3-types';
import { BRAND } from '@/theme';

function ItemCard({ item }: { item: DataQualityItem }) {
  return (
    <Paper withBorder radius="md" p="md" style={{ background: BRAND.bgSurface, borderColor: BRAND.border }}>
      <Group justify="space-between" align="flex-start" wrap="wrap" gap="xs">
        <div style={{ flex: '1 1 220px' }}>
          <Group gap="xs" wrap="wrap">
            <Text fw={700} c={BRAND.textPrimary}>
              {item.label}
            </Text>
            <Badge color="brand" variant="light" radius="sm">
              {fmtInt(item.count)}
            </Badge>
            {item.scope === 'catalog' && (
              <Badge color="gray" variant="outline" radius="sm" title="Item de cadastro: não depende do período">
                Cadastro
              </Badge>
            )}
          </Group>
          <Text size="sm" c={BRAND.textMuted} mt={4}>
            {item.description}
          </Text>
        </div>
        <Button
          component={Link}
          href={item.fix_link}
          size="xs"
          variant="light"
          color="brand"
          rightSection={<IconArrowRight size={14} />}
        >
          Corrigir
        </Button>
      </Group>

      {item.examples.length > 0 && (
        <List size="sm" mt="sm" spacing={2} c={BRAND.textMuted}>
          {item.examples.map((exemplo, indice) => (
            <List.Item key={`${item.code}-${exemplo.id ?? indice}`}>
              {exemplo.label}
              {exemplo.detail ? ` — ${maybeDate(exemplo.detail)}` : ''}
            </List.Item>
          ))}
        </List>
      )}

      {item.count > item.examples.length && item.examples.length > 0 && (
        <Text size="xs" c="dimmed" mt={4}>
          Mostrando {item.examples.length} de {item.count}.
        </Text>
      )}

      {item.limitations.map((limite) => (
        <Text key={limite} size="xs" c="dimmed" mt={4}>
          {limite}
        </Text>
      ))}
    </Paper>
  );
}

/** `detail` pode ser uma data ISO (exemplos de sessão) — formata quando for. */
function maybeDate(detail: string): string {
  return /^\d{4}-\d{2}-\d{2}/.test(detail) ? `${fmtDate(detail)}${detail.slice(10)}` : detail;
}

/**
 * `/dashboard/data-quality` — "dá para concluir alguma coisa com o que está
 * registrado?" (plano §8.3, F0.3).
 *
 * Endpoint: `GET /analytics/dashboard/data-quality`
 * (`api/app/analytics_dashboard.py`). Deliberadamente SEM "score": a tela
 * lista problemas concretos, a frase do que cada um impede de concluir e o
 * link da tela que resolve. Nenhuma regra é reavaliada aqui — severidade,
 * contagem e texto vêm do backend.
 *
 * Itens marcados como "Cadastro" (`scope: 'catalog'`) ignoram o período de
 * propósito: uma contribuição muscular que falta não deixa de faltar porque a
 * janela é curta.
 */
export function DataQualityClient() {
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();

  const dataQualityQuery = useQuery({
    queryKey: dashboardKeys.dataQuality(windowQuery),
    queryFn: () => fetchDataQuality(windowQuery),
  });

  const data = dataQualityQuery.data;

  const porSeveridade = useMemo(() => {
    const grupos = new Map<Severity, DataQualityItem[]>();
    for (const item of data?.items ?? []) {
      grupos.set(item.severity, [...(grupos.get(item.severity) ?? []), item]);
    }
    return SEVERITY_ORDER.filter((severidade) => grupos.has(severidade)).map((severidade) => ({
      severidade,
      itens: grupos.get(severidade) ?? [],
    }));
  }, [data]);

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <div>
          <Title order={2} c={BRAND.textPrimary}>
            Qualidade dos dados
          </Title>
          <Text size="sm" c={BRAND.textMuted}>
            O que está faltando ou inconsistente no que foi registrado — e o que isso impede de concluir.
          </Text>
        </div>
        <DashboardPeriodSelect filters={filters} onChange={setFilters} rangeError={rangeError} />
      </Group>

      {dataQualityQuery.isLoading && <DashboardCardSkeleton rows={5} />}
      {dataQualityQuery.isError && (
        <ErrorState title="Não foi possível carregar a qualidade dos dados." error={dataQualityQuery.error} />
      )}

      {data && (
        <Group gap="xs" wrap="wrap">
          <DataQualityBadge quality={data.overall} label="Confiança nas conclusões" />
          <Text size="xs" c="dimmed">
            {fmtInt(data.checked_rules)} verificações · {fmtDate(data.window.date_from)} a{' '}
            {fmtDate(data.window.date_to)}
          </Text>
        </Group>
      )}

      {data && data.items.length === 0 && (
        <AllClearState>Nenhum problema de qualidade encontrado no período.</AllClearState>
      )}

      {porSeveridade.map(({ severidade, itens }) => (
        <Stack key={severidade} gap="xs">
          <Group gap="xs">
            <SeverityBadge severity={severidade} />
            <Text size="sm" c={BRAND.textMuted}>
              {SEVERITY_DESCRIPTIONS[severidade]}
            </Text>
          </Group>
          {itens.map((item) => (
            <ItemCard key={item.code} item={item} />
          ))}
        </Stack>
      ))}

      {data && data.limitations.length > 0 && (
        <Alert color="gray" variant="light" icon={<IconInfoCircle size={18} />}>
          <List size="sm" spacing={2}>
            {data.limitations.map((limite) => (
              <List.Item key={limite}>{limite}</List.Item>
            ))}
          </List>
        </Alert>
      )}

    </Stack>
  );
}
