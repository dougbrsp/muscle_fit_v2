'use client';

import { Group, Loader, Stack, Text, Tooltip } from '@mantine/core';
import { Fragment } from 'react';
import type { MethodologyVolumeEvaluation } from '@/lib/methodology-volume-types';
import { countingModeOption } from '@/lib/volume-counting-mode';
import {
  buildWeeklySetsBars,
  WEEKLY_SETS_BAR_COLORS,
  WEEKLY_SETS_SCALE_FACTOR,
  type WeeklySetsBarRow,
} from '@/lib/weekly-sets-bar';
import { BRAND } from '@/theme';
import { FloatingPanel } from './FloatingPanel';
import type { FloatingPanelControls } from './useFloatingPanels';

export const WEEKLY_SETS_PANEL_ID = 'weekly-sets';
const TITLE = 'Séries por grupo';
const BAR_HEIGHT = 6;

/** Lote 3: a régua depende do modo de contagem do Modelo (texto do motor). */
function unitHint(countingMode: string | null | undefined): string {
  const mode = countingModeOption(countingMode);
  return `${mode.label}: ${mode.description} Séries por semana contra a meta semanal do macro-grupo. Escala até ${WEEKLY_SETS_SCALE_FACTOR.toLocaleString('pt-BR')} × o máximo da meta: cinza abaixo, verde dentro, vermelho acima.`;
}

function capitalize(text: string): string {
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}`;
}

function WeeklySetsBar({ row }: { row: WeeklySetsBarRow }) {
  const { status } = row;
  return (
    <div aria-label={row.ariaLabel} role="img">
      <Group justify="space-between" wrap="nowrap" gap={4}>
        <Text size="xs" c={BRAND.textPrimary} truncate>
          {row.groupName}
        </Text>
        <Group gap={4} wrap="nowrap" style={{ flexShrink: 0 }}>
          <Text size="xs" c={row.hasRange ? BRAND.textPrimary : 'dimmed'}>
            {row.text}
          </Text>
          <Tooltip label={status.label} openDelay={300}>
            <span style={{ display: 'inline-flex', color: status.hex }} title={status.label}>
              <status.Icon size={12} />
            </span>
          </Tooltip>
        </Group>
      </Group>
      <div
        style={{
          position: 'relative',
          height: BAR_HEIGHT,
          marginTop: 2,
          borderRadius: BAR_HEIGHT / 2,
          overflow: 'hidden',
          display: 'flex',
          background: 'var(--mantine-color-default-border)',
        }}
      >
        {row.segments && (
          <>
            <div style={{ width: `${row.segments.belowPct}%`, background: WEEKLY_SETS_BAR_COLORS.below, opacity: 0.35 }} />
            <div style={{ width: `${row.segments.withinPct}%`, background: WEEKLY_SETS_BAR_COLORS.within, opacity: 0.55 }} />
            <div style={{ width: `${row.segments.abovePct}%`, background: WEEKLY_SETS_BAR_COLORS.above, opacity: 0.35 }} />
          </>
        )}
        {row.markerPct != null && (
          <div
            data-marker
            style={{
              position: 'absolute',
              top: 0,
              bottom: 0,
              left: `calc(${row.markerPct}% - 1.5px)`,
              width: 3,
              background: BRAND.textPrimary,
            }}
          />
        )}
      </div>
    </div>
  );
}

export type WeeklySetsFloatingPanelProps = {
  evaluation: MethodologyVolumeEvaluation | null | undefined;
  isPending: boolean;
  isError: boolean;
  controls: FloatingPanelControls;
  index: number;
  withinPortal?: boolean;
};

/**
 * Fase 7 (Frente A) — janela "Séries por grupo" (só Modo B). Lê a MESMA
 * avaliação da metodologia que o painel do builder já busca
 * (`methodologyVolumeQuery`): nenhuma requisição extra. Grupos agrupados por
 * macro-grupo; sem meta semanal, barra neutra com "sem meta".
 */
export function WeeklySetsFloatingPanel({
  evaluation,
  isPending,
  isError,
  controls,
  index,
  withinPortal,
}: WeeklySetsFloatingPanelProps) {
  const rows = buildWeeklySetsBars(evaluation);
  const unit = countingModeOption(evaluation?.counting_mode).sets_unit_label;

  let body;
  if (rows.length > 0) {
    body = (
      <Stack gap={6}>
        {rows.map((row, rowIndex) => (
          <Fragment key={row.groupId}>
            {(rowIndex === 0 || rows[rowIndex - 1].classId !== row.classId) && (
              <Text size="xs" fw={700} c="dimmed" tt="uppercase" mt={rowIndex === 0 ? 0 : 4}>
                {row.className}
              </Text>
            )}
            <WeeklySetsBar row={row} />
          </Fragment>
        ))}
      </Stack>
    );
  } else if (isPending) {
    body = (
      <Group justify="center" py="xs">
        <Loader size="xs" color="brand" />
      </Group>
    );
  } else {
    body = (
      <Text size="xs" c="dimmed">
        {isError
          ? 'Não foi possível calcular as séries por grupo agora.'
          : `Adicione exercícios com séries para ver as ${unit} por semana.`}
      </Text>
    );
  }

  return (
    <FloatingPanel title={TITLE} index={index} controls={controls} withinPortal={withinPortal}>
      <Tooltip label={unitHint(evaluation?.counting_mode)} multiline w={280} openDelay={400}>
        <Text size="xs" c="dimmed" mb={6} style={{ cursor: 'help' }}>
          {capitalize(unit)}/semana · valor / meta
        </Text>
      </Tooltip>
      {body}
    </FloatingPanel>
  );
}
