'use client';

import { ActionIcon, Badge, Group, Stack, Text, Tooltip, UnstyledButton } from '@mantine/core';
import { IconChevronDown, IconChevronUp } from '@tabler/icons-react';
import type { CSSProperties } from 'react';
import { EquipmentPhoto } from '@/components/EquipmentPhoto';
import type { ItemActivationSummary } from '@/lib/item-activation-summary';
import { priorFatigueColor, priorFatigueLabel } from '@/lib/prior-fatigue';
import type { PriorFatigue } from '@/lib/workout-plan-templates-types';
import { BRAND } from '@/theme';
import { ItemActivationRoleLines } from '../ItemActivationLine';
import { DragHandle, type SortableHandle } from './sortable';

/**
 * Fundo do card de item no slot aberto, alternado pela posição (0-based) para
 * separar visualmente um card do seguinte — vale para o card aberto e para o
 * recolhido, nos dois modos e no cardio.
 */
export function itemCardBackground(itemIndex: number): string {
  return itemIndex % 2 === 0 ? BRAND.bgElevated : BRAND.bgSurface;
}

export function itemCardStyle(itemIndex: number): CSSProperties {
  // `relative`: âncora da faixa de fadiga prévia (`PriorFatigueStripe`).
  return { borderColor: BRAND.border, background: itemCardBackground(itemIndex), position: 'relative' };
}

/**
 * Faixa lateral do card com a fadiga prévia do item (verde = descansado →
 * vermelho = muito fatigado). Cor e rótulo vêm prontos do preview; sem
 * fadiga prévia (resposta antiga, cardio, item sem contribuição) não aparece.
 * Não é só cor: tooltip e `aria-label` dizem a faixa.
 */
export function PriorFatigueStripe({ priorFatigue }: { priorFatigue?: PriorFatigue | null }) {
  if (!priorFatigue) return null;
  const label = priorFatigueLabel(priorFatigue);
  return (
    <Tooltip label={label} openDelay={200} position="left" withArrow>
      <div
        role="img"
        aria-label={label}
        data-testid="prior-fatigue-stripe"
        data-band={priorFatigue.band}
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          bottom: 0,
          width: 6,
          background: priorFatigueColor(priorFatigue.level),
          borderTopLeftRadius: 'inherit',
          borderBottomLeftRadius: 'inherit',
        }}
      />
    </Tooltip>
  );
}

/**
 * Chevron de recolher/expandir o CARD (não confundir com o botão das
 * "Configurações avançadas", que é outro estado).
 */
export function ItemCollapseToggle({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const label = collapsed ? 'Expandir card' : 'Recolher card';
  return (
    <Tooltip label={label} openDelay={400}>
      <ActionIcon
        variant="subtle"
        color="gray"
        size="sm"
        onClick={onToggle}
        aria-label={label}
        aria-expanded={!collapsed}
        style={{ flexShrink: 0 }}
      >
        {collapsed ? <IconChevronDown size={14} /> : <IconChevronUp size={14} />}
      </ActionIcon>
    </Tooltip>
  );
}

type ItemCollapsedSummaryProps = {
  // Posição 0-based no slot; numeração 1-based, como no card aberto.
  itemIndex: number;
  dragHandle?: SortableHandle;
  // Linha 1: nome do grupo muscular (ou "Cardio").
  title: string;
  // Nome do exercício escolhido (modo equipamento). Ausente = não há.
  exercise?: string | null;
  // Linha do equipamento (ou o que fizer as vezes dele no item). No cardio,
  // o resumo pronto do dono (`describe_short`, M98 C1: "HIIT 8× 30 s Z4 /
  // 90 s Z2 · 5+5 min").
  detail: string;
  // `detail` é um texto neutro ("não escolhido"): sai esmaecido.
  detailMuted?: boolean;
  // Miniatura do equipamento (mesma foto/lightbox do card aberto). Ausente =
  // item sem equipamento escolhido ou que não tem equipamento.
  photo?: { url: string | null; alt: string; onOpen: () => void } | null;
  activation?: ItemActivationSummary | null;
  // Rótulo (já resolvido pelo catálogo de técnicas) da técnica avançada
  // aplicada ao item. Ausente/null = sem técnica.
  techniqueLabel?: string | null;
  // Quantidade de séries de aquecimento / de ajuste configuradas no item, como
  // estão no Modelo (sem conta nenhuma). Ausente/null/0 = não exibe.
  warmupSets?: number | null;
  feelerSets?: number | null;
  // Medida da série fora de reps, já formatada pelo espelho do dono
  // ("3 × 60 s"; `itemMeasureSummary`). Ausente/null = reps (não exibe).
  measureText?: string | null;
  onExpand: () => void;
};

// O recolhido mostra só o papel principal; o card aberto segue com os três.
const COLLAPSED_ACTIVATION_ROLES = ['primary'] as const;

function hasCount(value: number | null | undefined): value is number {
  return value != null && value > 0;
}

/**
 * Card de item RECOLHIDO no slot aberto: só o necessário para organizar e
 * arrastar — alça (o mesmo `handle` do dnd-kit), número, foto, grupo,
 * exercício e equipamento, as séries equivalentes do grupo principal (os
 * números que o card aberto já mostra; nenhuma chamada nova) e, quando
 * houver, a técnica avançada e as séries de aquecimento e de ajuste.
 */
export function ItemCollapsedSummary({
  itemIndex,
  dragHandle,
  title,
  exercise,
  detail,
  detailMuted,
  photo,
  activation,
  techniqueLabel,
  warmupSets,
  feelerSets,
  measureText,
  onExpand,
}: ItemCollapsedSummaryProps) {
  const showWarmup = hasCount(warmupSets);
  const showFeeler = hasCount(feelerSets);
  const hasExtras = !!measureText || !!techniqueLabel || showWarmup || showFeeler;
  return (
    <Stack gap={4} data-testid="item-collapsed-summary">
      <Group gap="xs" wrap="nowrap" align="center">
        <DragHandle handle={dragHandle} label="Arrastar para reordenar" />
        <Badge size="sm" circle variant="light" color="gray" aria-label={`Exercício número ${itemIndex + 1} do slot`}>
          {itemIndex + 1}
        </Badge>
        {photo && (
          <UnstyledButton
            onClick={() => photo.url && photo.onOpen()}
            disabled={!photo.url}
            aria-label="Ampliar foto do equipamento"
            style={{ cursor: photo.url ? 'pointer' : 'default', flexShrink: 0 }}
          >
            <EquipmentPhoto photoUrl={photo.url} alt={photo.alt} size={36} />
          </UnstyledButton>
        )}
        <Text
          size="sm"
          fw={600}
          c={BRAND.textPrimary}
          style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}
          data-testid="item-collapsed-title"
        >
          {title}
        </Text>
        <ItemCollapseToggle collapsed onToggle={onExpand} />
      </Group>
      {exercise && (
        <Text size="xs" fw={500} c={BRAND.textPrimary} truncate="end" title={exercise} data-testid="item-collapsed-exercise">
          {exercise}
        </Text>
      )}
      <Text
        size="xs"
        c={detailMuted ? 'dimmed' : BRAND.textMuted}
        truncate="end"
        title={detail}
        data-testid="item-collapsed-detail"
      >
        {detail}
      </Text>
      <ItemActivationRoleLines summary={activation} roles={COLLAPSED_ACTIVATION_ROLES} />
      {hasExtras && (
        <Group gap={4} wrap="wrap" data-testid="item-collapsed-extras">
          {measureText && (
            <Badge size="xs" variant="light" color="teal" maw="100%" data-testid="item-collapsed-measure">
              {measureText}
            </Badge>
          )}
          {techniqueLabel && (
            <Badge
              size="xs"
              variant="light"
              color="grape"
              maw="100%"
              title={`Técnica avançada: ${techniqueLabel}`}
              data-testid="item-collapsed-technique"
            >
              {techniqueLabel}
            </Badge>
          )}
          {showWarmup && (
            <Badge size="xs" variant="light" color="yellow" data-testid="item-collapsed-warmup">
              Aquecimento: {warmupSets}
            </Badge>
          )}
          {showFeeler && (
            <Badge size="xs" variant="light" color="cyan" data-testid="item-collapsed-feeler">
              Ajuste: {feelerSets}
            </Badge>
          )}
        </Group>
      )}
    </Stack>
  );
}
