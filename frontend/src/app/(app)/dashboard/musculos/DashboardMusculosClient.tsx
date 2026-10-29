'use client';

import {
  Accordion,
  Anchor,
  Badge,
  Box,
  Divider,
  Group,
  Paper,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  Title,
  Tooltip,
  UnstyledButton,
} from '@mantine/core';
import { IconChevronRight, IconInfoCircle, IconX } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { useMemo, useState } from 'react';
import { MuscleActivation7d } from './MuscleActivation7d';
import { MuscleGroupTrend } from './MuscleGroupTrend';
import { MuscleSessionComparatives } from './MuscleSessionComparatives';
import { DashboardPeriodSelect } from '@/components/dashboard/DashboardPeriodSelect';
import { MetricValue } from '@/components/dashboard/MetricValue';
import { MinTargetMaxBar } from '@/components/dashboard/MinTargetMaxBar';
import { PlannedVsRealizedBar } from '@/components/dashboard/PlannedVsRealizedBar';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import {
  DashboardCardSkeleton,
  DashboardSkeleton,
  EmptyState,
  ErrorState,
  PartialState,
} from '@/components/dashboard/states';
import {
  dashboardKeys,
  fetchMuscleDetail,
  fetchMuscles,
} from '@/lib/dashboard-api';
import { useDashboardFilters, type DashboardFilters } from '@/lib/dashboard-filters';
import { EM_DASH, fmtDate, fmtInt, fmtKg, fmtRpe, fmtSets } from '@/lib/dashboard-format';
import {
  MUSCLE_SORTS,
  MUSCLE_SORT_LABELS,
  contributionsByExercise,
  countByStatus,
  groupsOutsideTemplatePlan,
  groupsWithGenericTarget,
  humanizeHours,
  isTemplatePlanItem,
  sessionsForExercise,
  sortMuscleItems,
  type MuscleSort,
} from '@/lib/dashboard-muscles';
import { STATUS_DOMAIN_ORDER, statusVisual, volumeStatusDomain } from '@/lib/dashboard-status';
import type { MuscleItem, MuscleSession } from '@/lib/dashboard-v3-types';
import { exerciseProgressHref } from '@/lib/exercise-progress';
import { isWorkingSetType } from '@/lib/set-types';
import { BRAND } from '@/theme';

/**
 * `/dashboard/musculos` — "quais grupos estão atrasados ou em excesso, e o
 * volume chegou no músculo certo?" (plano §7, §11; etapa F1.2).
 *
 * Reescrita sobre os contratos v3 (`GET /analytics/dashboard/muscles` e
 * `/muscles/{id}`). O que muda em relação à versão anterior:
 *
 * - **Realizado, planejado e projetado ficam separados.** A versão v2
 *   classificava a SOMA de realizado + projetado (herança de
 *   `dashboard-cycle`), então um grupo ainda não treinado no ciclo aparecia
 *   "no alvo" porque o modelo prometia rodá-lo depois. Aqui o status vem do
 *   backend classificando SÓ o realizado (`status_basis: 'realized_only'`), e
 *   a projeção aparece à parte, hachurada, com selo `PROJECTED`.
 * - **Todo número passa por `MetricValue`**: natureza, qualidade e limitações
 *   vêm no envelope e são exibidas, em vez de um número solto.
 * - **Estabilizador não é fundido com secundário** — três colunas.
 * - **Drill-down até a série**: grupo → exercícios → sessões → séries, com
 *   cada exercício linkando para `/dashboard/exercicios/[id]` (F1.3) com o
 *   filtro preservado.
 *
 * As rotas v2 (`dashboard-muscle-status`, `dashboard-muscle-drilldown`,
 * `dashboard-stimulus-fatigue`) continuam existindo para outros consumidores;
 * esta tela apenas deixou de chamá-las. A F2.4 NAO as marcou como legadas:
 * elas ainda são a implementação de telas vivas (o drill-down desta aqui
 * inclusive). Só `dashboard-overview` e `dashboard-alerts`, sem nenhum
 * consumidor, foram removidas (M76 Lote 2c e COD-05).
 */
export function DashboardMusculosClient() {
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();
  const [sort, setSort] = useState<MuscleSort>('status');
  // A prontidão nasceu OPT-IN porque custava ~4s a mais (o modelo lia dois
  // datasets inteiros de séries por chamada). A M20 (F1.5) derrubou esse
  // custo, então ela vem LIGADA por padrão; o botão continua existindo como
  // preferência de exibição, não mais como defesa contra latência. A consulta
  // segue separada para a tabela pintar sem esperar pelo bloco.
  const [showReadiness, setShowReadiness] = useState(true);

  const selectedGroupId = filters.muscleGroupId;
  const setSelectedGroupId = (next: number | null) => setFilters({ muscleGroupId: next });

  // `include_comparatives` só na PRIMEIRA chamada (M34): o bloco por sessão
  // custa uma consulta a mais no backend, e a consulta da prontidão abaixo
  // devolveria exatamente o mesmo bloco. Pedi-lo nas duas pagaria dobrado
  // pelo mesmo resultado.
  const musclesQuery = useQuery({
    queryKey: dashboardKeys.muscles(windowQuery, false, true),
    queryFn: () => fetchMuscles(windowQuery, false, true),
  });

  const readinessQuery = useQuery({
    queryKey: dashboardKeys.muscles(windowQuery, true),
    queryFn: () => fetchMuscles(windowQuery, true),
    enabled: showReadiness,
  });

  const detailQuery = useQuery({
    queryKey: dashboardKeys.muscleDetail(selectedGroupId, windowQuery),
    queryFn: () => fetchMuscleDetail(selectedGroupId, windowQuery),
    enabled: selectedGroupId != null,
  });

  const payload = musclesQuery.data;
  const readinessById = useMemo(() => {
    const map = new Map<number, MuscleItem['readiness']>();
    for (const item of readinessQuery.data?.items ?? []) {
      map.set(item.muscle_group_id, item.readiness);
    }
    return map;
  }, [readinessQuery.data]);

  const items = useMemo(
    () => sortMuscleItems(payload?.items ?? [], sort),
    [payload?.items, sort],
  );
  const counts = useMemo(() => countByStatus(items), [items]);
  const genericTargets = useMemo(() => groupsWithGenericTarget(items), [items]);
  const outsideModel = useMemo(() => groupsOutsideTemplatePlan(items), [items]);
  // Meta do próprio Modelo por equipamento: 3 estados; demais origens: 5.
  const statusDomain = volumeStatusDomain(payload?.target_source);

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <div>
          <Title order={2} c={BRAND.textPrimary}>
            Grupos musculares
          </Title>
          <Text size="sm" c={BRAND.textMuted}>
            Volume <strong>realizado</strong> contra a meta semanal. Planejado e projetado aparecem
            à parte — o status classifica só o que você de fato treinou.
          </Text>
        </div>
        <DashboardPeriodSelect
          filters={filters}
          onChange={setFilters}
          rangeError={rangeError}
        />
      </Group>

      {rangeError && <ErrorState title="Período inválido" description={rangeError} />}

      <Paper
        withBorder
        radius="lg"
        p="md"
        style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}
      >
        <Group justify="space-between" align="flex-end" wrap="wrap" gap="sm">
          {/*
            F1.7 — os 4 critérios de ordenação somam ~500px de rótulo; num
            celular o SegmentedControl (que não quebra linha e corta o excesso
            com `overflow:hidden`) escondia as duas últimas opções, que ficavam
            inalcançáveis. No toque/telas pequenas o mesmo estado vira um
            Select; de `sm` para cima nada muda.
          */}
          <SegmentedControl
            visibleFrom="sm"
            size="xs"
            value={sort}
            onChange={(value) => setSort(value as MuscleSort)}
            data={MUSCLE_SORTS.map((value) => ({ value, label: MUSCLE_SORT_LABELS[value] }))}
          />
          <Select
            hiddenFrom="sm"
            size="xs"
            label="Ordenar por"
            allowDeselect={false}
            value={sort}
            onChange={(value) => value && setSort(value as MuscleSort)}
            data={MUSCLE_SORTS.map((value) => ({ value, label: MUSCLE_SORT_LABELS[value] }))}
          />
          <Switch
            size="sm"
            checked={showReadiness}
            onChange={(event) => setShowReadiness(event.currentTarget.checked)}
            label="Mostrar prontidão"
            description="Consulta separada, mais lenta"
          />
        </Group>

        {items.length > 0 && (
          <Group gap="xs" mt="sm" wrap="wrap">
            {STATUS_DOMAIN_ORDER[statusDomain].filter((code) => counts[code]).map((code) => (
              <StatusBadge
                key={code}
                domain={statusDomain}
                code={code}
                size="sm"
                suffix={` · ${counts[code]}`}
              />
            ))}
          </Group>
        )}
      </Paper>

      {payload && (
        <PartialState
          quality={payload.data_quality}
          limitations={[
            ...payload.limitations,
            genericTargets.length > 0
              ? `Sem meta cadastrada para: ${genericTargets.join(', ')}. Para esses grupos, "abaixo" e "acima" usam limiares genéricos.`
              : '',
            outsideModel.length > 0
              ? `Fora do seu Modelo: ${outsideModel.join(', ')}. O volume realizado nesses grupos é trabalho extra em relação ao plano, não déficit.`
              : '',
          ].filter(Boolean)}
          fixHref={genericTargets.length > 0 ? '/metas-ativacao' : '/dashboard/data-quality'}
          fixLabel={genericTargets.length > 0 ? 'Cadastrar metas' : 'Ver qualidade dos dados'}
        />
      )}

      {musclesQuery.isPending && <DashboardCardSkeleton rows={6} />}
      {musclesQuery.isError && <ErrorState error={musclesQuery.error} />}
      {musclesQuery.isSuccess && items.length === 0 && (
        <EmptyState
          title="Nenhum grupo com meta nem volume no período"
          description="Registre um treino ou cadastre uma Meta de Ativação para comparar o volume com um alvo."
          actionHref="/metas-ativacao"
          actionLabel="Cadastrar Meta de Ativação"
        />
      )}

      {items.length > 0 && (
        <>
          {/* Mobile-first: cartões empilhados abaixo de `md`, tabela acima. */}
          <Stack gap="sm" hiddenFrom="md">
            {items.map((item) => (
              <MuscleCard
                key={item.muscle_group_id}
                item={item}
                readiness={readinessById.get(item.muscle_group_id) ?? null}
                showReadiness={showReadiness}
                readinessPending={showReadiness && readinessQuery.isPending}
                selected={item.muscle_group_id === selectedGroupId}
                onSelect={() =>
                  setSelectedGroupId(
                    item.muscle_group_id === selectedGroupId ? null : item.muscle_group_id,
                  )
                }
                filters={filters}
              />
            ))}
          </Stack>

          <Paper
            withBorder
            radius="lg"
            p="md"
            visibleFrom="md"
            style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}
          >
            <Table.ScrollContainer minWidth={900}>
              <Table verticalSpacing="sm" highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Grupo</Table.Th>
                    <Table.Th>Realizado vs meta (séries/sem)</Table.Th>
                    <Table.Th>Efetivas</Table.Th>
                    <Table.Th>
                      <Tooltip
                        label="Direto (alvo principal) · indireto (auxiliar) · estabilizador (só segura a carga)"
                        withArrow
                        multiline
                        w={280}
                      >
                        <span style={{ cursor: 'help' }}>Dir · Ind · Est</span>
                      </Tooltip>
                    </Table.Th>
                    <Table.Th>Frequência</Table.Th>
                    <Table.Th>Planejado</Table.Th>
                    <Table.Th>Projetado</Table.Th>
                    {showReadiness && <Table.Th>Prontidão</Table.Th>}
                    <Table.Th>Último estímulo</Table.Th>
                    <Table.Th />
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {items.map((item) => (
                    <MuscleRow
                      key={item.muscle_group_id}
                      item={item}
                      readiness={readinessById.get(item.muscle_group_id) ?? null}
                      showReadiness={showReadiness}
                      readinessPending={showReadiness && readinessQuery.isPending}
                      selected={item.muscle_group_id === selectedGroupId}
                      onSelect={() =>
                        setSelectedGroupId(
                          item.muscle_group_id === selectedGroupId ? null : item.muscle_group_id,
                        )
                      }
                    />
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </Paper>
        </>
      )}

      {/*
        Bloco POR SESSÃO (M34). Fica DEPOIS da tabela por período e com
        cabeçalho próprio de propósito: a unidade é outra (T-0 é a última
        sessão do grupo, não "esta semana"), e misturar as duas leituras sem
        rótulo foi o defeito da tela legada `/volume-grupamentos`.
      */}
      {payload?.comparatives && <MuscleSessionComparatives comparatives={payload.comparatives} />}

      {selectedGroupId != null && (
        <Paper
          withBorder
          radius="lg"
          p="md"
          style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}
        >
          <Group justify="space-between" align="center" mb="sm">
            <Title order={4} c={BRAND.textPrimary}>
              De onde veio o estímulo
            </Title>
            <UnstyledButton onClick={() => setSelectedGroupId(null)} aria-label="Fechar detalhe">
              <Group gap={4}>
                <IconX size={14} />
                <Text size="sm" c={BRAND.textMuted}>
                  fechar
                </Text>
              </Group>
            </UnstyledButton>
          </Group>

          {detailQuery.isPending && <DashboardSkeleton rows={5} />}
          {detailQuery.isError && <ErrorState error={detailQuery.error} />}
          {detailQuery.isSuccess && (
            <MuscleDrilldown
              sessions={detailQuery.data.sessions}
              setsTruncated={detailQuery.data.sets_truncated}
              setsLimit={detailQuery.data.sets_limit}
              limitations={detailQuery.data.limitations}
              quality={detailQuery.data.data_quality}
              filters={filters}
            />
          )}
        </Paper>
      )}

      {/*
        Ativação muscular dos últimos 7 dias (veio de /treinos), contra as
        metas dos macro-grupos de volume. Janela fixa de 7 dias e consultas
        próprias, independentes do período do topo.
      */}
      <MuscleActivation7d />

      {/*
        Tendência por grupo muscular (veio de /grupos-musculares). Consulta e
        filtros próprios (granularidade/métrica/janela em dias), independentes
        do período do topo — por isso fica por último, como bloco separado.
      */}
      <MuscleGroupTrend />
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// Linha e cartão de um grupo
// ---------------------------------------------------------------------------

/**
 * Barra da meta conforme a ORIGEM dela: com meta do próprio Modelo por
 * equipamento (`template_plan`) só existem planejado e realizado — `min`/`max`
 * chegam `null` e nunca viram zero numa barra de três marcas.
 */
function TargetBar({ item }: { item: MuscleItem }) {
  if (isTemplatePlanItem(item)) {
    return (
      <PlannedVsRealizedBar
        planned={item.targets.target.value}
        actual={item.realized.weekly_effective_sets.value}
        status={item.status}
      />
    );
  }
  return (
    <MinTargetMaxBar
      min={item.targets.min.value}
      target={item.targets.target.value}
      max={item.targets.max.value}
      actual={item.realized.weekly_effective_sets.value}
      projected={null}
      status={item.status}
    />
  );
}

/**
 * Os três valores dividem a MESMA unidade, então ela aparece uma vez só, no
 * último — repetir "séries/sem" três vezes fazia a linha ocupar ~220px e, no
 * cartão do celular (coluna de ~150px), o conteúdo vazava por cima do campo
 * vizinho. Só apresentação: nenhum número é recalculado.
 */
const semUnidade = (metric: MuscleItem['realized']['direct_sets']) => ({ ...metric, unit: null });

function RolesCell({ item }: { item: MuscleItem }) {
  return (
    <Group gap={4} wrap="wrap">
      <MetricValue metric={semUnidade(item.realized.direct_sets)} digits={0} showNature={false} />
      <Text size="xs" c="dimmed">
        ·
      </Text>
      <MetricValue metric={semUnidade(item.realized.indirect_sets)} digits={0} showNature={false} />
      <Text size="xs" c="dimmed">
        ·
      </Text>
      <MetricValue metric={item.realized.stabilizer_sets} digits={0} showNature={false} />
    </Group>
  );
}

function FrequencyCell({ item }: { item: MuscleItem }) {
  return (
    <Stack gap={0}>
      <Group gap={4} wrap="nowrap">
        <MetricValue metric={item.frequency.direct_sessions} digits={0} showNature={false} />
        <Text size="xs" c="dimmed">
          diretas
        </Text>
      </Group>
      <Group gap={4} wrap="nowrap">
        <Text size="xs" c="dimmed">
          a cada
        </Text>
        <MetricValue metric={item.frequency.median_interval_hours} digits={0} size="xs" />
      </Group>
    </Stack>
  );
}

function ReadinessCell({
  readiness,
  pending,
}: {
  readiness: MuscleItem['readiness'];
  pending: boolean;
}) {
  if (pending) return <DashboardSkeleton rows={1} height={18} withHeader={false} />;
  if (!readiness) {
    return (
      <Text size="sm" c="dimmed">
        {EM_DASH}
      </Text>
    );
  }
  return (
    <StatusBadge
      domain="readiness"
      code={readiness.status}
      tooltip={readiness.explanation.join('\n')}
      size="sm"
    />
  );
}

function LastStimulus({ item }: { item: MuscleItem }) {
  const horas = humanizeHours(item.hours_since_last_stimulus.value);
  return (
    <Stack gap={0}>
      <Text size="sm">{fmtDate(item.last_stimulus_local_date)}</Text>
      {horas && (
        <Text size="xs" c="dimmed">
          há {horas}
        </Text>
      )}
    </Stack>
  );
}

function MuscleRow({
  item,
  readiness,
  showReadiness,
  readinessPending,
  selected,
  onSelect,
}: {
  item: MuscleItem;
  readiness: MuscleItem['readiness'];
  showReadiness: boolean;
  readinessPending: boolean;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <Table.Tr
      onClick={onSelect}
      style={{
        cursor: 'pointer',
        background: selected ? 'var(--mantine-color-dark-6)' : undefined,
      }}
    >
      <Table.Td>
        <Stack gap={2}>
          <Text fw={600}>{item.muscle_group}</Text>
          <Group gap={4} wrap="wrap">
            <StatusBadge domain={volumeStatusDomain(item.targets.source)} code={item.status} size="xs" />
            {(item.targets.source === 'fallback_generic' || item.targets.source === 'template_plan') && (
              <StatusBadge domain="targetSource" code={item.targets.source} size="xs" />
            )}
          </Group>
        </Stack>
      </Table.Td>
      <Table.Td style={{ minWidth: 200 }}>
        <TargetBar item={item} />
        <MetricValue metric={item.realized.weekly_effective_sets} fw={600} />
      </Table.Td>
      <Table.Td>
        <MetricValue metric={item.realized.effective_sets_stimulus} />
      </Table.Td>
      <Table.Td>
        <RolesCell item={item} />
      </Table.Td>
      <Table.Td>
        <FrequencyCell item={item} />
      </Table.Td>
      <Table.Td>
        <MetricValue metric={item.planned.effective_sets} />
      </Table.Td>
      <Table.Td>
        <MetricValue metric={item.projected.remaining_sets} />
      </Table.Td>
      {showReadiness && (
        <Table.Td>
          <ReadinessCell readiness={readiness} pending={readinessPending} />
        </Table.Td>
      )}
      <Table.Td>
        <LastStimulus item={item} />
      </Table.Td>
      <Table.Td>
        <IconChevronRight size={16} aria-hidden />
      </Table.Td>
    </Table.Tr>
  );
}

function MuscleCard({
  item,
  readiness,
  showReadiness,
  readinessPending,
  selected,
  onSelect,
  filters,
}: {
  item: MuscleItem;
  readiness: MuscleItem['readiness'];
  showReadiness: boolean;
  readinessPending: boolean;
  selected: boolean;
  onSelect: () => void;
  filters: DashboardFilters;
}) {
  return (
    <Paper
      withBorder
      radius="lg"
      p="md"
      onClick={onSelect}
      style={{
        background: selected ? 'var(--mantine-color-dark-6)' : BRAND.bgElevated,
        borderColor: selected
          ? statusVisual(volumeStatusDomain(item.targets.source), item.status).hex
          : BRAND.border,
        cursor: 'pointer',
      }}
    >
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <Text fw={600}>{item.muscle_group}</Text>
        <StatusBadge domain={volumeStatusDomain(item.targets.source)} code={item.status} size="sm" />
      </Group>

      <Box mt="xs">
        <TargetBar item={item} />
      </Box>

      <SimpleGrid cols={2} spacing="xs" mt="sm">
        <Campo rotulo="Realizado (sem.)">
          <MetricValue metric={item.realized.weekly_effective_sets} fw={600} />
        </Campo>
        <Campo rotulo="Efetivas no período">
          <MetricValue metric={item.realized.effective_sets_stimulus} />
        </Campo>
        <Campo rotulo="Direto · indireto · estab.">
          <RolesCell item={item} />
        </Campo>
        <Campo rotulo="Frequência">
          <FrequencyCell item={item} />
        </Campo>
        <Campo rotulo="Planejado">
          <MetricValue metric={item.planned.effective_sets} />
        </Campo>
        <Campo rotulo="Projetado (ciclo)">
          <MetricValue metric={item.projected.remaining_sets} />
        </Campo>
        {showReadiness && (
          <Campo rotulo="Prontidão">
            <ReadinessCell readiness={readiness} pending={readinessPending} />
          </Campo>
        )}
        <Campo rotulo="Último estímulo">
          <LastStimulus item={item} />
        </Campo>
      </SimpleGrid>

      {(item.targets.source === 'fallback_generic' || item.targets.source === 'template_plan') && (
        <Box mt="xs">
          <StatusBadge domain="targetSource" code={item.targets.source} size="xs" />
        </Box>
      )}

      {item.top_exercises.length > 0 && (
        <Text size="xs" c={BRAND.textMuted} mt="xs">
          Mais contribuiu:{' '}
          {item.top_exercises[0].exercise_id != null ? (
            <Anchor
              component={Link}
              href={exerciseProgressHref(item.top_exercises[0].exercise_id, filters)}
              c={BRAND.orange}
              onClick={(event) => event.stopPropagation()}
            >
              {item.top_exercises[0].name}
            </Anchor>
          ) : (
            item.top_exercises[0].name
          )}
        </Text>
      )}
    </Paper>
  );
}

function Campo({ rotulo, children }: { rotulo: string; children: React.ReactNode }) {
  return (
    <Stack gap={0}>
      <Text size="xs" c={BRAND.textMuted}>
        {rotulo}
      </Text>
      {children}
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// Drill-down: exercícios → sessões → séries
// ---------------------------------------------------------------------------

function MuscleDrilldown({
  sessions,
  setsTruncated,
  setsLimit,
  limitations,
  quality,
  filters,
}: {
  sessions: MuscleSession[];
  setsTruncated: boolean;
  setsLimit: number;
  limitations: string[];
  quality: MuscleItem['data_quality'];
  filters: DashboardFilters;
}) {
  const contribuicoes = useMemo(() => contributionsByExercise(sessions), [sessions]);

  if (sessions.length === 0) {
    return (
      <EmptyState
        title="Nenhuma série deste grupo no período"
        description="Nada foi registrado que gerasse estímulo para este grupo na janela selecionada."
      />
    );
  }

  return (
    <Stack gap="sm">
      <PartialState quality={quality} limitations={limitations} />
      {setsTruncated && (
        <Text size="xs" c={BRAND.textMuted}>
          <IconInfoCircle size={12} style={{ verticalAlign: 'middle' }} /> Mostrando as {setsLimit}{' '}
          séries mais recentes do período.
        </Text>
      )}

      <Accordion variant="separated" radius="md">
        {contribuicoes.map((contribuicao) => {
          const sessoes = sessionsForExercise(sessions, contribuicao.exerciseId, contribuicao.name);
          const chave = `${contribuicao.exerciseId ?? contribuicao.name}`;
          return (
            <Accordion.Item key={chave} value={chave}>
              <Accordion.Control>
                <Group justify="space-between" wrap="wrap" gap="xs" pr="sm">
                  <Group gap="xs" wrap="wrap">
                    <Text fw={600}>{contribuicao.name}</Text>
                    {contribuicao.roles.map((role) => (
                      <StatusBadge key={role} domain="muscleRole" code={role} size="xs" />
                    ))}
                  </Group>
                  <Group gap="xs" wrap="nowrap">
                    <Badge variant="light" color="gray" radius="sm">
                      {fmtSets(contribuicao.stimulus, 1)} efetivas
                    </Badge>
                    {contribuicao.share != null && (
                      <Badge variant="light" color="teal" radius="sm">
                        {Math.round(contribuicao.share * 100)}% do grupo
                      </Badge>
                    )}
                  </Group>
                </Group>
              </Accordion.Control>
              <Accordion.Panel>
                {contribuicao.exerciseId != null && (
                  <Anchor
                    component={Link}
                    href={exerciseProgressHref(contribuicao.exerciseId, filters)}
                    c={BRAND.orange}
                    size="sm"
                  >
                    Estou evoluindo neste exercício? →
                  </Anchor>
                )}
                <Stack gap="xs" mt="xs">
                  {sessoes.map((sessao) => (
                    <Box key={sessao.activity_id}>
                      <Group gap="xs" wrap="wrap">
                        <Text size="sm" fw={600}>
                          {fmtDate(sessao.local_date)}
                        </Text>
                        <Text size="xs" c={BRAND.textMuted}>
                          {sessao.activity_name}
                        </Text>
                        {!sessao.has_direct_work && (
                          <Badge size="xs" variant="light" color="gray" radius="sm">
                            sem trabalho direto
                          </Badge>
                        )}
                      </Group>
                      <Table verticalSpacing={4} withRowBorders={false} mt={4}>
                        <Table.Thead>
                          <Table.Tr>
                            <Table.Th>Série</Table.Th>
                            <Table.Th>Reps</Table.Th>
                            <Table.Th>Carga</Table.Th>
                            <Table.Th>RPE</Table.Th>
                            <Table.Th>Efetiva</Table.Th>
                          </Table.Tr>
                        </Table.Thead>
                        <Table.Tbody>
                          {sessao.exposures.flatMap((exposicao) =>
                            exposicao.sets.map((serie) => (
                              <Table.Tr key={serie.set_id}>
                                <Table.Td>
                                  <Group gap={4} wrap="nowrap">
                                    <Text size="sm">{fmtInt(serie.set_number)}</Text>
                                    {!isWorkingSetType(serie.set_type) && (
                                      <Badge size="xs" variant="outline" radius="sm">
                                        {serie.set_type}
                                      </Badge>
                                    )}
                                  </Group>
                                </Table.Td>
                                <Table.Td>
                                  <Text size="sm">{fmtInt(serie.reps)}</Text>
                                </Table.Td>
                                <Table.Td>
                                  <Text size="sm">{fmtKg(serie.weight_kg)}</Text>
                                </Table.Td>
                                <Table.Td>
                                  <Text size="sm">{fmtRpe(serie.rpe)}</Text>
                                </Table.Td>
                                <Table.Td>
                                  <Text size="sm">{fmtSets(serie.effective_sets_stimulus, 2)}</Text>
                                </Table.Td>
                              </Table.Tr>
                            )),
                          )}
                        </Table.Tbody>
                      </Table>
                      <Divider my={4} />
                    </Box>
                  ))}
                </Stack>
              </Accordion.Panel>
            </Accordion.Item>
          );
        })}
      </Accordion>
    </Stack>
  );
}
