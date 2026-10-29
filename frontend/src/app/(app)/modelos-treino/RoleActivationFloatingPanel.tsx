'use client';

import { Group, Loader, Table, Text, Tooltip } from '@mantine/core';
import { formatPerSession } from '@/lib/methodology-volume';
import { buildRoleActivationWeekly } from '@/lib/role-activation-weekly';
import type { ActivationRole, PreviewActivationResponse } from '@/lib/workout-plan-templates-types';
import { BRAND } from '@/theme';
import { FloatingPanel } from './FloatingPanel';
import { ROLE_STYLE } from './ItemActivationLine';
import type { FloatingPanelControls } from './useFloatingPanels';

export const ROLE_ACTIVATION_PANEL_ID = 'role-activation';
const TITLE = 'Ativação por participação';
const ROLES: ActivationRole[] = ['primary', 'secondary', 'stabilizer'];

const UNIT_HINT =
  'Séries equivalentes ponderadas (séries × peso de estímulo) numa janela de 7 dias a partir do primeiro treino do ciclo — um slot por dia, dando a volta em ciclos curtos e cortando o que passa do 7º dia —, separadas pela participação do grupo: principal, secundário e estabilizador. Mesma janela da metodologia de volume.';

export type RoleActivationFloatingPanelProps = {
  response: PreviewActivationResponse | null | undefined;
  isPending: boolean;
  isError: boolean;
  controls: FloatingPanelControls;
  index: number;
  withinPortal?: boolean;
};

/**
 * Fase 7 (Frente A) — janela "Ativação por participação" (Modos A e B). Lê a
 * MESMA query do preview de ativação das tabelas e das linhas por item
 * (`useActivationPreviewQuery`, deduplicada pelo React Query). Só números,
 * com os ícones/cores de papel de `ItemActivationLine`.
 */
export function RoleActivationFloatingPanel({
  response,
  isPending,
  isError,
  controls,
  index,
  withinPortal,
}: RoleActivationFloatingPanelProps) {
  const rows = buildRoleActivationWeekly(response);

  let body;
  if (rows.length > 0) {
    body = (
      <Table withRowBorders={false} verticalSpacing={2} horizontalSpacing={4} fz="xs">
        <Table.Thead>
          <Table.Tr>
            <Table.Th fz="xs">Grupo</Table.Th>
            {ROLES.map((role) => (
              <Table.Th key={role} ta="right" aria-label={ROLE_STYLE[role].label} title={ROLE_STYLE[role].label}>
                <span style={{ display: 'inline-flex', color: ROLE_STYLE[role].color }}>{ROLE_STYLE[role].icon}</span>
              </Table.Th>
            ))}
            <Table.Th fz="xs" ta="right">
              Total
            </Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((row) => (
            <Table.Tr key={row.muscleGroup}>
              <Table.Td>
                <Text size="xs" c={BRAND.textPrimary}>
                  {row.muscleGroup}
                </Text>
              </Table.Td>
              {ROLES.map((role) => (
                <Table.Td key={role} ta="right">
                  <Text size="xs" c={ROLE_STYLE[role].color} fw={role === 'primary' ? 600 : 400}>
                    {row[role] > 0 ? formatPerSession(row[role]) : '—'}
                  </Text>
                </Table.Td>
              ))}
              <Table.Td ta="right">
                <Text size="xs" fw={600} c={BRAND.textPrimary}>
                  {formatPerSession(row.total)}
                </Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
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
          ? 'Não foi possível calcular a ativação agora.'
          : 'Adicione exercícios aos slots para ver a ativação por participação.'}
      </Text>
    );
  }

  return (
    <FloatingPanel title={TITLE} index={index} controls={controls} withinPortal={withinPortal}>
      <Tooltip label={UNIT_HINT} multiline w={280} openDelay={400}>
        <Text size="xs" c="dimmed" mb={4} style={{ cursor: 'help' }}>
          Séries equivalentes/semana por participação
        </Text>
      </Tooltip>
      {body}
    </FloatingPanel>
  );
}
