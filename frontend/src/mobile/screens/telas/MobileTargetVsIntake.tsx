'use client';

import { Group, Progress, Stack, Text } from '@mantine/core';
import { NutritionLimitations } from '@/components/nutrition/NutritionLimitations';
import { macroRows, ringParts } from '@/lib/nutrition-dashboard';
import { nutrientField, nutritionNotice } from '@/lib/nutrition-constants';
import { formatKcal, formatSignedKcal, formatTotal, formatWithUnit, shareToPercent } from '@/lib/nutrition-format';
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

// Largura máxima da barra (100% da meta). Só apresentação: a fração e as partes vêm do motor.
const BARRA_CHEIA = 100;

/**
 * Energia e macros com gráfico de barra consumido × restante. Consumido, restante e excesso
 * vêm do motor (`BalanceRow`); o excesso aparece em texto ("Acima da meta"), não só pela cor.
 * Sem meta só o consumido; sem refeição o consumido é zero e o restante é a meta.
 */
function MacroBar({ row }: { row: BalanceRow }) {
  const campo = nutrientField(row.field);
  const unidade = campo?.unit ?? '';
  const nome = campo?.label ?? row.field;
  const consumido = row.intake ?? row.within_target;
  const acima = row.excess != null && row.excess > 0;
  const percentual = shareToPercent(row.share);
  const temMeta = row.target != null;
  // Mesmo critério do gráfico do desktop: sem as partes do motor (sem meta ou total incompleto) não há barra.
  const temGrafico = ringParts(row) !== null;
  const consumidoTexto = consumido == null ? '-' : formatTotal(consumido, unidade, row.intake == null || row.intake_complete);
  return (
    <ListBlock>
      <Stack gap={4}>
        <Group justify="space-between" wrap="nowrap" align="baseline">
          <Text fw={600}>{nome}</Text>
          <Text size="sm">Consumido: <b>{consumidoTexto}</b></Text>
        </Group>
        {!temGrafico ? (
          <Text size="sm" c="dimmed">
            Sem gráfico: falta meta ou o total do dia está incompleto.
          </Text>
        ) : null}
        {temMeta ? (
          <>
            {temGrafico ? (
              <Progress
                value={acima ? BARRA_CHEIA : Math.min(percentual ?? 0, BARRA_CHEIA)}
                // A tela não julga passar da meta (como no desktop): mesma cor, o excesso vai em texto.
                color="brand"
                size="md"
                aria-label={`${nome}: ${consumidoTexto} de ${formatWithUnit(row.target, unidade, 0)}${acima ? ', acima da meta' : ''}`}
              />
            ) : null}
            <Text size="sm" c="dimmed">
              Meta {formatWithUnit(row.target, unidade, 0)}
              {acima
                ? ` · Acima da meta: ${formatWithUnit(row.excess, unidade, 0)}`
                : row.to_consume != null
                  ? ` · Restante: ${formatWithUnit(row.to_consume, unidade, 0)}`
                  : ''}
            </Text>
          </>
        ) : (
          <Text size="sm" c="dimmed">
            Sem meta para este dia.
          </Text>
        )}
      </Stack>
    </ListBlock>
  );
}

/**
 * Meta do dia × ingestão no celular (slot `targetVsIntake` do cartão do desktop):
 * energia e macros em barra consumido × restante (excesso em texto); fibra em linha, só o que tem valor.
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
            {rows.map((row) =>
              macros.includes(row) ? <MacroBar key={row.field} row={row} /> : <BalanceItem key={row.field} row={row} />,
            )}
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
