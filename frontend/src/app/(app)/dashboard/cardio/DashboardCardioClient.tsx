'use client';

import {
  Alert,
  Badge,
  Card,
  Divider,
  Group,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import { IconCalendarEvent, IconHeartRateMonitor, IconInfoCircle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import { useMemo } from 'react';
import { ChartPlaceholder } from '@/components/charts/ChartPlaceholder';
import { DashboardPeriodSelect } from '@/components/dashboard/DashboardPeriodSelect';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { DashboardCardSkeleton, EmptyState, ErrorState, PartialState } from '@/components/dashboard/states';
import { dashboardKeys, fetchCardio } from '@/lib/dashboard-api';
import {
  adherenceStatusColor,
  collectCardioLimitations,
  groupSessionsByDay,
  hasAnythingToShow,
  hasZoneData,
  matchBadge,
  matchDeltaLabel,
  modeChartSummary,
  paiRows,
  sportLabel,
  weeklyModeRows,
  weeklyZoneRows,
  wellnessRows,
  zoneChartSummary,
  zoneSourceLabel,
} from '@/lib/dashboard-cardio';
import type {
  CardioAdherence,
  CardioNextSession,
  CardioPai,
  CardioResponse,
  CardioWellness,
  SessionHeartRate,
} from '@/lib/dashboard-cardio-types';
import { useDashboardFilters } from '@/lib/dashboard-filters';
import { EM_DASH, fmtDate, fmtDateTime, fmtDecimal, fmtInt, fmtPercent } from '@/lib/dashboard-format';
import type { AthleteHrZones } from '@/lib/hr-zones';
import { BRAND } from '@/theme';

// COD-14: recharts sob demanda; o placeholder tem a altura do gráfico (240).
const chartLoading = () => <ChartPlaceholder height={240} />;
const ZonasSemanaChart = dynamic(() => import('./CardioCharts').then((m) => m.ZonasSemanaChart), {
  ssr: false,
  loading: chartLoading,
});
const ModoSemanaChart = dynamic(() => import('./CardioCharts').then((m) => m.ModoSemanaChart), {
  ssr: false,
  loading: chartLoading,
});
const CargaChart = dynamic(() => import('./CardioCharts').then((m) => m.CargaChart), {
  ssr: false,
  loading: chartLoading,
});
const RecuperacaoChart = dynamic(() => import('./CardioCharts').then((m) => m.RecuperacaoChart), {
  ssr: false,
  loading: chartLoading,
});
const PaiChart = dynamic(() => import('./CardioCharts').then((m) => m.PaiChart), {
  ssr: false,
  loading: chartLoading,
});

/**
 * `/dashboard/cardio` — "quanto de cardio eu fiz, e com que FC eu treinei?"
 * (plano §11; etapa F2.3).
 *
 * Tudo vem pronto de `GET /analytics/dashboard/cardio`. Nada é recalculado
 * aqui — nem a média de FC, nem a cobertura, nem a classificação da
 * modalidade.
 *
 * Três coisas esta tela faz questão de deixar visíveis:
 * - **de onde vem o número**: as atividades são o CACHE do Intervals.icu, e a
 *   data da última sincronização aparece escrita. A tela antiga `/cardio`
 *   consulta a API ao vivo; esta não, e diz isso;
 * - **de onde vem CADA FC**: "FC do treino" é a medida registrada na própria
 *   sessão (import do relógio) e "FC associada" é a atividade do Intervals.icu
 *   casada por horário — medida e associação não são a mesma coisa. E "FC
 *   ambígua" marca o `MATCHED_AMBIGUOUS` do casamento, em vez de apresentar a
 *   FC como certeza;
 * - **ausência não é zero**: atividade sem sensor conta nos minutos e fica
 *   fora da média; sem sessão nenhuma a cobertura é `—`, nunca 0%.
 *
 * A associação FC ↔ sessão NÃO é feita aqui nem no endpoint: ela é do job
 * agendado (`app/intervals_sync.py`), que casa o treino com a atividade
 * WeightTraining pelo horário de início e grava o resultado. A tela lê.
 *
 * M98 C4 — acompanhamento: minutos por zona e contínuo × intensa por semana,
 * planejado × realizado, CTL/ATL/TSB, HRV, FC de repouso, PAI, próxima sessão
 * recomendada e as zonas em uso. Zonas, classe da sessão (regra do envelope),
 * status da aderência, TSB e a meta de PAI vêm prontos do backend; esta tela
 * só desenha. É a única tela de cardio: a `/cardio` ao vivo redireciona aqui.
 */

const STALE_TIME = 60_000;

export function DashboardCardioClient() {
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();

  const consulta = useQuery({
    queryKey: dashboardKeys.cardio(windowQuery),
    queryFn: () => fetchCardio(windowQuery),
    staleTime: STALE_TIME,
  });

  const payload = consulta.data;
  const limitacoes = useMemo(() => (payload ? collectCardioLimitations(payload) : []), [payload]);

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <div>
          <Title order={2} c={BRAND.textPrimary}>
            Cardio
          </Title>
          <Text size="sm" c={BRAND.textMuted}>
            O cardio sincronizado do Intervals.icu e a frequência cardíaca de cada sessão de treino.
          </Text>
        </div>
        <DashboardPeriodSelect filters={filters} onChange={setFilters} rangeError={rangeError} />
      </Group>

      {consulta.isLoading && <DashboardCardSkeleton rows={8} />}
      {consulta.isError && <ErrorState title="Não foi possível carregar o cardio." error={consulta.error} />}

      {payload && (
        <>
          {!payload.intervals_configured && (
            <Alert color="yellow" variant="light" icon={<IconInfoCircle size={18} />}>
              O cardio do hub vem do Intervals.icu, que ainda não está configurado no seu perfil.{' '}
              <Text component={Link} href="/intervals-icu" fw={600} c={BRAND.orange} span>
                Configurar agora
              </Text>
              . A frequência cardíaca das sessões já associadas continua aparecendo abaixo.
            </Alert>
          )}

          <ResumoCard payload={payload} />

          {payload.next_session && <ProximaSessaoCard sessao={payload.next_session} />}

          <PartialState quality={payload.data_quality} limitations={limitacoes} />

          {(payload.weeks?.length ?? 0) > 0 && payload.totals.sessions > 0 && <SemanasCard payload={payload} />}

          {payload.adherence && payload.adherence.sessions.length > 0 && (
            <AderenciaCard aderencia={payload.adherence} />
          )}

          {(payload.wellness?.points.length ?? 0) > 0 && <CargaRecuperacaoCard wellness={payload.wellness} />}

          {(payload.pai?.points.length ?? 0) > 0 && <PaiCard pai={payload.pai} />}

          {payload.zones && <ZonasCard zonas={payload.zones} />}

          {payload.by_sport.length > 0 && <ModalidadesCard payload={payload} />}

          <FrequenciaCardiacaCard payload={payload} />

          {payload.sessions.length > 0 && <AtividadesCard payload={payload} />}

          {!hasAnythingToShow(payload) && (
            <EmptyState
              title="Nada de cardio e nenhuma FC de sessão neste período"
              description="Ausência de registro não é ausência de treino: só quer dizer que nada chegou do Intervals.icu nesta janela."
            />
          )}

          <Divider />

          <Alert color="gray" variant="light" icon={<IconInfoCircle size={18} />}>
            A FC de uma sessão tem duas origens possíveis, e elas não valem o mesmo. Quando o treino
            foi importado do relógio com FC, esse número é a MEDIDA daquela sessão (&quot;FC do
            treino&quot;). Quando
            não, entra a da atividade WeightTraining do Intervals.icu casada pelo HORÁRIO de início
            (tolerância de 60 minutos) pelo sincronismo automático — uma associação por proximidade, não
            uma medida da sessão, e por isso a tela marca quando mais de uma atividade caiu na janela.
          </Alert>
        </>
      )}
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// Cartões
// ---------------------------------------------------------------------------

function CardBase({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <Card withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Stack gap="sm">
        <div>
          <Title order={3} size="h4" c={BRAND.textPrimary}>
            {title}
          </Title>
          {hint && (
            <Text size="sm" c={BRAND.textMuted}>
              {hint}
            </Text>
          )}
        </div>
        {children}
      </Stack>
    </Card>
  );
}

/** Um número com rótulo. `null` vira `—`: ausência nunca é renderizada como 0. */
function Numero({ label, value, unit }: { label: string; value: number | null; unit?: string }) {
  return (
    <Stack gap={2}>
      <Text size="xs" c={BRAND.textMuted}>
        {label}
      </Text>
      <Text size="lg" fw={700} c={BRAND.textPrimary}>
        {value == null ? EM_DASH : `${fmtDecimal(value, Number.isInteger(value) ? 0 : 1)}${unit ? ` ${unit}` : ''}`}
      </Text>
    </Stack>
  );
}

function ResumoCard({ payload }: { payload: CardioResponse }) {
  const { totals } = payload;
  return (
    <CardBase
      title="Resumo do período"
      hint={`${fmtDate(payload.window.date_from)} a ${fmtDate(payload.window.date_to)}. Atividade sem sensor de FC conta nos minutos e fica fora da média.`}
    >
      <Group gap="sm" wrap="wrap">
        <StatusBadge domain="dataNature" code={payload.nature} size="sm" />
        <StatusBadge domain="dataQuality" code={payload.data_quality} size="sm" />
        <Tooltip
          label="Os números vêm do cache mantido pelo sincronismo automático, não de uma consulta ao vivo."
          withArrow
          multiline
          w={280}
        >
          <Badge color="gray" variant="light" size="sm" style={{ cursor: 'help' }}>
            Sincronizado em {payload.last_synced_at ? fmtDateTime(payload.last_synced_at) : EM_DASH}
          </Badge>
        </Tooltip>
        {payload.other_activities_total > 0 && (
          <Tooltip
            label="Nem cardio nem força (yoga, alongamento...): ficam FORA dos totais — nada vira cardio por eliminação."
            withArrow
            multiline
            w={280}
          >
            <Badge color="grape" variant="light" size="sm" style={{ cursor: 'help' }}>
              {fmtInt(payload.other_activities_total)} outra(s)
            </Badge>
          </Tooltip>
        )}
      </Group>

      <SimpleGrid cols={{ base: 2, sm: 4, md: 7 }} spacing="sm">
        <Numero label="Sessões de cardio" value={totals.sessions} />
        <Numero label="Minutos" value={totals.minutes} />
        <Numero label="Distância" value={totals.distance_km} unit="km" />
        <Numero label="Calorias" value={totals.calories_kcal} unit="kcal" />
        <Numero label="Carga (Intervals)" value={totals.training_load} />
        <Numero label="FC média" value={totals.avg_hr_bpm} unit="bpm" />
        <Numero label="FC máxima" value={totals.max_hr_bpm} unit="bpm" />
      </SimpleGrid>
    </CardBase>
  );
}

/** Gráfico com descrição textual (leitor de tela); as tabelas trazem os números. */
function GraficoAcessivel({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div role="img" aria-label={label}>
      {children}
    </div>
  );
}

function ProximaSessaoCard({ sessao }: { sessao: CardioNextSession }) {
  return (
    <CardBase
      title="Próxima sessão recomendada"
      hint="Da última recomendação do Agente de Cardio, se ela ainda tem sessão de hoje em diante."
    >
      <Group gap="sm" wrap="wrap">
        <Badge color="orange" variant="light" size="lg" leftSection={<IconCalendarEvent size={14} />}>
          {sessao.date_label ?? EM_DASH}
        </Badge>
        {sessao.total_minutes_label && (
          <Badge color="gray" variant="outline" size="lg">
            {sessao.total_minutes_label}
          </Badge>
        )}
        {sessao.slot_name && (
          <Badge color="blue" variant="light" size="lg">
            {sessao.slot_name}
          </Badge>
        )}
      </Group>
      <Text fw={600} c={BRAND.textPrimary}>
        {sessao.prescription_text ?? EM_DASH}
      </Text>
      {sessao.rationale && (
        <Text size="sm" c={BRAND.textMuted}>
          {sessao.rationale}
        </Text>
      )}
      {sessao.adjustment_labels.length > 0 && (
        <Group gap={6} wrap="wrap">
          {sessao.adjustment_labels.map((rotulo) => (
            <Badge key={rotulo} color="yellow" variant="light" size="sm">
              {rotulo}
            </Badge>
          ))}
        </Group>
      )}
      <Text size="xs" c={BRAND.textMuted}>
        Gerada em {fmtDateTime(sessao.generated_at)}.{' '}
        <Text component={Link} href="/agente-cardio" size="xs" fw={600} c={BRAND.orange} span>
          Abrir o Agente de Cardio
        </Text>
      </Text>
    </CardBase>
  );
}

function SemanasCard({ payload }: { payload: CardioResponse }) {
  const zonas = useMemo(() => weeklyZoneRows(payload.weeks), [payload.weeks]);
  const modos = useMemo(() => weeklyModeRows(payload.weeks), [payload.weeks]);
  const comZona = hasZoneData(payload.weeks);
  return (
    <CardBase
      title="Cardio por semana"
      hint="Semana de segunda a domingo. Os minutos por zona vêm do tempo em zona do Intervals.icu; a sessão intensa (HIIT) é classificada pelo motor a partir do tempo nas zonas altas."
    >
      {comZona ? (
        <>
          <Text size="sm" fw={600} c={BRAND.textPrimary}>
            Minutos por zona
          </Text>
          <GraficoAcessivel label={zoneChartSummary(payload.weeks)}>
            <ZonasSemanaChart rows={zonas} />
          </GraficoAcessivel>
        </>
      ) : (
        <Text size="sm" c={BRAND.textMuted}>
          Nenhuma atividade da janela trouxe tempo em zona: os minutos por zona e a classe contínuo × intensa não
          podem ser mostrados.
        </Text>
      )}
      <Text size="sm" fw={600} c={BRAND.textPrimary}>
        Contínuo × intensa
      </Text>
      <GraficoAcessivel label={modeChartSummary(payload.weeks)}>
        <ModoSemanaChart rows={modos} />
      </GraficoAcessivel>
      <Table.ScrollContainer minWidth={640}>
        <Table striped highlightOnHover verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Semana</Table.Th>
              <Table.Th ta="right">Sessões</Table.Th>
              <Table.Th ta="right">Minutos</Table.Th>
              <Table.Th ta="right">Contínuo</Table.Th>
              <Table.Th ta="right">Intensa</Table.Th>
              <Table.Th ta="right">Sem zona</Table.Th>
              <Table.Th ta="right">kcal</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {payload.weeks.map((semana) => (
              <Table.Tr key={semana.week_start}>
                <Table.Td>{fmtDate(semana.week_start)}</Table.Td>
                <Table.Td ta="right">{fmtInt(semana.sessions)}</Table.Td>
                <Table.Td ta="right">{fmtInt(semana.minutes)}</Table.Td>
                <Table.Td ta="right">
                  {fmtInt(semana.continuous_sessions)} · {fmtInt(semana.continuous_minutes)} min
                </Table.Td>
                <Table.Td ta="right">
                  {fmtInt(semana.high_intensity_sessions)} · {fmtInt(semana.high_intensity_minutes)} min
                </Table.Td>
                <Table.Td ta="right">{fmtInt(semana.unclassified_sessions)}</Table.Td>
                <Table.Td ta="right">{semana.calories_kcal == null ? EM_DASH : fmtInt(semana.calories_kcal)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </CardBase>
  );
}

function AderenciaCard({ aderencia }: { aderencia: CardioAdherence }) {
  return (
    <CardBase
      title="Planejado × realizado"
      hint="O cardio prescrito no Modelo para o dia de treino, comparado com a atividade de cardio do Intervals.icu feita depois da força."
    >
      {aderencia.weeks.length > 0 && (
        <Table.ScrollContainer minWidth={560}>
          <Table striped verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Semana</Table.Th>
                <Table.Th ta="right">Feitas / previstas</Table.Th>
                <Table.Th ta="right">Min previstos</Table.Th>
                <Table.Th ta="right">Min feitos</Table.Th>
                <Table.Th ta="right">Tempo cumprido</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {aderencia.weeks.map((semana) => (
                <Table.Tr key={semana.week_start}>
                  <Table.Td>{fmtDate(semana.week_start)}</Table.Td>
                  <Table.Td ta="right">
                    {fmtInt(semana.done_sessions)} / {fmtInt(semana.planned_sessions)}
                  </Table.Td>
                  <Table.Td ta="right">{semana.planned_minutes == null ? EM_DASH : fmtInt(semana.planned_minutes)}</Table.Td>
                  <Table.Td ta="right">{semana.realized_minutes == null ? EM_DASH : fmtInt(semana.realized_minutes)}</Table.Td>
                  <Table.Td ta="right">{fmtPercent(semana.time_completion_pct)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
      <Table.ScrollContainer minWidth={640}>
        <Table striped highlightOnHover verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Data</Table.Th>
              <Table.Th>Prescrito</Table.Th>
              <Table.Th ta="right">Min prev. / feitos</Table.Th>
              <Table.Th ta="right">Tempo</Table.Th>
              <Table.Th ta="right">Zona</Table.Th>
              <Table.Th>Status</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {aderencia.sessions.map((sessao) => (
              <Table.Tr key={`${sessao.activity_id}-${sessao.local_date}`}>
                <Table.Td>{fmtDate(sessao.local_date)}</Table.Td>
                <Table.Td>{sessao.prescription_text ?? EM_DASH}</Table.Td>
                <Table.Td ta="right">
                  {sessao.planned_minutes == null ? EM_DASH : fmtInt(sessao.planned_minutes)} /{' '}
                  {sessao.realized_minutes == null ? EM_DASH : fmtInt(sessao.realized_minutes)}
                </Table.Td>
                <Table.Td ta="right">{fmtPercent(sessao.time_completion_pct)}</Table.Td>
                <Table.Td ta="right">{sessao.has_zone_data ? fmtPercent(sessao.zone_completion_pct) : EM_DASH}</Table.Td>
                <Table.Td>
                  <Badge color={adherenceStatusColor(sessao.status)} variant="light" size="sm">
                    {sessao.status_label}
                  </Badge>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </CardBase>
  );
}

function CargaRecuperacaoCard({ wellness }: { wellness: CardioWellness }) {
  const linhas = useMemo(() => wellnessRows(wellness.points), [wellness.points]);
  const { latest } = wellness;
  return (
    <CardBase
      title="Carga e recuperação"
      hint="Aptidão (CTL), fadiga (ATL) e forma (TSB) do Intervals.icu; HRV e FC de repouso como gravados no cache (inclusive a edição manual)."
    >
      <SimpleGrid cols={{ base: 2, sm: 5 }} spacing="sm">
        <Numero label="Aptidão (CTL)" value={latest.ctl} />
        <Numero label="Fadiga (ATL)" value={latest.atl} />
        <Numero label="Forma (TSB)" value={latest.tsb} />
        <Numero label="HRV" value={latest.hrv_ms} unit="ms" />
        <Numero label="FC de repouso" value={latest.resting_hr_bpm} unit="bpm" />
      </SimpleGrid>
      <GraficoAcessivel
        label={`Tendência de CTL, ATL e TSB. Último dia: CTL ${fmtDecimal(latest.ctl, 1)}, ATL ${fmtDecimal(latest.atl, 1)}, TSB ${fmtDecimal(latest.tsb, 1)}.`}
      >
        <CargaChart rows={linhas} />
      </GraficoAcessivel>
      <GraficoAcessivel
        label={`HRV e FC de repouso por dia. Último registro: HRV ${fmtDecimal(latest.hrv_ms, 0)} ms, FC de repouso ${fmtDecimal(latest.resting_hr_bpm, 0)} bpm.`}
      >
        <RecuperacaoChart rows={linhas} />
      </GraficoAcessivel>
    </CardBase>
  );
}

function PaiCard({ pai }: { pai: CardioPai }) {
  const linhas = useMemo(() => paiRows(pai), [pai]);
  return (
    <CardBase
      title="PAI 7 dias (estimado)"
      hint="Acumulado de 7 dias estimado do tempo em zona de FC das atividades do Intervals.icu (não é a leitura do relógio). A linha tracejada é a meta usada pelo Agente de Cardio."
    >
      <Group gap="sm" wrap="wrap">
        <Badge color="gray" variant="outline" size="lg">
          Último: {pai.latest == null ? EM_DASH : fmtInt(pai.latest)}
          {pai.latest_date ? ` (${fmtDate(pai.latest_date)})` : ''}
        </Badge>
        <Badge color="gray" variant="outline" size="lg">
          Meta: {fmtInt(pai.target_min)}
        </Badge>
        {pai.latest_below_target != null && (
          <Badge color={pai.latest_below_target ? 'orange' : 'teal'} variant="light" size="lg">
            {pai.latest_below_target ? 'Abaixo da meta' : 'Na meta'}
          </Badge>
        )}
      </Group>
      <GraficoAcessivel
        label={`PAI de 7 dias estimado por dia. Último: ${pai.latest == null ? 'sem valor' : fmtInt(pai.latest)}; meta ${fmtInt(pai.target_min)}.`}
      >
        <PaiChart rows={linhas} target={pai.target_min} />
      </GraficoAcessivel>
    </CardBase>
  );
}

function ZonasCard({ zonas }: { zonas: AthleteHrZones }) {
  return (
    <CardBase title="Zonas de FC em uso" hint="As zonas que o motor usa na prescrição, nos minutos por zona e na aderência.">
      <Group gap="sm" wrap="wrap">
        <Badge color={zonas.source === 'none' ? 'gray' : 'blue'} variant="light" size="lg">
          Origem: {zoneSourceLabel(zonas.source)}
        </Badge>
        {zonas.max_hr_bpm != null && (
          <Badge color="gray" variant="outline" size="lg">
            FCmáx {fmtInt(zonas.max_hr_bpm)} bpm
          </Badge>
        )}
      </Group>
      {zonas.zones.length === 0 ? (
        <Text size="sm" c={BRAND.textMuted}>
          Sem zonas definidas. Configure o Intervals.icu ou informe a FCmáx no{' '}
          <Text component={Link} href="/perfil" size="sm" fw={600} c={BRAND.orange} span>
            Perfil
          </Text>
          .
        </Text>
      ) : (
        <Table.ScrollContainer minWidth={320}>
          <Table striped verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Zona</Table.Th>
                <Table.Th ta="right">Faixa</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {zonas.zones.map((zona) => (
                <Table.Tr key={zona.zone}>
                  <Table.Td>{zona.label}</Table.Td>
                  <Table.Td ta="right">
                    {fmtInt(zona.lower_bpm)}–{fmtInt(zona.upper_bpm)} bpm
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </CardBase>
  );
}

function ModalidadesCard({ payload }: { payload: CardioResponse }) {
  return (
    <CardBase title="Por modalidade" hint="Da que tomou mais tempo para a que tomou menos.">
      <Table.ScrollContainer minWidth={520}>
        <Table striped highlightOnHover verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Modalidade</Table.Th>
              <Table.Th ta="right">Sessões</Table.Th>
              <Table.Th ta="right">Minutos</Table.Th>
              <Table.Th ta="right">Distância</Table.Th>
              <Table.Th ta="right">FC média</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {payload.by_sport.map((item) => (
              <Table.Tr key={item.sport_type}>
                <Table.Td>{sportLabel(item.sport_type)}</Table.Td>
                <Table.Td ta="right">{fmtInt(item.sessions)}</Table.Td>
                <Table.Td ta="right">{fmtInt(item.minutes)}</Table.Td>
                <Table.Td ta="right">
                  {item.distance_km == null ? EM_DASH : `${fmtDecimal(item.distance_km, 2)} km`}
                </Table.Td>
                <Table.Td ta="right">
                  {item.avg_hr_bpm == null ? EM_DASH : `${fmtDecimal(item.avg_hr_bpm, 0)} bpm`}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </CardBase>
  );
}

function LinhaDeSessao({ item }: { item: SessionHeartRate }) {
  const selo = matchBadge(item);
  const delta = matchDeltaLabel(item.match_delta_seconds);
  return (
    <Table.Tr>
      <Table.Td>{item.local_date ? fmtDate(item.local_date) : EM_DASH}</Table.Td>
      <Table.Td>{item.name || 'Treino'}</Table.Td>
      <Table.Td ta="right">{item.minutes == null ? EM_DASH : fmtInt(item.minutes)}</Table.Td>
      <Table.Td ta="right">
        {item.avg_hr_bpm == null ? EM_DASH : `${fmtDecimal(item.avg_hr_bpm, 0)} bpm`}
      </Table.Td>
      <Table.Td ta="right">
        {item.max_hr_bpm == null ? EM_DASH : `${fmtDecimal(item.max_hr_bpm, 0)} bpm`}
      </Table.Td>
      <Table.Td>
        <Tooltip label={delta ? `${selo.hint} (${delta})` : selo.hint} withArrow multiline w={300}>
          <Badge color={selo.color} variant="light" size="sm" style={{ cursor: 'help' }}>
            {selo.label}
          </Badge>
        </Tooltip>
      </Table.Td>
    </Table.Tr>
  );
}

function FrequenciaCardiacaCard({ payload }: { payload: CardioResponse }) {
  const bloco = payload.strength_heart_rate;
  return (
    <CardBase
      title="FC por sessão de treino"
      hint="A FC medida na própria sessão quando ela existe; senão, a da atividade do Intervals.icu casada pelo horário de início. O selo diz qual é qual."
    >
      <Group gap="sm" wrap="wrap">
        <Badge color="blue" variant="light" size="lg" leftSection={<IconHeartRateMonitor size={14} />}>
          {fmtInt(bloco.sessions_with_hr)} de {fmtInt(bloco.sessions_total)} com FC
        </Badge>
        <Badge color="gray" variant="outline" size="lg">
          Cobertura: {bloco.coverage_pct == null ? EM_DASH : `${fmtDecimal(bloco.coverage_pct, 1)}%`}
        </Badge>
        {bloco.from_activity_total > 0 && (
          <Tooltip
            label="FC medida na própria sessão, importada do relógio: não depende do casamento com o Intervals.icu."
            withArrow
            multiline
            w={300}
          >
            <Badge color="teal" variant="light" size="lg" style={{ cursor: 'help' }}>
              {fmtInt(bloco.from_activity_total)} medida(s) no treino
            </Badge>
          </Tooltip>
        )}
        {bloco.ambiguous_in_use_total > 0 && (
          <Tooltip
            label="Mais de uma atividade do Intervals.icu caiu na janela de tolerância: a FC mostrada é a da mais próxima no tempo."
            withArrow
            multiline
            w={300}
          >
            <Badge color="yellow" variant="light" size="lg" style={{ cursor: 'help' }}>
              {fmtInt(bloco.ambiguous_in_use_total)} ambígua(s)
            </Badge>
          </Tooltip>
        )}
        {/* M37: o LADO DO INTERVALS do mesmo casamento. Os selos acima contam
            SESSÕES (quantas ficaram sem FC); este conta ATIVIDADES que
            sobraram do outro lado — sinal de treino não registrado aqui, que
            não aparece em nenhum dos outros números. Só aparece quando há
            sobra: um "0 sem correspondência" fixo viraria ruído. */}
        {payload.unmatched_strength_activities_total > 0 && (
          <Tooltip
            label="Atividades de força (WeightTraining) do Intervals.icu que não casaram com nenhuma sessão registrada no MuscleFit: ou o treino não foi registrado aqui, ou o horário de início ficou fora da tolerância de 60 minutos."
            withArrow
            multiline
            w={320}
          >
            <Badge color="orange" variant="light" size="lg" style={{ cursor: 'help' }}>
              {fmtInt(payload.unmatched_strength_activities_total)} de{' '}
              {fmtInt(payload.strength_activities_total)} sessão(ões) do Intervals sem
              correspondência
            </Badge>
          </Tooltip>
        )}
      </Group>

      <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
        <Numero label="FC média das sessões" value={bloco.avg_hr_bpm} unit="bpm" />
        <Numero label="FC máxima registrada" value={bloco.max_hr_bpm} unit="bpm" />
      </SimpleGrid>

      {bloco.items.length === 0 ? (
        <EmptyState
          title="Nenhuma sessão de treino neste período"
          description="Sem sessão registrada não há FC de sessão para mostrar."
        />
      ) : (
        <Table.ScrollContainer minWidth={620}>
          <Table striped highlightOnHover verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Data</Table.Th>
                <Table.Th>Sessão</Table.Th>
                <Table.Th ta="right">Min</Table.Th>
                <Table.Th ta="right">FC média</Table.Th>
                <Table.Th ta="right">FC máx</Table.Th>
                <Table.Th>Associação</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {bloco.items.map((item) => (
                <LinhaDeSessao key={item.activity_id ?? `${item.local_date}-${item.name}`} item={item} />
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </CardBase>
  );
}

function AtividadesCard({ payload }: { payload: CardioResponse }) {
  const dias = useMemo(() => groupSessionsByDay(payload.sessions), [payload.sessions]);
  return (
    <CardBase title="Atividades de cardio" hint="Mais recente primeiro, agrupadas por dia.">
      <Stack gap="sm">
        {dias.map((dia) => (
          <div key={dia.activity_date}>
            <Text size="xs" fw={700} c={BRAND.textMuted}>
              {dia.activity_date === 'sem-data' ? 'Sem data' : fmtDate(dia.activity_date)}
            </Text>
            <Stack gap={4} mt={4}>
              {dia.sessions.map((sessao) => (
                <Group key={sessao.external_id} justify="space-between" wrap="wrap" gap="xs">
                  <Group gap="xs" wrap="wrap">
                    <Badge color="teal" variant="light" size="sm">
                      {sportLabel(sessao.sport_type)}
                    </Badge>
                    <Text size="sm" c={BRAND.textPrimary}>
                      {sessao.name || 'Atividade'}
                    </Text>
                  </Group>
                  <Group gap="md" wrap="wrap">
                    <Text size="sm" c={BRAND.textMuted}>
                      {sessao.minutes == null ? EM_DASH : `${fmtInt(sessao.minutes)} min`}
                    </Text>
                    <Text size="sm" c={BRAND.textMuted}>
                      {sessao.distance_km == null ? EM_DASH : `${fmtDecimal(sessao.distance_km, 2)} km`}
                    </Text>
                    <Text size="sm" c={BRAND.textMuted}>
                      {sessao.avg_hr_bpm == null ? EM_DASH : `${fmtDecimal(sessao.avg_hr_bpm, 0)} bpm`}
                    </Text>
                  </Group>
                </Group>
              ))}
            </Stack>
          </div>
        ))}
      </Stack>
    </CardBase>
  );
}
