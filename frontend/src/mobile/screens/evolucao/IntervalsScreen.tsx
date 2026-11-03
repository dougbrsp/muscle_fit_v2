'use client';

import { Button } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Bar, BarChart, Legend, Line, LineChart, ReferenceLine, Tooltip, XAxis, YAxis } from 'recharts';
import {
  CHART_COLORS,
  CHART_TOOLTIP_CONTENT_STYLE,
  CHART_TOOLTIP_ITEM_STYLE,
  CHART_TOOLTIP_LABEL_STYLE,
} from '@/components/charts/chartTheme';
import { apiFetch, ApiError } from '@/lib/api';
import { fmtDecimal } from '@/lib/dashboard-format';
import { averageOf, sumBy, type IntervalsDashboard, type IntervalsStatus, type IntervalsWellnessManualPatch, type IntervalsWellnessTrendPoint } from '@/lib/intervals-types';
import { formatDateBR, formatNumberOrDash } from '@/lib/physical-indicators-types';
import {
  BottomSheet,
  ListBlock,
  ListGroup,
  ListRow,
  MOBILE_CHART_AXIS,
  MOBILE_CHART_MARGIN,
  MobileChart,
  ResponsiveDataList,
  ScreenHeader,
  StatGrid,
  StatTile,
  useOnlineStatus,
} from '../../ui';
import styles from './evolucao.module.css';
import { plural } from './pure';
import { OfflineNote, QueryGate, SheetRow } from './shared';

const DAY_OPTIONS = [
  { value: '30', label: '30 dias' },
  { value: '90', label: '90 dias' },
  { value: '180', label: '180 dias' },
  { value: '365', label: '12 meses' },
  { value: '1095', label: '3 anos' },
];
const PAGE = 7;
const TOOLTIP = {
  contentStyle: CHART_TOOLTIP_CONTENT_STYLE,
  labelStyle: CHART_TOOLTIP_LABEL_STYLE,
  itemStyle: CHART_TOOLTIP_ITEM_STYLE,
};

/**
 * `/intervals-icu`: leitura do dashboard do Intervals.icu (mesmas consultas do desktop) e edição manual de FC de repouso e HRV numa folha.
 * Sem Intervals configurado, a tela explica e leva ao Perfil (mesma condição do desktop).
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
  // Só os dias com atividade (os zerados não têm o que mostrar e achatam as barras).
  const activeDays = useMemo(() => daily.filter((i) => i.activities > 0 || i.minutes > 0), [daily]);
  const wellnessTrend = useMemo(() => data?.wellness_trend ?? [], [data]);
  const recent = useMemo(() => data?.recent_activities ?? [], [data]);
  const dailyRows = useMemo(() => [...activeDays].reverse(), [activeDays]);
  const wellnessRows = useMemo(() => [...wellnessTrend].reverse(), [wellnessTrend]);
  const recentRows = useMemo(() => recent.map((item, index) => ({ item, key: `${item.date}-${item.name}-${index}` })), [recent]);

  const barData = useMemo(() => activeDays.map((i) => ({ data: formatDateBR(i.date), minutos: i.minutes, carga: i.load })), [activeDays]);
  const wellnessChartData = useMemo(
    () => wellnessTrend.map((i) => ({ data: formatDateBR(i.date), fitness: i.fitness, fatigue: i.fatigue, form: i.form, rampa: i.rampRate })),
    [wellnessTrend],
  );
  const latestW = wellnessTrend.length > 0 ? wellnessTrend[wellnessTrend.length - 1] : null;

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
      <ScreenHeader title="Intervals.icu" subtitle="Atividades ao vivo; wellness do histórico salvo, atualizado ao abrir e diariamente." />
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
            <ListGroup title="Resumo do período">
              <ListBlock>
                <StatGrid>
                  <StatTile label="Atividades" value={String(d.activity_count)} />
                  <StatTile label="Tempo total" value={`${totalMinutes.toLocaleString('pt-BR')} min`} />
                  <StatTile label="Carga (training load)" value={Math.round(totalLoad).toLocaleString('pt-BR')} />
                  <StatTile label="FC média" value={avgHr ? `${avgHr.toFixed(0)} bpm` : '-'} />
                </StatGrid>
                <p className={styles.meta}>
                  {totalDistance.toFixed(1)} km · {Math.round(totalCalories).toLocaleString('pt-BR')} kcal no período.
                </p>
              </ListBlock>
            </ListGroup>

            {barData.length > 0 ? (
              <ListGroup title="Minutos por dia" footer={`Só os ${activeDays.length} dias com atividade.`}>
                <div className={styles.chartBlock}>
                  <MobileChart summary={`${activeDays.length} dias com atividade em ${daily.length} dias.`} label="Minutos por dia com atividade">
                    <BarChart data={barData} margin={MOBILE_CHART_MARGIN}>
                      <XAxis dataKey="data" {...MOBILE_CHART_AXIS} />
                      <YAxis {...MOBILE_CHART_AXIS} width={36} />
                      <Tooltip {...TOOLTIP} formatter={(v) => [`${v} min`, 'Minutos']} />
                      <Bar dataKey="minutos" name="Minutos" fill={CHART_COLORS.primary} radius={[3, 3, 0, 0]} />
                    </BarChart>
                  </MobileChart>
                </div>
                <SheetRow title="Carga (training load) por dia" subtitle="Só os dias com atividade" sheetTitle="Carga por dia">
                  <div className={styles.chartBlock}>
                    <MobileChart summary={`Carga total no período: ${Math.round(totalLoad).toLocaleString('pt-BR')}.`} label="Carga por dia com atividade">
                      <BarChart data={barData} margin={MOBILE_CHART_MARGIN}>
                        <XAxis dataKey="data" {...MOBILE_CHART_AXIS} />
                        <YAxis {...MOBILE_CHART_AXIS} width={36} />
                        <Tooltip {...TOOLTIP} formatter={(v) => [fmtDecimal(Number(v), 0), 'Carga']} />
                        <Bar dataKey="carga" name="Carga" fill={CHART_COLORS.secondary} radius={[3, 3, 0, 0]} />
                      </BarChart>
                    </MobileChart>
                  </div>
                </SheetRow>
              </ListGroup>
            ) : null}

            {wellnessChartData.length > 0 && latestW ? (
              <ListGroup title="Fitness, fadiga e forma" footer="Wellness do Intervals.icu (CTL/ATL/TSB). Toque num dia da lista para ver o detalhe e editar FC de repouso e HRV.">
                <div className={styles.chartBlock}>
                  <MobileChart
                    summary={`Último dia: forma (TSB) ${formatNumberOrDash(latestW.form, 1)} · fitness ${formatNumberOrDash(latestW.fitness, 1)} · fadiga ${formatNumberOrDash(latestW.fatigue, 1)}.`}
                    label="Fitness, fadiga e forma por dia"
                  >
                    <LineChart data={wellnessChartData} margin={MOBILE_CHART_MARGIN}>
                      <XAxis dataKey="data" {...MOBILE_CHART_AXIS} />
                      <YAxis {...MOBILE_CHART_AXIS} width={36} />
                      <ReferenceLine y={0} stroke="rgba(255,255,255,0.2)" />
                      <Tooltip {...TOOLTIP} formatter={(v, n) => [fmtDecimal(Number(v), 1), String(n)]} />
                      <Legend wrapperStyle={{ fontSize: 11 }} />
                      <Line type="monotone" dataKey="fitness" name="Fitness" stroke={CHART_COLORS.primary} strokeWidth={2} dot={false} connectNulls />
                      <Line type="monotone" dataKey="fatigue" name="Fadiga" stroke={CHART_COLORS.secondary} strokeWidth={2} dot={false} connectNulls />
                      <Line type="monotone" dataKey="form" name="Forma" stroke={CHART_COLORS.positive} strokeWidth={2} strokeDasharray="5 3" dot={false} connectNulls />
                    </LineChart>
                  </MobileChart>
                </div>
                <SheetRow title="Ramp rate" subtitle="Ritmo de variação do fitness" value={formatNumberOrDash(latestW.rampRate, 1)} sheetTitle="Ramp rate por dia">
                  <div className={styles.chartBlock}>
                    <MobileChart summary={`Último: ${formatNumberOrDash(latestW.rampRate, 1)}.`} label="Ramp rate por dia">
                      <LineChart data={wellnessChartData} margin={MOBILE_CHART_MARGIN}>
                        <XAxis dataKey="data" {...MOBILE_CHART_AXIS} />
                        <YAxis {...MOBILE_CHART_AXIS} width={36} />
                        <Tooltip {...TOOLTIP} formatter={(v) => [fmtDecimal(Number(v), 1), 'Ramp rate']} />
                        <Line type="monotone" dataKey="rampa" name="Ramp rate" stroke={CHART_COLORS.primary} strokeWidth={2} dot={false} connectNulls />
                      </LineChart>
                    </MobileChart>
                  </div>
                </SheetRow>
              </ListGroup>
            ) : null}

            <p className={styles.sectionTitle}>Wellness por dia ({wellnessTrend.length})</p>
            <ResponsiveDataList
              aria-label="Wellness por dia"
              items={wellnessRows.slice(0, limits.wellness)}
              getKey={(i) => i.date}
              emptyTitle="Nenhum dado de wellness no período selecionado"
              renderItem={(i) => (
                <ListRow
                  title={formatDateBR(i.date)}
                  subtitle={`FC de repouso ${formatNumberOrDash(i.restingHr, 0)}${i.restingHrSource === 'manual' ? ' (editada)' : ''} · HRV ${formatNumberOrDash(i.hrv, 0)}${i.hrvSource === 'manual' ? ' (editado)' : ''}`}
                  value={`Forma ${formatNumberOrDash(i.form, 1)}`}
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

            <ListGroup title="Atividades">
              <SheetRow title="Atividades por dia" subtitle="Só os dias com atividade" value={`${activeDays.length} dias`} sheetTitle="Atividades por dia">
                <ResponsiveDataList
                  aria-label="Atividades por dia"
                  items={dailyRows.slice(0, limits.daily)}
                  getKey={(i) => i.date}
                  emptyTitle="Nenhuma atividade no período selecionado"
                  renderItem={(i) => (
                    <ListRow
                      title={formatDateBR(i.date)}
                      subtitle={`${plural(i.activities, 'atividade', 'atividades')} · ${i.calories || '-'} kcal · ${i.distanceKm.toFixed(2)} km · carga ${formatNumberOrDash(i.load, 1)} · FC ${i.avgHr != null ? i.avgHr.toFixed(0) : '-'}`}
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
              </SheetRow>
              <SheetRow
                title="Atividades recentes"
                subtitle="Últimas lidas do Intervals.icu (até 30); a gravação é feita pelo sync diário"
                value={`${recent.length}`}
                sheetTitle="Atividades recentes"
              >
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
              </SheetRow>
            </ListGroup>
          </>
        )}
      </QueryGate>

      <BottomSheet opened={edit !== null} onClose={() => setEdit(null)} title={edit ? `Wellness de ${formatDateBR(edit.date)}` : 'Wellness'}>
        {edit ? <EditorWellness key={edit.date} ponto={edit} onDone={() => setEdit(null)} /> : null}
      </BottomSheet>
    </>
  );
}

/** Patch só com o que mudou: campo vazio ou igual ao atual não envia (limpar a edição é ação própria). */
function montarPatch(
  campos: ReadonlyArray<readonly ['resting_hr_bpm' | 'hrv_ms', string, number | null]>,
): { patch: IntervalsWellnessManualPatch; erro: string | null } {
  const patch: IntervalsWellnessManualPatch = {};
  for (const [campo, texto, atual] of campos) {
    const t = texto.trim();
    if (t === '' || Number(t) === atual) continue;
    const n = Number(t);
    if (!Number.isFinite(n)) return { patch, erro: 'Informe um número.' };
    patch[campo] = n;
  }
  return { patch, erro: null };
}

function EditorWellness({ ponto, onDone }: { ponto: IntervalsWellnessTrendPoint; onDone: () => void }) {
  const online = useOnlineStatus();
  const qc = useQueryClient();
  const [rhr, setRhr] = useState(ponto.restingHr == null ? '' : String(ponto.restingHr));
  const [hrv, setHrv] = useState(ponto.hrv == null ? '' : String(ponto.hrv));
  const [erro, setErro] = useState<string | null>(null);
  const mutation = useMutation({
    networkMode: 'always', // offline falha na hora; nunca dispara sozinha ao voltar a rede
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
    const { patch, erro: e } = montarPatch([
      ['resting_hr_bpm', rhr, ponto.restingHr],
      ['hrv_ms', hrv, ponto.hrv],
    ]);
    if (e) {
      setErro(e);
      return;
    }
    if (Object.keys(patch).length === 0) {
      onDone();
      return;
    }
    mutation.mutate(patch);
  }

  return (
    <div className={styles.stack}>
      <ListGroup title="Dia">
        <ListRow title="Fitness (CTL)" value={formatNumberOrDash(ponto.fitness, 1)} />
        <ListRow title="Fadiga (ATL)" value={formatNumberOrDash(ponto.fatigue, 1)} />
        <ListRow title="Forma (TSB)" value={formatNumberOrDash(ponto.form, 1)} />
        <ListRow title="Ramp rate" value={formatNumberOrDash(ponto.rampRate, 1)} />
      </ListGroup>
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
      <Button color="brand" fullWidth mih={44} loading={mutation.isPending} disabled={!online} onClick={salvar}>
        Salvar
      </Button>
      {ponto.restingHrSource === 'manual' ? (
        <Button variant="default" fullWidth mih={44} disabled={mutation.isPending || !online} onClick={() => mutation.mutate({ resting_hr_bpm: null })}>
          Limpar edição manual da FC de repouso
        </Button>
      ) : null}
      {ponto.hrvSource === 'manual' ? (
        <Button variant="default" fullWidth mih={44} disabled={mutation.isPending || !online} onClick={() => mutation.mutate({ hrv_ms: null })}>
          Limpar edição manual do HRV
        </Button>
      ) : null}
      <OfflineNote what="salvar a edição" />
    </div>
  );
}
