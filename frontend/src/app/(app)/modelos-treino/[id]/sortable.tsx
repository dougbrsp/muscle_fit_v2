'use client';

import { ActionIcon } from '@mantine/core';
import type { DraggableAttributes, DraggableSyntheticListeners } from '@dnd-kit/core';
import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { IconGripVertical } from '@tabler/icons-react';
import type { CSSProperties, ReactNode } from 'react';

// Arraste do builder de Modelo (`@dnd-kit`). Dois tipos de sortable no MESMO
// `DndContext` (no builder), distinguidos pelo `data`:
//   - 'slot': reordena o ciclo inteiro; só começa pela alça do cabeçalho;
//   - 'item': reordena itens DENTRO do slot `slotLocalId`. Soltar sobre outro
//     slot é ignorado — mover entre slots não é escopo.
export type SortableData = { type: 'slot' } | { type: 'item'; slotLocalId: string };

// Alça de arraste: o nó ativador + atributos/listeners do `useSortable`.
export type SortableHandle = {
  setActivatorNodeRef: (element: HTMLElement | null) => void;
  attributes: DraggableAttributes;
  listeners: DraggableSyntheticListeners;
};

export function DragHandle({ handle, label }: { handle?: SortableHandle; label: string }) {
  return (
    <ActionIcon
      variant="subtle"
      color="gray"
      size="sm"
      ref={handle?.setActivatorNodeRef}
      {...handle?.attributes}
      {...handle?.listeners}
      aria-label={label}
      style={{ cursor: 'grab', touchAction: 'none' }}
    >
      <IconGripVertical size={14} />
    </ActionIcon>
  );
}

/** Item arrastável dentro de um slot (alça própria, lista vertical). */
export function SortableItem({
  id,
  slotLocalId,
  children,
}: {
  id: string;
  slotLocalId: string;
  children: (handle: SortableHandle) => ReactNode;
}) {
  const data: SortableData = { type: 'item', slotLocalId };
  const { setNodeRef, setActivatorNodeRef, attributes, listeners, transform, transition, isDragging } = useSortable({
    id,
    data,
  });
  const style: CSSProperties = {
    transform: CSS.Translate.toString(transform),
    transition,
    position: 'relative',
    zIndex: isDragging ? 2 : undefined,
    opacity: isDragging ? 0.85 : undefined,
  };
  return (
    <div ref={setNodeRef} style={style}>
      {children({ setActivatorNodeRef, attributes, listeners })}
    </div>
  );
}
