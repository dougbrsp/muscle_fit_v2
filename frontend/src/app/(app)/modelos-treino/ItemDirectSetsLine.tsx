'use client';

import { Group, Text } from '@mantine/core';
import { Fragment, memo } from 'react';
import type { ItemDirectSetsShareLine } from '@/lib/methodology-card-share';
import { TECHNIQUE_EQUIVALENT_HINT } from '@/lib/methodology-volume';
import { DEFAULT_COUNTING_MODE, setsUnitOf } from '@/lib/volume-counting-mode';
import { BRAND } from '@/theme';

type Props = {
  /** Linhas prontas de `buildItemDirectSetsShareLines`; vazio = nada a mostrar. */
  lines: ItemDirectSetsShareLine[];
  /** Primeira avaliação ainda carregando (sem dado anterior): placeholder discreto. */
  pending?: boolean;
  /**
   * Fase 5 (PR 5): "4 séries + 0,8 equiv. (rest-pause ×2)" — de onde vem o
   * número do card quando a técnica soma equivalente (`formatItemPlannedSetsNote`).
   */
  techniqueNote?: string | null;
  /** Lote 3: unidade do modo de contagem ("séries diretas" / "séries ponderadas"). */
  unitLabel?: string;
};

/**
 * M43 — participação por séries diretas de cada card do Modo B, abaixo da
 * linha de ativação ponderada (M11): `Quadríceps: 3/9/12 <ícone>` = séries
 * deste exercício / séries diretas do grupo neste dia / no Modelo (ciclo).
 *
 * O ícone é o status COMBINADO do bloco no Modelo inteiro (Fase 6: séries e
 * exercícios por sessão + meta semanal do macro-grupo) pelo domínio unificado
 * `methodologyVolume` (cinza abaixo, verde na faixa, laranja acima); cor nunca
 * é o único sinal — o ícone difere por estado e leva `aria-label`. Sem status
 * (`NOT_EVALUABLE`) não há ícone e o motivo fica no `title`. Em erro do
 * preview quem chama passa lista vazia: o card nunca é bloqueado.
 */
function ItemDirectSetsLineImpl({
  lines,
  pending = false,
  techniqueNote = null,
  unitLabel = setsUnitOf(DEFAULT_COUNTING_MODE).plural,
}: Props) {
  const label = `${unitLabel.charAt(0).toUpperCase()}${unitLabel.slice(1)}`;
  if (lines.length === 0) {
    if (!pending) return null;
    return (
      <Text size="xs" c={BRAND.textMuted} mt={4} aria-live="polite" data-testid="item-direct-sets-pending">
        {label}: calculando…
      </Text>
    );
  }

  return (
    <Group gap={6} wrap="wrap" mt={4} style={{ rowGap: 2 }} data-testid="item-direct-sets-line">
      <Text size="xs" c={BRAND.textMuted}>
        {label}
        {techniqueNote ? (
          <Text
            span
            size="xs"
            c={BRAND.textMuted}
            title={TECHNIQUE_EQUIVALENT_HINT}
            style={{ cursor: 'help' }}
            data-testid="item-direct-sets-technique"
          >
            {' '}
            — {techniqueNote}
          </Text>
        ) : null}
        :
      </Text>
      {lines.map((line, index) => {
        const StatusIcon = line.status?.Icon;
        return (
          <Fragment key={line.key}>
            {index > 0 && (
              <Text size="xs" c="dimmed" aria-hidden>
                |
              </Text>
            )}
            <Group gap={4} wrap="nowrap" title={line.title} style={{ cursor: 'help' }} data-status={line.statusCode}>
              <Text size="xs" fw={600} c={BRAND.textPrimary}>
                {line.text}
              </Text>
              {StatusIcon && line.status ? (
                <StatusIcon
                  size={14}
                  role="img"
                  aria-label={line.ariaLabel}
                  style={{ color: `var(--mantine-color-${line.status.color}-6)`, flexShrink: 0 }}
                />
              ) : (
                <Text size="xs" c={BRAND.textMuted} aria-label={line.ariaLabel}>
                  (sem status)
                </Text>
              )}
            </Group>
          </Fragment>
        );
      })}
    </Group>
  );
}

export const ItemDirectSetsLine = memo(ItemDirectSetsLineImpl);
