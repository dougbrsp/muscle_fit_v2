'use client';

import { Alert, Table, Text, ThemeIcon, Tooltip } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import type { MuscleActivationAssessment, MuscleActivationRow } from '@/lib/analytics-types';
import { plannedSeverityVisual, statusVisual, type StatusVisual } from '@/lib/dashboard-status';
import { BRAND } from '@/theme';
import { formatNumberOrDash } from '@/lib/format';

// Tabela "Ativação muscular" de `/registro-exercicios/[activityId]`.
//
// M76 E4: a avaliação (ícone/cor/tooltip) vem PRONTA do backend em
// `row.assessment` (`volume_service.assess_activation_against_target`: alvo do
// slot -> estados do planejado; meta do tipo de treino ou genérica -> os 5
// estados de `classify_weekly_volume_status`). Aqui não há limiar nenhum: o
// componente só desenha o código no domínio de `dashboard-status.ts`.
// "Muito abaixo do planejado" (decisão de 2026-10-07) também vem pronto:
// `assessment.severity = 'FAR_BELOW'` (`volume_service.planned_volume_severity`),
// e então vale o visual da gravidade em vez do do status.
//
// Cor ESPECÍFICA desta tabela (decisão do usuário 2026-10-07): aqui o vermelho
// significa "muito abaixo", então `BELOW_TARGET` do planejado SEM gravidade
// passa a amarelo. Só código de status/gravidade -> cor; o visual
// compartilhado (`/dashboard/musculos`, mapa corporal) não muda.
const YELLOW_HEX = '#f08c00';

function activationVisual(assessment: MuscleActivationAssessment): StatusVisual {
  const severityVisual = plannedSeverityVisual(assessment.severity);
  if (severityVisual) return severityVisual;
  const visual = statusVisual(assessment.status_domain, assessment.status);
  if (assessment.status_domain === 'plannedVolume' && assessment.status === 'BELOW_TARGET') {
    return { ...visual, color: 'yellow', hex: YELLOW_HEX };
  }
  return visual;
}

function formatActivationCell(series: number, equivalent: number): string {
  if (series === 0) return '-';
  const participationPct = Math.round((equivalent / series) * 100);
  return `${series} séries × ${participationPct}%`;
}

const TARGET_SOURCE_LABEL: Record<MuscleActivationAssessment['target_source'], string> = {
  template_slot: 'alvo do slot do Modelo',
  training_type: 'meta do tipo de treino',
  generic_fallback: 'meta genérica',
};

function hasOwnTarget(assessment: MuscleActivationAssessment | undefined): boolean {
  return assessment != null && assessment.target_source !== 'generic_fallback' && assessment.target_sets != null;
}

function metaLabel(assessment: MuscleActivationAssessment | undefined): string {
  if (!hasOwnTarget(assessment)) return '-';
  const target = formatNumberOrDash(assessment!.target_sets);
  return assessment!.max_sets != null ? `${target} (máx ${formatNumberOrDash(assessment!.max_sets)})` : target;
}

function assessmentTooltip(row: MuscleActivationRow, assessment: MuscleActivationAssessment, label: string): string {
  const actual = formatNumberOrDash(row.total_equivalent);
  if (!hasOwnTarget(assessment)) return `${label} (${actual} séries equivalentes, ${TARGET_SOURCE_LABEL.generic_fallback})`;
  const target = formatNumberOrDash(assessment.target_sets);
  return `${label} (${actual}/${target} séries equivalentes, ${TARGET_SOURCE_LABEL[assessment.target_source]})`;
}

type Props = {
  rows: MuscleActivationRow[];
  isLoading?: boolean;
  isError?: boolean;
  emptyMessage?: string;
  errorMessage?: string;
};

export function MuscleActivationTable({
  rows,
  isLoading = false,
  isError = false,
  emptyMessage = 'Nenhuma ativação muscular calculada ainda.',
  errorMessage = 'Não foi possível carregar a ativação muscular.',
}: Props) {
  if (isError) {
    return (
      <Alert color="red" icon={<IconAlertTriangle size={16} />}>
        {errorMessage}
      </Alert>
    );
  }

  const hasTargetColumn = rows.some((row) => hasOwnTarget(row.assessment));
  const columnCount = hasTargetColumn ? 7 : 6;

  return (
    <Table.ScrollContainer minWidth={600}>
      <Table striped highlightOnHover withTableBorder>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Grupo Muscular</Table.Th>
            <Table.Th>Ativação primária</Table.Th>
            <Table.Th>Ativação Secundária</Table.Th>
            <Table.Th>Ativação Estabilizadora</Table.Th>
            <Table.Th>Ativação Total</Table.Th>
            {hasTargetColumn && <Table.Th>Meta</Table.Th>}
            <Table.Th>Avaliação</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((row) => {
            const assessment = row.assessment;
            const visual = assessment ? activationVisual(assessment) : null;
            const AssessmentIcon = visual?.Icon;
            return (
              <Table.Tr key={row.muscle_group}>
                <Table.Td>{row.muscle_group}</Table.Td>
                <Table.Td>{formatActivationCell(row.primary_series, row.primary_equivalent)}</Table.Td>
                <Table.Td>{formatActivationCell(row.secondary_series, row.secondary_equivalent)}</Table.Td>
                <Table.Td>{formatActivationCell(row.stabilizer_series, row.stabilizer_equivalent)}</Table.Td>
                <Table.Td>{formatNumberOrDash(row.total_equivalent)}</Table.Td>
                {hasTargetColumn && <Table.Td>{metaLabel(assessment)}</Table.Td>}
                <Table.Td>
                  {assessment && visual && AssessmentIcon ? (
                    <Tooltip label={assessmentTooltip(row, assessment, visual.label)}>
                      <ThemeIcon
                        color={visual.color}
                        variant="light"
                        radius="xl"
                        size="sm"
                        aria-label={visual.label}
                        data-status={assessment.status}
                        data-severity={assessment.severity ?? undefined}
                      >
                        <AssessmentIcon size={14} />
                      </ThemeIcon>
                    </Tooltip>
                  ) : (
                    '-'
                  )}
                </Table.Td>
              </Table.Tr>
            );
          })}
          {!isLoading && rows.length === 0 && (
            <Table.Tr>
              <Table.Td colSpan={columnCount}>
                <Text c={BRAND.textMuted} ta="center" py="md">
                  {emptyMessage}
                </Text>
              </Table.Td>
            </Table.Tr>
          )}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
