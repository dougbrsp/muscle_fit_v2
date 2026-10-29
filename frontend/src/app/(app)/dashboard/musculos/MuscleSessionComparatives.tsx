'use client';

import {
  Accordion,
  Badge,
  Box,
  Group,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import { IconArrowDown, IconArrowRight, IconArrowUp } from '@tabler/icons-react';
import { useMemo, useState } from 'react';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { EmptyState, PartialState } from '@/components/dashboard/states';
import { EM_DASH, fmtDate, fmtInt, fmtKg, fmtRpe, fmtSets } from '@/lib/dashboard-format';
import {
  SESSION_UNIT_SUBTITLE,
  SESSION_UNIT_TITLE,
  equipmentLabel,
  pointsByOffset,
  sessionDelta,
  sessionOffsetLabel,
} from '@/lib/dashboard-muscles';
import { statusVisual } from '@/lib/dashboard-status';
import type {
  MuscleComparatives,
  MuscleEquipmentComparative,
  MuscleHeatmapSession,
  MuscleSessionComparative,
} from '@/lib/dashboard-v3-types';
import { BRAND } from '@/theme';

/**
 * Paridade de `/volume-grupamentos` que faltava no hub (F2.5 / M34):
 * comparativo das ÚLTIMAS SESSÕES, comparativo por EQUIPAMENTO e mapa de
 * calor.
 *
 * ## Por que tudo isto mora num bloco à parte, com cabeçalho próprio
 *
 * O resto de `/dashboard/musculos` compara por JANELA (o período do filtro
 * global, normalizado para séries efetivas por semana). Estes três blocos
 * comparam por SESSÃO. São unidades diferentes, e o projeto já aprendeu o
 * preço de deixá-las conviver sem rótulo: na tela legada, quatro tabelas
 * empilhadas — duas por sessão, duas por semana — usavam os mesmos nomes de
 * coluna (`T-0..T-3`), e não havia como saber se "caiu de T-1 para T-0" era
 * "caiu do treino passado para este" ou "caiu da semana passada para esta".
 *
 * Então: cabeçalho único dizendo a unidade, e nenhuma soma, razão ou
 * comparação entre um número daqui e um número da tabela de cima.
 *
 * ## Séries preparatórias
 *
 * Fora de tudo, inclusive do RPE — o backend lê apenas `working`
 * (`sql_not_preparatory`). RPE de aquecimento não mede o esforço da série de
 * trabalho, e incluí-lo faria uma sessão dura parecer leve.
 *
 * ## Mapa de calor e acessibilidade (§14)
 *
 * A faixa (`band`) vem CLASSIFICADA do backend e passa por
 * `dashboard-status.ts`, que dá rótulo, ícone e cor. Cada célula exibe nome do
 * grupo + valor + rótulo da faixa, e carrega `aria-label` com a frase inteira:
 * sem cor nenhuma, a leitura continua completa.
 */

const COLUNAS = 4;
const OFFSETS = Array.from({ length: COLUNAS }, (_, index) => index);

export function MuscleSessionComparatives({
  comparatives,
}: {
  comparatives: MuscleComparatives;
}) {
  if (comparatives.sessions_compared === 0) {
    return (
      <Bloco>
        <EmptyState
          title="Nenhuma sessão no período"
          description="O comparativo por sessão precisa de pelo menos um treino registrado no período selecionado."
        />
      </Bloco>
    );
  }

  return (
    <Bloco quality={comparatives.data_quality} limitations={comparatives.limitations}>
      <Stack gap="lg">
        <SessionTable rows={comparatives.by_session} />
        <EquipmentComparative groups={comparatives.by_equipment} />
        <Heatmap sessions={comparatives.heatmap} />
      </Stack>
    </Bloco>
  );
}

function Bloco({
  children,
  quality,
  limitations,
}: {
  children: React.ReactNode;
  quality?: MuscleComparatives['data_quality'];
  limitations?: string[];
}) {
  return (
    <Paper
      withBorder
      radius="lg"
      p="md"
      style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}
    >
      <Title order={3} c={BRAND.textPrimary}>
        {SESSION_UNIT_TITLE}
      </Title>
      {/*
        A unidade é dita AQUI, uma vez, para o bloco inteiro — e é a razão de
        ele existir separado da tabela por período que abre a tela.
      */}
      <Text size="sm" c={BRAND.textMuted} mt={4}>
        {SESSION_UNIT_SUBTITLE}
      </Text>
      {quality && <PartialState quality={quality} limitations={limitations ?? []} />}
      <Box mt="md">{children}</Box>
    </Paper>
  );
}

// ---------------------------------------------------------------------------
// Variação T-0 vs T-1
// ---------------------------------------------------------------------------

/**
 * Seta + cor + texto. A seta é o sinal primário; a cor só reforça. Note que
 * NÃO há veredito: mais volume numa sessão não é "bom" nem "ruim" — quem
 * classifica volume contra meta é a tabela por período, com o status do
 * backend.
 */
function Delta({ values, digits = 1 }: { values: Array<number | null>; digits?: number }) {
  const { delta, direction } = sessionDelta(values);

  if (direction === 'none' || delta == null) {
    return (
      <Text size="xs" c="dimmed">
        sem T-1
      </Text>
    );
  }

  if (direction === 'stable') {
    return (
      <Group gap={4} wrap="nowrap">
        <IconArrowRight size={14} color={BRAND.orange} aria-hidden />
        <Text size="xs" c={BRAND.textMuted}>
          estável
        </Text>
      </Group>
    );
  }

  const Icon = direction === 'up' ? IconArrowUp : IconArrowDown;
  const color = direction === 'up' ? 'teal.4' : 'orange.4';
  const sinal = delta > 0 ? '+' : '';

  return (
    <Group gap={4} wrap="nowrap">
      <Icon size={14} aria-hidden />
      <Text size="xs" c={color}>
        {sinal}
        {delta.toLocaleString('pt-BR', {
          minimumFractionDigits: digits,
          maximumFractionDigits: digits,
        })}
      </Text>
    </Group>
  );
}

// ---------------------------------------------------------------------------
// 1) Comparativo por SESSÃO
// ---------------------------------------------------------------------------

function SessionCellText({
  sets,
  rpe,
  tonnage,
}: {
  sets: number | null;
  rpe: number | null;
  tonnage: number | null;
}) {
  if (sets == null) {
    return (
      <Text size="sm" c="dimmed">
        {EM_DASH}
      </Text>
    );
  }
  return (
    <Stack gap={0}>
      <Text size="sm" c={BRAND.textPrimary}>
        {fmtSets(sets)} efetivas
      </Text>
      <Text size="xs" c={BRAND.textMuted}>
        RPE {fmtRpe(rpe)} · {fmtKg(tonnage)}
      </Text>
    </Stack>
  );
}

function SessionTable({ rows }: { rows: MuscleSessionComparative[] }) {
  if (rows.length === 0) return null;

  return (
    <Stack gap="xs">
      <Text fw={700} c={BRAND.textPrimary}>
        Por grupo muscular
      </Text>

      <Paper visibleFrom="md" withBorder radius="md" p={0} style={{ borderColor: BRAND.border }}>
        <Table.ScrollContainer minWidth={820}>
          <Table verticalSpacing="sm" highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Grupo</Table.Th>
                {OFFSETS.map((offset) => (
                  <Table.Th key={offset}>{sessionOffsetLabel(offset)}</Table.Th>
                ))}
                <Table.Th>T-0 vs T-1</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((row) => {
                const slots = pointsByOffset(row.points, COLUNAS);
                return (
                  <Table.Tr key={row.muscle_group_id}>
                    <Table.Td>
                      <Stack gap={0}>
                        <Text size="sm" fw={600} c={BRAND.textPrimary}>
                          {row.muscle_group}
                        </Text>
                        <Text size="xs" c="dimmed">
                          {fmtInt(row.sessions_in_window)} sessão(ões) no período
                        </Text>
                      </Stack>
                    </Table.Td>
                    {slots.map((point, index) => (
                      <Table.Td key={index}>
                        <Stack gap={0}>
                          <Text size="xs" c="dimmed">
                            {point ? fmtDate(point.local_date) : EM_DASH}
                          </Text>
                          <SessionCellText
                            sets={point ? point.effective_sets_stimulus : null}
                            rpe={point?.avg_rpe ?? null}
                            tonnage={point?.tonnage_kg ?? null}
                          />
                        </Stack>
                      </Table.Td>
                    ))}
                    <Table.Td>
                      <Delta values={slots.map((p) => p?.effective_sets_stimulus ?? null)} />
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>

      {/* Mobile: uma linha por sessão dentro do cartão do grupo — a tabela de
          5 colunas não cabe em 320px sem rolagem horizontal da página. */}
      <Stack gap="sm" hiddenFrom="md">
        {rows.map((row) => {
          const slots = pointsByOffset(row.points, COLUNAS);
          return (
            <Paper
              key={row.muscle_group_id}
              withBorder
              radius="md"
              p="sm"
              style={{ background: BRAND.bgSurface, borderColor: BRAND.border }}
            >
              <Group justify="space-between" align="flex-start" wrap="wrap" gap={4}>
                <Text size="sm" fw={600} c={BRAND.textPrimary}>
                  {row.muscle_group}
                </Text>
                <Delta values={slots.map((p) => p?.effective_sets_stimulus ?? null)} />
              </Group>
              <Stack gap={6} mt={6}>
                {slots.map((point, index) => (
                  <Group key={index} justify="space-between" wrap="wrap" gap={4}>
                    <Text size="xs" c="dimmed" style={{ minWidth: 92 }}>
                      {sessionOffsetLabel(index)} · {point ? fmtDate(point.local_date) : EM_DASH}
                    </Text>
                    <SessionCellText
                      sets={point ? point.effective_sets_stimulus : null}
                      rpe={point?.avg_rpe ?? null}
                      tonnage={point?.tonnage_kg ?? null}
                    />
                  </Group>
                ))}
              </Stack>
            </Paper>
          );
        })}
      </Stack>
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// 2) Comparativo por EQUIPAMENTO
// ---------------------------------------------------------------------------

function EquipmentComparative({ groups }: { groups: MuscleEquipmentComparative[] }) {
  if (groups.length === 0) return null;

  return (
    <Stack gap="xs">
      <Text fw={700} c={BRAND.textPrimary}>
        Por equipamento
      </Text>
      {/*
        As colunas são as MESMAS sessões T-0..T-3 do grupo. Um equipamento que
        não apareceu em T-2 fica com um traço nessa coluna em vez de deslizar
        o T-3 para lá: deslizar sugeriria uma comparação que não aconteceu.
      */}
      <Text size="xs" c={BRAND.textMuted}>
        As colunas são as mesmas sessões do grupo. Um traço significa que o
        equipamento não foi usado naquela sessão.
      </Text>

      <Accordion variant="separated" radius="md" chevronPosition="left">
        {groups.map((group) => (
          <Accordion.Item key={group.muscle_group_id} value={String(group.muscle_group_id)}>
            <Accordion.Control>
              <Group gap="xs" wrap="wrap">
                <Text size="sm" fw={600} c={BRAND.textPrimary}>
                  {group.muscle_group}
                </Text>
                <Text size="xs" c="dimmed">
                  {fmtInt(group.equipments.length)} equipamento(s)
                </Text>
                {group.equipment_truncated && (
                  <Tooltip
                    label={`Mais de ${group.equipment_limit} equipamentos: a lista mostra os que mais contribuíram.`}
                    withArrow
                    multiline
                    w={260}
                  >
                    <Badge size="xs" color="gray" variant="light">
                      lista cortada
                    </Badge>
                  </Tooltip>
                )}
              </Group>
            </Accordion.Control>
            <Accordion.Panel>
              <Stack gap="xs">
                {group.equipments.map((row) => {
                  const slots = pointsByOffset(row.points, COLUNAS);
                  return (
                    <Paper
                      key={row.equipment_name ?? '__sem_equipamento__'}
                      withBorder
                      radius="md"
                      p="sm"
                      style={{ background: BRAND.bgSurface, borderColor: BRAND.border }}
                    >
                      <Group justify="space-between" align="flex-start" wrap="wrap" gap={4}>
                        <Text size="sm" fw={600} c={BRAND.textPrimary}>
                          {equipmentLabel(row.equipment_name)}
                        </Text>
                        <Text size="xs" c={BRAND.textMuted}>
                          {fmtSets(row.effective_sets_stimulus)} efetivas no recorte
                          {row.share_of_group != null
                            ? ` · ${Math.round(row.share_of_group * 100)}% do grupo`
                            : ''}
                        </Text>
                      </Group>
                      <SimpleGrid cols={{ base: 2, xs: 4 }} spacing="xs" mt={8}>
                        {slots.map((point, index) => (
                          <Stack key={index} gap={0}>
                            <Text size="xs" c="dimmed">
                              {sessionOffsetLabel(index)}
                            </Text>
                            {point ? (
                              <>
                                <Text size="sm" c={BRAND.textPrimary}>
                                  {fmtSets(point.effective_sets_stimulus)}
                                </Text>
                                <Text size="xs" c={BRAND.textMuted}>
                                  RPE {fmtRpe(point.avg_rpe)}
                                </Text>
                              </>
                            ) : (
                              <Text size="sm" c="dimmed">
                                {EM_DASH}
                              </Text>
                            )}
                          </Stack>
                        ))}
                      </SimpleGrid>
                    </Paper>
                  );
                })}
              </Stack>
            </Accordion.Panel>
          </Accordion.Item>
        ))}
      </Accordion>
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// 3) MAPA DE CALOR
// ---------------------------------------------------------------------------

function sessionOptionLabel(session: MuscleHeatmapSession): string {
  const nome = session.activity_name?.trim();
  return `${fmtDate(session.local_date)}${nome ? ` · ${nome}` : ''}`;
}

/**
 * Grade de células, NÃO um treemap.
 *
 * O treemap legado codificava o volume duas vezes (área e cor) e, num
 * celular, colapsava os grupos menores a ponto de o rótulo sumir — sobrava só
 * a cor, que é justamente o que §14 proíbe como sinal único. A grade dá a
 * mesma leitura "quem levou mais estímulo nesta sessão" com nome, número e
 * faixa sempre visíveis, e reflui de 1 a 4 colunas sem rolagem horizontal.
 */
function Heatmap({ sessions }: { sessions: MuscleHeatmapSession[] }) {
  const [activityId, setActivityId] = useState<string | null>(null);

  const selected = useMemo(() => {
    if (sessions.length === 0) return null;
    return sessions.find((s) => String(s.activity_id) === activityId) ?? sessions[0];
  }, [sessions, activityId]);

  if (!selected) return null;

  return (
    <Stack gap="xs">
      <Group justify="space-between" align="flex-end" wrap="wrap" gap="sm">
        <div>
          <Text fw={700} c={BRAND.textPrimary}>
            Mapa de calor da sessão
          </Text>
          <Text size="xs" c={BRAND.textMuted}>
            A intensidade é relativa ao grupo mais trabalhado DESTA sessão — ela
            compara grupos dentro de um treino, nunca treinos entre si.
          </Text>
        </div>
        <Select
          size="xs"
          label="Sessão"
          allowDeselect={false}
          value={String(selected.activity_id)}
          onChange={setActivityId}
          data={sessions.map((session) => ({
            value: String(session.activity_id),
            label: sessionOptionLabel(session),
          }))}
          maw={260}
        />
      </Group>

      {selected.cells.length === 0 ? (
        <Text size="sm" c="dimmed">
          Esta sessão não gerou estímulo atribuído a nenhum grupo.
        </Text>
      ) : (
        <SimpleGrid cols={{ base: 1, xs: 2, sm: 3, lg: 4 }} spacing="xs">
          {selected.cells.map((cell) => {
            const visual = statusVisual('muscleHeatmap', cell.band);
            const descricao =
              `${cell.muscle_group}: ${fmtSets(cell.effective_sets_stimulus)} séries efetivas, ` +
              `intensidade ${visual.label.toLowerCase()}` +
              (cell.avg_rpe != null ? `, RPE médio ${fmtRpe(cell.avg_rpe)}` : '');

            return (
              <Paper
                key={cell.muscle_group_id}
                withBorder
                radius="md"
                p="sm"
                aria-label={descricao}
                style={{
                  background: BRAND.bgSurface,
                  // A cor é REFORÇO, não o sinal: ela vive numa barra lateral,
                  // e o texto abaixo dá o mesmo dado sem depender dela.
                  borderColor: BRAND.border,
                  borderLeft: `4px solid ${visual.hex}`,
                }}
              >
                <Text size="sm" fw={600} c={BRAND.textPrimary}>
                  {cell.muscle_group}
                </Text>
                <Text size="lg" fw={700} c={BRAND.textPrimary}>
                  {fmtSets(cell.effective_sets_stimulus)}
                  <Text span size="xs" c={BRAND.textMuted}>
                    {' '}
                    efetivas
                  </Text>
                </Text>
                <Group gap={6} mt={4} wrap="wrap">
                  <StatusBadge domain="muscleHeatmap" code={cell.band} size="xs" />
                  <Text size="xs" c={BRAND.textMuted}>
                    RPE {fmtRpe(cell.avg_rpe)}
                  </Text>
                </Group>
              </Paper>
            );
          })}
        </SimpleGrid>
      )}
    </Stack>
  );
}
