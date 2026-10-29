/**
 * Tela de cadastro das regras de carga da próxima série (`/regras-carga`, R4).
 *
 * Só FORMA e montagem dos campos: rótulos e opções vêm de
 * `motor-constants.generated.json` (`next_set_load.rule_vocabulary`, exportado
 * pelo dono `api/app/domain/next_set_load.py`), os limites de
 * `next_set_load.rule_limits`. Nenhuma regra é avaliada aqui: o texto de cada
 * regra vem do servidor (`text` / `POST /describe`), a simulação é sempre
 * `POST /simulate` e a validação semântica é do dono (`codes` do 422).
 */
import { ApiError } from './api';
import motorConstants from './motor-constants.generated.json';
import type {
  RuleCondition,
  RulePayload,
  RuleSetErrorDetail,
  RuleSetInput,
  RuleValue,
  SimulateFacts,
} from './next-set-load-rules-types';

const NEXT_SET_LOAD = motorConstants.next_set_load;

export const RULE_VOCABULARY = NEXT_SET_LOAD.rule_vocabulary;
export const RULE_LIMITS = NEXT_SET_LOAD.rule_limits;
/** O conjunto padrão em código (versão 0), exportado pelo dono. */
export const MOTOR_DEFAULT_RULESET: RuleSetInput = NEXT_SET_LOAD.default_ruleset as RuleSetInput;

export type SelectOption = { value: string; label: string };

// Códigos do vocabulário que a tela trata de forma especial (forma, não regra):
// "qualquer" (sem condição de posição/fadiga), o operador de intervalo (pede
// o 2º valor) e a ação sem magnitude.
const ANY_VALUE = 'any';
const BETWEEN_OP = 'between';
const HOLD_ACTION = 'HOLD';
/** Valor do Select de origem do número (o resto são os tokens do vocabulário). */
export const NUMBER_SOURCE = 'number';
/** Valor do Select de operador para "sem condição". */
export const NO_CONDITION = '';
/** Faixa de fadiga desconhecida no simulador (`fatigue_band: null`). */
export const UNKNOWN_FATIGUE_BAND = 'unknown';

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

export const NEXT_SET_LOAD_RULES_QUERY_KEY = ['next-set-load-rules'] as const;
export const PUBLISHED_RULES_QUERY_KEY = [...NEXT_SET_LOAD_RULES_QUERY_KEY, 'published'] as const;
export const DRAFT_RULES_QUERY_KEY = [...NEXT_SET_LOAD_RULES_QUERY_KEY, 'draft'] as const;
export const VERSIONS_RULES_QUERY_KEY = [...NEXT_SET_LOAD_RULES_QUERY_KEY, 'versions'] as const;

// ---------------------------------------------------------------------------
// Opções dos Selects (todas do vocabulário do dono)
// ---------------------------------------------------------------------------

function options(items: readonly { value: string; label: string }[]): SelectOption[] {
  return items.map((item) => ({ value: item.value, label: item.label }));
}

export const SET_POSITION_OPTIONS: SelectOption[] = options(RULE_VOCABULARY.set_positions);
export const FATIGUE_OPTIONS: SelectOption[] = options(RULE_VOCABULARY.fatigue_conditions);
export const ACTION_OPTIONS: SelectOption[] = options(RULE_VOCABULARY.actions);
export const MAGNITUDE_KIND_OPTIONS: SelectOption[] = options(RULE_VOCABULARY.magnitude_kinds);
export const CONDITION_OP_OPTIONS: SelectOption[] = [
  { value: NO_CONDITION, label: 'sem condição' },
  ...options(RULE_VOCABULARY.condition_ops),
];
export const REPS_VALUE_SOURCE_OPTIONS: SelectOption[] = [
  { value: NUMBER_SOURCE, label: 'número' },
  ...options(RULE_VOCABULARY.reps_tokens),
];
export const RPE_VALUE_SOURCE_OPTIONS: SelectOption[] = [
  { value: NUMBER_SOURCE, label: 'número' },
  ...RULE_VOCABULARY.rpe_tokens.map((token) => ({ value: token.value, label: `${token.label} do dia` })),
];
/** Faixas de fadiga de uma série (simulador): as do vocabulário menos "qualquer", mais "desconhecida". */
export const FATIGUE_BAND_OPTIONS: SelectOption[] = [
  ...FATIGUE_OPTIONS.filter((option) => option.value !== ANY_VALUE),
  { value: UNKNOWN_FATIGUE_BAND, label: 'desconhecida' },
];

export function optionLabel(items: readonly SelectOption[], value: string | null | undefined): string {
  return items.find((item) => item.value === value)?.label ?? String(value ?? '');
}

export function isBetween(op: string): boolean {
  return op === BETWEEN_OP;
}

export function actionHasMagnitude(action: string): boolean {
  return action !== HOLD_ACTION;
}

/** Limites do campo de magnitude pelo tipo (passos ou percentual). */
export function magnitudeBounds(kind: string): { min: number; max: number; integer: boolean } {
  if (kind === 'percent') {
    return { min: RULE_LIMITS.magnitude_percent_min, max: RULE_LIMITS.magnitude_percent_max, integer: false };
  }
  return { min: RULE_LIMITS.magnitude_steps_min, max: RULE_LIMITS.magnitude_steps_max, integer: true };
}

export const REPS_VALUE_BOUNDS = { min: RULE_LIMITS.reps_floor_min, max: RULE_LIMITS.reps_ceiling_max } as const;
export const RPE_VALUE_BOUNDS = { min: RULE_LIMITS.rule_rpe_min, max: RULE_LIMITS.rule_rpe_max } as const;

// ---------------------------------------------------------------------------
// Rascunho local (com `localId`) <-> payload
// ---------------------------------------------------------------------------

export type DraftNumber = number | '';

export type ValueDraft = { source: string; number: DraftNumber };

export type ConditionDraft = { op: string; value: ValueDraft; valueTo: ValueDraft };

export type RuleDraft = {
  localId: string;
  set_position: string;
  fatigue: string;
  reps: ConditionDraft | null;
  rpe: ConditionDraft | null;
  respect_planned_ramp: boolean;
  action: string;
  /** Guardada mesmo em "Manter" (volta ao trocar a ação); só vai no payload com ação que mexe na carga. */
  magnitude: { kind: string; value: DraftNumber };
  /** Texto do servidor (`text`/`POST /describe`); null até chegar. */
  text: string | null;
};

export type RuleSetDraft = {
  reps_floor: DraftNumber;
  reps_ceiling: DraftNumber;
  rules: RuleDraft[];
  notes: string;
};

let localIdCounter = 0;

export function nextLocalId(): string {
  localIdCounter += 1;
  return `rule-${localIdCounter}`;
}

function valueFromPayload(value: RuleValue | null | undefined): ValueDraft {
  if (typeof value === 'number') return { source: NUMBER_SOURCE, number: value };
  if (typeof value === 'string' && value !== '') return { source: value, number: '' };
  return { source: NUMBER_SOURCE, number: '' };
}

function conditionFromPayload(condition: RuleCondition | null | undefined): ConditionDraft | null {
  if (!condition) return null;
  return { op: condition.op, value: valueFromPayload(condition.value), valueTo: valueFromPayload(condition.value_to) };
}

function defaultMagnitude(): RuleDraft['magnitude'] {
  const kind = MAGNITUDE_KIND_OPTIONS[0]?.value ?? 'steps';
  return { kind, value: magnitudeBounds(kind).min };
}

export function ruleFromPayload(rule: RulePayload): RuleDraft {
  return {
    localId: nextLocalId(),
    set_position: rule.set_position,
    fatigue: rule.fatigue,
    reps: conditionFromPayload(rule.reps),
    rpe: conditionFromPayload(rule.rpe),
    respect_planned_ramp: rule.respect_planned_ramp,
    action: rule.action,
    magnitude: rule.magnitude ? { kind: rule.magnitude.kind, value: rule.magnitude.value } : defaultMagnitude(),
    text: rule.text ?? null,
  };
}

export function draftFromRuleSet(ruleset: RuleSetInput, notes: string | null = null): RuleSetDraft {
  return {
    reps_floor: ruleset.reps_floor,
    reps_ceiling: ruleset.reps_ceiling,
    rules: [...ruleset.rules]
      .sort((a, b) => (a.position ?? 0) - (b.position ?? 0))
      .map(ruleFromPayload),
    notes: notes ?? '',
  };
}

function valueToPayload(value: ValueDraft): RuleValue | null {
  if (value.source !== NUMBER_SOURCE) return value.source;
  return value.number === '' ? null : value.number;
}

function conditionToPayload(condition: ConditionDraft | null): RuleCondition | null | undefined {
  if (!condition || condition.op === NO_CONDITION) return null;
  const value = valueToPayload(condition.value);
  if (value === null) return undefined;
  if (!isBetween(condition.op)) return { op: condition.op, value };
  const valueTo = valueToPayload(condition.valueTo);
  if (valueTo === null) return undefined;
  return { op: condition.op, value, value_to: valueTo };
}

/**
 * Rascunho -> corpo de `PUT /draft`/`/describe`/`/simulate`. A ordem da lista
 * vira `position` 1..n; `text` e `reason_code` não vão (o servidor gera o
 * texto e regra cadastrada usa o motivo genérico). `null` = campo vazio
 * (ver `shapeIssues`).
 */
export function draftToPayload(draft: RuleSetDraft): RuleSetInput | null {
  if (draft.reps_floor === '' || draft.reps_ceiling === '') return null;
  const rules: RulePayload[] = [];
  for (const [index, rule] of draft.rules.entries()) {
    const reps = conditionToPayload(rule.reps);
    const rpe = conditionToPayload(rule.rpe);
    if (reps === undefined || rpe === undefined) return null;
    let magnitude: RulePayload['magnitude'] = null;
    if (actionHasMagnitude(rule.action)) {
      if (rule.magnitude.value === '') return null;
      magnitude = { kind: rule.magnitude.kind, value: rule.magnitude.value };
    }
    rules.push({
      position: index + 1,
      set_position: rule.set_position,
      fatigue: rule.fatigue,
      reps,
      rpe,
      respect_planned_ramp: rule.respect_planned_ramp,
      action: rule.action,
      magnitude,
    });
  }
  return { reps_floor: draft.reps_floor, reps_ceiling: draft.reps_ceiling, rules };
}

/** Mesmo conteúdo (para "alterações não salvas"): compara os payloads. */
export function sameRuleSet(a: RuleSetDraft | null, b: RuleSetDraft | null): boolean {
  if (!a || !b) return a === b;
  return (
    JSON.stringify(draftToPayload(a)) === JSON.stringify(draftToPayload(b)) && a.notes.trim() === b.notes.trim()
  );
}

// ---------------------------------------------------------------------------
// Edição da lista
// ---------------------------------------------------------------------------

/** Move a regra de `from` para `to` (arrastar); a posição sai da ordem da lista. */
export function reorderRules(rules: readonly RuleDraft[], from: number, to: number): RuleDraft[] {
  const next = [...rules];
  if (from < 0 || from >= next.length || to < 0 || to >= next.length || from === to) return next;
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

/** Regra nova: sem condição de posição/fadiga, reps acima do teto, aumentar o mínimo de passos. */
export function newRuleDraft(): RuleDraft {
  const ceilingToken = RULE_VOCABULARY.reps_tokens.find((token) => token.value === 'ceiling')?.value ?? NUMBER_SOURCE;
  return {
    localId: nextLocalId(),
    set_position: SET_POSITION_OPTIONS[0]?.value ?? ANY_VALUE,
    fatigue: FATIGUE_OPTIONS[0]?.value ?? ANY_VALUE,
    reps: { op: RULE_VOCABULARY.condition_ops[0].value, value: { source: ceilingToken, number: '' }, valueTo: { source: NUMBER_SOURCE, number: '' } },
    rpe: null,
    respect_planned_ramp: false,
    action: ACTION_OPTIONS.find((option) => actionHasMagnitude(option.value))?.value ?? HOLD_ACTION,
    magnitude: defaultMagnitude(),
    text: null,
  };
}

export function addRule(rules: readonly RuleDraft[]): RuleDraft[] {
  return [...rules, newRuleDraft()];
}

/** Cópia logo abaixo da original. */
export function duplicateRule(rules: readonly RuleDraft[], localId: string): RuleDraft[] {
  const index = rules.findIndex((rule) => rule.localId === localId);
  if (index < 0) return [...rules];
  const copy: RuleDraft = { ...structuredCloneRule(rules[index]), localId: nextLocalId() };
  return [...rules.slice(0, index + 1), copy, ...rules.slice(index + 1)];
}

function structuredCloneRule(rule: RuleDraft): RuleDraft {
  return JSON.parse(JSON.stringify(rule)) as RuleDraft;
}

export function removeRule(rules: readonly RuleDraft[], localId: string): RuleDraft[] {
  return rules.filter((rule) => rule.localId !== localId);
}

/** Mudar a regra apaga o texto que veio do servidor até a próxima descrição (`POST /describe`). */
export function updateRule(rules: readonly RuleDraft[], localId: string, patch: Partial<RuleDraft>): RuleDraft[] {
  return rules.map((rule) => (rule.localId === localId ? { ...rule, ...patch, text: null } : rule));
}

/** Condição vazia ao ligar um operador ("sem condição" -> operador). */
export function emptyCondition(op: string): ConditionDraft {
  return { op, value: { source: NUMBER_SOURCE, number: '' }, valueTo: { source: NUMBER_SOURCE, number: '' } };
}

// ---------------------------------------------------------------------------
// Validação de FORMA (limites de `rule_limits`); a semântica é do servidor
// ---------------------------------------------------------------------------

function outside(value: number, bounds: { min: number; max: number }): boolean {
  return value < bounds.min || value > bounds.max;
}

function valueIssues(
  label: string,
  value: ValueDraft,
  bounds: { min: number; max: number },
): string[] {
  if (value.source !== NUMBER_SOURCE) return [];
  if (value.number === '') return [`${label}: informe o valor.`];
  if (outside(value.number, bounds)) return [`${label}: use um valor entre ${bounds.min} e ${bounds.max}.`];
  return [];
}

function conditionIssues(label: string, condition: ConditionDraft | null, bounds: { min: number; max: number }): string[] {
  if (!condition || condition.op === NO_CONDITION) return [];
  const issues = valueIssues(label, condition.value, bounds);
  if (isBetween(condition.op)) issues.push(...valueIssues(`${label} (até)`, condition.valueTo, bounds));
  return issues;
}

/** Problemas de forma do rascunho (campos vazios, números fora de `rule_limits`). Vazio = pode enviar. */
export function shapeIssues(draft: RuleSetDraft): string[] {
  const issues: string[] = [];
  if (draft.reps_floor === '') issues.push('Informe o piso de repetições.');
  else if (draft.reps_floor < RULE_LIMITS.reps_floor_min)
    issues.push(`Piso de repetições: no mínimo ${RULE_LIMITS.reps_floor_min}.`);
  if (draft.reps_ceiling === '') issues.push('Informe o teto de repetições.');
  else if (draft.reps_ceiling > RULE_LIMITS.reps_ceiling_max)
    issues.push(`Teto de repetições: no máximo ${RULE_LIMITS.reps_ceiling_max}.`);
  if (draft.rules.length === 0) issues.push('Cadastre ao menos uma regra.');
  if (draft.rules.length > RULE_LIMITS.max_rules) issues.push(`No máximo ${RULE_LIMITS.max_rules} regras.`);
  draft.rules.forEach((rule, index) => {
    const prefix = `Regra ${index + 1}`;
    issues.push(...conditionIssues(`${prefix}, reps`, rule.reps, REPS_VALUE_BOUNDS));
    issues.push(...conditionIssues(`${prefix}, RPE`, rule.rpe, RPE_VALUE_BOUNDS));
    if (actionHasMagnitude(rule.action)) {
      const bounds = magnitudeBounds(rule.magnitude.kind);
      if (rule.magnitude.value === '') issues.push(`${prefix}: informe a magnitude.`);
      else if (outside(rule.magnitude.value, bounds))
        issues.push(`${prefix}: magnitude entre ${bounds.min} e ${bounds.max}.`);
      else if (bounds.integer && !Number.isInteger(rule.magnitude.value))
        issues.push(`${prefix}: passos são números inteiros.`);
    }
  });
  return issues;
}

// ---------------------------------------------------------------------------
// Códigos do dono -> mensagem legível
// ---------------------------------------------------------------------------

const CODE_MESSAGES: Readonly<Record<string, string>> = {
  RULESET_EMPTY: 'O conjunto precisa de ao menos uma regra.',
  RULESET_TOO_MANY_RULES: `O conjunto passou do limite de ${RULE_LIMITS.max_rules} regras.`,
  RULESET_POSITIONS_NOT_SEQUENTIAL: 'As posições das regras não estão em sequência; recarregue a tela.',
  REPS_FLOOR_BELOW_MIN: `O piso de repetições está abaixo de ${RULE_LIMITS.reps_floor_min}.`,
  REPS_CEILING_ABOVE_MAX: `O teto de repetições está acima de ${RULE_LIMITS.reps_ceiling_max}.`,
  REPS_FLOOR_NOT_BELOW_CEILING: 'O piso de repetições precisa ficar abaixo do teto.',
  RULE_ACTION_UNKNOWN: 'ação desconhecida.',
  RULE_WITHOUT_CONDITION: 'sem nenhuma condição (casaria com toda série); escolha ao menos uma.',
  RULE_HOLD_WITH_MAGNITUDE: '"Manter" não leva magnitude.',
  RULE_MAGNITUDE_MISSING: 'informe a magnitude.',
  RULE_MAGNITUDE_OUT_OF_RANGE: 'magnitude fora dos limites.',
  RULE_STEPS_NOT_INTEGER: 'passos precisam ser inteiros.',
  RULE_REPS_TOKEN_UNKNOWN: 'valor de repetições desconhecido.',
  RULE_REPS_OUT_OF_RANGE: `repetições fora da faixa ${RULE_LIMITS.reps_floor_min}–${RULE_LIMITS.reps_ceiling_max}.`,
  RULE_RPE_TOKEN_UNKNOWN: 'valor de RPE desconhecido.',
  RULE_RPE_OUT_OF_RANGE: `RPE fora da faixa ${RULE_LIMITS.rule_rpe_min}–${RULE_LIMITS.rule_rpe_max}.`,
  RULE_BETWEEN_INVALID: '"entre" precisa de dois valores, o primeiro menor que o segundo.',
  RULE_RAMP_WITHOUT_RPE: '"Respeita rampa planejada" só vale com condição de RPE.',
  RULE_REASON_CODE_UNKNOWN: 'motivo desconhecido.',
  WARN_NO_DECREASE_RULE: 'Nenhuma regra reduz a carga (a dor continua reduzindo antes das regras).',
  WARN_RULE_UNREACHABLE: 'nunca vai casar: uma regra acima dela casa com toda série.',
};

/** `RULE_X:3` -> "Regra 3: …"; código sem texto conhecido sai como veio. */
export function codeMessage(code: string): string {
  const [base, position] = code.split(':');
  const message = CODE_MESSAGES[base] ?? base;
  if (!position) return message;
  return `Regra ${position}: ${message}`;
}

/** `detail` estruturado do `ApiError` destas rotas (ou null). */
export function ruleSetErrorDetail(error: unknown): RuleSetErrorDetail | null {
  if (!(error instanceof ApiError)) return null;
  const detail = error.detail as Partial<RuleSetErrorDetail> | undefined;
  if (detail && typeof detail === 'object' && !Array.isArray(detail) && typeof detail.error === 'string') {
    return {
      error: detail.error,
      message: typeof detail.message === 'string' ? detail.message : error.message,
      codes: Array.isArray(detail.codes) ? detail.codes.map(String) : [],
      current_version: typeof detail.current_version === 'number' ? detail.current_version : null,
    };
  }
  return null;
}

export const EDGE_BLOCKED_MESSAGE =
  'A requisição foi barrada pela proteção da borda (WAF). Tire caracteres como < e > das notas e tente de novo.';

/** Mensagens legíveis de um erro de gravação/simulação. */
export function ruleSetErrorMessages(error: unknown, fallback: string): string[] {
  const detail = ruleSetErrorDetail(error);
  if (detail) {
    const codes = detail.codes ?? [];
    return codes.length > 0 ? codes.map(codeMessage) : [detail.message];
  }
  if (error instanceof ApiError) {
    // 403 da API (`require_admin`) traz `detail` em texto; 403 sem corpo da API
    // (mensagem padrão do `apiFetch`) é da borda (WAF), que só existe em produção.
    if (error.status === 403) {
      return [error.message.includes('(HTTP 403)') ? EDGE_BLOCKED_MESSAGE : error.message];
    }
    // 422 do Pydantic (forma): lista de erros sem código do dono.
    if (Array.isArray(error.detail)) return ['Formato do conjunto inválido; revise os campos.'];
    return [error.message];
  }
  return [fallback];
}

export function isPublishConflict(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

// ---------------------------------------------------------------------------
// Simulador: formulário -> fatos
// ---------------------------------------------------------------------------

export type SimulateForm = {
  working_set_index: DraftNumber;
  fatigue_band: string;
  reps: DraftNumber;
  rpe: DraftNumber;
  rpe_ceiling: DraftNumber;
  planned_rpe: DraftNumber;
  weight_kg: DraftNumber;
  step_kg: DraftNumber;
  joint_pain: boolean;
  muscle_pain: DraftNumber;
};

export const EMPTY_SIMULATE_FORM: SimulateForm = {
  working_set_index: '',
  fatigue_band: FATIGUE_BAND_OPTIONS[0]?.value ?? UNKNOWN_FATIGUE_BAND,
  reps: '',
  rpe: '',
  rpe_ceiling: '',
  planned_rpe: '',
  weight_kg: '',
  step_kg: '',
  joint_pain: false,
  muscle_pain: '',
};

function orNull(value: DraftNumber): number | null {
  return value === '' ? null : value;
}

export function simulateFacts(form: SimulateForm): SimulateFacts {
  return {
    working_set_index: orNull(form.working_set_index),
    fatigue_band: form.fatigue_band === UNKNOWN_FATIGUE_BAND ? null : form.fatigue_band,
    reps: orNull(form.reps),
    rpe: orNull(form.rpe),
    rpe_ceiling: orNull(form.rpe_ceiling),
    planned_rpe: orNull(form.planned_rpe),
    weight_kg: orNull(form.weight_kg),
    step_kg: orNull(form.step_kg),
    joint_pain: form.joint_pain,
    muscle_pain: orNull(form.muscle_pain),
  };
}

/** Campos mínimos para a simulação fazer sentido (o motor devolve null sem eles). */
export function simulateMissing(form: SimulateForm): string[] {
  const missing: string[] = [];
  if (form.reps === '') missing.push('reps');
  if (form.weight_kg === '') missing.push('carga');
  if (form.step_kg === '') missing.push('passo');
  return missing;
}

export function toDraftNumber(value: string | number): DraftNumber {
  if (value === '' || value === null || value === undefined) return '';
  const parsed = typeof value === 'number' ? value : Number(String(value).replace(',', '.'));
  return Number.isFinite(parsed) ? parsed : '';
}
