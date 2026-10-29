'use client';

import { Group, Paper, Stack, Table, Text, Tooltip } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { DashboardSkeleton, EmptyState, ErrorState } from '@/components/dashboard/states';
import { dashboardKeys, fetchProgressionByMuscleGroup } from '@/lib/dashboard-api';
import type { DashboardDayRange } from '@/lib/dashboard-filters';
import { fmtDate, fmtInt, fmtKgTotal, fmtSignedPercent } from '@/lib/dashboard-format';
import { performanceTooltip } from '@/lib/dashboard-muscle-performance';
import { MUSCLE_GROUP_PROGRESSION_ORDER, statusVisual } from '@/lib/dashboard-status';
import type { MuscleGroupProgressionItem } from '@/lib/dashboard-v2-types';
import { BRAND } from '@/theme';

/**
 * Progressão de TODOS os grupos musculares de uma vez (M7). Dado 100% do
 * servidor (`GET /analytics/dashboard-progression-by-muscle-group`): tonelagem
 * (reps × carga) das séries de trabalho e das aplicações de técnica (sem
 * aquecimento/ajuste), ponderada por `stimulus_weight`, do
 * período selecionado contra o período imediatamente anterior de mesmo
 * tamanho. A fase da trilha é só informação secundária.
 *
 * M50: a janela atual termina HOJE e corta o ciclo do Modelo no meio, então
 * comparar TOTAIS acusava "regredindo" só por haver menos sessões até agora.
 * A variação exibida é a da tonelagem POR SESSÃO que treinou o grupo; o
 * servidor só classifica como progredindo/regredindo quando a variação por
 * sessão e a do total concordam, e devolve `DADOS_INSUFICIENTES` com menos de
 * `min_sessions_per_window` sessões numa das janelas.
 *
 * Volume x Desempenho (2026-10-02): reduzir séries de propósito derruba a
 * tonelagem enquanto a força sobe, e a tela dizia "regredindo" em 10 grupos
 * do doug com o e1RM subindo em quase todos. Agora são DUAS colunas:
 * "Volume" (tonelagem, `volume_status`, rótulos neutros — domínio
 * `muscleGroupVolume`) e "Desempenho" (melhor e1RM do mesmo exercício nas duas
 * janelas, `performance_status` — domínio `muscleGroupProgression`). O resumo
 * do topo conta DESEMPENHO, nunca volume. `classification` (deprecado) não é
 * mais lido aqui.
 *
 * M61: na janela personalizada a tela manda as duas datas e o período atual é
 * exatamente o escolhido (o anterior é o de mesmo tamanho logo antes dele).
 *
 * A classificação é apresentada pelo `StatusBadge`, do registro único — este
 * arquivo não tem mais tabela de rótulo/cor própria.
 */
export function MuscleGroupProgressionPanel({ range }: { range: DashboardDayRange }) {
  const query = useQuery({
    queryKey: dashboardKeys.progressionByMuscleGroup(range),
    queryFn: () => fetchProgressionByMuscleGroup(range),
  });
  const { days } = range;
  const isCustom = range.date_from != null;

  const items = query.data?.items ?? [];
  const data = query.data;

  return (
    <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Text fw={700} c={BRAND.textPrimary}>
        Progressão por grupo muscular
      </Text>
      <Text size="xs" c={BRAND.textMuted}>
        {isCustom ? `Período escolhido (${days} dias)` : `Últimos ${days} dias`} contra os {days} dias anteriores
        {data
          ? ` (${fmtDate(data.current_from)}–${fmtDate(data.current_to)} × ${fmtDate(data.previous_from)}–${fmtDate(
              data.previous_to,
            )})`
          : ''}
        . Duas leituras separadas:
      </Text>
      <Text size="xs" c={BRAND.textMuted}>
        <b>Volume</b> — tonelagem (carga × reps das séries de trabalho e das técnicas, sem aquecimento/ajuste,
        ponderada pela ativação muscular), comparada pela média por sessão. Menos volume pode ser planejado e não
        quer dizer regressão.
        {data ? ` Com menos de ${data.min_sessions_per_window} sessões numa das janelas, não há leitura.` : ''}
      </Text>
      <Text size="xs" c={BRAND.textMuted} mb="sm">
        <b>Desempenho</b> — melhor e1RM (Epley) de cada exercício feito nas duas janelas, no mesmo equipamento, média
        ponderada pela ativação do grupo. Variação acima de ±50% num exercício é tratada como atípica (conferir
        registro) e fica fora da média; com ativação somada abaixo de 50%, ou sem exercício em comum, não há leitura.
      </Text>

      {items.length > 0 && <PerformanceSummary items={items} />}

      {query.isLoading && <DashboardSkeleton rows={5} withHeader={false} />}
      {query.isError && (
        <ErrorState title="Não foi possível carregar a progressão por grupo muscular." error={query.error} />
      )}
      {query.isSuccess && items.length === 0 && (
        <EmptyState
          description={
            isCustom
              ? 'Nenhuma série de trabalho registrada no período escolhido nem no anterior.'
              : `Nenhuma série de trabalho registrada nos últimos ${days * 2} dias.`
          }
        />
      )}

      {items.length > 0 && (
        <Table.ScrollContainer minWidth={760}>
          <Table striped highlightOnHover withTableBorder verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Grupo</Table.Th>
                <Table.Th>Período atual</Table.Th>
                <Table.Th>Período anterior</Table.Th>
                <Table.Th>Volume</Table.Th>
                <Table.Th>Desempenho</Table.Th>
                <Table.Th>Fase</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {items.map((item) => (
                <Table.Tr key={item.muscle_group_id}>
                  <Table.Td>{item.muscle_group_name ?? `Grupo ${item.muscle_group_id}`}</Table.Td>
                  <Table.Td>
                    <PeriodCell
                      tonnage={item.current_tonnage_kg}
                      sets={item.current_sets}
                      sessions={item.current_sessions}
                      perSession={item.current_tonnage_per_session_kg}
                    />
                  </Table.Td>
                  <Table.Td>
                    <PeriodCell
                      tonnage={item.previous_tonnage_kg}
                      sets={item.previous_sets}
                      sessions={item.previous_sessions}
                      perSession={item.previous_tonnage_per_session_kg}
                    />
                  </Table.Td>
                  <Table.Td>
                    <VolumeCell item={item} />
                  </Table.Td>
                  <Table.Td>
                    <PerformanceCell item={item} />
                  </Table.Td>
                  <Table.Td>
                    <Text size="xs" c={BRAND.textMuted}>
                      {item.phase ?? '-'}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </Paper>
  );
}

function PeriodCell({
  tonnage,
  sets,
  sessions,
  perSession,
}: {
  tonnage: number;
  sets: number;
  sessions: number | undefined;
  perSession: number | null | undefined;
}) {
  return (
    <>
      {fmtKgTotal(tonnage)}{' '}
      <Text span size="xs" c={BRAND.textMuted}>
        ({fmtInt(sets)} séries, {fmtInt(sessions ?? 0)} sessões)
      </Text>
      {perSession != null && (
        <Text size="xs" c={BRAND.textMuted}>
          {fmtKgTotal(perSession)} por sessão
        </Text>
      )}
    </>
  );
}

/** Contagem por status de DESEMPENHO (nunca de volume), na ordem canônica. */
function PerformanceSummary({ items }: { items: MuscleGroupProgressionItem[] }) {
  const counts = MUSCLE_GROUP_PROGRESSION_ORDER.map((code) => ({
    code,
    count: items.filter((item) => item.performance_status === code).length,
  })).filter((entry) => entry.count > 0);
  return (
    <Group gap="xs" mb="sm">
      <Text size="xs" fw={600} c={BRAND.textPrimary}>
        Desempenho:
      </Text>
      {counts.map(({ code, count }) => (
        <StatusBadge key={code} domain="muscleGroupProgression" code={code} suffix={` · ${count}`} />
      ))}
    </Group>
  );
}

function VolumeCell({ item }: { item: MuscleGroupProgressionItem }) {
  return (
    <Stack gap={4} align="flex-start">
      {item.change_pct != null && (
        <Tooltip
          withArrow
          multiline
          w={260}
          label={`Média por sessão. Tonelagem total: ${fmtSignedPercent(item.total_change_pct)}. Maior/menor só quando a média por sessão e o total mudam no mesmo sentido.`}
        >
          <Text span size="sm" style={{ cursor: 'help' }}>
            {fmtSignedPercent(item.change_pct)}
          </Text>
        </Tooltip>
      )}
      <StatusBadge domain="muscleGroupVolume" code={item.volume_status} size="xs" />
    </Stack>
  );
}

function PerformanceCell({ item }: { item: MuscleGroupProgressionItem }) {
  const tooltip = performanceTooltip(item);
  const visual = statusVisual('muscleGroupProgression', item.performance_status);
  const badge = <StatusBadge domain="muscleGroupProgression" code={item.performance_status} size="xs" />;
  return (
    <Stack gap={4} align="flex-start">
      {item.performance_change_pct != null && tooltip && (
        <Tooltip withArrow multiline w={340} style={{ whiteSpace: 'pre-line' }} label={tooltip}>
          <Text span size="sm" c={visual.hex} fw={600} style={{ cursor: 'help' }}>
            {fmtSignedPercent(item.performance_change_pct)}
          </Text>
        </Tooltip>
      )}
      {item.performance_change_pct == null && tooltip ? (
        // Sem leitura: o motivo (atípico / ativação baixa) fica no badge.
        <Tooltip withArrow multiline w={340} style={{ whiteSpace: 'pre-line' }} label={tooltip}>
          <span style={{ cursor: 'help', display: 'inline-flex' }}>{badge}</span>
        </Tooltip>
      ) : (
        badge
      )}
      {item.performance_exercises_excluded > 0 && item.performance_change_pct != null && (
        <Text size="xs" c={BRAND.textMuted}>
          {fmtInt(item.performance_exercises_excluded)} atípico(s) fora do cálculo
        </Text>
      )}
    </Stack>
  );
}
