'use client';

import { ActionIcon, Box, Container, Switch, Text } from '@mantine/core';
import { IconX } from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { BRAND } from '@/theme';
import { WORKOUT_MODE_CSS_VARS } from './ui/workout-mode-css-vars';

/**
 * Casca full-screen do `/modo-treino` (PR4 "uma tela"): barra fina no topo
 * ("Modo de treino" + toggle "IA por série" + sair, 44 px) com uma vaga
 * (`onHeaderSlot`) logo abaixo para a linha de progresso da execução — sem
 * card próprio —, e o conteúdo numa coluna flexível da altura da viewport
 * (`100dvh`), para o rodapé de ação de cada tela descer até o fim dela.
 */
export function WorkoutModeShell({
  aiToggle,
  onExit,
  onHeaderSlot,
  inSession,
  children,
}: {
  // Toggle "IA por série": só com sessão ativa.
  aiToggle?: { checked: boolean; onChange: (checked: boolean) => void } | null;
  onExit: () => void;
  // Vaga da linha de progresso (portal da tela de execução).
  onHeaderSlot?: (element: HTMLDivElement | null) => void;
  // Com sessão, o rodapé de cada tela já respeita a safe-area: sem respiro extra.
  inSession: boolean;
  children: ReactNode;
}) {
  return (
    <Box
      style={{
        ...WORKOUT_MODE_CSS_VARS,
        minHeight: '100dvh',
        display: 'flex',
        flexDirection: 'column',
        background: BRAND.bg,
      }}
    >
      <Box
        style={{
          position: 'sticky',
          top: 0,
          zIndex: 10,
          background: 'rgba(10,10,10,0.92)',
          backdropFilter: 'blur(10px)',
          borderBottom: `1px solid ${BRAND.border}`,
          paddingTop: 'env(safe-area-inset-top)',
        }}
      >
        <Container size="sm" px="md">
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, minHeight: 44 }}>
            <Text
              fw={800}
              fz={15}
              c={BRAND.textPrimary}
              className="brand-gradient-text"
              style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 }}
            >
              Modo de treino
            </Text>
            <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexShrink: 0 }}>
              {aiToggle && (
                <Switch
                  size="sm"
                  label="IA por série"
                  color="brand"
                  checked={aiToggle.checked}
                  onChange={(event) => aiToggle.onChange(event.currentTarget.checked)}
                  styles={{ label: { fontSize: 13, paddingLeft: 6, whiteSpace: 'nowrap' } }}
                />
              )}
              <ActionIcon size={44} radius="xl" variant="subtle" color="gray" aria-label="Sair do modo de treino" onClick={onExit}>
                <IconX size={20} />
              </ActionIcon>
            </div>
          </div>
          <div ref={onHeaderSlot} />
        </Container>
      </Box>

      <Container
        size="sm"
        px="md"
        w="100%"
        style={{
          flex: '1 1 auto',
          display: 'flex',
          flexDirection: 'column',
          paddingBottom: inSession ? 0 : 'calc(env(safe-area-inset-bottom) + 24px)',
        }}
      >
        {children}
      </Container>
    </Box>
  );
}
