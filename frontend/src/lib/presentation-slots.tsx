'use client';

import { createContext, useContext, type ComponentType, type ReactNode } from 'react';
import type { WeekRow } from '@/components/nutrition/WeekTargetsTable';
import type { Checkin, CheckinDayMacros } from '@/lib/nutrition-checkin';
import type { FoodPortion, NutritionWeek, WeekReport, FoodSearchItem, MealItem, UsdaSearchItem } from '@/lib/nutrition-types';

/**
 * Pontos de apresentação alternativa (celular). Componentes compartilhados que
 * hoje desenham uma `<table>` consultam o slot: sem provider (desktop) nada
 * muda e a tabela de sempre é desenhada; o shell mobile, nas suas telas, provê
 * uma lista no lugar. Só apresentação: os dados e as funções puras são as mesmas.
 */
export type CheckinWeekRow = { label: string; week: string; reference: string; note?: string };

export type RecordListRow = {
  key: string | number;
  title: string;
  subtitle?: string;
  value?: string;
  /** Todos os dados da linha (as colunas da tabela do desktop), para o detalhe por toque. */
  details: ReadonlyArray<readonly [string, string]>;
  onEdit?: () => void;
  onDelete?: () => void;
  deleting?: boolean;
};

export type PeriodComparisonRow = {
  key: string;
  label: ReactNode;
  /** Texto longo (slot, última sessão): só no detalhe por toque no celular. */
  caption?: ReactNode;
  /** [período][métrica], já desenhados (valores e setas de tendência prontos). */
  periods: ReactNode[][];
  evolution?: ReactNode;
  toggle?: { expanded: boolean; onToggle: () => void };
};

export type PresentationSlots = {
  /** Resumo da semana do diário (orçamento, aderência, banco) em tiles, sem os campos sem dado. */
  weekReport: ComponentType<{ report: WeekReport }>;
  /** Cabeçalho do dia do diário (celular): dia da semana uma vez, data no campo. */
  dayHeader: ComponentType<{ date: string | null; today: string | null; onDateChange: (date: string) => void }>;
  /** Ação principal da página (celular: fixa acima da tab bar). */
  primaryAction: ComponentType<{ label: string; onClick: () => void }>;
  /** Os 7 dias da semana do diário numa faixa (celular), no lugar dos cartões. */
  weekDays: ComponentType<{ days: NutritionWeek['days']; today: string; selected: string | null; onSelect: (date: string) => void }>;
  /** Comparativo por período (T-3..T-0, S-3..S-0) em lista, com o detalhe por toque. */
  periodComparison: ComponentType<{
    groupingLabel: string;
    periodLabels: string[];
    metricLabels: string[];
    rows: PeriodComparisonRow[];
    emptyText: string;
  }>;
  /** Cabeçalho de página genérico (título e descrição). */
  pageHeader: ComponentType<{ title: string; description: ReactNode }>;
  /** Lista de registros (bioimpedância, medidas, sono...) no lugar da tabela; detalhe e ações por toque. */
  recordList: ComponentType<{ label: string; rows: RecordListRow[]; emptyText: string }>;
  /** Cabeçalho das páginas de Nutrição (título, descrição e troca de seção). */
  nutritionHeader: ComponentType<{ active: string; title: string; description: ReactNode }>;
  weekTargets: ComponentType<{ rows: WeekRow[]; today: string }>;
  mealItems: ComponentType<{ items: MealItem[] }>;
  foodResults: ComponentType<{
    items: FoodSearchItem[];
    selectedId: number | null;
    withStar: boolean;
    onSelect: (item: FoodSearchItem) => void;
    onToggleFavorite: (item: FoodSearchItem) => void;
    togglingId: number | null;
  }>;
  usdaResults: ComponentType<{
    items: UsdaSearchItem[];
    /** Item em importação (o do clique), ou null. */
    importing: UsdaSearchItem | null;
    onImport: (item: UsdaSearchItem) => void;
  }>;
  portions: ComponentType<{ portions: FoodPortion[]; basis: string }>;
  checkinWeek: ComponentType<{ rows: CheckinWeekRow[] }>;
  checkinMacros: ComponentType<{ before: CheckinDayMacros; after: CheckinDayMacros; scopeLabel: string }>;
};

export type { Checkin };

const PresentationContext = createContext<Partial<PresentationSlots>>({});

export const PresentationProvider = PresentationContext.Provider;

/** Há apresentação alternativa (celular) em volta deste componente? */
export function usePresentationActive(): boolean {
  return Object.keys(useContext(PresentationContext)).length > 0;
}

/** Slot de apresentação alternativa, ou `undefined` (desktop: desenha o padrão). */
export function usePresentationSlot<K extends keyof PresentationSlots>(name: K): PresentationSlots[K] | undefined {
  return useContext(PresentationContext)[name] as PresentationSlots[K] | undefined;
}
