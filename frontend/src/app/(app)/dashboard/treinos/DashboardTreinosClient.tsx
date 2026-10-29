'use client';

import {
  Accordion,
  Badge,
  Button,
  Card,
  Divider,
  Grid,
  Group,
  Paper,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { useMemo, useState } from 'react';
import { DashboardPeriodSelect } from '@/components/dashboard/DashboardPeriodSelect';
import { MetricValue } from '@/components/dashboard/MetricValue';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import {
  DashboardCardSkeleton,
  EmptyState,
  ErrorState,
  PartialState,
} from '@/components/dashboard/states';
import {
  dashboardKeys,
  fetchAdherence,
  fetchWorkoutDetail,
  fetchWorkoutsV3,
} from '@/lib/dashboard-api';
import {
  dashboardWindowDays,
  useDashboardFilters,
  type DashboardWindow,
} from '@/lib/dashboard-filters';
import { EM_DASH, fmtDate, fmtDuration, fmtInt } from '@/lib/dashboard-format';
import {
  groupSessionsBySlot,
  itemStatusEntries,
  ratePercent,
  totalItems,
} from '@/lib/dashboard-workouts';
import { textoTecnicaAplicada } from '@/lib/dashboard-technique-indicator';
import type { PlanItem, WorkoutSession } from '@/lib/dashboard-workouts-types';
import { exerciseProgressHref } from '@/lib/exercise-progress';
import { BRAND } from '@/theme';
import { AnaliseIaBloco } from './AnaliseIaBloco';
import { DistribuicaoRpeBloco } from './DistribuicaoRpeBloco';
import { EvolucaoSemanalBloco } from './EvolucaoSemanalBloco';
import { ResumoSemanalBloco } from './ResumoSemanalBloco';

/**
 * `/dashboard/treinos` (F1.1) — "cumpri o planejado?".
 *
 * Três blocos, todos vindos de `response_model`:
 *
 * 1. **Consistência** (`GET /analytics/dashboard/adherence`): ciclo atual,
 *    sessões esperadas/realizadas/não executadas e a próxima do ciclo.
 * 2. **Sessões** (`GET /analytics/dashboard/workouts`, paginado): cada sessão
 *    com o status dela e a contagem de itens por status DERIVADO (§5).
 * 3. **Detalhe** (`GET /analytics/dashboard/workouts/{id}`): prescrito x
 *    executado item a item, com o motivo da substituição e o quanto faltou
 *    quando interrompido.
 *
 * ## O que mudou em relação à tela anterior
 *
 * A v2 agrupava sessões por `activities.name` — "Treino A" e "treino a " eram
 * grupos diferentes e renomear o treino partia a série histórica. Agora existe
 * vínculo real (`activities.workout_plan_id` → `template_id` +
 * `template_slot_sequence`) e o agrupamento é por SLOT do Modelo, com o nome
 * apenas como rótulo. A chave chega pronta em `session.group` (dono
 * `slot_session_comparison.session_group_of`); `lib/dashboard-workouts.ts` só
 * junta e ordena.
 *
 * As rotas v2 (`dashboard-workout-sessions`, `dashboard-cycle`) continuam
 * existindo; esta tela deixou de consumi-las. Não foram marcadas como
 * legadas na F2.4 — `dashboard-cycle` ainda serve `/dashboard/ciclo`.
 *
 * Nenhum cálculo aqui: séries efetivas, status, recordes, aderência e
 * limitações chegam prontos do backend.
 *
 * ## Blocos herdados de `/treinos`
 *
 * A tela da raiz do menu `/treinos` está sendo absorvida aqui (a remoção dela
 * vem em PR separado). Vieram, cada um em seu arquivo nesta pasta:
 *
 * - **Resumo da semana** (`training-evolution.weekly_summary`) — sempre a
 *   semana corrente, não segue o seletor;
 * - **Evolução por semana ISO** (`training-trend?period=week`) e
 *   **Distribuição de RPE** (`rpe-distribution`) — rotas v2 com `days`,
 *   derivado do seletor por `dashboardWindowDays()` (`cycle` vira 28 dias,
 *   como em toda rota v2);
 * - **Análise com personal trainer IA** (job assíncrono + polling), com as
 *   tabelas do snapshot que a IA recebeu.
 *
 * Ficaram de fora por já existirem em outra aba: "Cardio & Recuperação"
 * (FC das sessões em `/dashboard/cardio`; sono/HRV em dia com treino × sem
 * treino em `/dashboard/recovery`) e "Recuperação muscular" (prontidão por
 * grupo em `/dashboard/recovery`, mesmo `MuscleRecoveryService`).
 */

/** Janelas desta tela. `7d` fica de fora: uma semana não compara ciclos. */
const TREINOS_WINDOWS = ['cycle', '28d', '90d', '180d', 'custom'] as const satisfies
  readonly DashboardWindow[];

const PAGE_SIZE = 20;

const CARD_STYLE = { background: BRAND.bgElevated, borderColor: BRAND.border };

// ---------------------------------------------------------------------------
// Blocos
// ---------------------------------------------------------------------------

function ConsistenciaBloco() {
  const { windowQuery } = useDashboardFilters();
  const consulta = useQuery({
    queryKey: dashboardKeys.adherence(windowQuery),
    queryFn: () => fetchAdherence(windowQuery),
  });

  if (consulta.isLoading) return <DashboardCardSkeleton rows={3} />;
  if (consulta.isError) {
    return <ErrorState title="Não foi possível carregar a consistência." error={consulta.error} />;
  }
  const dados = consulta.data;
  if (!dados) return null;

  const percentual = ratePercent(dados.rate.value);

  return (
    <Paper withBorder radius="lg" p="md" style={CARD_STYLE}>
      <Group justify="space-between" align="flex-start" wrap="wrap" mb="sm">
        <div>
          <Title order={4} c={BRAND.textPrimary}>
            Consistência
          </Title>
          <Text size="sm" c={BRAND.textMuted}>
            {dados.has_template
              ? `Modelo "${dados.template_name ?? 'sem nome'}" · ciclo de ${fmtInt(dados.cycle_length_days)} dias`
              : 'Sem Modelo de Ficha atribuído.'}
          </Text>
        </div>
        {percentual != null && (
          <Badge size="lg" color={percentual >= 80 ? 'green' : percentual >= 50 ? 'orange' : 'red'}>
            {fmtInt(percentual)}% do planejado
          </Badge>
        )}
      </Group>

      <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
        <Metrica titulo="Sessões esperadas" metric={dados.expected_sessions} />
        <Metrica titulo="Sessões realizadas" metric={dados.completed_sessions} />
        <Metrica titulo="Com ficha" metric={dados.planned_sessions} />
        <Metrica titulo="Não executadas" metric={dados.missed_sessions} />
      </SimpleGrid>

      {dados.slots.length > 0 && (
        <>
          <Divider my="sm" />
          <Text size="xs" c={BRAND.textMuted} mb={6}>
            Posição no ciclo
          </Text>
          <Group gap="xs" wrap="wrap">
            {dados.slots.map((slot) => (
              <StatusBadge
                key={slot.sequence}
                domain="cycleSlot"
                code={slot.status}
                suffix={` · ${slot.name ?? `Slot ${slot.sequence}`}`}
                tooltip={
                  slot.last_local_date
                    ? `Última sessão deste slot no período: ${fmtDate(slot.last_local_date)}`
                    : 'Sem sessão deste slot no período.'
                }
              />
            ))}
          </Group>
          {/* Decisão de produto (2026-10-02): a aderência NÃO adivinha descanso
              pelo conteúdo do slot. Slot "Normal" sem exercícios conta como
              treino esperado; o editor do Modelo avisa e oferece a troca. */}
          <Text size="xs" c={BRAND.textMuted} mt={6}>
            Slots do tipo &quot;Normal&quot; contam como treino esperado, mesmo sem exercícios. Dia de descanso
            precisa estar marcado como &quot;Descanso&quot;{' '}
            {dados.template_id != null ? (
              <Text component={Link} href={`/modelos-treino/${dados.template_id}`} size="xs" c={BRAND.orange} span>
                no Modelo
              </Text>
            ) : (
              'no Modelo'
            )}
            .
          </Text>
        </>
      )}

      <PartialState
        quality={dados.data_quality}
        limitations={dados.limitations}
        title="Aderência parcial"
        fixHref={dados.has_template ? '/dashboard/data-quality' : '/workout-plan-templates'}
        fixLabel={dados.has_template ? 'Ver qualidade dos dados' : 'Ver Modelos de Ficha'}
      />
    </Paper>
  );
}

function Metrica({ titulo, metric }: { titulo: string; metric: Parameters<typeof MetricValue>[0]['metric'] }) {
  return (
    <div>
      <Text size="xs" c={BRAND.textMuted}>
        {titulo}
      </Text>
      <MetricValue metric={metric} digits={0} size="md" fw={700} showNature={false} />
    </div>
  );
}

function ContagemDeItens({ session }: { session: WorkoutSession }) {
  const entradas = itemStatusEntries(session.item_status_counts);
  if (entradas.length === 0) {
    return (
      <Text size="xs" c={BRAND.textMuted}>
        Sem itens classificados
      </Text>
    );
  }
  return (
    <Group gap={6} wrap="wrap">
      {entradas.map((entrada) => (
        <StatusBadge
          key={entrada.code}
          domain="planItemStatus"
          code={entrada.code}
          size="xs"
          suffix={` ${entrada.count}`}
        />
      ))}
    </Group>
  );
}

function SessaoCard({
  session,
  aberta,
  onToggle,
}: {
  session: WorkoutSession;
  aberta: boolean;
  onToggle: () => void;
}) {
  return (
    <Card withBorder radius="md" p="sm" style={CARD_STYLE}>
      <Group justify="space-between" align="flex-start" wrap="wrap" gap="xs">
        <div>
          <Group gap="xs">
            <Text fw={700} c={BRAND.textPrimary}>
              {fmtDate(session.local_date)}
            </Text>
            <StatusBadge domain="sessionStatus" code={session.session_status} size="xs" />
            {/* M97 F4: sessão de deload sinalizada (rótulo pronto do backend). */}
            {session.deload_label && (
              <Badge size="xs" variant="outline" color="gray">
                {session.deload_label}
              </Badge>
            )}
          </Group>
          <Text size="sm" c={BRAND.textMuted}>
            {session.name || 'Sem nome'}
            {session.plan_link?.template_slot_name
              ? ` · ${session.plan_link.template_slot_name}`
              : ''}
          </Text>
        </div>
        <Button size="compact-sm" variant="light" onClick={onToggle}>
          {aberta ? 'Fechar detalhe' : 'Ver detalhe'}
        </Button>
      </Group>

      <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="xs" mt="sm">
        <Metrica titulo="Séries" metric={session.physical_sets} />
        <Metrica titulo="Estímulo" metric={session.effective_sets_stimulus} />
        <Metrica titulo="Fadiga" metric={session.effective_sets_fatigue} />
        <Metrica titulo="RPE médio" metric={session.avg_rpe} />
        <Metrica titulo="Recordes" metric={session.records} />
        <Metrica titulo="Progressões" metric={session.progressions} />
        <div>
          <Text size="xs" c={BRAND.textMuted}>
            Duração
          </Text>
          <Text size="md" fw={700}>
            {fmtDuration(session.duration_seconds)}
          </Text>
        </div>
        <div>
          <Text size="xs" c={BRAND.textMuted}>
            Exercícios
          </Text>
          <Text size="md" fw={700}>
            {fmtInt(session.exercises_count)}
          </Text>
        </div>
      </SimpleGrid>

      <Group mt="sm" gap="xs" wrap="wrap">
        <ContagemDeItens session={session} />
        <Text size="xs" c={BRAND.textMuted}>
          {fmtInt(totalItems(session.item_status_counts))} item(ns)
        </Text>
      </Group>

      {aberta && <DetalheDaSessao activityId={session.activity_id} />}
    </Card>
  );
}

function ItemLinha({ item }: { item: PlanItem }) {
  const { filters } = useDashboardFilters();
  const executado = item.performed;
  const exerciseId = executado?.exercise_id ?? item.substitution?.planned_exercise_id ?? null;
  const nome = item.planned_name ?? executado?.name ?? 'Exercício';

  return (
    <Table.Tr>
      <Table.Td>
        <Stack gap={2}>
          <Group gap={6} wrap="wrap">
            <StatusBadge domain="planItemStatus" code={item.status} size="xs" />
            {exerciseId != null ? (
              <Text
                component={Link}
                href={exerciseProgressHref(exerciseId, filters)}
                size="sm"
                fw={600}
                c={BRAND.orange}
              >
                {nome}
              </Text>
            ) : (
              <Text size="sm" fw={600}>
                {nome}
              </Text>
            )}
            {item.is_record && (
              <Tooltip
                multiline
                w={260}
                withArrow
                label="Melhor carga já registrada neste exercício, no mesmo equipamento e convenção de carga (assistido: menos assistência). Trocar de máquina não conta."
              >
                <Badge size="xs" color="teal" variant="light">
                  Recorde de carga
                </Badge>
              </Tooltip>
            )}
          </Group>

          {item.status === 'SUBSTITUTED' && item.substitution && (
            <Text size="xs" c={BRAND.textMuted}>
              Prescrito: {item.substitution.planned_name ?? EM_DASH} → executado:{' '}
              {item.substitution.performed_name ?? EM_DASH}
            </Text>
          )}
          {item.status === 'INTERRUPTED' && item.missing_sets != null && (
            <Text size="xs" c={BRAND.textMuted}>
              Faltaram {fmtInt(item.missing_sets)} série(s) para o prescrito.
            </Text>
          )}
          {item.technique && (
            <Text size="xs" c={BRAND.textMuted}>
              {textoTecnicaAplicada(item.technique)}
            </Text>
          )}
          {item.limitations.map((limitacao) => (
            <Text key={limitacao} size="xs" c={BRAND.textMuted}>
              {limitacao}
            </Text>
          ))}
        </Stack>
      </Table.Td>
      <Table.Td>
        <Text size="sm">
          {item.prescribed.sets_count != null
            ? `${fmtInt(item.prescribed.sets_count)}× ${item.prescribed.reps_target ?? EM_DASH}`
            : EM_DASH}
        </Text>
        <Text size="xs" c={BRAND.textMuted}>
          {item.prescribed.load_target ? `${item.prescribed.load_target} · ` : ''}
          {item.prescribed.rpe_target ? `RPE ${item.prescribed.rpe_target}` : ''}
        </Text>
      </Table.Td>
      <Table.Td>
        <Text size="sm">
          {executado ? `${fmtInt(executado.working_sets)} série(s) de trabalho` : EM_DASH}
        </Text>
        {executado && (
          <Text size="xs" c={BRAND.textMuted}>
            {executado.sets
              .filter((serie) => serie.is_working)
              .map((serie) => `${serie.reps ?? EM_DASH}×${serie.weight_kg ?? EM_DASH}kg`)
              .join(' · ') || 'Sem série de trabalho'}
          </Text>
        )}
      </Table.Td>
    </Table.Tr>
  );
}

function DetalheDaSessao({ activityId }: { activityId: number }) {
  const consulta = useQuery({
    queryKey: dashboardKeys.workoutDetail(activityId),
    queryFn: () => fetchWorkoutDetail(activityId),
  });

  if (consulta.isLoading) return <DashboardCardSkeleton rows={4} />;
  if (consulta.isError) {
    return <ErrorState title="Não foi possível carregar a sessão." error={consulta.error} />;
  }
  const dados = consulta.data;
  if (!dados) return null;

  return (
    <Stack gap="sm" mt="md">
      <Divider label="Prescrito × executado" labelPosition="left" />

      {dados.items.length === 0 ? (
        <EmptyState description="Nenhum exercício registrado nesta sessão." />
      ) : (
        <Table.ScrollContainer minWidth={560}>
          <Table striped withTableBorder verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Exercício</Table.Th>
                <Table.Th>Prescrito</Table.Th>
                <Table.Th>Executado</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {dados.items.map((item, index) => (
                <ItemLinha key={`${item.plan_exercise_id ?? 'extra'}-${index}`} item={item} />
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}

      {dados.notes && (
        <Text size="sm" c={BRAND.textMuted}>
          Observações: {dados.notes}
        </Text>
      )}

      {dados.sets_truncated && (
        <Text size="xs" c={BRAND.textMuted}>
          Há séries fora desta lista: a resposta é limitada a {fmtInt(dados.sets_limit)} séries.
        </Text>
      )}

      <PartialState quality={dados.data_quality} limitations={dados.limitations} />
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// Tela
// ---------------------------------------------------------------------------

export function DashboardTreinosClient() {
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();
  const [offset, setOffset] = useState(0);
  const [sessaoAberta, setSessaoAberta] = useState<number | null>(null);

  const consulta = useQuery({
    queryKey: dashboardKeys.workouts(windowQuery, PAGE_SIZE, offset),
    queryFn: () => fetchWorkoutsV3(windowQuery, PAGE_SIZE, offset),
  });

  const days = dashboardWindowDays(filters);
  const sessoes = useMemo(() => consulta.data?.items ?? [], [consulta.data]);
  const grupos = useMemo(() => groupSessionsBySlot(sessoes), [sessoes]);

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <div>
          <Title order={2} c={BRAND.textPrimary}>
            Treinos
          </Title>
          <Text size="sm" c={BRAND.textMuted}>
            O que estava planejado, o que foi executado e o que ficou pelo caminho.
          </Text>
        </div>
        <DashboardPeriodSelect
          filters={filters}
          onChange={(patch) => {
            setOffset(0);
            setFilters(patch);
          }}
          allowed={TREINOS_WINDOWS}
          rangeError={rangeError}
        />
      </Group>

      <ConsistenciaBloco />

      <ResumoSemanalBloco />

      <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="lg">
        <EvolucaoSemanalBloco days={days} />
        <DistribuicaoRpeBloco days={days} />
      </SimpleGrid>

      <AnaliseIaBloco days={days} />

      <Title order={3} c={BRAND.textPrimary}>
        Sessões
      </Title>

      {consulta.isLoading && <DashboardCardSkeleton rows={6} />}
      {consulta.isError && (
        <ErrorState title="Não foi possível carregar as sessões." error={consulta.error} />
      )}

      {consulta.isSuccess && sessoes.length === 0 && (
        <EmptyState description="Nenhum treino registrado no período selecionado." />
      )}

      {sessoes.length > 0 && (
        <>
          <PartialState
            quality={consulta.data?.data_quality}
            limitations={consulta.data?.limitations ?? []}
          />

          <Accordion multiple defaultValue={grupos.map((grupo) => grupo.key)} variant="separated">
            {grupos.map((grupo) => (
              <Accordion.Item key={grupo.key} value={grupo.key}>
                <Accordion.Control>
                  <Group gap="xs" wrap="wrap">
                    <Text fw={700} c={BRAND.textPrimary}>
                      {grupo.label}
                    </Text>
                    <Text size="xs" c={BRAND.textMuted}>
                      {grupo.kind === 'slot'
                        ? `${grupo.templateName ?? 'Modelo sem nome'} · slot ${grupo.slotSequence} · `
                        : ''}
                      {grupo.sessions.length} sessão(ões)
                      {grupo.kind === 'unlinked' ? ' · sem vínculo com ficha' : ''}
                      {grupo.kind === 'no_template' ? ' · ficha sem vínculo com Modelo' : ''}
                    </Text>
                  </Group>
                </Accordion.Control>
                <Accordion.Panel>
                  <Stack gap="sm">
                    {grupo.sessions.map((sessao) => (
                      <SessaoCard
                        key={sessao.activity_id}
                        session={sessao}
                        aberta={sessaoAberta === sessao.activity_id}
                        onToggle={() =>
                          setSessaoAberta((atual) =>
                            atual === sessao.activity_id ? null : sessao.activity_id,
                          )
                        }
                      />
                    ))}
                  </Stack>
                </Accordion.Panel>
              </Accordion.Item>
            ))}
          </Accordion>

          <Group justify="space-between" wrap="wrap">
            <Text size="xs" c={BRAND.textMuted}>
              {fmtInt(offset + sessoes.length)} de {fmtInt(consulta.data?.total)} sessões
            </Text>
            <Grid gutter="xs">
              <Grid.Col span="content">
                <Button
                  size="compact-sm"
                  variant="default"
                  disabled={offset === 0}
                  onClick={() => setOffset((atual) => Math.max(0, atual - PAGE_SIZE))}
                >
                  Anteriores
                </Button>
              </Grid.Col>
              <Grid.Col span="content">
                <Button
                  size="compact-sm"
                  variant="default"
                  disabled={!consulta.data?.has_more}
                  onClick={() => setOffset((atual) => atual + PAGE_SIZE)}
                >
                  Mais antigas
                </Button>
              </Grid.Col>
            </Grid>
          </Group>
        </>
      )}
    </Stack>
  );
}
