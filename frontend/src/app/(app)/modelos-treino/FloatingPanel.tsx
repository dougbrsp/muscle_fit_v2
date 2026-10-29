'use client';

import { ActionIcon, Group, Paper, Portal, ScrollArea, Text, Tooltip } from '@mantine/core';
import { useElementSize, useViewportSize } from '@mantine/hooks';
import { IconWindowMaximize, IconWindowMinimize, IconX } from '@tabler/icons-react';
import { useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode, type RefObject } from 'react';
import { clampPosition, defaultPosition, type PanelPosition } from '@/lib/floating-panel-geometry';
import { BRAND } from '@/theme';
import type { FloatingPanelControls } from './useFloatingPanels';

/** Abaixo do Modal do Mantine (200) e acima do header do AppShell (100). */
export const FLOATING_PANEL_Z_INDEX = 150;
const DEFAULT_WIDTH = 300;
/** Altura estimada antes da primeira medição (só a barra de título). */
const FALLBACK_HEIGHT = 36;

export type FloatingPanelProps = {
  title: string;
  /** Ordem na posição inicial (0 = mais à direita). */
  index: number;
  controls: FloatingPanelControls;
  width?: number;
  /** `false` só em teste (o SSR não monta `Portal`). */
  withinPortal?: boolean;
  children: ReactNode;
};

type DragState = { offsetX: number; offsetY: number; last: PanelPosition };

/**
 * Fase 7 (Frente A) — janela flutuante genérica do builder: `Paper` fixo,
 * arrastável pela barra de título (pointer events, sem dependência nova),
 * com minimizar e fechar. A posição fica sempre dentro da viewport e abaixo
 * do header (`clampPosition`), inclusive quando a janela do navegador encolhe.
 * Durante o arrasto a posição é local; ao soltar, sobe para `controls.onMove`
 * (que persiste).
 */
export function FloatingPanel({
  title,
  index,
  controls,
  width = DEFAULT_WIDTH,
  withinPortal = true,
  children,
}: FloatingPanelProps) {
  const { state, onMove, onToggleMinimized, onClose } = controls;
  const { ref, height } = useElementSize<HTMLDivElement>();
  const viewport = useViewportSize();
  const [dragPosition, setDragPosition] = useState<PanelPosition | null>(null);
  const drag = useRef<DragState | null>(null);

  const size = { width, height: height || FALLBACK_HEIGHT };
  const viewportKnown = viewport.width > 0 && viewport.height > 0;
  const position = viewportKnown
    ? (dragPosition ?? clampPosition(state.position ?? defaultPosition(index, size, viewport), size, viewport))
    : { x: 0, y: 0 };

  function handlePointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    // Clique nos botões da barra não inicia arrasto.
    if ((event.target as HTMLElement).closest('button')) return;
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const start = { x: rect.left, y: rect.top };
    drag.current = { offsetX: event.clientX - rect.left, offsetY: event.clientY - rect.top, last: start };
    setDragPosition(start);
  }

  function handlePointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const current = drag.current;
    if (!current) return;
    const rect = ref.current?.getBoundingClientRect();
    const next = clampPosition(
      { x: event.clientX - current.offsetX, y: event.clientY - current.offsetY },
      { width: rect?.width ?? size.width, height: rect?.height ?? size.height },
      { width: window.innerWidth, height: window.innerHeight },
    );
    current.last = next;
    setDragPosition(next);
  }

  function handlePointerEnd(event: ReactPointerEvent<HTMLDivElement>) {
    const current = drag.current;
    if (!current) return;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    onMove(current.last);
    setDragPosition(null);
  }

  const panel = (
    <Paper
      // `useElementSize` tipa o ref como `RefObject<T | null>` (React 19); o Paper quer `Ref<T>`.
      ref={ref as RefObject<HTMLDivElement>}
      withBorder
      shadow="md"
      radius="md"
      role="region"
      aria-label={title}
      data-floating-panel={title}
      style={{
        position: 'fixed',
        left: position.x,
        top: position.y,
        width,
        zIndex: FLOATING_PANEL_Z_INDEX,
        background: BRAND.bgElevated,
        borderColor: BRAND.border,
        visibility: viewportKnown ? 'visible' : 'hidden',
        overflow: 'hidden',
      }}
    >
      <Group
        justify="space-between"
        wrap="nowrap"
        gap={4}
        px="xs"
        py={4}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerEnd}
        onPointerCancel={handlePointerEnd}
        style={{
          cursor: dragPosition ? 'grabbing' : 'grab',
          touchAction: 'none',
          userSelect: 'none',
          borderBottom: state.minimized ? undefined : `1px solid ${BRAND.border}`,
        }}
      >
        <Text size="xs" fw={600} c={BRAND.textPrimary} truncate>
          {title}
        </Text>
        <Group gap={2} wrap="nowrap">
          <Tooltip label={state.minimized ? 'Restaurar' : 'Minimizar'} openDelay={400}>
            <ActionIcon
              size="sm"
              variant="subtle"
              color="gray"
              onClick={onToggleMinimized}
              aria-label={state.minimized ? `Restaurar ${title}` : `Minimizar ${title}`}
            >
              {state.minimized ? <IconWindowMaximize size={14} /> : <IconWindowMinimize size={14} />}
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Fechar (reabre pelo botão Janelas)" openDelay={400}>
            <ActionIcon size="sm" variant="subtle" color="gray" onClick={onClose} aria-label={`Fechar ${title}`}>
              <IconX size={14} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>
      {!state.minimized && (
        <ScrollArea.Autosize mah={360} type="auto">
          <div style={{ padding: 'var(--mantine-spacing-xs)' }}>{children}</div>
        </ScrollArea.Autosize>
      )}
    </Paper>
  );

  return withinPortal ? <Portal>{panel}</Portal> : panel;
}
