'use client';

import { useQuery } from '@tanstack/react-query';
import dynamic from 'next/dynamic';
import { useMemo } from 'react';
import { ChartPlaceholder } from '@/components/charts/ChartPlaceholder';
import { DashboardCardSkeleton, EmptyState, ErrorState } from '@/components/dashboard/states';
import { dashboardKeys, fetchTrainingTrend } from '@/lib/dashboard-api';
import { isoWeekRows } from '@/lib/dashboard-training-analytics';
import { BlocoCard } from './BlocoCard';

// COD-14: recharts sob demanda; o placeholder tem a altura do gráfico (260).
const EvolucaoSemanalChart = dynamic(() => import('./TreinosCharts').then((m) => m.EvolucaoSemanalChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={260} />,
});

/**
 * "Evolução por semana ISO" (herdado de `/treinos`):
 * `GET /analytics/training-trend?period=week&days=`. Semana de CALENDÁRIO —
 * diferente da "Tendência semanal (janela móvel)" do snapshot da IA.
 */
export function EvolucaoSemanalBloco({ days }: { days: number }) {
  const consulta = useQuery({
    queryKey: dashboardKeys.trainingTrend(days),
    queryFn: () => fetchTrainingTrend(days),
  });
  const linhas = useMemo(() => isoWeekRows(consulta.data?.items), [consulta.data]);

  if (consulta.isLoading) return <DashboardCardSkeleton rows={6} />;
  if (consulta.isError) {
    return <ErrorState title="Não foi possível carregar a evolução semanal." error={consulta.error} />;
  }

  return (
    <BlocoCard
      titulo="Evolução por semana ISO"
      descricao={`Volume por semana de calendário (ISO) nos últimos ${days} dias.`}
    >
      {linhas.length === 0 ? (
        <EmptyState description="Sem treinos registrados no período selecionado." />
      ) : (
        <EvolucaoSemanalChart linhas={linhas} />
      )}
    </BlocoCard>
  );
}
