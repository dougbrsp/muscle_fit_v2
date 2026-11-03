'use client';

import { useState, type ReactNode } from 'react';
import { PresentationProvider, type PresentationSlots, type RecordListRow } from '@/lib/presentation-slots';
import { BottomSheet, EmptyState, ListGroup, ListRow, ScreenHeader } from '../../ui';
import { SheetButton } from './common';
import { PortalFieldsStyle } from './PortalFieldsStyle';

/**
 * Lista de registros no lugar da tabela (slot `recordList`): 1 dado principal por
 * linha, detalhe completo (as colunas do desktop) e Editar/Excluir por toque.
 */
function RecordList({ label, rows, emptyText }: { label: string; rows: RecordListRow[]; emptyText: string }) {
  const [openKey, setOpenKey] = useState<string | number | null>(null);
  const selected = rows.find((row) => row.key === openKey) ?? null;
  if (rows.length === 0) return <EmptyState title={emptyText} />;
  return (
    <>
      <ListGroup aria-label={label}>
        {rows.map((row) => (
          <ListRow key={row.key} title={row.title} subtitle={row.subtitle} value={row.value} onClick={() => setOpenKey(row.key)} />
        ))}
      </ListGroup>
      <BottomSheet
        opened={selected !== null}
        onClose={() => setOpenKey(null)}
        title={selected?.title ?? label}
        footer={
          selected && (selected.onEdit || selected.onDelete) ? (
            <>
              {selected.onDelete ? (
                <SheetButton
                  variant="default"
                  color="red"
                  loading={selected.deleting}
                  onClick={() => {
                    selected.onDelete?.();
                    setOpenKey(null);
                  }}
                >
                  Excluir
                </SheetButton>
              ) : null}
              {selected.onEdit ? (
                <SheetButton
                  color="brand"
                  onClick={() => {
                    selected.onEdit?.();
                    setOpenKey(null);
                  }}
                >
                  Editar
                </SheetButton>
              ) : null}
            </>
          ) : undefined
        }
      >
        {selected ? (
          <ListGroup>
            {selected.details.map(([k, v]) => (
              <ListRow key={k} title={k} value={v} />
            ))}
          </ListGroup>
        ) : null}
      </BottomSheet>
    </>
  );
}

const SLOTS: Partial<PresentationSlots> = {
  recordList: RecordList,
  pageHeader: ({ title, description }) => <ScreenHeader title={title} subtitle={typeof description === 'string' ? description : undefined} />,
};

export function MobileRecordsPresentation({ children }: { children: ReactNode }) {
  return (
    <PresentationProvider value={SLOTS}>
      <PortalFieldsStyle />
      {children}
    </PresentationProvider>
  );
}
