'use client';

import { createContext, useContext, type ComponentType, type ReactNode } from 'react';
import type { WeekRow } from '@/components/nutrition/WeekTargetsTable';
import type { Checkin, CheckinDayMacros } from '@/lib/nutrition-checkin';
import type { FoodPortion, FoodSearchItem, MealItem, UsdaSearchItem } from '@/lib/nutrition-types';

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

export type PresentationSlots = {
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
    importingId: number | null;
    importing: boolean;
    onImport: (fdcId: number) => void;
  }>;
  portions: ComponentType<{ portions: FoodPortion[]; basis: string }>;
  checkinWeek: ComponentType<{ rows: CheckinWeekRow[] }>;
  checkinMacros: ComponentType<{ before: CheckinDayMacros; after: CheckinDayMacros; scopeLabel: string }>;
};

export type { Checkin };

const PresentationContext = createContext<Partial<PresentationSlots>>({});

export const PresentationProvider = PresentationContext.Provider;

/** Slot de apresentação alternativa, ou `undefined` (desktop: desenha o padrão). */
export function usePresentationSlot<K extends keyof PresentationSlots>(name: K): PresentationSlots[K] | undefined {
  return useContext(PresentationContext)[name] as PresentationSlots[K] | undefined;
}
