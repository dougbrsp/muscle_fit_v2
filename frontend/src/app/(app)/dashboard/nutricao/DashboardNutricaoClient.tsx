'use client';

import { Alert, Badge, Button, Card, Group, Progress, Select, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { ChartPlaceholder } from '@/components/charts/ChartPlaceholder';
import { DashboardCardSkeleton, EmptyState, ErrorState } from '@/components/dashboard/states';
import { CheckinHistory } from '@/components/nutrition/CheckinHistory';
import { NutritionLimitations } from '@/components/nutrition/NutritionLimitations';
import { NutritionSubnav } from '@/components/nutrition/NutritionSubnav';
import { apiFetch } from '@/lib/api';
import type { SessionUser } from '@/lib/auth';
import { fmtDate, fmtDecimal, fmtInt } from '@/lib/dashboard-format';
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
import { nutritionErrorInfo } from '@/lib/nutrition-api';
import { BRAND } from '@/theme';

// COD-14: recharts sob demanda; cada placeholder tem a altura do gráfico que substitui.
const ShareBars = dynamic(() => import('@/components/nutrition/NutritionCharts').then((m) => m.ShareBars), {
  ssr: false,
  loading: () => <ChartPlaceholder height={180} />,
});
const WeekAdherenceBars = dynamic(() => import('@/components/nutrition/NutritionCharts').then((m) => m.WeekAdherenceBars), {
  ssr: false,
  loading: () => <ChartPlaceholder height={220} />,
});
const WeightChart = dynamic(() => import('@/components/nutrition/NutritionCharts').then((m) => m.WeightChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={240} />,
});
const SingleSeriesChart = dynamic(() => import('@/components/nutrition/NutritionCharts').then((m) => m.SingleSeriesChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={200} />,
});

async function fetchCurrentUser(): Promise<SessionUser> {
  return apiFetch<SessionUser>('/auth/me');
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <Card withBorder radius="md" bg={BRAND.bgSurface}>
      <Stack gap="sm">
        <Stack gap={2}>
          <Title order={3} c={BRAND.textPrimary}>
            {title}
          </Title>
          {hint && (
            <Text size="sm" c={BRAND.textMuted}>
              {hint}
            </Text>
          )}
        </Stack>
        {children}
      </Stack>
    </Card>
  );
}

function Kpi({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <Card withBorder radius="md" bg={BRAND.bgElevated} p="sm">
      <Text size="xs" fw={700} tt="uppercase" c={BRAND.textMuted}>
        {label}
      </Text>
      <Text size="xl" fw={700} c={BRAND.textPrimary}>
        {value}
      </Text>
      {detail && (
        <Text size="xs" c="dimmed">
          {detail}
        </Text>
      )}
    </Card>
  );
}

function AdherenceSection({ data }: { data: NutritionDashboard }) {
  const { adherence } = data;
  const semanas = weekAdherenceRows(data.weeks, shortDateLabel);
  return (
    <Section
      title="Aderência"
      hint="Dia a dia e semana a semana, só com dias de refeições de total completo e com meta calculada."
    >
      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="xs">
        <Kpi
          label="Dias registrados"
          value={`${fmtInt(adherence.logged_days)} de ${fmtInt(adherence.counted_days)}`}
          detail={adherence.open_today ? 'hoje ainda não conta' : undefined}
        />
        <Kpi label="Sem registro" value={fmtInt(adherence.no_record_days)} detail="dias sem nenhuma refeição" />
        <Kpi
          label="Energia na tolerância"
          value={fmtInt(adherence.kcal_within_tolerance_days)}
          detail={`de ${fmtInt(adherence.logged_days)} dia(s) registrado(s)`}
        />
        <Kpi
          label="Proteína no mínimo"
          value={fmtInt(adherence.protein_ok_days)}
          detail={`de ${fmtInt(adherence.logged_days)} dia(s) registrado(s)`}
        />
      </SimpleGrid>
      {adherence.incomplete_days.length > 0 && (
        <Group gap={6} wrap="wrap" aria-label="Dias incompletos">
          <Text size="sm" c={BRAND.textMuted}>
            Dias com total incompleto:
          </Text>
          {adherence.incomplete_days.map((dia) => (
            <Badge key={dia} color="yellow" variant="light">
              {fmtDate(dia)}
            </Badge>
          ))}
        </Group>
      )}
      {semanas.length > 0 && hasValues(semanas, (l) => l.kcal ?? l.proteina) ? (
        <>
          <div role="img" aria-label="Barras da aderência por semana: energia e proteína em porcentagem da meta. A tabela abaixo traz os mesmos números.">
            <WeekAdherenceBars rows={semanas} />
          </div>
          <Table.ScrollContainer minWidth={420}>
            <Table verticalSpacing={4} fz="sm" aria-label="Aderência por semana">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Semana</Table.Th>
                  <Table.Th>Dias registrados</Table.Th>
                  <Table.Th>Energia</Table.Th>
                  <Table.Th>Proteína</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.weeks.map((semana) => (
                  <Table.Tr key={semana.start}>
                    <Table.Td>
                      {fmtDate(semana.start)}
                      {semana.partial ? ' (parcial)' : ''}
                      {!semana.report.adherence.valid_week && (
                        <Badge ml={6} size="xs" color="yellow" variant="light">
                          semana incompleta
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td>{fmtInt(semana.report.adherence.logged_days)}</Table.Td>
                    <Table.Td>{formatShareAsPercent(semana.report.adherence.kcal_share, 0)}</Table.Td>
                    <Table.Td>{formatShareAsPercent(semana.report.adherence.protein_share, 0)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </>
      ) : (
        <Text size="sm" c="dimmed">
          Ainda não há semana com refeições registradas no período: o gráfico aparece quando houver.
        </Text>
      )}
    </Section>
  );
}

function MacrosSection({ data }: { data: NutritionDashboard }) {
  const campos = MACRO_ORDER;
  return (
    <Section
      title="Energia e macros × meta"
      hint="Cada gráfico mostra a fração da meta atingida por dia; a linha tracejada é 100% da meta."
    >
      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
        {campos.map((campo) => {
          const linhas = shareRows(data.days, campo, shortDateLabel);
          return (
            <Stack key={campo} gap={4}>
              <Text size="sm" fw={700} c={BRAND.textPrimary}>
                {macroTitle(campo)} (% da meta)
              </Text>
              {hasValues(linhas, (l) => l.percent) ? (
                <div role="img" aria-label={`Barras de ${macroTitle(campo)} em porcentagem da meta, por dia`}>
                  <ShareBars rows={linhas} name={macroTitle(campo)} />
                </div>
              ) : (
                <Text size="sm" c="dimmed">
                  Sem dia fechado com meta e refeição de total completo no período.
                </Text>
              )}
            </Stack>
          );
        })}
      </SimpleGrid>
    </Section>
  );
}

function BodySection({ data }: { data: NutritionDashboard }) {
  const linhas = useMemo(() => bodyRows(data.body.points, shortDateLabel), [data.body.points]);
  const ritmo = data.body.weight_trend.weekly_change_kg;
  const pesoAlvo = data.plan?.goal_weight_kg ?? null;
  const gorduraAlvo = data.plan?.goal_body_fat_percent ?? null;
  const temPeso = hasSeries(linhas, 'peso');
  const temGordura = hasSeries(linhas, 'gordura');
  const temMusculo = hasSeries(linhas, 'musculo');
  return (
    <Section
      title="Peso, % de gordura e massa muscular"
      hint="Pesagens da balança e bioimpedância (estimada); a linha tracejada é o alvo do plano, quando há."
    >
      {temPeso ? (
        <Stack gap={4}>
          <Text size="sm" fw={700} c={BRAND.textPrimary}>
            Peso (kg)
          </Text>
          <Text size="sm" c={BRAND.textMuted}>
            Ritmo no período: <b>{ritmo == null ? '-' : `${fmtDecimal(ritmo, 2)} kg por semana`}</b>
            {pesoAlvo != null && (
              <>
                {' '}
                · Peso-alvo: <b>{fmtDecimal(pesoAlvo, 1)} kg</b>
              </>
            )}
          </Text>
          <div role="img" aria-label="Linha do peso com a média móvel e o peso-alvo do plano">
            <WeightChart rows={linhas} unit="kg" goal={pesoAlvo} goalLabel="Peso-alvo" />
          </div>
        </Stack>
      ) : (
        <EmptyState
          title="Sem pesagem no período"
          description="Este painel lê só as pesagens de balança e bioimpedância de Indicadores físicos. Peso que está apenas no relógio ou no Intervals.icu não aparece aqui."
          actionHref="/indicadores-fisicos" actionLabel="Ir para Indicadores físicos" />
      )}
      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
        {temGordura && (
          <Stack gap={4}>
            <Text size="sm" fw={700} c={BRAND.textPrimary}>
              % de gordura
            </Text>
            <div role="img" aria-label="Linha do percentual de gordura da bioimpedância e o alvo do plano">
              <SingleSeriesChart rows={linhas} unit="%" dataKey="gordura" name="% de gordura" goal={gorduraAlvo} goalLabel="% G alvo" />
            </div>
          </Stack>
        )}
        {temMusculo && (
          <Stack gap={4}>
            <Text size="sm" fw={700} c={BRAND.textPrimary}>
              Massa muscular (kg)
            </Text>
            <div role="img" aria-label="Linha da massa muscular da bioimpedância">
              <SingleSeriesChart rows={linhas} unit="kg" dataKey="musculo" name="Massa muscular" />
            </div>
          </Stack>
        )}
      </SimpleGrid>
      {!temGordura && !temMusculo && (
        <Text size="sm" c="dimmed">
          Sem bioimpedância no período: o % de gordura e a massa muscular aparecem aqui quando houver medida.
        </Text>
      )}
      <NutritionLimitations
        items={data.body.weight_trend.limitation_items}
        title="Sobre o ritmo do peso"
        tone="info"
      />
    </Section>
  );
}

function ProjectionSection({ data }: { data: NutritionDashboard }) {
  const projecao = data.projection;
  if (!projecao) {
    return (
      <Section title="Projeção até o alvo">
        {data.plan ? (
          <EmptyState
            title="Sem alvo no plano"
            description="Defina o peso-alvo ou o % de gordura alvo no plano para ver o prazo estimado."
            actionHref="/nutricao/plano"
            actionLabel="Abrir o plano"
          />
        ) : (
          <EmptyState
            title="Sem plano publicado"
            description="Crie e publique o seu plano de nutrição, com peso-alvo ou % de gordura alvo, para ver o prazo estimado."
            actionHref="/nutricao/plano"
            actionLabel="Abrir o plano"
          />
        )}
      </Section>
    );
  }
  const status = PROJECTION_STATUS_OPTIONS.find((o) => o.value === projecao.status)?.label ?? projecao.status;
  const unidade = projecao.goal_kind === 'body_fat' ? '%' : 'kg';
  return (
    <Section title="Projeção até o alvo">
      <Group gap="sm" wrap="wrap">
        <Badge color={projecao.status === 'projected' || projecao.status === 'goal_reached' ? 'green' : 'gray'} variant="light">
          {status}
        </Badge>
        {projecao.is_minimum_estimate && (
          <Badge color="yellow" variant="light">
            estimativa mínima
          </Badge>
        )}
      </Group>
      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="xs">
        <Kpi label="Atual" value={projecao.current == null ? '-' : `${fmtDecimal(projecao.current, 1)} ${unidade}`} />
        <Kpi label="Alvo" value={projecao.target == null ? '-' : `${fmtDecimal(projecao.target, 1)} ${unidade}`} />
        <Kpi
          label="Prazo estimado"
          value={projecao.weeks == null ? '-' : `${fmtDecimal(projecao.weeks, 0)} sem.`}
          detail={projecao.eta_date ? `por volta de ${fmtDate(projecao.eta_date)}` : undefined}
        />
        <Kpi
          label="Do caminho"
          value={projecao.progress_percent == null ? '-' : `${fmtDecimal(projecao.progress_percent, 0)}%`}
        />
      </SimpleGrid>
      {projecao.progress_percent != null && (
        <Progress value={projecao.progress_percent} color="brand" aria-label={`${fmtDecimal(projecao.progress_percent, 0)}% do caminho até o alvo`} />
      )}
      {projecao.is_minimum_estimate && (
        <Alert color="yellow" variant="light" icon={<IconInfoCircle size={16} />}>
          O prazo supõe que a massa livre de gordura atual não muda: é um limite inferior, o real tende a ser maior.
        </Alert>
      )}
      <NutritionLimitations items={projecao.reason_items} title="Sobre a projeção" tone="info" />
    </Section>
  );
}

function AdaptiveSection({ data }: { data: NutritionDashboard }) {
  const { observed, suggestion } = data.adaptive_tdee;
  return (
    <Section
      title="Gasto observado (TDEE adaptativo)"
      hint="Estimado pelo que você comeu e pela variação do peso. É só uma sugestão: nada muda no seu plano nem nas suas metas."
    >
      <SimpleGrid cols={{ base: 1, xs: 3 }} spacing="xs">
        <Kpi label="Pela fórmula" value={formatKcal(suggestion.formula_kcal)} />
        <Kpi label="Observado" value={formatKcal(observed.kcal)} detail={observed.kcal == null ? undefined : `${fmtInt(observed.logged_days)} dia(s) com diário`} />
        <Kpi label="Sugestão" value={formatKcal(suggestion.suggested_kcal)} detail="não aplicada" />
      </SimpleGrid>
      <NutritionLimitations
        items={observed.kcal == null ? observed.limitation_items : suggestion.limitation_items}
        title="Por que ainda não há sugestão"
        tone="info"
      />
    </Section>
  );
}

/** `/dashboard/nutricao` (N5): aderência, macros × meta, peso/%G × alvo, projeção e gasto observado. Tudo vem pronto de `GET /nutrition/dashboard`. */
export function DashboardNutricaoClient() {
  // `null` = a janela padrão da API; as janelas oferecidas vêm na resposta (`window_choices`).
  const [days, setDays] = useState<number | null>(null);

  const currentUserQuery = useQuery({ queryKey: ['current-user'], queryFn: fetchCurrentUser, staleTime: 60_000, retry: false });

  const consulta = useQuery({
    queryKey: dashboardKeys.window(days),
    queryFn: () => fetchNutritionDashboard(days),
    enabled: currentUserQuery.isSuccess,
    retry: false,
    // Trocar o período mantém o painel anterior enquanto o novo carrega.
    placeholderData: keepPreviousData,
  });
  const data = consulta.data;
  // O seletor guarda as janelas da última resposta: não some enquanto carrega nem depois de um erro.
  const [janelas, setJanelas] = useState<{ choices: number[]; atual: number | null } | null>(null);
  useEffect(() => {
    if (consulta.data && !consulta.isPlaceholderData) {
      setJanelas({ choices: consulta.data.window_choices, atual: consulta.data.window_days });
    }
  }, [consulta.data, consulta.isPlaceholderData]);

  return (
    <Stack gap="lg">
      <Stack gap="xs">
        <NutritionSubnav active="/dashboard/nutricao" />
        <Title order={1} c={BRAND.textPrimary}>
          Nutrição: painel
        </Title>
        <Text c={BRAND.textMuted}>
          Como a alimentação está indo em relação ao plano e ao corpo.{' '}
          <Link href="/nutricao" style={{ color: BRAND.orange }}>
            Abrir o diário
          </Link>
        </Text>
      </Stack>

      <Group align="flex-end" gap="md" wrap="wrap">
        {janelas && (
          <Select
            label="Período"
            w={200}
            allowDeselect={false}
            data={janelas.choices.map((n) => ({ value: String(n), label: `Últimos ${n} dias` }))}
            value={days != null ? String(days) : janelas.atual != null ? String(janelas.atual) : null}
            onChange={(valor) => valor && setDays(Number(valor))}
          />
        )}
        {consulta.isPlaceholderData && (
          <Text size="sm" c="dimmed" role="status">
            Atualizando...
          </Text>
        )}
      </Group>

      {currentUserQuery.isError && (
        <ErrorState title="Não foi possível identificar o usuário" description="Entre de novo ou tente em instantes." />
      )}
      {(consulta.isLoading || currentUserQuery.isLoading) && <DashboardCardSkeleton rows={6} />}
      {consulta.isError && (
        <Stack gap="xs" align="flex-start">
          <ErrorState title="Não foi possível carregar o painel de nutrição" description={nutritionErrorInfo(consulta.error, 'Tente novamente em instantes.').message} />
          <Button variant="light" color="brand" onClick={() => void consulta.refetch()}>
            Tentar de novo
          </Button>
        </Stack>
      )}
      {data && (
        <Stack gap="lg">
          <Text size="sm" c={BRAND.textMuted}>
            {fmtDate(data.start)} a {fmtDate(data.end)}
            {data.plan ? ` · plano ${STRATEGY_OPTIONS.find((o) => o.value === data.plan?.strategy)?.label ?? data.plan.strategy}` : ''}
          </Text>
          <NutritionLimitations items={data.empty_states} title="O que falta para o painel ficar completo" tone="info" />
          <AdherenceSection data={data} />
          <MacrosSection data={data} />
          <BodySection data={data} />
          <ProjectionSection data={data} />
          <AdaptiveSection data={data} />
          <CheckinHistory />
        </Stack>
      )}
    </Stack>
  );
}
