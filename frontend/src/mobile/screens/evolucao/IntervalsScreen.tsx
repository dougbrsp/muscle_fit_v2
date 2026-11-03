'use client';

import { Button } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { LoadChart, MinutesChart, RampRateChart, WellnessChart } from '@/app/(app)/intervals-icu/IntervalsIcuCharts';
import { apiFetch, ApiError } from '@/lib/api';
import { averageOf, sumBy, type IntervalsDashboard, type IntervalsStatus, type IntervalsWellnessManualPatch, type IntervalsWellnessTrendPoint } from '@/lib/intervals-types';
import { formatDateBR, formatNumberOrDash } from '@/lib/physical-indicators-types';
import {
  BottomSheet,
  ListBlock,
  ListGroup,
  ListRow,
  ResponsiveDataList,
  ScreenHeader,
  StatGrid,
  StatTile,
} from '../../ui';
import styles from './evolucao.module.css';
import { QueryGate } from './shared';

const DAY_OPTIONS = [
  { value: '30', label: '30 dias' },
  { value: '90', label: '90 dias' },
  { value: '180', label: '180 dias' },
  { value: '365', label: '12 meses' },
  { value: '1095', label: '3 anos' },
];
const PAGE = 20;

/**
 * `/intervals-icu`: leitura do dashboard do Intervals.icu (mesmas consultas do desktop) e a edição manual de FC de repouso e HRV,
 * agora numa folha. Sem Intervals configurado, a tela explica e leva ao Perfil (mesma condição do desktop).
 */
export function IntervalsScreen() {
  const [days, setDays] = useState('90');
  const [picker, { open: openPicker, close: closePicker }] = useDisclosure(false);
  const [limits, setLimits] = useState({ daily: PAGE, wellness: PAGE, recent: PAGE });
  const [edit, setEdit] = useState<IntervalsWellnessTrendPoint | null>(null);

  const statusQuery = useQuery({
    queryKey: ['intervals-icu-status'],
    queryFn: () => apiFetch<IntervalsStatus>('/integrations/intervals/status'),
  });
  const configured = statusQuery.data?.configured === true;
  const dashboardQuery = useQuery({
    queryKey: ['intervals-icu-dashboard', days],
    queryFn: () => apiFetch<IntervalsDashboard>(`/integrations/intervals/dashboard?days=${days}`),
    enabled: configured,
  });
  const data = dashboardQuery.data;
  const daily = useMemo(() => data?.daily_activity ?? [], [data]);
  const wellnessTrend = useMemo(() => data?.wellness_trend ?? [], [data]);
  const recent = useMemo(() => data?.recent_activities ?? [], [data]);
  const dailyRows = useMemo(() => [...daily].reverse(), [daily]);
  const wellnessRows = useMemo(() => [...wellnessTrend].reverse(), [wellnessTrend]);
  const recentRows = useMemo(() => recent.map((item, index) => ({ item, key: `${item.date}-${item.name}-${index}` })), [recent]);

  const minutesChartData = useMemo(() => daily.map((i) => ({ data: formatDateBR(i.date), minutos: i.minutes })), [daily]);
  const loadChartData = useMemo(() => daily.map((i) => ({ data: formatDateBR(i.date), carga: i.load })), [daily]);
  const wellnessChartData = useMemo(
    () => wellnessTrend.map((i) => ({ data: formatDateBR(i.date), fitness: i.fitness, fatigue: i.fatigue, form: i.form })),
    [wellnessTrend],
  );
  const rampRateChartData = useMemo(() => wellnessTrend.map((i) => ({ data: formatDateBR(i.date), rampa: i.rampRate })), [wellnessTrend]);

  const totalMinutes = sumBy(daily, (i) => i.minutes);
  const totalCalories = sumBy(daily, (i) => i.calories);
  const totalDistance = sumBy(daily, (i) => i.distanceKm);
  const totalLoad = sumBy(daily, (i) => i.load);
  const avgHr = averageOf(daily.map((i) => i.avgHr));

  if (statusQuery.isSuccess && !configured) {
    return (
      <>
        <ScreenHeader title="Intervals.icu" subtitle="Dashboard de wellness e atividades. A sincronização é automática." />
        <ListGroup title="Integração não configurada" footer="Configure sua chave do Intervals.icu no seu perfil para usar esta tela.">
          <ListRow title="Ir para o perfil" href="/perfil" />
        </ListGroup>
      </>
    );
  }

  return (
    <>
      <ScreenHeader
        title="Intervals.icu"
        subtitle="Atividades ao vivo; wellness do histórico salvo, atualizado ao abrir e diariamente."
      />
      <ListGroup>
        <ListRow title="Janela" value={DAY_OPTIONS.find((o) => o.value === days)?.label} onClick={openPicker} />
      </ListGroup>
      <BottomSheet opened={picker} onClose={closePicker} title="Janela">
        <ListGroup>
          {DAY_OPTIONS.map((o) => (
            <ListRow
              key={o.value}
              title={o.label}
              current={o.value === days}
              onClick={() => {
                setDays(o.value);
                setLimits({ daily: PAGE, wellness: PAGE, recent: PAGE });
                closePicker();
              }}
            />
          ))}
        </ListGroup>
      </BottomSheet>

      <QueryGate
        query={{
          data,
          isLoading: statusQuery.isLoading || (configured && dashboardQuery.isLoading),
          isError: statusQuery.isError || dashboardQuery.isError,
          error: statusQuery.error ?? dashboardQuery.error,
          refetch: () => (statusQuery.isError ? statusQuery.refetch() : dashboardQuery.refetch()),
        }}
        title="Intervals.icu"
        rows={4}
      >
        {(d) => (
          <>
            <ListGroup>
              <ListBlock>
                <StatGrid>
                  <StatTile label="Atividades" value={String(d.activity_count)} />
                  <StatTile label="Tempo total" value={`${totalMinutes.toLocaleString('pt-BR')} min`} />
                  <StatTile label="Carga (training load)" value={Math.round(totalLoad).toLocaleString('pt-BR')} />
                  <StatTile
                    label="FC média"
                    value={avgHr ? avgHr.toFixed(0) : '-'}
                    hint={`${totalDistance.toFixed(1)} km · ${Math.round(totalCalories).toLocaleString('pt-BR')} kcal`}
                  />
                </StatGrid>
              </ListBlock>
            </ListGroup>

            {minutesChartData.length > 0 || loadChartData.length > 0 ? (
              <>
                <ListGroup title="Minutos por dia">
                  <div className={styles.chartBlock} role="img" aria-label="Minutos por dia">
                    <MinutesChart minutesChartData={minutesChartData} />
                  </div>
                </ListGroup>
                <ListGroup title="Carga (training load) por dia">
                  <div className={styles.chartBlock} role="img" aria-label="Carga por dia">
                    <LoadChart loadChartData={loadChartData} />
                  </div>
                </ListGroup>
              </>
            ) : null}
            {wellnessChartData.length > 0 || rampRateChartData.length > 0 ? (
              <>
                <ListGroup title="Fitness, fadiga e forma">
                  <div className={styles.chartBlock} role="img" aria-label="Fitness, fadiga e forma por dia">
                    <WellnessChart wellnessChartData={wellnessChartData} />
                  </div>
                </ListGroup>
                <ListGroup title="Ramp rate">
                  <div className={styles.chartBlock} role="img" aria-label="Ramp rate por dia">
                    <RampRateChart rampRateChartData={rampRateChartData} />
                  </div>
                </ListGroup>
              </>
            ) : null}

            <p className={styles.sectionTitle}>Atividades por dia ({daily.length} dias)</p>
            <ResponsiveDataList
              aria-label="Atividades por dia"
              items={dailyRows.slice(0, limits.daily)}
              getKey={(i) => i.date}
              emptyTitle="Nenhuma atividade no período selecionado"
              renderItem={(i) => (
                <ListRow
                  title={formatDateBR(i.date)}
                  subtitle={`${i.activities} atividade(s) · ${i.calories || '-'} kcal · ${i.distanceKm.toFixed(2)} km · carga ${formatNumberOrDash(i.load, 1)} · FC ${i.avgHr != null ? i.avgHr.toFixed(0) : '-'}`}
                  value={`${i.minutes} min`}
                />
              )}
            />
            {dailyRows.length > limits.daily ? (
              <div className={styles.pager}>
                <Button variant="default" mih={44} onClick={() => setLimits((l) => ({ ...l, daily: l.daily + PAGE }))}>
                  Mostrar mais
                </Button>
              </div>
            ) : null}

            <p className={styles.sectionTitle}>Fitness, fadiga e forma ({wellnessTrend.length} dias)</p>
            <p className={styles.note} style={{ marginBottom: 8 }}>
              Wellness do Intervals.icu (CTL/ATL/TSB, HRV, FC de repouso e ramp rate). FC de repouso e HRV podem ser editados; o
              Intervals sobrescreve se enviar o dado do dia. Toque num dia para editar.
            </p>
            <ResponsiveDataList
              aria-label="Wellness por dia"
              items={wellnessRows.slice(0, limits.wellness)}
              getKey={(i) => i.date}
              emptyTitle="Nenhum dado de wellness no período selecionado"
              renderItem={(i) => (
                <ListRow
                  title={formatDateBR(i.date)}
                  subtitle={`CTL ${formatNumberOrDash(i.fitness, 1)} · ATL ${formatNumberOrDash(i.fatigue, 1)} · TSB ${formatNumberOrDash(i.form, 1)} · ramp ${formatNumberOrDash(i.rampRate, 1)}`}
                  value={`FC ${formatNumberOrDash(i.restingHr, 0)}${i.restingHrSource === 'manual' ? ' ✎' : ''} · HRV ${formatNumberOrDash(i.hrv, 0)}${i.hrvSource === 'manual' ? ' ✎' : ''}`}
                  onClick={() => setEdit(i)}
                />
              )}
            />
            {wellnessRows.length > limits.wellness ? (
              <div className={styles.pager}>
                <Button variant="default" mih={44} onClick={() => setLimits((l) => ({ ...l, wellness: l.wellness + PAGE }))}>
                  Mostrar mais
                </Button>
              </div>
            ) : null}

            <p className={styles.sectionTitle}>Atividades recentes ({recent.length})</p>
            <p className={styles.note} style={{ marginBottom: 8 }}>
              Últimas atividades lidas do Intervals.icu (até 30). A gravação no banco é feita pelo sync automático diário.
            </p>
            <ResponsiveDataList
              aria-label="Atividades recentes"
              items={recentRows.slice(0, limits.recent)}
              getKey={(r) => r.key}
              emptyTitle="Nenhuma atividade recente encontrada"
              renderItem={({ item }) => (
                <ListRow
                  title={item.name}
                  subtitle={`${formatDateBR(item.date)} · ${item.sport} · ${item.calories || '-'} kcal · FC ${item.avgHr ?? '-'} · carga ${item.load ?? '-'}`}
                  value={`${item.minutes} min`}
                />
              )}
            />
            {recentRows.length > limits.recent ? (
              <div className={styles.pager}>
                <Button variant="default" mih={44} onClick={() => setLimits((l) => ({ ...l, recent: l.recent + PAGE }))}>
                  Mostrar mais
                </Button>
              </div>
            ) : null}
          </>
        )}
      </QueryGate>

      <BottomSheet opened={edit !== null} onClose={() => setEdit(null)} title={edit ? `Wellness de ${formatDateBR(edit.date)}` : 'Wellness'}>
        {edit ? <EditorWellness key={edit.date} ponto={edit} onDone={() => setEdit(null)} /> : null}
      </BottomSheet>
    </>
  );
}

function EditorWellness({ ponto, onDone }: { ponto: IntervalsWellnessTrendPoint; onDone: () => void }) {
  const qc = useQueryClient();
  const [rhr, setRhr] = useState(ponto.restingHr == null ? '' : String(ponto.restingHr));
  const [hrv, setHrv] = useState(ponto.hrv == null ? '' : String(ponto.hrv));
  const [erro, setErro] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: (patch: IntervalsWellnessManualPatch) =>
      apiFetch(`/integrations/intervals/wellness/${ponto.date}`, { method: 'PATCH', body: JSON.stringify(patch) }),
    onSuccess: () => {
      setErro(null);
      qc.invalidateQueries({ queryKey: ['intervals-icu-dashboard'] });
      onDone();
    },
    onError: (err) => setErro(err instanceof ApiError ? err.message : 'Não foi possível salvar.'),
  });

  function salvar() {
    const patch: IntervalsWellnessManualPatch = {};
    for (const [campo, texto, atual] of [
      ['resting_hr_bpm', rhr, ponto.restingHr],
      ['hrv_ms', hrv, ponto.hrv],
    ] as const) {
      const t = texto.trim();
      // Campo vazio não apaga nada: limpar a edição é a ação própria.
      if (t === '' || Number(t) === atual) continue;
      const n = Number(t);
      if (!Number.isFinite(n)) {
        setErro('Informe um número.');
        return;
      }
      patch[campo] = n;
    }
    if (Object.keys(patch).length === 0) {
      onDone();
      return;
    }
    mutation.mutate(patch);
  }

  return (
    <div className={styles.stack}>
      <p className={styles.meta} style={{ margin: 0 }}>
        Editado manualmente, o valor será substituído se o Intervals enviar dado deste dia. A faixa aceita é validada pelo servidor.
      </p>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor="mob-int-rhr">FC de repouso (bpm){ponto.restingHrSource === 'manual' ? ' · editado manualmente' : ''}</label>
        <input id="mob-int-rhr" type="number" inputMode="decimal" className={styles.dateInput} value={rhr} onChange={(e) => setRhr(e.currentTarget.value)} disabled={mutation.isPending} />
      </div>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor="mob-int-hrv">HRV (ms){ponto.hrvSource === 'manual' ? ' · editado manualmente' : ''}</label>
        <input id="mob-int-hrv" type="number" inputMode="decimal" className={styles.dateInput} value={hrv} onChange={(e) => setHrv(e.currentTarget.value)} disabled={mutation.isPending} />
      </div>
      {erro ? <p className={styles.err} role="alert">{erro}</p> : null}
      <Button color="brand" fullWidth mih={44} loading={mutation.isPending} onClick={salvar}>
        Salvar
      </Button>
      {ponto.restingHrSource === 'manual' ? (
        <Button variant="default" fullWidth mih={44} disabled={mutation.isPending} onClick={() => mutation.mutate({ resting_hr_bpm: null })}>
          Limpar edição manual da FC de repouso
        </Button>
      ) : null}
      {ponto.hrvSource === 'manual' ? (
        <Button variant="default" fullWidth mih={44} disabled={mutation.isPending} onClick={() => mutation.mutate({ hrv_ms: null })}>
          Limpar edição manual do HRV
        </Button>
      ) : null}
    </div>
  );
}
