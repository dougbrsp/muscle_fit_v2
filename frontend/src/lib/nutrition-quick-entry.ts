// Lançamento rápido de refeição (nutricao-melhorias, Frente C). Só transporte e
// apresentação: o motor (`api/app/domain/nutrition/diary.py`) valida, congela os
// valores e decide a refeição de uma hora (`GET /nutrition/meals/slot-for-time`).
// Nomes dos campos, peso máximo e tamanho do nome vêm do bloco `nutrition.quick_entry`
// de `motor-constants.generated.json`; a tela não escreve limite nem regra.

import { ApiError, apiFetch } from './api';
import motorConstants from './motor-constants.generated.json';
import { ITEM_NUTRIENT_FIELDS, type NutritionCode } from './nutrition-constants';
import type {
  MealCreatePayload,
  NutritionLimitation,
  QuickEntryPayload,
  QuickEntrySnapshot,
  SlotForTime,
} from './nutrition-types';

const QUICK = motorConstants.nutrition.quick_entry;

/** Campo do peso informado (g) e campos de nutriente, na ordem do dono. */
export const QUICK_ENTRY_WEIGHT_FIELD: string = QUICK.weight_field;
export const QUICK_ENTRY_NUTRIENT_FIELDS: ReadonlyArray<string> = QUICK.nutrient_fields;
export const QUICK_ENTRY_NAME_MAX_CHARS: number = QUICK.name_max_chars;
/** Fonte do item gravado (`food_source`) e natureza própria do lançamento rápido. */
export const QUICK_ENTRY_SOURCE: NutritionCode = QUICK.source;
export const QUICK_ENTRY_NATURE: NutritionCode = QUICK.nature;
export const QUICK_ENTRY_UNIT: string = QUICK.unit;

/** Rótulo e unidade de cada campo numérico (peso e nutrientes), com o rótulo do dono para os nutrientes. */
export const QUICK_ENTRY_NUMERIC_FIELDS: ReadonlyArray<{ code: string; label: string; unit: string }> = [
  { code: QUICK_ENTRY_WEIGHT_FIELD, label: 'Peso', unit: QUICK_ENTRY_UNIT },
  ...QUICK_ENTRY_NUTRIENT_FIELDS.map((code) => {
    const campo = ITEM_NUTRIENT_FIELDS.find((item) => item.code === code);
    return { code, label: campo?.label ?? code, unit: campo?.unit ?? '' };
  }),
];

/** Rascunho dos 6 campos. `''` enquanto o campo está vazio. `saved` = valores já gravados (edição). */
export type QuickEntryDraft = {
  name: string;
  values: Record<string, number | string>;
  saved: QuickEntryPayload | null;
};

export function emptyQuickDraft(): QuickEntryDraft {
  return {
    name: '',
    values: Object.fromEntries(QUICK_ENTRY_NUMERIC_FIELDS.map((campo) => [campo.code, ''])),
    saved: null,
  };
}

function toNumber(valor: number | string | null | undefined): number | null {
  if (valor === '' || valor === null || valor === undefined) return null;
  const numero = Number(valor);
  return Number.isFinite(numero) ? numero : null;
}

/** Os 6 campos no formato da API; `null` enquanto o nome ou algum número está vazio (o motor confere o resto). */
export function quickEntryPayload(draft: QuickEntryDraft): QuickEntryPayload | null {
  const nome = draft.name.trim();
  if (nome === '') return null;
  const corpo: Record<string, string | number> = { name: nome };
  for (const campo of QUICK_ENTRY_NUMERIC_FIELDS) {
    const valor = toNumber(draft.values[campo.code]);
    if (valor === null) return null;
    corpo[campo.code] = valor;
  }
  return corpo as unknown as QuickEntryPayload;
}

/** Rascunho a partir do item gravado (edição): guarda o gravado para saber se mudou. */
export function draftFromSnapshot(snapshot: QuickEntrySnapshot): QuickEntryDraft {
  const values: Record<string, number | string> = {};
  for (const campo of QUICK_ENTRY_NUMERIC_FIELDS) {
    values[campo.code] = campo.code === QUICK_ENTRY_WEIGHT_FIELD ? snapshot.weight_g : (snapshot.nutrients[campo.code] ?? '');
  }
  const draft: QuickEntryDraft = { name: snapshot.name, values, saved: null };
  return { ...draft, saved: quickEntryPayload(draft) };
}

/** O item gravado foi alterado na tela? Sem gravado (item novo), sempre. */
export function quickEntryChanged(draft: QuickEntryDraft): boolean {
  const atual = quickEntryPayload(draft);
  if (draft.saved === null || atual === null) return true;
  const antes = draft.saved as unknown as Record<string, string | number>;
  const depois = atual as unknown as Record<string, string | number>;
  return Object.keys(depois).some((chave) => depois[chave] !== antes[chave]);
}

/** Corpo de `POST /nutrition/meals` do lançamento rápido; `null` sem refeição ou com campo vazio. */
export function quickMealPayload(entrada: {
  date: string;
  slot: NutritionCode | null;
  eatenAt: string;
  draft: QuickEntryDraft;
}): MealCreatePayload | null {
  const item = quickEntryPayload(entrada.draft);
  if (item === null || entrada.slot === null) return null;
  return {
    local_date: entrada.date,
    meal_slot: entrada.slot,
    eaten_at: entrada.eatenAt || null,
    notes: null,
    items: [{ quick_entry: item }],
  };
}

// ---------------------------------------------------------------------------
// Hora -> refeição (motor: `diary.meal_slot_for_time`)
// ---------------------------------------------------------------------------

export const slotForTimeKeys = {
  time: (time: string) => ['nutrition-slot-for-time', time] as const,
};

export function fetchSlotForTime(time: string): Promise<SlotForTime> {
  return apiFetch<SlotForTime>(`/nutrition/meals/slot-for-time?time=${encodeURIComponent(time)}`);
}

/** Hora do relógio do aparelho como `HH:MM` (apenas o valor inicial do campo; a refeição vem da API). */
export function clockTime(agora: Date = new Date()): string {
  return agora.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
}

// ---------------------------------------------------------------------------
// Erros da API ({code, label}) no campo certo
// ---------------------------------------------------------------------------

export type QuickEntryErrors = {
  /** Mensagem do motor por campo (`name` ou o código do campo numérico). */
  byField: Record<string, string>;
  /** Erros sem campo (refeição, hora, dia): aparecem juntos acima do botão. */
  general: string[];
};

// Códigos sem sufixo de campo, mapeados pelo campo do dono.
const CODE_TO_FIELD: Record<string, string> = {
  quick_entry_weight_invalid: QUICK_ENTRY_WEIGHT_FIELD,
  item_amount_out_of_range: QUICK_ENTRY_WEIGHT_FIELD,
  invalid_food_name: 'name',
};

function fieldOf(code: string): string | null {
  const [prefixo, sufixo] = code.split(':');
  if (sufixo !== undefined && (sufixo === 'name' || QUICK_ENTRY_NUMERIC_FIELDS.some((campo) => campo.code === sufixo))) {
    return sufixo;
  }
  return CODE_TO_FIELD[prefixo] ?? null;
}

/** Distribui `errors` e `items[].errors` do 400 (`invalid_meal`) pelos campos do formulário. */
export function quickEntryErrors(error: unknown, fallback: string): QuickEntryErrors {
  const resultado: QuickEntryErrors = { byField: {}, general: [] };
  const coletados: NutritionLimitation[] = [];
  if (error instanceof ApiError && typeof error.detail === 'object' && error.detail !== null) {
    const detalhe = error.detail as {
      errors?: NutritionLimitation[];
      items?: Array<{ errors?: NutritionLimitation[] }>;
    };
    coletados.push(...(detalhe.errors ?? []));
    for (const item of detalhe.items ?? []) coletados.push(...(item.errors ?? []));
  }
  for (const erro of coletados) {
    const campo = fieldOf(erro.code);
    if (campo !== null) resultado.byField[campo] ??= erro.label;
    else resultado.general.push(erro.label);
  }
  if (coletados.length === 0) {
    resultado.general.push(error instanceof ApiError && error.message ? error.message : fallback);
  }
  return resultado;
}
