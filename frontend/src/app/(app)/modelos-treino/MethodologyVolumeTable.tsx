'use client';

import { ActionIcon, Badge, Group, Stack, Table, Text, Tooltip } from '@mantine/core';
import { IconArrowsDiff, IconChevronDown, IconChevronRight, IconTarget } from '@tabler/icons-react';
import { Fragment } from 'react';
import {
  TECHNIQUE_EQUIVALENT_HINT,
  type MethodologyAxisView,
  type MethodologyOverallView,
  type MethodologyRowView,
} from '@/lib/methodology-volume';
import { countingModeOption, DEFAULT_COUNTING_MODE, setsUnitOf } from '@/lib/volume-counting-mode';
import { BRAND } from '@/theme';

function capitalize(text: string): string {
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}`;
}

/** Selo da faixa/meta copiada do modo default e ainda não calibrada (Lote 3). */
export const UNCALIBRATED_BADGE_LABEL = 'cópia — não calibrada';
export const UNCALIBRATED_HINT = `A faixa ou a meta usada neste modo de contagem ainda é a cópia da de "${
  countingModeOption(DEFAULT_COUNTING_MODE).label
}". Calibre em Domínio de exercícios › Macro-grupos de volume.`;

/**
 * Tabela PURA do painel de metodologia (Fase 4, PR F): recebe linhas já
 * formatadas por `buildMethodologyRows` e só desenha. Sem query, sem estado —
 * é o que o teste de render cobre (`MethodologyVolumeTable.test.ts`).
 *
 * Unidade escrita em TODA célula: "exercícios por sessão", "séries diretas
 * por sessão" e "séries diretas por semana" (nunca "séries" sozinho — a
 * ativação ponderada ao lado é outra unidade). Status sempre com rótulo +
 * ícone + cor e `aria-label`; a origem da faixa (metodologia, derivada ou
 * admin) vem escrita ao lado, com a evidência no tooltip.
 *
 * Fase 6: coluna "Macro-grupo" (no lugar do porte), coluna semanal com
 * status (meta semanal do macro-grupo) e o status COMBINADO do grupo abaixo
 * do nome, com o sinal de conflito quando um eixo está abaixo e outro acima.
 *
 * `expansion` (PR G, tela de diagnóstico): quando informado, cada linha ganha
 * um botão que abre a contribuição POR SESSÃO do bloco (`row.sessions`). O
 * estado de expansão fica com quem chama — a tabela continua pura.
 */

type AxisCellProps = { view: MethodologyAxisView };

function AxisCell({ view }: AxisCellProps) {
  const StatusIcon = view.status.Icon;
  return (
    <Stack gap={4}>
      {view.reasonLabel ? (
        <Tooltip label={view.reasonHint ?? view.reasonLabel} multiline w={280} withArrow disabled={!view.reasonHint}>
          <Text size="sm" c={BRAND.textMuted}>
            — <Text component="span" size="xs">({view.reasonLabel})</Text>
          </Text>
        </Tooltip>
      ) : (
        <Text size="sm" c={BRAND.textPrimary}>
          <Text component="span" fw={700}>
            {view.valueText}
          </Text>{' '}
          <Text component="span" size="xs" c={BRAND.textMuted}>
            {view.unit}
          </Text>
          {view.rangeText !== '—' && (
            <>
              {' · '}
              <Text component="span" size="xs" c={BRAND.textMuted}>
                {view.rangeWord} {view.rangeText}
              </Text>
              {view.sourceVisual && (
                <Tooltip
                  label={`${view.sourceVisual.description ?? view.sourceVisual.label} ${view.evidence ?? ''}`.trim()}
                  multiline
                  w={320}
                  withArrow
                >
                  <Text
                    component="span"
                    size="xs"
                    c={BRAND.textMuted}
                    td={view.isDerived ? 'underline dotted' : undefined}
                    data-range-source={view.sourceCode ?? undefined}
                  >
                    {' '}
                    {view.isDerived ? '(derivada)' : `(${view.sourceVisual.label.toLowerCase()})`}
                  </Text>
                </Tooltip>
              )}
            </>
          )}
        </Text>
      )}
      <Tooltip
        label={
          view.isInformational
            ? `${view.status.description ?? view.status.label} Desvio esperado pela intenção declarada: não é aviso.`
            : view.status.description ?? view.status.label
        }
        multiline
        w={300}
        withArrow
      >
        <Badge
          size="sm"
          variant={view.isInformational ? 'outline' : 'light'}
          color={view.status.color}
          leftSection={<StatusIcon size={12} aria-hidden />}
          aria-label={view.ariaLabel}
          data-status={view.statusCode}
          style={{ textTransform: 'none', maxWidth: '100%' }}
        >
          {view.status.label}
          {view.isInformational ? ' (esperado)' : ''}
        </Badge>
      </Tooltip>
    </Stack>
  );
}

/** Status combinado do grupo (Fase 6): rótulo + ícone + cor, conflito escrito. */
function OverallBadge({ view }: { view: MethodologyOverallView }) {
  const StatusIcon = view.status.Icon;
  return (
    <Tooltip label={view.reasonHint ? `${view.description} ${view.reasonHint}` : view.description} multiline w={320} withArrow>
      <Group gap={4} wrap="nowrap" mt={4}>
        <Badge
          size="xs"
          variant={view.isInformational ? 'outline' : 'light'}
          color={view.status.color}
          leftSection={<StatusIcon size={10} aria-hidden />}
          aria-label={view.ariaLabel}
          data-overall-status={view.statusCode}
          style={{ textTransform: 'none' }}
        >
          {view.status.label}
          {view.isInformational ? ' (esperado)' : ''}
        </Badge>
        {view.conflict && (
          <Badge
            size="xs"
            variant="outline"
            color="orange"
            leftSection={<IconArrowsDiff size={10} aria-hidden />}
            data-status-conflict
            style={{ textTransform: 'none' }}
          >
            eixos em conflito
          </Badge>
        )}
      </Group>
    </Tooltip>
  );
}

/** Contribuição por sessão de um bloco (linha expandida da tela de diagnóstico). */
function SessionBreakdown({ row, setsUnit }: { row: MethodologyRowView; setsUnit: string }) {
  return (
    <Stack gap={6} py={4}>
      <Text size="xs" c={BRAND.textMuted}>
        Sessões do ciclo com trabalho direto em <b>{row.groupName}</b> — {row.cycleTotalsText}. Sessões sem trabalho
        direto no bloco não aparecem.
      </Text>
      {row.sessions.length === 0 ? (
        <Text size="xs" c={BRAND.textMuted}>
          Nenhuma sessão do ciclo trabalha este bloco diretamente.
        </Text>
      ) : (
        <Table withTableBorder withColumnBorders verticalSpacing={4} horizontalSpacing="sm" w="auto">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Sessão (slot do ciclo)</Table.Th>
              <Table.Th>Exercícios diretos na sessão (nº de exercícios)</Table.Th>
              <Table.Th>{capitalize(setsUnit)} na sessão (nº de séries)</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {row.sessions.map((session) => (
              <Table.Tr key={session.key} data-session-row={session.key}>
                <Table.Td>
                  <Text size="sm" c={BRAND.textPrimary}>
                    {session.label}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size="sm" c={BRAND.textPrimary} ta="right">
                    {session.exercisesText}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size="sm" c={BRAND.textPrimary} ta="right">
                    {session.setsText}
                  </Text>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}

export type MethodologyTableExpansion = {
  /** Chaves (`row.key` = id do bloco) atualmente expandidas. */
  expandedKeys: ReadonlySet<number>;
  onToggle: (key: number) => void;
};

type Props = {
  rows: MethodologyRowView[];
  /** Linha de contexto (modo, ciclo, normalização), já escrita. */
  summary: string;
  /** Linha expandível por sessão (tela de diagnóstico). Ausente = tabela do builder. */
  expansion?: MethodologyTableExpansion;
  /** Lote 3: `counting_mode` da avaliação (a unidade das séries vem do motor). */
  countingMode?: string | null;
};

export function MethodologyVolumeTable({ rows, summary, expansion, countingMode = DEFAULT_COUNTING_MODE }: Props) {
  const setsUnit = setsUnitOf(countingMode).plural;
  const columnCount = expansion ? 7 : 6;
  return (
    <Stack gap="xs">
      <Text size="xs" c={BRAND.textMuted}>
        {summary}
      </Text>
      <Table.ScrollContainer minWidth={980}>
        <Table striped highlightOnHover withTableBorder>
          <Table.Thead>
            <Table.Tr>
              {expansion && <Table.Th w={40} aria-label="Sessões" />}
              <Table.Th>Grupo / bloco</Table.Th>
              <Table.Th>Macro-grupo</Table.Th>
              <Table.Th>Frequência semanal</Table.Th>
              <Table.Th>Exercícios por sessão (valor · faixa · origem)</Table.Th>
              <Table.Th>{capitalize(setsUnit)} por sessão (valor · faixa · origem)</Table.Th>
              <Table.Th>{capitalize(setsUnit)} por semana (valor · meta · origem)</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((row) => {
              const isExpanded = expansion?.expandedKeys.has(row.key) ?? false;
              return (
                <Fragment key={row.key}>
                  <Table.Tr>
                    {expansion && (
                      <Table.Td>
                        <ActionIcon
                          variant="subtle"
                          color="brand"
                          size="sm"
                          onClick={() => expansion.onToggle(row.key)}
                          aria-expanded={isExpanded}
                          aria-label={`${isExpanded ? 'Ocultar' : 'Mostrar'} sessões de ${row.groupName}`}
                          data-expand-group={row.key}
                        >
                          {isExpanded ? <IconChevronDown size={16} /> : <IconChevronRight size={16} />}
                        </ActionIcon>
                      </Table.Td>
                    )}
                    <Table.Td>
                      <Group gap={6} wrap="nowrap">
                        <Text size="sm" c={BRAND.textPrimary} fw={600}>
                          {row.groupName}
                        </Text>
                        {row.isIntentTarget && (
                          <Tooltip label="Grupo-alvo da intenção declarada" withArrow>
                            <Badge
                              size="xs"
                              variant="light"
                              color="brand"
                              leftSection={<IconTarget size={10} aria-hidden />}
                              aria-label="Grupo-alvo da intenção declarada"
                            >
                              alvo
                            </Badge>
                          </Tooltip>
                        )}
                      </Group>
                      {row.memberCount > 1 && (
                        <Text size="xs" c={BRAND.textMuted}>
                          soma de {row.memberCount} fatias
                        </Text>
                      )}
                      {row.techniqueNote && (
                        <Tooltip label={TECHNIQUE_EQUIVALENT_HINT} multiline w={320} withArrow>
                          <Text size="xs" c={BRAND.textMuted} style={{ cursor: 'help' }} data-technique-note={row.key}>
                            {row.techniqueNote}
                          </Text>
                        </Tooltip>
                      )}
                      <OverallBadge view={row.overall} />
                      {row.uncalibrated && (
                        <Tooltip label={UNCALIBRATED_HINT} multiline w={300} withArrow>
                          <Badge size="xs" variant="outline" color="yellow" mt={4} data-uncalibrated={row.key}>
                            {UNCALIBRATED_BADGE_LABEL}
                          </Badge>
                        </Tooltip>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" c={BRAND.textPrimary}>
                        {row.volumeClassName}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" c={BRAND.textPrimary}>
                        {row.frequencyText}
                      </Text>
                      {row.frequencyNote && (
                        <Text size="xs" c={BRAND.textMuted}>
                          {row.frequencyNote}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <AxisCell view={row.exercises} />
                    </Table.Td>
                    <Table.Td>
                      <AxisCell view={row.sets} />
                    </Table.Td>
                    <Table.Td>
                      <AxisCell view={row.weekly} />
                    </Table.Td>
                  </Table.Tr>
                  {expansion && isExpanded && (
                    <Table.Tr data-session-detail={row.key}>
                      <Table.Td colSpan={columnCount} style={{ background: BRAND.bgElevated }}>
                        <SessionBreakdown row={row} setsUnit={setsUnit} />
                      </Table.Td>
                    </Table.Tr>
                  )}
                </Fragment>
              );
            })}
            {rows.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={columnCount}>
                  <Text c={BRAND.textMuted} ta="center" py="sm">
                    Nenhum grupo muscular apareceu nos slots preenchidos.
                  </Text>
                </Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Stack>
  );
}
