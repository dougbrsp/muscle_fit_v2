'use client';

import { useState, type ReactNode } from 'react';
import {
  PresentationProvider,
  type PeriodComparisonRow,
  type PresentationSlots,
  type RecordListRow,
} from '@/lib/presentation-slots';
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

/**
 * Comparativo por período (T-3..T-0, S-3..S-0) em lista: o período mais recente
 * é o dado principal da linha; o toque abre todos os períodos e a evolução.
 */
function PeriodComparison({
  groupingLabel,
  periodLabels,
  metricLabels,
  rows,
  emptyText,
}: {
  groupingLabel: string;
  periodLabels: string[];
  metricLabels: string[];
  rows: PeriodComparisonRow[];
  emptyText: string;
}) {
  const [openKey, setOpenKey] = useState<string | null>(null);
  const selected = rows.find((row) => row.key === openKey) ?? null;
  if (rows.length === 0) return emptyText ? <EmptyState title={emptyText} /> : null;
  const last = periodLabels.length - 1;
  return (
    <>
      <ListGroup aria-label={groupingLabel}>
        {rows.map((row) => (
          <ListRow
            key={row.key}
            title={row.label}
            subtitle={`${periodLabels[last]} · ${metricLabels[0]}`}
            value={row.periods[last]?.[0]}
            onClick={() => setOpenKey(row.key)}
          />
        ))}
      </ListGroup>
      <BottomSheet opened={selected !== null} onClose={() => setOpenKey(null)} title={groupingLabel}>
        {selected ? (
          <>
            <ListGroup>
              <ListRow title={selected.label} />
            </ListGroup>
            {periodLabels.map((period, p) => (
              <ListGroup key={period} title={period}>
                {metricLabels.map((metric, m) => (
                  <ListRow key={metric} title={metric} value={selected.periods[p]?.[m]} />
                ))}
              </ListGroup>
            ))}
            {selected.evolution ? (
              <ListGroup title="Evolução">
                <ListRow title="Tendência" trailing={selected.evolution} />
              </ListGroup>
            ) : null}
            {selected.toggle ? (
              <SheetButton
                variant="light"
                color="brand"
                onClick={() => {
                  selected.toggle?.onToggle();
                  setOpenKey(null);
                }}
              >
                {selected.toggle.expanded ? 'Recolher equipamentos' : 'Expandir equipamentos'}
              </SheetButton>
            ) : null}
          </>
        ) : null}
      </BottomSheet>
    </>
  );
}

const SLOTS: Partial<PresentationSlots> = {
  recordList: RecordList,
  periodComparison: PeriodComparison,
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
