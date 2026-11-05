'use client';

import { ActionIcon, Badge, Button, Group, Text, TextInput } from '@mantine/core';
import { IconChevronLeft, IconChevronRight, IconDownload, IconPlus, IconStar, IconStarFilled } from '@tabler/icons-react';
import type { ReactNode } from 'react';
import { quantidade } from '@/components/nutrition/MealCard';
import { trainingText } from '@/components/nutrition/WeekTargetsTable';
import {
  ENERGY_NUTRIENT,
  FOOD_PER_100_FIELDS,
  FOOD_REQUIRED_FIELDS,
  FOOD_SOURCE_OPTIONS,
  ITEM_NUTRIENT_FIELDS,
  USDA_DATA_TYPE_OPTIONS,
  nutritionLabel,
} from '@/lib/nutrition-constants';
import { formatDayLong, formatGrams, formatKcal, formatShareAsPercent, formatSignedKcal, formatWithUnit, shiftIsoDate, weekdayShort } from '@/lib/nutrition-format';
import { formatDateBR } from '@/lib/physical-indicators-types';
import { PresentationProvider, type PresentationSlots } from '@/lib/presentation-slots';
import { EmptyState, ListBlock, ListGroup, ListRow, ScreenHeader, StatGrid, StatTile, StickyAction } from '../../ui';
import { NutricaoNav, type NutricaoSecao } from './NutricaoNav';
import { MobileTargetVsIntake } from './MobileTargetVsIntake';
import { PortalFieldsStyle } from './PortalFieldsStyle';

const FOOD_COLUMNS = FOOD_PER_100_FIELDS.filter((campo) => FOOD_REQUIRED_FIELDS.includes(campo.code));

function per100Summary(per100: Record<string, number | null | undefined>): string {
  // Só o que a fonte informa: sem traço para valor ausente.
  return FOOD_COLUMNS.filter((campo) => per100[campo.code] != null)
    .map((campo) => `${campo.label} ${formatWithUnit(per100[campo.code] as number, campo.unit, 1)}`)
    .join(' · ');
}

const SLOTS: Partial<PresentationSlots> = {
  targetVsIntake: MobileTargetVsIntake,
  weekReport: ({ report }) => {
    const tiles: { label: string; value: string }[] = [];
    if (report.budget.kcal != null) tiles.push({ label: 'Orçamento', value: formatKcal(report.budget.kcal) });
    if (report.adherence.kcal_share != null) {
      tiles.push({ label: 'Aderência (kcal)', value: `${formatShareAsPercent(report.adherence.kcal_share)}${report.adherence.kcal_within_tolerance === true ? ' ✓' : ''}` });
    }
    if (report.adherence.protein_share != null) {
      tiles.push({ label: 'Proteína', value: `${formatShareAsPercent(report.adherence.protein_share)}${report.adherence.protein_ok === true ? ' ✓' : ''}` });
    }
    if (report.bank.balance_kcal != null) tiles.push({ label: 'Banco', value: formatSignedKcal(report.bank.balance_kcal) });
    const dias = report.adherence.logged_days;
    // Semana sem nada: um estado vazio único (o título e o aviso do gráfico, do componente
    // do desktop, são escondidos por CSS na tela do Diário).
    if (tiles.length === 0 && dias === 0) {
      return <EmptyState title="Semana sem registros" description="As refeições e a meta da semana aparecem aqui." />;
    }
    return (
      <div>
        {tiles.length > 0 ? (
          <StatGrid>
            {tiles.map((t) => (
              <StatTile key={t.label} label={t.label} value={t.value} />
            ))}
          </StatGrid>
        ) : null}
        <Text size="sm" c="dimmed" mt="xs">
          {dias === 0 ? 'Nenhum dia registrado' : `${dias} ${dias === 1 ? 'dia registrado' : 'dias registrados'}`}
          {report.adherence.valid_week ? ' · semana válida' : ''}
        </Text>
      </div>
    );
  },

  dayHeader: ({ date, today, onDateChange }) => {
    const weekday = date ? formatDayLong(date).split(',')[0] : '';
    return (
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <ActionIcon variant="light" color="brand" size={44} aria-label="Dia anterior" disabled={!date} onClick={() => date && onDateChange(shiftIsoDate(date, -1))}>
            <IconChevronLeft size={20} />
          </ActionIcon>
          <TextInput
            style={{ flex: 1 }}
            type="date"
            size="md"
            aria-label="Data do diário"
            value={date ?? ''}
            onChange={(event) => {
              const valor = event.currentTarget.value;
              if (valor) onDateChange(valor);
            }}
          />
          <ActionIcon variant="light" color="brand" size={44} aria-label="Dia seguinte" disabled={!date} onClick={() => date && onDateChange(shiftIsoDate(date, 1))}>
            <IconChevronRight size={20} />
          </ActionIcon>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 8 }}>
          <h3 style={{ margin: 0, fontSize: 18, fontWeight: 700 }}>{weekday.charAt(0).toUpperCase() + weekday.slice(1)}</h3>
          {today && date !== today ? (
            <Button variant="subtle" color="brand" mih={44} onClick={() => onDateChange(today)}>
              Hoje
            </Button>
          ) : null}
        </div>
      </div>
    );
  },

  primaryAction: ({ label, onClick }) => (
    <StickyAction>
      <Button fullWidth size="lg" color="brand" mih={48} leftSection={<IconPlus size={20} />} onClick={onClick}>
        {label}
      </Button>
    </StickyAction>
  ),

  weekDays: ({ days, today, selected, onSelect }) => (
    <div data-mf-weekdays role="group" aria-label="Dias da semana" style={{ display: 'grid', gridTemplateColumns: 'repeat(7, minmax(0, 1fr))', gap: 4 }}>
      {days.map((dia) => {
        const ativo = dia.local_date === selected;
        return (
          <button
            key={dia.local_date}
            type="button"
            aria-pressed={ativo}
            aria-label={`Abrir ${formatDateBR(dia.local_date)}${dia.logged ? ', registrado' : ''}`}
            onClick={() => onSelect(dia.local_date)}
            style={{
              minHeight: 56,
              padding: 0,
              borderRadius: 10,
              border: dia.local_date === today ? '1px solid var(--mantine-color-brand-5)' : '1px solid transparent',
              background: ativo ? 'var(--mantine-color-brand-light)' : 'transparent',
              color: 'inherit',
              cursor: 'pointer',
            }}
          >
            <span style={{ display: 'block', fontSize: 11, textTransform: 'capitalize' }}>{weekdayShort(dia.local_date).slice(0, 3)}</span>
            <span style={{ display: 'block', fontSize: 16, fontWeight: 700 }}>{formatDateBR(dia.local_date).slice(0, 2)}</span>
            <span aria-hidden style={{ display: 'block', fontSize: 10, color: dia.logged ? '#51cf66' : 'transparent' }}>●</span>
          </button>
        );
      })}
    </div>
  ),

  // Introdução de uma linha; as demais seções da Nutrição ficam no fim da página.
  nutritionHeader: ({ title, description }) => (
    <ScreenHeader title={title} subtitle={typeof description === 'string' ? description.split('. ')[0].replace(/\.$/, '') : undefined} />
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
                item.origin?.label,
                `${formatWithUnit(item.per_100[ENERGY_NUTRIENT], 'kcal', 0)} por 100 ${item.basis}`,
                item.serving.household_measure || item.brand || item.category,
              ]
                .filter(Boolean)
                .join(' · ')}
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
              style={{ minWidth: 48, minHeight: 48, background: 'none', border: 0, color: item.is_favorite ? '#fab005' : 'inherit', cursor: 'pointer' }}
            >
              {item.is_favorite ? <IconStarFilled size={22} /> : <IconStar size={22} />}
            </button>
          ) : null}
        </div>
      ))}
    </ListGroup>
  ),

  usdaResults: ({ items, importing, onImport }) => (
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
            loading={importing === item}
            disabled={importing !== null && importing !== item}
            onClick={() => onImport(item)}
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
// Semana sem dados: o título "Energia da semana" e o aviso do gráfico (WeekStrip, do desktop)
// somem; o estado vazio único vem do slot weekReport. Com gráfico, o título é seguido por
// um <div>, então nada disto casa.
const WEEK_EMPTY_CSS = `[data-mf-weekdays] + p:has(+ p), [data-mf-weekdays] + p + p { display: none; }`;

export function MobileNutritionPresentation({ children, active }: { children: ReactNode; active?: NutricaoSecao }) {
  return (
    <PresentationProvider value={SLOTS}>
      <PortalFieldsStyle />
      <style>{WEEK_EMPTY_CSS}</style>
      {children}
      {active ? <NutricaoNav active={active} /> : null}
    </PresentationProvider>
  );
}
