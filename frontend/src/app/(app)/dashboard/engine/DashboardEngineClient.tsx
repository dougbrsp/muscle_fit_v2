'use client';

import {
  Alert,
  Badge,
  Group,
  List,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { DashboardPeriodSelect } from '@/components/dashboard/DashboardPeriodSelect';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { DashboardCardSkeleton, EmptyState, ErrorState, PartialState } from '@/components/dashboard/states';
import { dashboardKeys, fetchEnginePlan, fetchEngineRuns } from '@/lib/dashboard-api';
import {
  AUDIT_SOURCE_META,
  REASON_KIND_META,
  collectEngineLimitations,
  executedSummary,
  generatedSummary,
  plannedSummary,
  reasonText,
  runOptionLabel,
  runTimestampLabel,
} from '@/lib/dashboard-engine';
import type { EngineItem, EnginePlanResponse, EngineRun } from '@/lib/dashboard-engine-types';
import { useDashboardFilters } from '@/lib/dashboard-filters';
import { EM_DASH, fmtDecimal, fmtInt } from '@/lib/dashboard-format';
import { BRAND } from '@/theme';
import { versionLabel } from '@/lib/template-versions';

/**
 * `/dashboard/engine` — "por que a minha ficha saiu assim, e o que eu fiz com
 * ela?" (plano §4 "Engine runs", §17 F3.1).
 *
 * Três colunas por item, e a fronteira entre elas é o ponto da tela:
 *
 * - **planejado** — o que o MODELO pediu. No modo `percent_budget` o Modelo
 *   não nomeia exercício nenhum: ele pede VOLUME por grupo, e a coluna mostra
 *   o orçamento com essa ressalva escrita. Escrever um nome ali atribuiria ao
 *   Modelo uma decisão que foi do motor;
 * - **gerado** — o que o motor colocou na ficha, com o PORQUE ao lado
 *   (seleção, limite de segurança, substituição, caminho alternativo e a
 *   decisão de progressão);
 * - **executado** — o que a sessão registrou, pelo vínculo
 *   `exercises.workout_plan_exercise_id`.
 *
 * Endpoints (F3.1, `api/app/analytics_dashboard.py`):
 * - `GET /analytics/dashboard/engine` — as gerações da janela (o seletor);
 * - `GET /analytics/dashboard/engine/plans/{plan_id}` — as três colunas.
 *
 * A tela v2 (`GET /analytics/dashboard-engine-audit`) respondia só o orçamento
 * por grupo muscular e o pipeline: nem o Modelo, nem a execução. Ela continua
 * existindo com telemetria de legado — medir antes de remover.
 *
 * **Nada é recalculado aqui.** Status de item, limitações, contagens,
 * orçamentos e códigos de motivo chegam prontos do `response_model`; o que o
 * cliente faz é rotular (`lib/dashboard-engine.ts`).
 *
 * O que está selecionado vive na URL (`?planId=`), mesmo padrão das demais
 * sub-rotas — uma auditoria específica é compartilhável.
 */

const STALE_TIME = 60_000;

export function DashboardEngineClient() {
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();
  const planId = filters.planId;

  const runsQuery = useQuery({
    queryKey: dashboardKeys.engineRuns(windowQuery),
    queryFn: () => fetchEngineRuns(windowQuery),
    staleTime: STALE_TIME,
  });

  const planQuery = useQuery({
    queryKey: dashboardKeys.enginePlan(planId),
    queryFn: () => fetchEnginePlan(planId),
    enabled: planId != null,
    staleTime: STALE_TIME,
  });

  const runs = runsQuery.data?.items ?? [];

  /**
   * Só as gerações que VIRARAM ficha entram no seletor: o detalhe é por
   * `plan_id`. As que não viraram continuam visíveis na lista abaixo — apagá-las
   * esconderia justamente o que a migração 054 passou a registrar.
   */
  const options = useMemo(
    () =>
      runs
        .filter((run) => run.workout_plan_id != null)
        .map((run) => ({ value: String(run.workout_plan_id), label: runOptionLabel(run) })),
    [runs],
  );

  const payload = planQuery.data;
  const limitacoes = useMemo(() => (payload ? collectEngineLimitations(payload) : []), [payload]);

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <div>
          <Title order={2} c={BRAND.textPrimary}>
            Motor de geração
          </Title>
          <Text size="sm" c={BRAND.textMuted}>
            Por que a sua ficha saiu assim, e o que você fez com ela: o que o Modelo planejou, o que o
            motor gerou (e por quê) e o que foi executado.
          </Text>
        </div>
        <DashboardPeriodSelect filters={filters} onChange={setFilters} rangeError={rangeError} />
      </Group>

      {runsQuery.isLoading && <DashboardCardSkeleton rows={4} />}
      {runsQuery.isError && (
        <ErrorState title="Não foi possível carregar as gerações do período." error={runsQuery.error} />
      )}

      {runsQuery.data && (
        <>
          <PartialState
            quality={runsQuery.data.data_quality}
            limitations={runsQuery.data.limitations}
            title="Histórico de gerações"
            fixHref={null}
          />

          <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
            <Select
              label="Ficha gerada"
              placeholder={options.length === 0 ? 'Nenhuma ficha gerada no período' : 'Selecione uma ficha'}
              data={options}
              value={planId != null ? String(planId) : null}
              onChange={(value) => setFilters({ planId: value ? Number(value) : null })}
              searchable
              clearable
              w={420}
              nothingFoundMessage="Nenhuma ficha encontrada"
              disabled={options.length === 0}
            />
          </Paper>

          <RunsCard runs={runs} />
        </>
      )}

      {planId == null && runs.length > 0 && (
        <Alert color="gray" variant="light" icon={<IconInfoCircle size={18} />}>
          Selecione uma ficha acima para ver planejado × gerado × executado, item a item.
        </Alert>
      )}

      {planId != null && planQuery.isLoading && <DashboardCardSkeleton rows={6} />}
      {planId != null && planQuery.isError && (
        <ErrorState title="Não foi possível carregar a auditoria desta ficha." error={planQuery.error} />
      )}

      {payload && (
        <>
          <PlanHeaderCard payload={payload} />
          <PartialState quality={payload.data_quality} limitations={limitacoes} fixHref={null} />
          <ItemsCard payload={payload} />
          <BudgetsCard payload={payload} />
          <RejectionsCard payload={payload} />
        </>
      )}
    </Stack>
  );
}

/**
 * O histórico de gerações da janela. Ele existe separado do seletor porque
 * responde outra coisa: quantas vezes o motor rodou, com qual versão, e
 * quantas dessas gerações NÃO viraram ficha.
 */
function RunsCard({ runs }: { runs: EngineRun[] }) {
  if (runs.length === 0) {
    return (
      <EmptyState
        title="Nenhuma geração neste período."
        description="A tabela `generation_runs` passou a registrar cada geração em 2026-09-26; antes disso só existe o que as fichas salvas guardaram no próprio payload."
      />
    );
  }
  return (
    <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Title order={4} mb="sm" c={BRAND.textPrimary}>
        Gerações no período
      </Title>
      <Table.ScrollContainer minWidth={760}>
        <Table striped highlightOnHover withTableBorder verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Quando</Table.Th>
              <Table.Th>Ficha</Table.Th>
              <Table.Th>Motor</Table.Th>
              <Table.Th>Modo</Table.Th>
              <Table.Th>Ajustes</Table.Th>
              <Table.Th>Origem</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {runs.map((run) => {
              const meta = AUDIT_SOURCE_META[run.source];
              return (
                <Table.Tr key={`${run.source}-${run.run_id ?? run.workout_plan_id}`}>
                  <Table.Td>{runTimestampLabel(run)}</Table.Td>
                  <Table.Td>
                    {run.workout_plan_id == null ? (
                      <Text size="sm" c={BRAND.textMuted}>
                        rascunho não salvo
                      </Text>
                    ) : (
                      run.plan_name ?? `Ficha ${run.workout_plan_id}`
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Group gap={6}>
                      <Text size="sm">{run.engine_version ?? EM_DASH}</Text>
                      {run.llm_used === true && (
                        <Badge variant="light" color="grape" radius="sm">
                          IA
                        </Badge>
                      )}
                    </Group>
                  </Table.Td>
                  <Table.Td>{run.volume_mode ?? EM_DASH}</Table.Td>
                  <Table.Td>
                    <Text size="sm" c={BRAND.textMuted}>
                      {fmtInt(run.safety_clamp_count)} limite(s) · {fmtInt(run.substitution_count)} troca(s) ·{' '}
                      {fmtInt(run.fallback_count)} alternativa(s)
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Tooltip label={meta.hint} withArrow multiline w={320}>
                      <Badge variant="light" color={meta.color} radius="sm" style={{ cursor: 'help' }}>
                        {meta.label}
                      </Badge>
                    </Tooltip>
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Paper>
  );
}

function PlanHeaderCard({ payload }: { payload: EnginePlanResponse }) {
  const meta = AUDIT_SOURCE_META[payload.run.source];
  const contagens = payload.item_status_counts;
  return (
    <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Group justify="space-between" mb="xs" wrap="wrap">
        <Title order={4} c={BRAND.textPrimary}>
          {payload.plan_name ?? `Ficha ${payload.plan_id}`}
        </Title>
        <Group gap="xs">
          {payload.run.engine_version && (
            <Badge variant="light" color="gray" radius="sm">
              Motor {payload.run.engine_version}
            </Badge>
          )}
          {payload.volume_mode && (
            <Badge variant="light" color="gray" radius="sm">
              {payload.volume_mode}
            </Badge>
          )}
          <Tooltip label={meta.hint} withArrow multiline w={320}>
            <Badge variant="light" color={meta.color} radius="sm" style={{ cursor: 'help' }}>
              {meta.label}
            </Badge>
          </Tooltip>
        </Group>
      </Group>
      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="xs">
        <Text size="sm" c={BRAND.textMuted}>
          Modelo: {payload.template_name ?? EM_DASH}
          {versionLabel(payload.template_version_number) ? ` ${versionLabel(payload.template_version_number)}` : ''}
          {payload.template_slot_name ? ` · slot ${payload.template_slot_name}` : ''}
        </Text>
        <Text size="sm" c={BRAND.textMuted}>
          Gerada em: {runTimestampLabel(payload.run)}
        </Text>
        <Text size="sm" c={BRAND.textMuted}>
          Sessões registradas: {fmtInt(payload.sessions_count)}
        </Text>
        <Text size="sm" c={BRAND.textMuted}>
          Itens: {contagens.completed} concluído(s) · {contagens.interrupted} interrompido(s) ·{' '}
          {contagens.skipped} pulado(s) · {contagens.substituted} substituído(s) · {contagens.extra} extra(s)
        </Text>
      </SimpleGrid>

      {payload.plan_level_reasons.length > 0 && (
        <List size="sm" mt="sm" c={BRAND.textMuted} withPadding>
          {payload.plan_level_reasons.map((reason, index) => (
            <List.Item key={`${reason.code}-${index}`}>{reasonText(reason)}</List.Item>
          ))}
        </List>
      )}
    </Paper>
  );
}

/** As três colunas, uma linha por item da ficha (mais os extras da sessão). */
function ItemsCard({ payload }: { payload: EnginePlanResponse }) {
  if (payload.items.length === 0) {
    return <EmptyState title="Esta ficha não tem itens." description="Nada a comparar." />;
  }
  return (
    <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Title order={4} mb="sm" c={BRAND.textPrimary}>
        Planejado × gerado × executado
      </Title>
      <Table.ScrollContainer minWidth={980}>
        <Table striped highlightOnHover withTableBorder verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>#</Table.Th>
              <Table.Th>O Modelo planejou</Table.Th>
              <Table.Th>O motor gerou</Table.Th>
              <Table.Th>Por quê</Table.Th>
              <Table.Th>Você executou</Table.Th>
              <Table.Th>Status</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {payload.items.map((item, index) => (
              <ItemRow key={item.plan_exercise_id ?? `extra-${index}`} item={item} />
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Paper>
  );
}

function ItemRow({ item }: { item: EngineItem }) {
  return (
    <Table.Tr>
      <Table.Td>{item.plan_sequence ?? EM_DASH}</Table.Td>
      <Table.Td>
        <Text size="sm">{plannedSummary(item.planned)}</Text>
        {item.planned.source === 'muscle_group_budget' && (
          <Text size="xs" c={BRAND.textMuted}>
            o Modelo pediu volume para o grupo, não este exercício
          </Text>
        )}
      </Table.Td>
      <Table.Td>
        <Text size="sm" fw={600} c={BRAND.textPrimary}>
          {item.generated_name ?? EM_DASH}
        </Text>
        <Text size="xs" c={BRAND.textMuted}>
          {generatedSummary(item)}
        </Text>
      </Table.Td>
      <Table.Td>
        {item.reasons.length === 0 ? (
          <Text size="xs" c={BRAND.textMuted}>
            {EM_DASH}
          </Text>
        ) : (
          <Stack gap={4}>
            {item.reasons.map((reason, index) => (
              <Group key={`${reason.kind}-${reason.code}-${index}`} gap={6} wrap="nowrap" align="flex-start">
                <Badge variant="light" color={REASON_KIND_META[reason.kind]?.color ?? 'gray'} radius="sm">
                  {REASON_KIND_META[reason.kind]?.label ?? reason.kind}
                </Badge>
                <Text size="xs" c={BRAND.textMuted}>
                  {reasonText(reason)}
                </Text>
              </Group>
            ))}
          </Stack>
        )}
      </Table.Td>
      <Table.Td>
        <Text size="sm">{executedSummary(item)}</Text>
        {item.missing_sets != null && item.missing_sets > 0 && (
          <Text size="xs" c={BRAND.textMuted}>
            faltaram {item.missing_sets} série(s) para o prescrito
          </Text>
        )}
        {item.substitution && (
          <Text size="xs" c={BRAND.textMuted}>
            no lugar de {item.substitution.planned_name ?? EM_DASH}
          </Text>
        )}
      </Table.Td>
      <Table.Td>
        <Stack gap={4}>
          <StatusBadge domain="planItemStatus" code={item.status} />
          {item.limitations.length > 0 && (
            <Tooltip label={item.limitations.join(' ')} withArrow multiline w={340}>
              <Text size="xs" c={BRAND.textMuted} style={{ cursor: 'help' }}>
                ressalva
              </Text>
            </Tooltip>
          )}
        </Stack>
      </Table.Td>
    </Table.Tr>
  );
}

/** Orçamento por grupo — só existe no modo `percent_budget`. */
function BudgetsCard({ payload }: { payload: EnginePlanResponse }) {
  if (payload.group_budgets.length === 0) return null;
  return (
    <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Title order={4} mb="sm" c={BRAND.textPrimary}>
        Orçamento por grupo muscular
      </Title>
      <Table.ScrollContainer minWidth={760}>
        <Table striped highlightOnHover withTableBorder verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Grupo</Table.Th>
              <Table.Th>Meta / semana</Table.Th>
              <Table.Th>Faixa do slot</Table.Th>
              <Table.Th>Gerado</Table.Th>
              <Table.Th>Desvio</Table.Th>
              <Table.Th>Recuperação</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {payload.group_budgets.map((row, index) => (
              <Table.Tr key={`${row.muscle_group_id ?? 'sem-grupo'}-${index}`}>
                <Table.Td fw={600}>{row.muscle_group_name ?? EM_DASH}</Table.Td>
                <Table.Td>{fmtDecimal(row.target_weekly_sets)}</Table.Td>
                <Table.Td>
                  {row.target_min_sets != null || row.target_max_sets != null
                    ? `${fmtDecimal(row.target_min_sets)} – ${fmtDecimal(row.target_max_sets)}`
                    : EM_DASH}
                </Table.Td>
                <Table.Td>{fmtDecimal(row.generated_sets)}</Table.Td>
                <Table.Td>{row.deviation_status ?? EM_DASH}</Table.Td>
                <Table.Td>{row.recovery_status ?? EM_DASH}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Paper>
  );
}

/** "Por que este exercício, e não aquele" — os candidatos recusados. */
function RejectionsCard({ payload }: { payload: EnginePlanResponse }) {
  if (payload.rejections.length === 0) return null;
  return (
    <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Title order={4} mb="sm" c={BRAND.textPrimary}>
        Exercícios avaliados e não escolhidos
      </Title>
      <Table.ScrollContainer minWidth={680}>
        <Table striped highlightOnHover withTableBorder verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Exercício</Table.Th>
              <Table.Th>Ordem</Table.Th>
              <Table.Th>Motivo</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {payload.rejections.map((row, index) => (
              <Table.Tr key={`${row.exercise_id ?? 'sem-id'}-${index}`}>
                <Table.Td>{row.exercise_name ?? (row.exercise_id != null ? `#${row.exercise_id}` : EM_DASH)}</Table.Td>
                <Table.Td>{row.rank ?? EM_DASH}</Table.Td>
                <Table.Td>
                  {reasonText({ kind: 'selection', code: row.reason, detail: null, data: row.data })}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Paper>
  );
}
