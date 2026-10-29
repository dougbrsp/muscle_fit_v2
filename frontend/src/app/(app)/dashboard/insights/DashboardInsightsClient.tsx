'use client';

import { Button, Group, Stack, Switch, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { DashboardPeriodSelect } from '@/components/dashboard/DashboardPeriodSelect';
import { InsightCard } from '@/components/dashboard/InsightCard';
import { AllClearState, DashboardCardSkeleton, ErrorState, PartialState } from '@/components/dashboard/states';
import { dashboardKeys, fetchInsights } from '@/lib/dashboard-api';
import { useDashboardFilters } from '@/lib/dashboard-filters';
import { INSIGHTS_BULK_LIMIT, needsServerInsightsPage, sliceInsightsPage } from '@/lib/dashboard-insights-page';
import { fmtInt } from '@/lib/dashboard-format';
import { NO_ACTIONS_MESSAGE } from '@/lib/dashboard-overview';
import { BRAND } from '@/theme';

const PAGE_SIZE = 20;

/**
 * `/dashboard/insights` — TODOS os insights disparados na janela (F1.6),
 * deduplicados por fingerprint e ordenados por severidade e confiança, com
 * paginação simples. É a mesma avaliação da visão geral: as duas primeiras
 * entradas daqui são as que a home mostra em "Próxima ação".
 *
 * F2.2 — cooldown: por padrão a lista traz só o que você ainda não viu (ou viu e
 * PIOROU). O que ficou de fora é contado e fica a um clique de distância — some
 * do caminho, nunca do alcance. Um cartão que desaparece sem explicação é
 * indistinguível de defeito; por isso o selo de cada cartão diz em que pé ele
 * está e a contagem dos silenciados aparece mesmo quando eles não aparecem.
 */
export function DashboardInsightsClient() {
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();
  const [offset, setOffset] = useState(0);
  const [mostrarSilenciados, setMostrarSilenciados] = useState(false);

  // Trocar de janela (ou de recorte) volta para a primeira página.
  useEffect(() => {
    setOffset(0);
  }, [windowQuery.window, windowQuery.date_from, windowQuery.date_to, mostrarSilenciados]);

  // PERF-02: o servidor reavalia a visão geral inteira a cada chamada. Pede-se a
  // lista completa UMA vez por (janela, silenciados) e a página é recortada aqui:
  // "Próximas"/"Anteriores" não custam chamada. Só uma lista acima do máximo do
  // endpoint volta a paginar no servidor (`needsServerInsightsPage`).
  const lista = useQuery({
    queryKey: [...dashboardKeys.insights(windowQuery, INSIGHTS_BULK_LIMIT, 0), mostrarSilenciados],
    queryFn: () => fetchInsights(windowQuery, INSIGHTS_BULK_LIMIT, 0, mostrarSilenciados),
  });
  const bulk = lista.data;
  const precisaServidor = bulk !== undefined && needsServerInsightsPage(bulk, offset, PAGE_SIZE);
  const paginaServidor = useQuery({
    queryKey: [...dashboardKeys.insights(windowQuery, PAGE_SIZE, offset), mostrarSilenciados],
    queryFn: () => fetchInsights(windowQuery, PAGE_SIZE, offset, mostrarSilenciados),
    enabled: precisaServidor,
  });
  const consulta = precisaServidor ? paginaServidor : lista;
  const payload = useMemo(
    () =>
      bulk === undefined ? undefined : precisaServidor ? paginaServidor.data : sliceInsightsPage(bulk, offset, PAGE_SIZE),
    [bulk, precisaServidor, paginaServidor.data, offset],
  );

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <div>
          <Title order={2} c={BRAND.textPrimary}>
            Sugestões
          </Title>
          <Text size="sm" c={BRAND.textMuted}>
            Tudo que as regras determinísticas dispararam no período. Nenhuma sugestão muda ficha, meta ou
            prescrição: você decide.
          </Text>
        </div>
        <DashboardPeriodSelect filters={filters} onChange={setFilters} rangeError={rangeError} />
      </Group>

      {payload?.cooldown_available && (payload.suppressed_total > 0 || mostrarSilenciados) && (
        <Switch
          checked={mostrarSilenciados}
          onChange={(event) => setMostrarSilenciados(event.currentTarget.checked)}
          color="brand"
          label={
            mostrarSilenciados
              ? 'Mostrando também os que você já viu'
              : `Mostrar também ${fmtInt(payload.suppressed_total)} que você já viu`
          }
          description="Um alerta já visto e ainda no mesmo estado fica quieto por um período. Se ele piorar, aparece mesmo assim."
        />
      )}

      {consulta.isLoading && <DashboardCardSkeleton rows={6} />}
      {consulta.isError && <ErrorState title="Não foi possível carregar as sugestões." error={consulta.error} />}

      {payload && (
        <>
          <Text size="xs" c={BRAND.textMuted}>
            {fmtInt(payload.total)} sugestão(ões) · {fmtInt(payload.rules_evaluated)} regras avaliadas
            {payload.rules_disabled.length > 0 && ` · desligadas: ${payload.rules_disabled.join(', ')}`}
            {!mostrarSilenciados &&
              payload.suppressed_total > 0 &&
              ` · ${fmtInt(payload.suppressed_total)} silenciada(s) por já ter(em) sido vista(s)`}
          </Text>

          <PartialState quality={payload.data_quality} limitations={payload.limitations} fixHref={null} />

          {payload.items.length === 0 ? (
            <AllClearState>
              {payload.suppressed_total > 0
                ? 'Nada novo por aqui: o que as regras encontraram no período você já viu. Ligue a chave acima para rever.'
                : NO_ACTIONS_MESSAGE}
            </AllClearState>
          ) : (
            <Stack gap="md">
              {payload.items.map((insight) => (
                <InsightCard key={insight.fingerprint} insight={insight} filters={filters} />
              ))}
            </Stack>
          )}

          {(offset > 0 || payload.has_more) && (
            <Group justify="space-between">
              <Button variant="light" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                Anteriores
              </Button>
              <Text size="sm" c={BRAND.textMuted}>
                {fmtInt(offset + 1)}–{fmtInt(offset + payload.items.length)} de {fmtInt(payload.total)}
              </Text>
              <Button variant="light" disabled={!payload.has_more} onClick={() => setOffset(offset + PAGE_SIZE)}>
                Próximas
              </Button>
            </Group>
          )}
        </>
      )}
    </Stack>
  );
}
