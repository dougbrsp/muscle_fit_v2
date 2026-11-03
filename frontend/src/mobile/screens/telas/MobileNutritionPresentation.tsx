'use client';

import { Badge, Button, Group, Text } from '@mantine/core';
import { IconDownload, IconStar, IconStarFilled } from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { quantidade } from '@/components/nutrition/MealCard';
import { trainingText } from '@/components/nutrition/WeekTargetsTable';
import {
  FOOD_PER_100_FIELDS,
  FOOD_REQUIRED_FIELDS,
  FOOD_SOURCE_OPTIONS,
  ITEM_NUTRIENT_FIELDS,
  USDA_DATA_TYPE_OPTIONS,
  nutritionLabel,
} from '@/lib/nutrition-constants';
import { formatGrams, formatKcal, formatWithUnit, weekdayShort } from '@/lib/nutrition-format';
import { formatDateBR } from '@/lib/physical-indicators-types';
import { PresentationProvider, type PresentationSlots } from '@/lib/presentation-slots';
import { ListBlock, ListGroup, ListRow, ScreenHeader } from '../../ui';
import { NutricaoNav, type NutricaoSecao } from './NutricaoNav';
import { PortalFieldsStyle } from './PortalFieldsStyle';

const FOOD_COLUMNS = FOOD_PER_100_FIELDS.filter((campo) => FOOD_REQUIRED_FIELDS.includes(campo.code));

function per100Summary(per100: Record<string, number | null | undefined>): string {
  return FOOD_COLUMNS.map((campo) => `${campo.label} ${formatWithUnit(per100[campo.code] as number | null, campo.unit, 1)}`).join(' · ');
}

const SLOTS: Partial<PresentationSlots> = {
  nutritionHeader: ({ active, title, description }) => (
    <>
      <ScreenHeader title={title} subtitle={typeof description === 'string' ? description : undefined} />
      <NutricaoNav active={active as NutricaoSecao} />
    </>
  ),

  weekTargets: ({ rows, today }) => (
    <ListGroup title="Metas dos 7 dias">
      {rows.map(({ local_date, target }) => {
        const hoje = local_date === today;
        const dia = `${weekdayShort(local_date)} ${formatDateBR(local_date).slice(0, 5)}${hoje ? ' (hoje)' : ''}`;
        if (!target) return <ListRow key={local_date} title={dia} subtitle="Sem meta para este dia." />;
        const avisos = target.limitations.length > 0 ? target.limitations.map((i) => i.label).join(' ') : null;
        return (
          <ListRow
            key={local_date}
            title={dia}
            subtitle={[
              trainingText(target),
              `Proteína ${formatGrams(target.protein_g)} · Carboidrato ${formatGrams(target.carbs_g)} · Gordura ${formatGrams(target.fat_g)} · Fibra ${formatGrams(target.fiber_g)}`,
              target.frozen ? 'Meta congelada' : null,
              avisos,
            ]
              .filter(Boolean)
              .join(' | ')}
            value={formatKcal(target.kcal)}
          />
        );
      })}
    </ListGroup>
  ),

  mealItems: ({ items }) => (
    <ListGroup title="Itens">
      {items.map((item) => (
        <ListRow
          key={item.id ?? `novo-${item.position}`}
          title={item.label}
          subtitle={[
            quantidade(item),
            ITEM_NUTRIENT_FIELDS.slice(1)
              .map((campo) => `${campo.label} ${formatWithUnit(item.nutrients[campo.code], campo.unit, 1)}`)
              .join(' · '),
            item.limitations.map((l) => l.label).join(' '),
          ]
            .filter(Boolean)
            .join(' | ')}
          value={formatWithUnit(item.nutrients[ITEM_NUTRIENT_FIELDS[0].code], ITEM_NUTRIENT_FIELDS[0].unit, 0)}
        />
      ))}
    </ListGroup>
  ),

  foodResults: ({ items, selectedId, withStar, onSelect, onToggleFavorite, togglingId }) => (
    <ListGroup title="Resultados">
      {items.map((item) => (
        <div key={item.id} style={{ display: 'flex', alignItems: 'stretch' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <ListRow
              title={item.name}
              subtitle={[
                [item.brand, item.category].filter(Boolean).join(' · '),
                `${nutritionLabel(FOOD_SOURCE_OPTIONS, item.source)}${item.is_public ? '' : item.is_own ? ' · Meu' : ' · Privado'}`,
                per100Summary(item.per_100),
                item.citation ?? 'Cadastro sem citação de tabela',
              ]
                .filter(Boolean)
                .join(' | ')}
              current={item.id === selectedId}
              onClick={() => onSelect(item)}
            />
          </div>
          {withStar ? (
            <button
              type="button"
              aria-label={item.is_favorite ? `Remover ${item.name} dos preferidos` : `Favoritar ${item.name}`}
              aria-pressed={item.is_favorite}
              disabled={togglingId === item.id}
              onClick={() => onToggleFavorite(item)}
              style={{ minWidth: 48, background: 'none', border: 0, color: item.is_favorite ? '#fab005' : 'inherit', cursor: 'pointer' }}
            >
              {item.is_favorite ? <IconStarFilled size={20} /> : <IconStar size={20} />}
            </button>
          ) : null}
        </div>
      ))}
    </ListGroup>
  ),

  usdaResults: ({ items, importing, importingId, onImport }) => (
    <ListGroup title="Resultados do USDA">
      {items.map((item) => (
        <ListBlock key={item.fdc_id}>
          <Text fw={600} lang="en">
            {item.name}
          </Text>
          <Group gap="xs" my={4}>
            <Badge size="sm" variant="light" color="gray">
              {nutritionLabel(USDA_DATA_TYPE_OPTIONS, item.data_type)}
            </Badge>
            <Text size="xs" c="dimmed">
              {[item.category, `FDC ID ${item.fdc_id}`].filter(Boolean).join(' · ')}
            </Text>
          </Group>
          <Text size="sm" c="dimmed" mb="xs">
            {per100Summary(item.per_100)}
          </Text>
          <Button
            variant="light"
            color="brand"
            fullWidth
            mih={44}
            leftSection={<IconDownload size={14} />}
            loading={importing && importingId === item.fdc_id}
            disabled={importing && importingId !== item.fdc_id}
            onClick={() => onImport(item.fdc_id)}
          >
            Importar
          </Button>
        </ListBlock>
      ))}
    </ListGroup>
  ),

  portions: ({ portions, basis }) => (
    <ListGroup>
      {portions.map((porcao) => (
        <ListRow
          key={porcao.id}
          title={porcao.label}
          subtitle={porcao.is_public ? 'Pública' : 'Minha'}
          value={formatWithUnit(porcao.amount, basis)}
        />
      ))}
    </ListGroup>
  ),

  checkinWeek: ({ rows }) => (
    <ListGroup>
      {rows.map((linha) => (
        <ListRow
          key={linha.label}
          title={linha.label}
          subtitle={[linha.reference, linha.note].filter(Boolean).join(' · ')}
          value={linha.week}
        />
      ))}
    </ListGroup>
  ),

  checkinMacros: ({ before, after, scopeLabel }) => {
    const fmt = (v: number | null, unit: string) => (v == null ? '-' : `${Math.round(v)} ${unit}`);
    const linhas = [
      { label: 'Energia', a: before.kcal, d: after.kcal, unit: 'kcal' },
      { label: 'Proteína', a: before.protein_g, d: after.protein_g, unit: 'g' },
      { label: 'Carboidrato', a: before.carb_g, d: after.carb_g, unit: 'g' },
      { label: 'Gordura', a: before.fat_g, d: after.fat_g, unit: 'g' },
    ];
    return (
      <ListGroup title={`${scopeLabel}: agora → proposta`}>
        {linhas.map((l) => (
          <ListRow key={l.label} title={l.label} value={`${fmt(l.a, l.unit)} → ${fmt(l.d, l.unit)}`} />
        ))}
      </ListGroup>
    );
  },
};

/**
 * Provê a apresentação em lista dos componentes de nutrição que no desktop
 * desenham tabela, e o estilo de 16 px para os campos de modais portalados.
 */
export function MobileNutritionPresentation({ children }: { children: ReactNode }) {
  return (
    <PresentationProvider value={SLOTS}>
      <PortalFieldsStyle />
      {children}
    </PresentationProvider>
  );
}
