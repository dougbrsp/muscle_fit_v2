'use client';

import { useQuery } from '@tanstack/react-query';
import dynamic from 'next/dynamic';
import { useMemo } from 'react';
import { ChartPlaceholder } from '@/components/charts/ChartPlaceholder';
import { DashboardCardSkeleton, EmptyState, ErrorState } from '@/components/dashboard/states';
import { dashboardKeys, fetchRpeDistribution } from '@/lib/dashboard-api';
import { fmtInt } from '@/lib/dashboard-format';
import { rpeRows, totalRpeSets } from '@/lib/dashboard-training-analytics';
import { BlocoCard } from './BlocoCard';

// COD-14: recharts sob demanda; o placeholder tem a altura do gráfico (260).
const DistribuicaoRpeChart = dynamic(() => import('./TreinosCharts').then((m) => m.DistribuicaoRpeChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={260} />,
});

/** "Distribuição de RPE" (herdado de `/treinos`): `GET /analytics/rpe-distribution?days=`. */
export function DistribuicaoRpeBloco({ days }: { days: number }) {
  const consulta = useQuery({
    queryKey: dashboardKeys.rpeDistribution(days),
    queryFn: () => fetchRpeDistribution(days),
  });
  const linhas = useMemo(() => rpeRows(consulta.data?.items), [consulta.data]);

  if (consulta.isLoading) return <DashboardCardSkeleton rows={6} />;
  if (consulta.isError) {
    return <ErrorState title="Não foi possível carregar a distribuição de RPE." error={consulta.error} />;
  }

  return (
    <BlocoCard
      titulo="Distribuição de RPE"
      descricao={
        linhas.length > 0
          ? `${fmtInt(totalRpeSets(linhas))} série(s) com RPE nos últimos ${days} dias, por nota.`
          : `Séries por nota de RPE nos últimos ${days} dias.`
      }
    >
      {linhas.length === 0 ? (
        <EmptyState description="Sem séries registradas com RPE no período selecionado." />
      ) : (
        <DistribuicaoRpeChart linhas={linhas} />
      )}
    </BlocoCard>
  );
}
