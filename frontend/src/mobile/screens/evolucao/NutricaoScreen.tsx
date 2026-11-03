'use client';

import { Badge, Progress } from '@mantine/core';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { NutritionLimitations } from '@/components/nutrition/NutritionLimitations';
import {
  ShareBars,
  SingleSeriesChart,
  WeekAdherenceBars,
  WeightChart,
} from '@/components/nutrition/NutritionCharts';
import { apiFetch } from '@/lib/api';
import type { SessionUser } from '@/lib/auth';
import { fmtDate, fmtDecimal, fmtInt } from '@/lib/dashboard-format';
import { checkinKeys, fetchCheckinHistory, formatAdjustment, verdictTone, weekRangeLabel } from '@/lib/nutrition-checkin';
import { nutritionErrorInfo } from '@/lib/nutrition-api';
import { PROJECTION_STATUS_OPTIONS, STRATEGY_OPTIONS } from '@/lib/nutrition-constants';
import {
  bodyRows,
  dashboardKeys,
  fetchNutritionDashboard,
  hasSeries,
  hasValues,
  MACRO_ORDER,
  macroTitle,
  shareRows,
  shortDateLabel,
  weekAdherenceRows,
  type NutritionDashboard,
} from '@/lib/nutrition-dashboard';
import { formatKcal, formatShareAsPercent } from '@/lib/nutrition-format';
import {
  EmptyState,
  ListBlock,
  ListGroup,
  ListRow,
  ResponsiveDataList,
  ScreenHeader,
  StatGrid,
  StatTile,
  useOnlineStatus,
} from '../../ui';
import styles from './evolucao.module.css';
import { QueryGate } from './shared';

async function fetchCurrentUser(): Promise<SessionUser> {
  return apiFetch<SessionUser>('/auth/me');
}

/** Painel de nutrição no celular: mesmas consultas do desktop (`/auth/me` + `GET /nutrition/dashboard`). */
export function NutricaoScreen() {
  const [days, setDays] = useState<number | null>(null);
  const currentUser = useQuery({ queryKey: ['current-user'], queryFn: fetchCurrentUser, staleTime: 60_000, retry: false });
  const consulta = useQuery({
    queryKey: dashboardKeys.window(days),
    queryFn: () => fetchNutritionDashboard(days),
    enabled: currentUser.isSuccess,
    retry: false,
    placeholderData: keepPreviousData,
  });
  const [janelas, setJanelas] = useState<{ choices: number[]; atual: number | null } | null>(null);
  useEffect(() => {
    if (consulta.data && !consulta.isPlaceholderData) {
      setJanelas({ choices: consulta.data.window_choices, atual: consulta.data.window_days });
    }
  }, [consulta.data, consulta.isPlaceholderData]);

  const gate = {
    data: consulta.data,
    isLoading: currentUser.isLoading || consulta.isLoading,
    isError: currentUser.isError || consulta.isError,
    error: currentUser.isError
      ? new Error('Não foi possível identificar o usuário. Entre de novo ou tente em instantes.')
      : consulta.isError
        ? new Error(nutritionErrorInfo(consulta.error, 'Tente novamente em instantes.').message)
        : null,
    refetch: () => (currentUser.isError ? currentUser.refetch() : consulta.refetch()),
  };

  return (
    <>
      <ScreenHeader title="Nutrição: painel" subtitle="Como a alimentação está indo em relação ao plano e ao corpo." />
      <ListGroup>
        <ListRow title="Abrir o diário" href="/nutricao" />
        {janelas ? (
          <ListBlock>
            <label className={styles.fieldLabel} htmlFor="mob-nutri-periodo">
              Período
            </label>
            <select
              id="mob-nutri-periodo"
              className={styles.dateInput}
              value={days != null ? String(days) : janelas.atual != null ? String(janelas.atual) : ''}
              onChange={(e) => setDays(Number(e.currentTarget.value))}
            >
              {janelas.choices.map((n) => (
                <option key={n} value={String(n)}>
                  Últimos {n} dias
                </option>
              ))}
            </select>
            {consulta.isPlaceholderData ? (
              <p className={styles.meta} role="status">
                Atualizando...
              </p>
            ) : null}
          </ListBlock>
        ) : null}
      </ListGroup>
      <QueryGate query={gate} title="painel de nutrição" rows={5}>
        {(data) => <Painel data={data} />}
      </QueryGate>
    </>
  );
}

function Painel({ data }: { data: NutritionDashboard }) {
  return (
    <>
      <p className={styles.note} style={{ marginBottom: 8 }}>
        {fmtDate(data.start)} a {fmtDate(data.end)}
        {data.plan ? ` · plano ${STRATEGY_OPTIONS.find((o) => o.value === data.plan?.strategy)?.label ?? data.plan.strategy}` : ''}
      </p>
      <div className={styles.gap}>
        <NutritionLimitations items={data.empty_states} title="O que falta para o painel ficar completo" tone="info" />
      </div>
      <Aderencia data={data} />
      <Macros data={data} />
      <Corpo data={data} />
      <Projecao data={data} />
      <Adaptativo data={data} />
      <Checkins />
    </>
  );
}

function Aderencia({ data }: { data: NutritionDashboard }) {
  const a = data.adherence;
  const semanas = weekAdherenceRows(data.weeks, shortDateLabel);
  return (
    <>
      <ListGroup title="Aderência" footer="Dia a dia e semana a semana, só com dias de refeições de total completo e com meta calculada.">
        <ListBlock>
          <StatGrid>
            <StatTile
              label="Dias registrados"
              value={`${fmtInt(a.logged_days)} de ${fmtInt(a.counted_days)}`}
              hint={a.open_today ? 'hoje ainda não conta' : undefined}
            />
            <StatTile label="Sem registro" value={fmtInt(a.no_record_days)} hint="dias sem nenhuma refeição" />
            <StatTile label="Energia na tolerância" value={fmtInt(a.kcal_within_tolerance_days)} hint={`de ${fmtInt(a.logged_days)} dia(s)`} />
            <StatTile label="Proteína no mínimo" value={fmtInt(a.protein_ok_days)} hint={`de ${fmtInt(a.logged_days)} dia(s)`} />
          </StatGrid>
          {a.incomplete_days.length > 0 ? (
            <p className={styles.meta}>Dias com total incompleto: {a.incomplete_days.map((d) => fmtDate(d)).join(', ')}</p>
          ) : null}
        </ListBlock>
        {semanas.length > 0 && hasValues(semanas, (l) => l.kcal ?? l.proteina) ? (
          <div className={styles.chartBlock} role="img" aria-label="Barras da aderência por semana: energia e proteína em porcentagem da meta. A lista abaixo traz os mesmos números.">
            <WeekAdherenceBars rows={semanas} />
          </div>
        ) : (
          <ListBlock>
            <p className={styles.body}>Ainda não há semana com refeições registradas no período: o gráfico aparece quando houver.</p>
          </ListBlock>
        )}
      </ListGroup>
      {data.weeks.length > 0 ? (
        <ListGroup>
          {data.weeks.map((s) => (
            <ListRow
              key={s.start}
              title={`${fmtDate(s.start)}${s.partial ? ' (parcial)' : ''}`}
              subtitle={`${fmtInt(s.report.adherence.logged_days)} dias registrados · energia ${formatShareAsPercent(s.report.adherence.kcal_share, 0)} · proteína ${formatShareAsPercent(s.report.adherence.protein_share, 0)}${!s.report.adherence.valid_week ? ' · semana incompleta' : ''}`}
            />
          ))}
        </ListGroup>
      ) : null}
    </>
  );
}

function Macros({ data }: { data: NutritionDashboard }) {
  return (
    <>
      <p className={styles.sectionTitle}>Energia e macros × meta</p>
      <p className={styles.note} style={{ marginBottom: 8 }}>
        Cada gráfico mostra a fração da meta atingida por dia; a linha tracejada é 100% da meta.
      </p>
      {MACRO_ORDER.map((campo) => {
        const linhas = shareRows(data.days, campo, shortDateLabel);
        return (
          <ListGroup key={campo} title={`${macroTitle(campo)} (% da meta)`}>
            {hasValues(linhas, (l) => l.percent) ? (
              <div className={styles.chartBlock} role="img" aria-label={`Barras de ${macroTitle(campo)} em porcentagem da meta, por dia`}>
                <ShareBars rows={linhas} name={macroTitle(campo)} />
              </div>
            ) : (
              <ListBlock>
                <p className={styles.body}>Sem dia fechado com meta e refeição de total completo no período.</p>
              </ListBlock>
            )}
          </ListGroup>
        );
      })}
    </>
  );
}

function Corpo({ data }: { data: NutritionDashboard }) {
  const linhas = useMemo(() => bodyRows(data.body.points, shortDateLabel), [data.body.points]);
  const ritmo = data.body.weight_trend.weekly_change_kg;
  const pesoAlvo = data.plan?.goal_weight_kg ?? null;
  const gorduraAlvo = data.plan?.goal_body_fat_percent ?? null;
  const temPeso = hasSeries(linhas, 'peso');
  const temGordura = hasSeries(linhas, 'gordura');
  const temMusculo = hasSeries(linhas, 'musculo');
  return (
    <>
      <ListGroup
        title="Peso, % de gordura e massa muscular"
        footer="Pesagens da balança e bioimpedância (estimada); a linha tracejada é o alvo do plano, quando há."
      >
        {temPeso ? (
          <>
            <ListBlock>
              <p className={styles.body}>
                Ritmo no período: <strong>{ritmo == null ? '-' : `${fmtDecimal(ritmo, 2)} kg por semana`}</strong>
                {pesoAlvo != null ? ` · Peso-alvo: ${fmtDecimal(pesoAlvo, 1)} kg` : ''}
              </p>
            </ListBlock>
            <div className={styles.chartBlock} role="img" aria-label="Linha do peso com a média móvel e o peso-alvo do plano">
              <WeightChart rows={linhas} unit="kg" goal={pesoAlvo} goalLabel="Peso-alvo" />
            </div>
          </>
        ) : (
          <ListBlock>
            <p className={styles.body}>
              Sem pesagem no período. Este painel lê só as pesagens de balança e bioimpedância de Indicadores físicos; peso que está
              apenas no relógio ou no Intervals.icu não aparece aqui.
            </p>
          </ListBlock>
        )}
        {temPeso ? null : <ListRow title="Ir para Indicadores físicos" href="/indicadores-fisicos" />}
        {temGordura ? (
          <div className={styles.chartBlock} role="img" aria-label="Linha do percentual de gordura da bioimpedância e o alvo do plano">
            <SingleSeriesChart rows={linhas} unit="%" dataKey="gordura" name="% de gordura" goal={gorduraAlvo} goalLabel="% G alvo" />
          </div>
        ) : null}
        {temMusculo ? (
          <div className={styles.chartBlock} role="img" aria-label="Linha da massa muscular da bioimpedância">
            <SingleSeriesChart rows={linhas} unit="kg" dataKey="musculo" name="Massa muscular" />
          </div>
        ) : null}
        {!temGordura && !temMusculo ? (
          <ListBlock>
            <p className={styles.meta} style={{ marginTop: 0 }}>
              Sem bioimpedância no período: o % de gordura e a massa muscular aparecem aqui quando houver medida.
            </p>
          </ListBlock>
        ) : null}
      </ListGroup>
      <div className={styles.gap}>
        <NutritionLimitations items={data.body.weight_trend.limitation_items} title="Sobre o ritmo do peso" tone="info" />
      </div>
    </>
  );
}

function Projecao({ data }: { data: NutritionDashboard }) {
  const p = data.projection;
  if (!p) {
    return (
      <EmptyState
        title={data.plan ? 'Sem alvo no plano' : 'Sem plano publicado'}
        description={
          data.plan
            ? 'Defina o peso-alvo ou o % de gordura alvo no plano para ver o prazo estimado.'
            : 'Crie e publique o seu plano de nutrição, com peso-alvo ou % de gordura alvo, para ver o prazo estimado.'
        }
        action={undefined}
      />
    );
  }
  const status = PROJECTION_STATUS_OPTIONS.find((o) => o.value === p.status)?.label ?? p.status;
  const unidade = p.goal_kind === 'body_fat' ? '%' : 'kg';
  return (
    <>
      <ListGroup title="Projeção até o alvo">
        <ListBlock>
          <div className={styles.badges}>
            <Badge color="gray" variant="light" size="lg">{status}</Badge>
            {p.is_minimum_estimate ? <Badge color="yellow" variant="light" size="lg">estimativa mínima</Badge> : null}
          </div>
          <StatGrid>
            <StatTile label="Atual" value={p.current == null ? '-' : `${fmtDecimal(p.current, 1)} ${unidade}`} />
            <StatTile label="Alvo" value={p.target == null ? '-' : `${fmtDecimal(p.target, 1)} ${unidade}`} />
            <StatTile
              label="Prazo estimado"
              value={p.weeks == null ? '-' : `${fmtDecimal(p.weeks, 0)} sem.`}
              hint={p.eta_date ? `por volta de ${fmtDate(p.eta_date)}` : undefined}
            />
            <StatTile label="Do caminho" value={p.progress_percent == null ? '-' : `${fmtDecimal(p.progress_percent, 0)}%`} />
          </StatGrid>
          {p.progress_percent != null ? (
            <div style={{ marginTop: 12 }}>
              <Progress value={p.progress_percent} color="brand" aria-label={`${fmtDecimal(p.progress_percent, 0)}% do caminho até o alvo`} />
            </div>
          ) : null}
          {p.is_minimum_estimate ? (
            <p className={styles.meta}>O prazo supõe que a massa livre de gordura atual não muda: é um limite inferior, o real tende a ser maior.</p>
          ) : null}
        </ListBlock>
      </ListGroup>
      <div className={styles.gap}>
        <NutritionLimitations items={p.reason_items} title="Sobre a projeção" tone="info" />
      </div>
    </>
  );
}

function Adaptativo({ data }: { data: NutritionDashboard }) {
  const { observed, suggestion } = data.adaptive_tdee;
  return (
    <>
      <ListGroup
        title="Gasto observado (TDEE adaptativo)"
        footer="Estimado pelo que você comeu e pela variação do peso. É só uma sugestão: nada muda no seu plano nem nas suas metas."
      >
        <ListRow title="Pela fórmula" value={formatKcal(suggestion.formula_kcal)} />
        <ListRow
          title="Observado"
          subtitle={observed.kcal == null ? undefined : `${fmtInt(observed.logged_days)} dia(s) com diário`}
          value={formatKcal(observed.kcal)}
        />
        <ListRow title="Sugestão" subtitle="não aplicada" value={formatKcal(suggestion.suggested_kcal)} />
      </ListGroup>
      <div className={styles.gap}>
        <NutritionLimitations
          items={observed.kcal == null ? observed.limitation_items : suggestion.limitation_items}
          title="Por que ainda não há sugestão"
          tone="info"
        />
      </div>
    </>
  );
}

function Checkins() {
  const online = useOnlineStatus();
  const query = useQuery({ queryKey: checkinKeys.history(), queryFn: () => fetchCheckinHistory(), retry: false });
  return (
    <>
      <ListGroup title="Check-ins semanais" footer="A avaliação de cada semana fechada e o que mudou nas metas.">
        <ListRow title="Abrir o check-in" href="/nutricao/checkin" />
      </ListGroup>
      <ResponsiveDataList
        aria-label="Histórico de check-ins semanais"
        items={query.data?.items}
        getKey={(c) => c.id}
        isLoading={query.isLoading}
        isError={query.isError}
        isOffline={!online}
        onRetry={() => void query.refetch()}
        errorTitle="Não foi possível carregar o histórico de check-ins"
        emptyTitle="Nenhum check-in ainda"
        emptyDescription="O primeiro aparece depois da primeira semana fechada com o plano publicado."
        renderItem={(c) => (
          <ListRow
            title={weekRangeLabel(c)}
            subtitle={`${formatAdjustment(c.adjustment_kcal) ?? 'sem mudança'} · ${c.status_label}`}
            trailing={<Badge color={verdictTone(c.verdict)} variant="light">{c.verdict_label}</Badge>}
          />
        )}
      />
    </>
  );
}
