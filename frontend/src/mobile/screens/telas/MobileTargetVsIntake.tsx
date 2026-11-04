'use client';

import { Text } from '@mantine/core';
import { NutritionLimitations } from '@/components/nutrition/NutritionLimitations';
import { macroRows } from '@/lib/nutrition-dashboard';
import { nutrientField, nutritionNotice } from '@/lib/nutrition-constants';
import { formatKcal, formatSignedKcal, formatTotal, formatWithUnit } from '@/lib/nutrition-format';
import type { BalanceRow, NutritionDay } from '@/lib/nutrition-types';
import { ListBlock, ListGroup, ListRow } from '../../ui';

function BalanceItem({ row }: { row: BalanceRow }) {
  const campo = nutrientField(row.field);
  const unidade = campo?.unit ?? '';
  const consumido = row.intake ?? row.within_target;
  const partes: string[] = [];
  if (row.target != null) partes.push(`Meta ${formatWithUnit(row.target, unidade, 0)}`);
  if (row.excess != null && row.excess > 0) partes.push(`Acima ${formatWithUnit(row.excess, unidade, 0)}`);
  else if (row.to_consume != null) partes.push(`Falta ${formatWithUnit(row.to_consume, unidade, 0)}`);
  return (
    <ListRow
      title={campo?.label ?? row.field}
      subtitle={partes.length > 0 ? partes.join(' · ') : undefined}
      value={consumido == null ? undefined : formatTotal(consumido, unidade, row.intake == null || row.intake_complete)}
    />
  );
}

/**
 * Meta do dia × ingestão no celular (slot `targetVsIntake` do cartão do desktop):
 * uma linha por nutriente com consumido, meta e falta/excesso, e só o que tem valor.
 * Sem dado nenhum, um estado vazio único em vez de uma fileira de traços. Todos os
 * números vêm do motor (`day.balance`); aqui só se apresenta.
 */
export function MobileTargetVsIntake({ day }: { day: NutritionDay }) {
  const meta = day.target;
  const macros = macroRows(day.balance.rows);
  const rows = [...macros, ...day.balance.rows.filter((row) => !macros.includes(row))].filter(
    (row) => row.target != null || row.intake != null || row.within_target != null,
  );
  const gasto = day.balance.tdee_kcal;
  const balanco = day.balance.energy_balance_kcal;
  const vazio = rows.length === 0 && gasto == null && balanco == null;
  const avisos = [meta?.frozen ? nutritionNotice('target_frozen') : null, meta?.backfilled ? nutritionNotice('target_backfilled') : null]
    .filter(Boolean)
    .join(' ');
  return (
    <>
      <ListGroup title="Meta × ingestão" footer={avisos || undefined}>
        {vazio ? (
          <ListBlock>
            <Text size="sm" c="dimmed">
              Sem meta nem refeições neste dia.
            </Text>
          </ListBlock>
        ) : (
          <>
            {rows.map((row) => (
              <BalanceItem key={row.field} row={row} />
            ))}
            {gasto != null ? <ListRow title="Gasto do dia (TDEE)" value={formatKcal(gasto)} /> : null}
            {balanco != null ? <ListRow title="Balanço (ingestão − gasto)" value={formatSignedKcal(balanco)} /> : null}
          </>
        )}
      </ListGroup>
      <NutritionLimitations items={day.target_notices} title="Meta do dia" tone="info" />
      <NutritionLimitations items={day.balance.limitations} title="Sobre o balanço" tone="info" />
      {meta ? <NutritionLimitations items={meta.limitations} title="Sobre a meta" tone="info" /> : null}
    </>
  );
}
