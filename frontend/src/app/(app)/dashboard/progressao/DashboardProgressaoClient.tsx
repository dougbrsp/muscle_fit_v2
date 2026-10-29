'use client';

import dynamic from 'next/dynamic';
import { ChartPlaceholder } from '@/components/charts/ChartPlaceholder';
import {
  Alert,
  Badge,
  Button,
  Group,
  Paper,
  SegmentedControl,
  Select,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { IconArrowRight, IconInfoCircle, IconTargetArrow } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { useMemo, useState } from 'react';
import { DashboardPeriodSelect } from '@/components/dashboard/DashboardPeriodSelect';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { TrendBadge } from '@/components/dashboard/TrendBadge';
import { DashboardCardSkeleton, EmptyState, ErrorState } from '@/components/dashboard/states';
import { CHART_LEGEND_STYLE } from '@/components/charts/chartTheme';
import {
  dashboardKeys,
  fetchExerciseOptions,
  fetchProgression,
  fetchProgressionSuggestion,
  fetchProgressionTrend,
} from '@/lib/dashboard-api';
import { useDashboardFilters } from '@/lib/dashboard-filters';
import { loadConventionLabel } from '@/lib/equipment-exercises-types';
import { exerciseProgressHref, fatigueContextLabel, statusReasonsText } from '@/lib/exercise-progress';
import { INTENSITY_STATUS_COLORS, INTENSITY_STATUS_LABELS } from '@/lib/intensity-status';
import { buildProgressionChart, isSessionE1rmReliable, sessionLoad, sessionRepsText } from '@/lib/progression-chart';
import { fmtDate, fmtInt, fmtKg, fmtRpe } from '@/lib/dashboard-format';
import { TrainingGoalBadge } from '@/components/TrainingGoalBadge';
import {
  ALL_GOALS,
  defaultGoalValue,
  goalQueryParam,
  isHighlightedGoal,
  sessionGoalFilterOptions,
} from '@/lib/training-goal-filter';
import { useTrainingGoals } from '@/lib/training-goals';
import { BRAND } from '@/theme';
import { MuscleGroupProgressionPanel } from './MuscleGroupProgressionPanel';
import { StrengthPanel } from './StrengthPanel';

// COD-14: recharts sob demanda; o placeholder tem a altura do gráfico.
const ProgressaoChart = dynamic(() => import('./ProgressaoChart').then((m) => m.ProgressaoChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height="100%" />,
});

/**
 * `/dashboard/progressao` — evolução de carga, reps, RPE e e1RM de UM
 * exercício, com a classificação de tendência e a sugestão para a próxima
 * sessão.
 *
 * Endpoints (`api/app/analytics.py`):
 * - `GET /analytics/dashboard-progression` (tabela + gráfico, e1RM por sessão,
 *   e a seta "Evolução" de cada sessão já classificada no backend);
 * - `GET /analytics/dashboard-progression-trend` (veredito: a MESMA regra da
 *   home e do drill-down, `classify_exercise_progression` — nunca
 *   reimplementada aqui);
 * - `GET /analytics/dashboard-progression-suggestion` (motor de prescrição
 *   por par, o mesmo do gerador de ficha; no objetivo do filtro, M97 F4).
 *
 * Recorte diferente de `/exercicios` (Etapa 9), que usa
 * `GET /analytics/exercise-progress`: lá é volume/carga agregados por sessão,
 * aqui é e1RM por sessão + veredito de tendência + sugestão de progressão.
 *
 * Os três endpoints só aceitam `exercise_name`/`mapping_id`/`days` (e, na
 * janela `custom`, `date_from`/`date_to` — M61) — não há
 * filtro por academia/equipamento no backend, então nenhum controle desses é
 * exposto. O filtro de grupo muscular é local e só restringe a lista do
 * seletor de exercício.
 */
export function DashboardProgressaoClient() {
  const { filters, setFilters, dayRange, windowQuery, rangeError } = useDashboardFilters();
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);
  // M97 F4: filtro de objetivo. "Todos" = a tabela de sempre (sem `goal`) e o
  // veredito no objetivo default; um objetivo filtra as duas leituras.
  const goals = useTrainingGoals().data;
  const [goalFilter, setGoalFilter] = useState<string>(ALL_GOALS);
  const goal = goalQueryParam(goals, goalFilter);
  const defaultGoal = defaultGoalValue(goals);

  const optionsQuery = useQuery({
    queryKey: dashboardKeys.exerciseOptions(),
    queryFn: fetchExerciseOptions,
    // Catálogo de exercícios muda por cadastro, não por treino: 5 min é
    // suficiente e evita recarregar a lista a cada troca de exercício.
    staleTime: 5 * 60_000,
  });

  const allOptions = useMemo(() => optionsQuery.data?.items ?? [], [optionsQuery.data]);

  const muscleGroups = useMemo(() => {
    const groups = new Set<string>();
    for (const option of allOptions) {
      if (option.primary_group) groups.add(option.primary_group);
    }
    return Array.from(groups).sort((a, b) => a.localeCompare(b, 'pt-BR'));
  }, [allOptions]);

  const filteredOptions = useMemo(
    () => (selectedGroup ? allOptions.filter((option) => option.primary_group === selectedGroup) : allOptions),
    [allOptions, selectedGroup],
  );

  const mappingId = filters.exerciseMappingId;

  const progressionQuery = useQuery({
    queryKey: dashboardKeys.progression(mappingId, dayRange, goal),
    queryFn: () => fetchProgression(mappingId, dayRange, goal),
    enabled: mappingId != null,
  });

  const trendQuery = useQuery({
    queryKey: dashboardKeys.progressionTrend(mappingId, dayRange, goal),
    queryFn: () => fetchProgressionTrend(mappingId, dayRange, goal),
    enabled: mappingId != null,
  });

  const suggestionQuery = useQuery({
    // M97 F4: "Todos" = o objetivo default (a sugestão de sempre); um
    // objetivo = a trilha/escada dele, decidida pelo motor.
    queryKey: dashboardKeys.progressionSuggestion(mappingId, goal),
    queryFn: () => fetchProgressionSuggestion(mappingId, goal),
    enabled: mappingId != null,
    retry: false,
  });

  const items = progressionQuery.data?.items ?? [];

  // Um único eixo Y: e1RM e carga de topo estão ambos em kg, então dividem a
  // mesma escala. Nunca usar dual-axis (regra das Etapas 26a/26b). Só as
  // sessões da chave do veredito entram (a mesma do "Comparado em"); e1RM de
  // série com mais de 12 reps fica fora da linha (não é confiável).
  const chart = useMemo(
    () => buildProgressionChart(items, (value) => isHighlightedGoal(value, defaultGoal)),
    [items, defaultGoal],
  );
  const chartData = chart.points;

  const trend = trendQuery.data;
  const trendReasons = useMemo(() => statusReasonsText(trend?.status_reasons), [trend?.status_reasons]);

  return (
    <Stack gap="lg">
      <div>
        <Title order={2} c={BRAND.textPrimary}>
          Progressão
        </Title>
        <Text size="sm" c={BRAND.textMuted}>
          Evolução por grupo muscular (todos de uma vez) e, abaixo, detalhe por exercício com veredito de tendência.
        </Text>
      </div>

      <MuscleGroupProgressionPanel range={dayRange} />

      <StrengthPanel window={windowQuery} filters={filters} />

      <Title order={3} c={BRAND.textPrimary}>
        Detalhe por exercício
      </Title>

      <Group gap="sm" align="flex-end" wrap="wrap">
        <Select
          label="Grupo muscular"
          placeholder="Todos"
          data={muscleGroups}
          value={selectedGroup}
          searchable
          clearable
          w={220}
          onChange={(value) => {
            setSelectedGroup(value);
            // Trocar de grupo pode tirar o exercício selecionado da lista —
            // limpa a seleção para não deixar filtro e dado inconsistentes.
            if (value && mappingId != null) {
              const stillValid = allOptions.some(
                (option) => option.id === mappingId && option.primary_group === value,
              );
              if (!stillValid) setFilters({ exerciseMappingId: null, exerciseName: null });
            }
          }}
        />
        <Select
          label="Exercício"
          placeholder={optionsQuery.isLoading ? 'Carregando...' : 'Selecione um exercício'}
          data={filteredOptions.map((option) => ({
            value: String(option.id),
            label: option.exercise_name || option.amazfit_name || `Exercício ${option.id}`,
          }))}
          value={mappingId != null ? String(mappingId) : null}
          searchable
          clearable
          w={340}
          nothingFoundMessage="Nenhum exercício encontrado"
          onChange={(value) => {
            const option = filteredOptions.find((item) => String(item.id) === value);
            setFilters({
              exerciseMappingId: value ? Number(value) : null,
              exerciseName: option?.exercise_name ?? null,
            });
          }}
        />
        <DashboardPeriodSelect filters={filters} onChange={setFilters} rangeError={rangeError} />
        {goals && (
          <Stack gap={4}>
            <Text size="sm" fw={500}>
              Objetivo
            </Text>
            <SegmentedControl
              data={sessionGoalFilterOptions(goals)}
              value={goalQueryParam(goals, goalFilter) ?? ALL_GOALS}
              onChange={setGoalFilter}
            />
          </Stack>
        )}
        {/* A tela nova (F1.3) responde a mesma pergunta COM comparabilidade:
            equipamento e convenção de carga da chave atual. O link leva o
            filtro global junto para a janela não mudar na navegação. */}
        {mappingId != null && (
          <Button
            component={Link}
            href={exerciseProgressHref(mappingId, filters)}
            variant="light"
            color="brand"
            rightSection={<IconArrowRight size={14} />}
          >
            Ver progresso com comparabilidade
          </Button>
        )}
      </Group>

      {mappingId == null && (
        <Alert color="blue" variant="light" icon={<IconInfoCircle size={18} />}>
          Selecione um exercício para ver a evolução de carga, repetições, RPE e 1RM estimado.
        </Alert>
      )}

      {mappingId != null && (
        <>
          {(progressionQuery.isLoading || trendQuery.isLoading) && <DashboardCardSkeleton rows={5} />}
          {(progressionQuery.isError || trendQuery.isError) && (
            <ErrorState
              title="Não foi possível carregar a progressão deste exercício."
              error={progressionQuery.error ?? trendQuery.error}
            />
          )}

          {trend && (
            <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
              <Group justify="space-between" mb="xs">
                <Group gap="xs">
                  <IconTargetArrow size={18} color={BRAND.orange} />
                  <Text fw={700} c={BRAND.textPrimary}>
                    Tendência
                  </Text>
                </Group>
                <StatusBadge domain="exerciseProgression" code={trend.status} size="lg" />
              </Group>
              {trendReasons.map((reason) => (
                <Text key={reason.code} size="sm" c={BRAND.textPrimary}>
                  • {reason.text}
                </Text>
              ))}
              {/* O veredito compara só o run final de MESMA chave (equipamento
                  + convenção de carga), como o drill-down: dizer onde. */}
              {trend.exposures_in_window > 0 && (
                <Text size="xs" c={BRAND.textMuted} mt={4}>
                  Comparado em: {trend.equipment_name || 'equipamento não identificado'} ·{' '}
                  {loadConventionLabel(trend.load_convention)}
                  {trend.load_convention_source === 'default_total' ? ' (assumida)' : ''} ·{' '}
                  {fatigueContextLabel(trend.fatigue_context)
                    ? `condição ${fatigueContextLabel(trend.fatigue_context)} · `
                    : ''}
                  {fmtInt(trend.comparable_exposures)} de {fmtInt(trend.exposures_in_window)} exposição(ões) no
                  período
                </Text>
              )}
              {suggestionQuery.data && (
                <Text size="sm" mt="sm" c={BRAND.textPrimary}>
                  <b>Sugestão para a próxima sessão:</b> carga {suggestionQuery.data.load_target || '-'} · reps{' '}
                  {suggestionQuery.data.reps_target} — {suggestionQuery.data.progression_note}{' '}
                  <TrainingGoalBadge value={suggestionQuery.data.training_goal} onlyNonDefault size="xs" />
                </Text>
              )}
            </Paper>
          )}

          {chartData.length > 0 && (
            <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
              <Text fw={700} c={BRAND.textPrimary}>
                1RM estimado e carga de topo (kg)
              </Text>
              <Text size="xs" c={BRAND.textMuted} mb="xs">
                Só as sessões comparáveis com o veredito (mesmo equipamento e convenção de carga).
                {chart.otherKeySessions > 0 &&
                  ` ${fmtInt(chart.otherKeySessions)} sessão(ões) em outro equipamento/convenção ficaram fora do gráfico (seguem na tabela).`}
                {chart.unreliableE1rm > 0 &&
                  ` ${fmtInt(chart.unreliableE1rm)} 1RM estimado(s) de séries com mais de 12 repetições omitido(s) — pouco confiável(is).`}
                {(chart.strengthPoints ?? 0) > 0 && ' Pontos destacados: sessões de força (o 1RM estimado é um só).'}
                {(chart.deloadSessions ?? 0) > 0 &&
                  ` ${fmtInt(chart.deloadSessions ?? 0)} sessão(ões) de deload marcada(s), fora da linha do 1RM estimado.`}
              </Text>
              <div style={{ height: 300 }}>
                <ProgressaoChart chartData={chartData} />
              </div>
              <Text size="xs" style={CHART_LEGEND_STYLE} mt={4}>
                Ambas as séries estão em kg e compartilham o mesmo eixo.
              </Text>
            </Paper>
          )}

          {progressionQuery.isSuccess && items.length === 0 && (
            <EmptyState description="Nenhuma sessão registrada para este exercício no período selecionado." />
          )}

          {items.length > 0 && (
            <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
              <Table.ScrollContainer minWidth={720}>
                <Table striped highlightOnHover withTableBorder verticalSpacing="xs">
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Data</Table.Th>
                      <Table.Th>Carga (topo)</Table.Th>
                      <Table.Th>Reps (séries de topo)</Table.Th>
                      <Table.Th>RPE</Table.Th>
                      <Table.Th>Intensidade</Table.Th>
                      <Table.Th>Séries</Table.Th>
                      <Table.Th>1RM estimado</Table.Th>
                      <Table.Th>Evolução</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {items.map((item) => (
                      <Table.Tr key={item.activity_id}>
                        <Table.Td>
                          {fmtDate(item.local_date)}
                          {fatigueContextLabel(item.fatigue_context) && (
                            <Text size="xs" c={BRAND.textMuted}>
                              {fatigueContextLabel(item.fatigue_context)}
                            </Text>
                          )}
                          {item.in_verdict_key === false && (
                            <Text size="xs" c={BRAND.textMuted}>
                              outro equipamento, convenção ou condição de fadiga
                            </Text>
                          )}
                          {(isHighlightedGoal(item.training_goal, defaultGoal) || item.deload_label) && (
                            <Group gap={4} mt={2}>
                              <TrainingGoalBadge value={item.training_goal} onlyNonDefault size="xs" />
                              {item.deload_label && (
                                <Badge size="xs" variant="outline" color="gray">
                                  {item.deload_label}
                                </Badge>
                              )}
                            </Group>
                          )}
                        </Table.Td>
                        <Table.Td>{fmtKg(sessionLoad(item))}</Table.Td>
                        <Table.Td>{sessionRepsText(item) ?? '-'}</Table.Td>
                        <Table.Td>{fmtRpe(item.avg_rpe)}</Table.Td>
                        {/* §5.7: o status é do servidor (alvo efetivo gravado na
                            série + evento de segurança), nunca recalculado aqui. */}
                        <Table.Td>
                          {item.intensity_status ? (
                            <Badge
                              size="sm"
                              variant="light"
                              color={INTENSITY_STATUS_COLORS[item.intensity_status] || 'gray'}
                            >
                              {INTENSITY_STATUS_LABELS[item.intensity_status] || item.intensity_status}
                              {item.rpe_target_effective != null ? ` · alvo ${item.rpe_target_effective}` : ''}
                            </Badge>
                          ) : (
                            <Text size="xs" c={BRAND.textMuted}>
                              -
                            </Text>
                          )}
                        </Table.Td>
                        <Table.Td>{fmtInt(item.sets_count)}</Table.Td>
                        <Table.Td>
                          {fmtKg(item.e1rm)}
                          {item.e1rm != null && !isSessionE1rmReliable(item) && (
                            <Text size="xs" c={BRAND.textMuted}>
                              pouco confiável (&gt;12 reps)
                            </Text>
                          )}
                        </Table.Td>
                        <Table.Td>
                          <TrendBadge evolution={item.evolution} />
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            </Paper>
          )}
        </>
      )}
    </Stack>
  );
}
