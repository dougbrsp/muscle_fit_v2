'use client';

import { ThemeIcon } from '@mantine/core';
import type { MuscleActivationRow } from '@/lib/analytics-types';
import { formatNumberOrDash } from '@/lib/format';
import {
  activationVisual,
  assessmentTooltip,
  formatActivationCell,
  metaLabel,
} from '@/components/MuscleActivationTable';
import { EmptyState, ErrorState, ListGroup, ListRow, LoadingState } from '../../ui';

/**
 * Ativação muscular do treino em lista (celular). A avaliação (status, cor,
 * alvo) vem pronta da API em `row.assessment`; as funções de apresentação são
 * as mesmas da tabela do desktop. Sem Tooltip: o texto completo vai no subtítulo.
 */
export function MuscleActivationList({
  rows,
  isLoading,
  isError,
}: {
  rows: MuscleActivationRow[];
  isLoading: boolean;
  isError: boolean;
}) {
  if (isError) return <ErrorState title="Não foi possível carregar a ativação muscular deste treino." />;
  if (isLoading) return <LoadingState label="Carregando ativação muscular..." />;
  if (rows.length === 0) return <EmptyState title="Nenhuma ativação muscular calculada para este treino ainda." />;
  return (
    <ListGroup title="Ativação muscular">
      {rows.map((row) => {
        const assessment = row.assessment;
        const visual = assessment ? activationVisual(assessment) : null;
        const Icon = visual?.Icon;
        const meta = metaLabel(assessment);
        const detail = [
          `Primária ${formatActivationCell(row.primary_series, row.primary_equivalent)}`,
          `Secundária ${formatActivationCell(row.secondary_series, row.secondary_equivalent)}`,
          `Estabilizadora ${formatActivationCell(row.stabilizer_series, row.stabilizer_equivalent)}`,
          meta !== '-' ? `Meta ${meta}` : null,
          assessment && visual ? assessmentTooltip(row, assessment, visual.label) : null,
        ]
          .filter(Boolean)
          .join(' · ');
        return (
          <ListRow
            key={row.muscle_group}
            title={row.muscle_group}
            subtitle={detail}
            value={formatNumberOrDash(row.total_equivalent)}
            trailing={
              assessment && visual && Icon ? (
                <ThemeIcon
                  color={visual.color}
                  variant="light"
                  radius="xl"
                  size="md"
                  aria-label={visual.label}
                  data-status={assessment.status}
                  data-severity={assessment.severity ?? undefined}
                >
                  <Icon size={16} />
                </ThemeIcon>
              ) : undefined
            }
          />
        );
      })}
    </ListGroup>
  );
}
