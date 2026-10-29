'use client';

import { Group, Stack, Text, Tooltip } from '@mantine/core';
import { IconLink, IconShield, IconTarget } from '@tabler/icons-react';
import { Fragment, memo, type ReactNode } from 'react';
import {
  ACTIVATION_ROLE_ORDER,
  formatItemActivationLine,
  type ItemActivationSummary,
} from '@/lib/item-activation-summary';
import type { ActivationRole } from '@/lib/workout-plan-templates-types';

// Cores discretas por papel. `*-light-color` é a variável do Mantine que se
// ajusta ao esquema claro/escuro. Exportado (Fase 7) para a janela
// "Ativação por participação" usar os mesmos ícones e cores.
export const ROLE_STYLE: Record<ActivationRole, { color: string; icon: ReactNode; label: string }> = {
  primary: {
    color: 'var(--mantine-color-orange-light-color)',
    icon: <IconTarget size={12} />,
    label: 'Principal',
  },
  secondary: {
    color: 'var(--mantine-color-blue-light-color)',
    icon: <IconLink size={12} />,
    label: 'Secundários',
  },
  stabilizer: {
    color: 'var(--mantine-color-gray-light-color)',
    icon: <IconShield size={12} />,
    label: 'Estabilizadores',
  },
};

const UNIT_HINT =
  'Séries equivalentes (séries × peso de estímulo) que ESTA linha entrega ao grupo / total do grupo somando todos os slots da janela do preview — a mesma conta da tabela "Preview de ativação semanal". Atualiza junto com a tabela.';

/**
 * M11 — linha compacta abaixo de cada item do builder: quanto este item
 * entrega a cada grupo vs. o total do grupo no ciclo. Sem `summary` (item sem
 * exercício/mapping, sem resposta do servidor ainda, ou estrutura mudando)
 * não renderiza nada — nunca mostra número inventado.
 */
function ItemActivationLineImpl({ summary }: { summary: ItemActivationSummary | null | undefined }) {
  if (!summary) return null;
  const roles = ACTIVATION_ROLE_ORDER.filter((role) => summary[role].length > 0);

  return (
    <Tooltip label={UNIT_HINT} multiline w={320} openDelay={400}>
      <Group
        gap={6}
        wrap="wrap"
        mt={6}
        aria-label={`Ativação deste item: ${formatItemActivationLine(summary)}`}
        style={{ rowGap: 2, cursor: 'help' }}
      >
        {roles.map((role, index) => {
          const style = ROLE_STYLE[role];
          return (
            <Fragment key={role}>
              {index > 0 && (
                <Text size="xs" c="dimmed" aria-hidden>
                  |
                </Text>
              )}
              <Group gap={4} wrap="wrap" style={{ rowGap: 2, color: style.color }}>
                <span title={style.label} style={{ display: 'inline-flex' }}>
                  {style.icon}
                </span>
                {role === 'stabilizer' && (
                  <Text size="xs" fw={600} c={style.color}>
                    Estabilizadores:
                  </Text>
                )}
                {summary[role].map((entry, entryIndex) => (
                  <Text key={entry.muscleGroup} size="xs" c={style.color} fw={role === 'primary' ? 600 : 400}>
                    {entryIndex > 0 ? '- ' : ''}
                    {entry.text}
                  </Text>
                ))}
              </Group>
            </Fragment>
          );
        })}
      </Group>
    </Tooltip>
  );
}

export const ItemActivationLine = memo(ItemActivationLineImpl);

/**
 * Variante vertical do M11 para o card RECOLHIDO do builder: um papel por
 * linha, com os mesmos ícones/cores de `ROLE_STYLE` e os mesmos números da
 * linha compacta. `roles` escolhe quais papéis aparecem (padrão: os três, na
 * ordem canônica). Papel sem grupo mostra "—". Sem `summary`, não renderiza
 * nada.
 */
function ItemActivationRoleLinesImpl({
  summary,
  roles = ACTIVATION_ROLE_ORDER,
}: {
  summary: ItemActivationSummary | null | undefined;
  roles?: readonly ActivationRole[];
}) {
  if (!summary) return null;
  const shownRoles = ACTIVATION_ROLE_ORDER.filter((role) => roles.includes(role));
  const ariaText =
    shownRoles.length === ACTIVATION_ROLE_ORDER.length
      ? formatItemActivationLine(summary)
      : shownRoles
          .map((role) => `${ROLE_STYLE[role].label}: ${summary[role].map((entry) => entry.text).join(' · ') || '—'}`)
          .join(' | ');
  return (
    <Tooltip label={UNIT_HINT} multiline w={320} openDelay={400}>
      <Stack gap={2} aria-label={`Ativação deste item: ${ariaText}`} style={{ cursor: 'help', minWidth: 0 }}>
        {shownRoles.map((role) => {
          const style = ROLE_STYLE[role];
          const entries = summary[role];
          return (
            <Group
              key={role}
              gap={4}
              wrap="nowrap"
              align="flex-start"
              data-role={role}
              style={{ color: style.color, minWidth: 0 }}
            >
              <span title={style.label} style={{ display: 'inline-flex', paddingTop: 2, flexShrink: 0 }}>
                {style.icon}
              </span>
              <Text size="xs" c={style.color} fw={role === 'primary' ? 600 : 400} style={{ overflowWrap: 'anywhere' }}>
                {style.label}: {entries.length > 0 ? entries.map((entry) => entry.text).join(' · ') : '—'}
              </Text>
            </Group>
          );
        })}
      </Stack>
    </Tooltip>
  );
}

export const ItemActivationRoleLines = memo(ItemActivationRoleLinesImpl);
