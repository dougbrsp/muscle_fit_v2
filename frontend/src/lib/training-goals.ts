'use client';

// Objetivo de treino (M97 F3): hipertrofia ou força.
//
// Tudo vem de `GET /training-goals` (backend `api/app/training_goals.py`,
// montado pelos donos `domain/training_goal.py`, `prescription_defaults` e
// `training_safety`; mapa `docs/motores/FONTES_DA_VERDADE.md` §66.3). A tela
// NÃO escreve número, nome nem literal de objetivo: vocabulário, rótulo,
// defaults de reps/RPE/descanso/séries e técnicas fora da política chegam
// prontos.
//
// O único espelho de regra é a PRECEDÊNCIA do editor (`effectiveItemGoal`:
// override do item > objetivo do Modelo > default), porque o card mostra o
// default do objetivo efetivo a cada troca, antes de salvar. Ela roda no
// vitest contra `training-goal.parity.json`, gerado do dono `resolve_goal`
// (`api/tests/unit/test_m97_f3_objetivo_telas.py --gravar`). O valor que vale
// é o que o servidor carimba na ficha (`plan_goal_stamp`).

import { useQuery } from '@tanstack/react-query';
import { apiFetch } from './api';

export type TrainingGoalDefaults = {
  training_goal: string;
  reps_target: string;
  rpe_target: string;
  min_rest_seconds: number;
  max_rest_seconds: number;
  rest_seconds: number;
  sets_count: number;
  rest_seconds_sanity_min: number;
  rest_seconds_sanity_max: number;
};

export type TrainingGoalOption = {
  value: string;
  label: string;
  is_default: boolean;
  defaults: TrainingGoalDefaults;
  /** Item sem descanso próprio: "da geração" (true) ou o descanso do perfil (false). */
  rest_follows_generation_interval: boolean;
  target_rpe: number;
  rpe_ceiling: number | null;
  disallowed_techniques: string[];
};

export type TrainingGoalsResponse = {
  default: string;
  items: TrainingGoalOption[];
};

export const TRAINING_GOALS_QUERY_KEY = ['training-goals'] as const;

/** Valor do Select de override do item que significa "herda do Modelo" (o payload leva `null`). */
export const INHERIT_GOAL_VALUE = 'inherit';

/** `GET /training-goals` (sem banco; muda só com deploy). */
export function useTrainingGoals() {
  return useQuery({
    queryKey: TRAINING_GOALS_QUERY_KEY,
    queryFn: () => apiFetch<TrainingGoalsResponse>('/training-goals'),
    staleTime: 60 * 60 * 1000,
  });
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim().toLowerCase() : '';
}

function known(goals: TrainingGoalsResponse | null | undefined, value: unknown): TrainingGoalOption | null {
  const wanted = text(value);
  if (!wanted || !goals) return null;
  return goals.items.find((item) => item.value === wanted) ?? null;
}

/** Objetivo default (o do dono); `null` enquanto a lista não chegou. */
export function defaultGoal(goals: TrainingGoalsResponse | null | undefined): TrainingGoalOption | null {
  if (!goals) return null;
  return goals.items.find((item) => item.value === goals.default) ?? goals.items.find((item) => item.is_default) ?? null;
}

/** Opção do objetivo (leitura tolerante, como `read_goal`: ausente/desconhecido = default). */
export function goalOption(goals: TrainingGoalsResponse | null | undefined, value: unknown): TrainingGoalOption | null {
  return known(goals, value) ?? defaultGoal(goals);
}

/** Valor normalizado do objetivo (ausente/desconhecido = default; `null` sem lista). */
export function goalValue(goals: TrainingGoalsResponse | null | undefined, value: unknown): string | null {
  return goalOption(goals, value)?.value ?? null;
}

/** Override do item (como `read_override`): desconhecido/vazio = herda (`null`). */
export function overrideValue(goals: TrainingGoalsResponse | null | undefined, value: unknown): string | null {
  return known(goals, value)?.value ?? null;
}

/**
 * Espelho de `training_goal.resolve_goal` (precedência: override do item >
 * objetivo do Modelo > default). Paridade em `training-goal.parity.json`.
 */
export function effectiveItemGoal(
  goals: TrainingGoalsResponse | null | undefined,
  itemOverride: unknown,
  templateGoal: unknown,
): TrainingGoalOption | null {
  return known(goals, itemOverride) ?? goalOption(goals, templateGoal);
}

export function isDefaultGoal(goals: TrainingGoalsResponse | null | undefined, value: unknown): boolean {
  return goalOption(goals, value)?.is_default ?? true;
}

/** Opções do Select "Objetivo" do cabeçalho do Modelo. */
export function goalSelectData(goals: TrainingGoalsResponse | null | undefined): { value: string; label: string }[] {
  return (goals?.items ?? []).map((item) => ({ value: item.value, label: item.label }));
}

/** Opções do override do item: "Herdar do Modelo (X)" + cada objetivo. */
export function itemGoalSelectData(
  goals: TrainingGoalsResponse | null | undefined,
  templateGoal: unknown,
): { value: string; label: string }[] {
  const inherited = goalOption(goals, templateGoal);
  return [
    { value: INHERIT_GOAL_VALUE, label: inherited ? `Herdar do Modelo (${inherited.label})` : 'Herdar do Modelo' },
    ...goalSelectData(goals),
  ];
}

/** Select do item -> valor do payload (`null` = herda). */
export function itemGoalFromSelect(goals: TrainingGoalsResponse | null | undefined, selected: string | null): string | null {
  if (!selected || selected === INHERIT_GOAL_VALUE) return null;
  return overrideValue(goals, selected);
}

/** Valor do Select do item a partir do estado (`null`/desconhecido = herda). */
export function itemGoalSelectValue(goals: TrainingGoalsResponse | null | undefined, override: unknown): string {
  return overrideValue(goals, override) ?? INHERIT_GOAL_VALUE;
}

/** O objetivo permite a técnica? (lista do dono `training_safety.goal_disallowed_techniques`). */
export function goalAllowsTechnique(option: TrainingGoalOption | null | undefined, techniqueCode: string | null | undefined): boolean {
  const code = (techniqueCode ?? '').trim();
  if (!code || !option) return true;
  return !option.disallowed_techniques.includes(code);
}

/**
 * Técnica que o Modo Treino oferece para a linha da ficha: a da linha, salvo
 * quando o objetivo dela (carimbado pelo servidor) não a permite — aí `null`
 * (o motor também a tira da prescrição). Sem a lista de objetivos ainda, a da
 * linha (hipertrofia não proíbe nenhuma; a de força chega com a lista).
 */
export function techniqueForGoal(
  goals: TrainingGoalsResponse | null | undefined,
  lineGoal: unknown,
  techniqueCode: string | null | undefined,
): string | null {
  const code = techniqueCode ?? null;
  if (!code || !goals) return code;
  return goalAllowsTechnique(goalOption(goals, lineGoal), code) ? code : null;
}

/**
 * Descanso default de uma linha SEM `rest_seconds`: o do perfil do objetivo
 * dela (`defaults_for(goal).rest_seconds`, da API); sem a lista, `fallback`
 * (o espelho do default de hipertrofia, `prescription-defaults`).
 */
export function goalDefaultRestSeconds(
  goals: TrainingGoalsResponse | null | undefined,
  lineGoal: unknown,
  fallback: number,
): number {
  return goalOption(goals, lineGoal)?.defaults.rest_seconds ?? fallback;
}

/** Placeholders dos campos do card, pelo objetivo efetivo do item (defaults do dono). */
export function itemFieldPlaceholders(option: TrainingGoalOption | null | undefined): {
  reps: string;
  rpe: string;
  rest: string;
  sets: string;
} | null {
  if (!option) return null;
  const d = option.defaults;
  return {
    reps: `ex.: ${d.reps_target}`,
    rpe: `ex.: ${d.rpe_target}`,
    rest: option.rest_follows_generation_interval ? 'da geração' : `${d.rest_seconds} (${option.label})`,
    sets: String(d.sets_count),
  };
}

/** Resumo dos defaults do objetivo (números interpolados da API). */
export function trainingGoalDescription(option: TrainingGoalOption | null | undefined): string | undefined {
  if (!option) return undefined;
  const d = option.defaults;
  return (
    `Padrão dos itens sem valor próprio: ${d.sets_count} séries de ${d.reps_target} reps, RPE ${d.rpe_target}, ` +
    `descanso ${d.min_rest_seconds}–${d.max_rest_seconds} s. Cada item pode sobrepor o objetivo.`
  );
}

/** Itens do slot que aceitam objetivo: os de grupo muscular e de equipamento (cardio não). */
export function itemAcceptsGoal(item: { item_kind: string }): boolean {
  return item.item_kind === 'muscle_group' || item.item_kind === 'equipment';
}

/**
 * "Objetivo do dia" (Modelo híbrido): aplica o MESMO override a todos os itens
 * do slot que aceitam objetivo, de uma vez; sem nível novo de dado (o payload
 * continua por item). `null` = todos voltam a herdar do Modelo.
 */
export function applySlotGoal<T extends { item_kind: string; training_goal?: string | null }>(
  items: T[],
  override: string | null,
): T[] {
  return items.map((item) => (itemAcceptsGoal(item) ? { ...item, training_goal: override } : item));
}

/** Override comum a todos os itens do slot que aceitam objetivo; `undefined` = misto ou slot sem item. */
export function slotCommonGoal(
  goals: TrainingGoalsResponse | null | undefined,
  items: ReadonlyArray<{ item_kind: string; training_goal?: string | null }>,
): string | null | undefined {
  const values = items.filter(itemAcceptsGoal).map((item) => overrideValue(goals, item.training_goal));
  if (values.length === 0) return undefined;
  return values.every((value) => value === values[0]) ? values[0] : undefined;
}

/** Objetivos distintos presentes nos itens (efetivos), na ordem do vocabulário. */
export function slotEffectiveGoals(
  goals: TrainingGoalsResponse | null | undefined,
  items: ReadonlyArray<{ item_kind: string; training_goal?: string | null }>,
  templateGoal: unknown,
): TrainingGoalOption[] {
  const present = new Set(
    items.filter(itemAcceptsGoal).map((item) => effectiveItemGoal(goals, item.training_goal, templateGoal)?.value),
  );
  return (goals?.items ?? []).filter((item) => present.has(item.value));
}

/** Cor do badge: default discreto, os demais destacados. */
export function goalBadgeColor(option: TrainingGoalOption | null | undefined): string {
  return option && !option.is_default ? 'orange' : 'gray';
}
