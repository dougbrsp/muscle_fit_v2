/**
 * Tela de cadastro das regras de carga da próxima série (`/regras-carga`).
 *
 * Só FORMA e montagem dos campos: variáveis, escopos, operadores, tokens,
 * ações e rótulos vêm de `motor-constants.generated.json`
 * (`next_set_load.rule_vocabulary`, exportado pelo dono
 * `api/app/domain/next_set_load.py`), os limites de `next_set_load.rule_limits`.
 * Nenhuma regra é avaliada aqui: o texto de cada regra vem do servidor
 * (`text` / `POST /describe`), a simulação é sempre `POST /simulate` e a
 * validação semântica é do dono (`codes` do 422).
 */
import { ApiError } from './api';
import motorConstants from './motor-constants.generated.json';
import type {
  ConditionGroupPayload,
  ConditionLeafPayload,
  RulePayload,
  RuleSetErrorDetail,
  RuleSetInput,
  RuleValue,
  SimulateFacts,
} from './next-set-load-rules-types';

const NEXT_SET_LOAD = motorConstants.next_set_load;

export const RULE_VOCABULARY = NEXT_SET_LOAD.rule_vocabulary;
export const RULE_LIMITS = NEXT_SET_LOAD.rule_limits;
/** O conjunto padrão em código (versão 0), exportado pelo dono (já na árvore v2). */
export const MOTOR_DEFAULT_RULESET: RuleSetInput = NEXT_SET_LOAD.default_ruleset as RuleSetInput;

export type SelectOption = { value: string; label: string };

// Identificadores do vocabulário que a tela trata de forma especial (forma, não
// regra): "qualquer" (sem condição), o operador de intervalo (pede o 2º valor),
// as modalidades de magnitude e as variáveis cujas faixas vêm de `rule_limits`.
const ANY_VALUE = 'any';
const BETWEEN_OP = 'between';
const OP_EQ = '=';
const OP_LT = '<';
const MAGNITUDE_NONE = 'none';
const MAGNITUDE_OPTIONAL = 'optional';
const MAGNITUDE_REQUIRED = 'required';
const VAR_SET_INDEX = 'set_index';
const VAR_FATIGUE_BAND = 'fatigue_band';
const VAR_REPS = 'reps';
const VAR_RPE = 'rpe';
const SCOPE_CURRENT = 'current';
const LOGIC_AND = 'and';
/** Valor do Select de origem do número (o resto são os tokens/enums do vocabulário). */
export const NUMBER_SOURCE = 'number';
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
// Vocabulário (tudo do dono): variáveis, escopos, operadores, tokens, ações
// ---------------------------------------------------------------------------

function options(items: readonly { value: string; label: string }[]): SelectOption[] {
  return items.map((item) => ({ value: item.value, label: item.label }));
}

export type VariableDef = {
  value: string;
  label: string;
  kind: string;
  scopes: string[];
  ops: string[];
  tokens?: SelectOption[];
  enum_values?: SelectOption[];
};

export type ActionDef = { value: string; label: string; magnitude: string };

export const VARIABLES: VariableDef[] = RULE_VOCABULARY.variables as VariableDef[];
export const VARIABLE_OPTIONS: SelectOption[] = options(VARIABLES);
export const SCOPE_OPTIONS: SelectOption[] = options(RULE_VOCABULARY.scopes);
export const LOGIC_OPTIONS: SelectOption[] = options(RULE_VOCABULARY.logic_ops);
export const ACTIONS: ActionDef[] = RULE_VOCABULARY.actions as ActionDef[];
export const ACTION_OPTIONS: SelectOption[] = options(ACTIONS);
export const MAGNITUDE_KIND_OPTIONS: SelectOption[] = options(RULE_VOCABULARY.magnitude_kinds);
export const FATIGUE_OPTIONS: SelectOption[] = options(RULE_VOCABULARY.fatigue_conditions);
const OP_LABELS: Readonly<Record<string, string>> = Object.fromEntries(
  RULE_VOCABULARY.condition_ops.map((item) => [item.value, item.label]),
);
/** Faixas de fadiga de uma série (simulador): as do vocabulário menos "qualquer", mais "desconhecida". */
export const FATIGUE_BAND_OPTIONS: SelectOption[] = [
  ...FATIGUE_OPTIONS.filter((option) => option.value !== ANY_VALUE),
  { value: UNKNOWN_FATIGUE_BAND, label: 'desconhecida' },
];

export function variableDef(variable: string): VariableDef | undefined {
  return VARIABLES.find((item) => item.value === variable);
}

export function optionLabel(items: readonly SelectOption[], value: string | null | undefined): string {
  return items.find((item) => item.value === value)?.label ?? String(value ?? '');
}

export function scopeOptionsFor(variable: string): SelectOption[] {
  const allowed = variableDef(variable)?.scopes ?? [];
  return SCOPE_OPTIONS.filter((option) => allowed.includes(option.value));
}

export function opOptionsFor(variable: string): SelectOption[] {
  const allowed = variableDef(variable)?.ops ?? [];
  return allowed.map((op) => ({ value: op, label: OP_LABELS[op] ?? op }));
}

export function isEnumVariable(variable: string): boolean {
  return variableDef(variable)?.kind === 'enum';
}

/** Origem do valor: enum = os valores do vocabulário; número = "número" + tokens da variável. */
export function valueSourceOptionsFor(variable: string): SelectOption[] {
  const def = variableDef(variable);
  if (!def) return [{ value: NUMBER_SOURCE, label: 'número' }];
  if (def.kind === 'enum') return options(def.enum_values ?? []);
  return [{ value: NUMBER_SOURCE, label: 'número' }, ...options(def.tokens ?? [])];
}

export function isBetween(op: string): boolean {
  return op === BETWEEN_OP;
}

export function actionDef(action: string): ActionDef | undefined {
  return ACTIONS.find((item) => item.value === action);
}

/** "none" não leva magnitude; "optional" aceita vazia; "required" exige. */
export function magnitudeMode(action: string): string {
  return actionDef(action)?.magnitude ?? MAGNITUDE_NONE;
}

export function actionHasMagnitude(action: string): boolean {
  return magnitudeMode(action) !== MAGNITUDE_NONE;
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
export const SET_INDEX_BOUNDS = { min: 1, max: RULE_LIMITS.set_index_max } as const;

/** Faixa numérica da variável (só forma: `rule_limits`); sem faixa conhecida = null. */
export function valueBoundsFor(variable: string): { min: number; max: number } | null {
  if (variable === VAR_SET_INDEX) return SET_INDEX_BOUNDS;
  if (variable === VAR_REPS) return REPS_VALUE_BOUNDS;
  if (variable === VAR_RPE) return RPE_VALUE_BOUNDS;
  return null;
}

/** A folha do RPE aceita "limitar pelo RPE planejado" (`ramp_cap`). */
export function supportsRampCap(variable: string, scope: string): boolean {
  return variable === VAR_RPE && scope === SCOPE_CURRENT;
}

// ---------------------------------------------------------------------------
// Rascunho local (com ids) <-> payload
// ---------------------------------------------------------------------------

export type DraftNumber = number | '';

export type ValueDraft = { source: string; number: DraftNumber };

export type LeafDraft = {
  kind: 'leaf';
  id: string;
  variable: string;
  scope: string;
  op: string;
  value: ValueDraft;
  valueTo: ValueDraft;
  rampCap: boolean;
};

export type GroupDraft = { kind: 'group'; id: string; logic: string; children: NodeDraft[] };

export type NodeDraft = LeafDraft | GroupDraft;

export type RuleDraft = {
  localId: string;
  when: GroupDraft;
  action: string;
  /** Guardada mesmo em "Manter" (volta ao trocar a ação); só vai no payload com ação que a aceita. */
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

function nextNodeId(): string {
  localIdCounter += 1;
  return `node-${localIdCounter}`;
}

const emptyValue = (): ValueDraft => ({ source: NUMBER_SOURCE, number: '' });

function valueFromPayload(value: RuleValue | null | undefined): ValueDraft {
  if (typeof value === 'number') return { source: NUMBER_SOURCE, number: value };
  if (typeof value === 'string' && value !== '') return { source: value, number: '' };
  return emptyValue();
}

function isGroupPayload(node: ConditionLeafPayload | ConditionGroupPayload): node is ConditionGroupPayload {
  return 'children' in node;
}

function nodeFromPayload(node: ConditionLeafPayload | ConditionGroupPayload): NodeDraft {
  if (isGroupPayload(node)) return groupFromPayload(node);
  return {
    kind: 'leaf',
    id: nextNodeId(),
    variable: node.variable,
    scope: node.scope,
    op: node.op,
    value: valueFromPayload(node.value),
    valueTo: valueFromPayload(node.value_to),
    rampCap: node.ramp_cap === true,
  };
}

function groupFromPayload(group: ConditionGroupPayload): GroupDraft {
  return { kind: 'group', id: nextNodeId(), logic: group.logic, children: group.children.map(nodeFromPayload) };
}

function defaultMagnitude(): RuleDraft['magnitude'] {
  const kind = MAGNITUDE_KIND_OPTIONS[0]?.value ?? 'steps';
  return { kind, value: magnitudeBounds(kind).min };
}

function magnitudeFromPayload(rule: RulePayload): RuleDraft['magnitude'] {
  if (rule.magnitude) return { kind: rule.magnitude.kind, value: rule.magnitude.value };
  // Ação com magnitude opcional e sem ela (ex.: recomendação do motor): campo vazio.
  if (magnitudeMode(rule.action) === MAGNITUDE_OPTIONAL) return { kind: defaultMagnitude().kind, value: '' };
  return defaultMagnitude();
}

export function ruleFromPayload(rule: RulePayload): RuleDraft {
  // O dono sempre devolve v2 (`when`); sem ele o contrato foi quebrado, e a tela não converte regra localmente.
  if (!rule.when) throw new Error('Regra sem condições (`when`): resposta fora do contrato v2 do servidor.');
  return {
    localId: nextLocalId(),
    when: groupFromPayload(rule.when),
    action: rule.action,
    magnitude: magnitudeFromPayload(rule),
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

/** `undefined` = campo vazio (não dá para enviar). */
function nodeToPayload(node: NodeDraft): ConditionLeafPayload | ConditionGroupPayload | undefined {
  if (node.kind === 'group') return groupToPayload(node);
  const leaf: ConditionLeafPayload = { variable: node.variable, scope: node.scope, op: node.op };
  const value = valueToPayload(node.value);
  if (value === null) {
    if (!node.rampCap) return undefined;
  } else {
    leaf.value = value;
  }
  if (isBetween(node.op)) {
    const valueTo = valueToPayload(node.valueTo);
    if (valueTo === null) return undefined;
    leaf.value_to = valueTo;
  }
  if (node.rampCap) leaf.ramp_cap = true;
  return leaf;
}

function groupToPayload(group: GroupDraft): ConditionGroupPayload | undefined {
  const children: ConditionGroupPayload['children'] = [];
  for (const child of group.children) {
    const payload = nodeToPayload(child);
    if (payload === undefined) return undefined;
    children.push(payload);
  }
  return { logic: group.logic, children };
}

/**
 * Rascunho -> corpo de `PUT /draft`/`/describe`/`/simulate` (v2). A ordem da
 * lista vira `position` 1..n; `text` e `reason_code` não vão (o servidor gera o
 * texto). `null` = campo vazio (ver `shapeIssues`).
 */
export function draftToPayload(draft: RuleSetDraft): RuleSetInput | null {
  if (draft.reps_floor === '' || draft.reps_ceiling === '') return null;
  const rules: RulePayload[] = [];
  for (const [index, rule] of draft.rules.entries()) {
    const when = groupToPayload(rule.when);
    if (when === undefined) return null;
    const payload: RulePayload = { position: index + 1, when, action: rule.action };
    const mode = magnitudeMode(rule.action);
    if (mode !== MAGNITUDE_NONE) {
      if (rule.magnitude.value === '') {
        if (mode !== MAGNITUDE_OPTIONAL) return null;
      } else {
        payload.magnitude = { kind: rule.magnitude.kind, value: rule.magnitude.value };
      }
    }
    rules.push(payload);
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
// Edição da lista de regras
// ---------------------------------------------------------------------------

/** Move a regra de `from` para `to` (arrastar); a posição sai da ordem da lista. */
export function reorderRules(rules: readonly RuleDraft[], from: number, to: number): RuleDraft[] {
  const next = [...rules];
  if (from < 0 || from >= next.length || to < 0 || to >= next.length || from === to) return next;
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

function leafForVariable(variable: string, id: string): LeafDraft {
  const def = variableDef(variable);
  const sources = valueSourceOptionsFor(variable);
  const first = def?.kind === 'enum' ? sources[0] : sources[1];
  return {
    kind: 'leaf',
    id,
    variable,
    scope: def?.scopes[0] ?? SCOPE_CURRENT,
    op: def?.ops[0] ?? '',
    value: first ? { source: first.value, number: '' } : emptyValue(),
    valueTo: emptyValue(),
    rampCap: false,
  };
}

/** Folha nova: a primeira variável com tokens, já com valor (a regra nova nasce enviável). */
export function newLeaf(): LeafDraft {
  const def = VARIABLES.find((item) => (item.tokens?.length ?? 0) > 0) ?? VARIABLES[0];
  return leafForVariable(def.value, nextNodeId());
}

/** Troca a variável: escopo, operador e valor voltam a combinações aceitas por ela. */
export function leafWithVariable(leaf: LeafDraft, variable: string): LeafDraft {
  return leafForVariable(variable, leaf.id);
}

/** Troca o escopo; se a variável não aceita, fica o primeiro permitido. */
export function leafWithScope(leaf: LeafDraft, scope: string): LeafDraft {
  const allowed = variableDef(leaf.variable)?.scopes ?? [];
  const next = allowed.includes(scope) ? scope : (allowed[0] ?? scope);
  return { ...leaf, scope: next, rampCap: next === SCOPE_CURRENT ? leaf.rampCap : false };
}

export function newGroup(logic: string, children?: NodeDraft[]): GroupDraft {
  return { kind: 'group', id: nextNodeId(), logic, children: children ?? [newLeaf()] };
}

/** Lógica oposta (E <-> OU): o subgrupo novo alterna com o pai. */
export function oppositeLogic(logic: string): string {
  return LOGIC_OPTIONS.find((option) => option.value !== logic)?.value ?? logic;
}

/** Regra nova: um grupo com uma folha pronta e a primeira ação que exige magnitude. */
export function newRuleDraft(): RuleDraft {
  const action = ACTIONS.find((item) => item.magnitude === MAGNITUDE_REQUIRED)?.value ?? ACTIONS[0].value;
  return {
    localId: nextLocalId(),
    when: newGroup(LOGIC_OPTIONS[0]?.value ?? LOGIC_AND),
    action,
    magnitude: defaultMagnitude(),
    text: null,
  };
}

export function addRule(rules: readonly RuleDraft[]): RuleDraft[] {
  return [...rules, newRuleDraft()];
}

function cloneNode(node: NodeDraft): NodeDraft {
  if (node.kind === 'leaf') return { ...node, id: nextNodeId(), value: { ...node.value }, valueTo: { ...node.valueTo } };
  return { ...node, id: nextNodeId(), children: node.children.map(cloneNode) };
}

/** Cópia logo abaixo da original (ids novos em toda a árvore). */
export function duplicateRule(rules: readonly RuleDraft[], localId: string): RuleDraft[] {
  const index = rules.findIndex((rule) => rule.localId === localId);
  if (index < 0) return [...rules];
  const source = rules[index];
  const copy: RuleDraft = {
    ...source,
    localId: nextLocalId(),
    when: cloneNode(source.when) as GroupDraft,
    magnitude: { ...source.magnitude },
  };
  return [...rules.slice(0, index + 1), copy, ...rules.slice(index + 1)];
}

export function removeRule(rules: readonly RuleDraft[], localId: string): RuleDraft[] {
  return rules.filter((rule) => rule.localId !== localId);
}

/** Mudar a regra apaga o texto que veio do servidor até a próxima descrição (`POST /describe`). */
export function updateRule(rules: readonly RuleDraft[], localId: string, patch: Partial<RuleDraft>): RuleDraft[] {
  return rules.map((rule) => (rule.localId === localId ? { ...rule, ...patch, text: null } : rule));
}

/** Troca a ação; ação que exige magnitude com o campo vazio volta ao mínimo. */
export function ruleWithAction(rule: RuleDraft, action: string): Partial<RuleDraft> {
  const needs = magnitudeMode(action) === MAGNITUDE_REQUIRED && rule.magnitude.value === '';
  return { action, magnitude: needs ? defaultMagnitude() : rule.magnitude };
}

// ---------------------------------------------------------------------------
// Edição da árvore (imutável)
// ---------------------------------------------------------------------------

export function countLeaves(node: NodeDraft): number {
  if (node.kind === 'leaf') return 1;
  return node.children.reduce((total, child) => total + countLeaves(child), 0);
}

/** Profundidade do grupo (a raiz conta 1; as folhas não contam). */
export function groupDepth(group: GroupDraft): number {
  const depths = group.children.filter((child): child is GroupDraft => child.kind === 'group').map(groupDepth);
  return 1 + (depths.length > 0 ? Math.max(...depths) : 0);
}

export function setChild(group: GroupDraft, index: number, node: NodeDraft | null): GroupDraft {
  const children = [...group.children];
  if (node === null) children.splice(index, 1);
  else children[index] = node;
  return { ...group, children };
}

export function addLeafChild(group: GroupDraft): GroupDraft {
  return { ...group, children: [...group.children, newLeaf()] };
}

/** Subgrupo (parênteses) com a lógica oposta à do pai e uma folha pronta. */
export function addGroupChild(group: GroupDraft): GroupDraft {
  return { ...group, children: [...group.children, newGroup(oppositeLogic(group.logic))] };
}

export function duplicateChild(group: GroupDraft, index: number): GroupDraft {
  const source = group.children[index];
  if (!source) return group;
  const children = [...group.children];
  children.splice(index + 1, 0, cloneNode(source));
  return { ...group, children };
}

// ---------------------------------------------------------------------------
// Validação de FORMA (limites de `rule_limits`); a semântica é do servidor
// ---------------------------------------------------------------------------

function outside(value: number, bounds: { min: number; max: number }): boolean {
  return value < bounds.min || value > bounds.max;
}

function valueIssues(label: string, value: ValueDraft, bounds: { min: number; max: number } | null): string[] {
  if (value.source !== NUMBER_SOURCE) return [];
  if (value.number === '') return [`${label}: informe o valor.`];
  if (bounds && outside(value.number, bounds)) return [`${label}: use um valor entre ${bounds.min} e ${bounds.max}.`];
  return [];
}

function leafIssues(label: string, leaf: LeafDraft): string[] {
  const bounds = valueBoundsFor(leaf.variable);
  const name = optionLabel(VARIABLE_OPTIONS, leaf.variable);
  const issues: string[] = [];
  const skipValue = leaf.rampCap && leaf.value.source === NUMBER_SOURCE && leaf.value.number === '';
  if (!skipValue) issues.push(...valueIssues(`${label}, ${name}`, leaf.value, bounds));
  if (isBetween(leaf.op)) issues.push(...valueIssues(`${label}, ${name} (até)`, leaf.valueTo, bounds));
  return issues;
}

function groupIssues(label: string, group: GroupDraft, depth: number): string[] {
  const issues: string[] = [];
  if (depth > RULE_LIMITS.max_depth) issues.push(`${label}: no máximo ${RULE_LIMITS.max_depth} níveis de parênteses.`);
  if (group.children.length === 0) issues.push(`${label}: há um grupo vazio; adicione uma condição ou remova o grupo.`);
  for (const child of group.children) {
    if (child.kind === 'group') issues.push(...groupIssues(label, child, depth + 1));
    else issues.push(...leafIssues(label, child));
  }
  return issues;
}

/** Problemas de forma do rascunho (vazios, profundidade, folhas, faixas de `rule_limits`). Vazio = pode enviar. */
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
    if (countLeaves(rule.when) > RULE_LIMITS.max_leaves_per_rule)
      issues.push(`${prefix}: no máximo ${RULE_LIMITS.max_leaves_per_rule} condições por regra.`);
    issues.push(...groupIssues(prefix, rule.when, 1));
    const mode = magnitudeMode(rule.action);
    if (mode !== MAGNITUDE_NONE) {
      const bounds = magnitudeBounds(rule.magnitude.kind);
      if (rule.magnitude.value === '') {
        if (mode !== MAGNITUDE_OPTIONAL) issues.push(`${prefix}: informe a magnitude.`);
      } else if (outside(mode === MAGNITUDE_OPTIONAL ? Math.abs(rule.magnitude.value) : rule.magnitude.value, bounds))
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
  RULE_GROUP_EMPTY: 'há um grupo de condições vazio.',
  RULE_DEPTH_EXCEEDED: `parênteses demais (no máximo ${RULE_LIMITS.max_depth} níveis).`,
  RULE_TOO_MANY_LEAVES: `condições demais (no máximo ${RULE_LIMITS.max_leaves_per_rule} por regra).`,
  RULE_VARIABLE_UNKNOWN: 'variável desconhecida.',
  RULE_SCOPE_INVALID: 'esta variável não aceita o escopo escolhido (série executada ou anterior).',
  RULE_OP_INVALID_FOR_VARIABLE: 'o operador não vale para esta variável.',
  RULE_RAMP_CAP_INVALID: '"limitar pelo RPE planejado" só vale na condição de RPE da série executada.',
  RULE_ENGINE_TARGET_MAGNITUDE_INVALID: 'a recomendação do motor aceita só uma magnitude opcional válida.',
  RULE_SET_INDEX_OUT_OF_RANGE: `número da série fora da faixa ${SET_INDEX_BOUNDS.min}–${SET_INDEX_BOUNDS.max}.`,
  RULE_ENUM_VALUE_UNKNOWN: 'valor desconhecido para esta variável.',
  RULE_LOGIC_UNKNOWN: 'lógica desconhecida (use E ou OU).',
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
  rpe_target: DraftNumber;
  weight_kg: DraftNumber;
  step_kg: DraftNumber;
  engine_target_kg: DraftNumber;
  previous_reps: DraftNumber;
  previous_rpe: DraftNumber;
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
  rpe_target: '',
  weight_kg: '',
  step_kg: '',
  engine_target_kg: '',
  previous_reps: '',
  previous_rpe: '',
  joint_pain: false,
  muscle_pain: '',
};

function orNull(value: DraftNumber): number | null {
  return value === '' ? null : value;
}

export function simulateFacts(form: SimulateForm): SimulateFacts {
  const hasPrevious = form.previous_reps !== '' || form.previous_rpe !== '';
  return {
    working_set_index: orNull(form.working_set_index),
    fatigue_band: form.fatigue_band === UNKNOWN_FATIGUE_BAND ? null : form.fatigue_band,
    reps: orNull(form.reps),
    rpe: orNull(form.rpe),
    rpe_ceiling: orNull(form.rpe_ceiling),
    planned_rpe: orNull(form.planned_rpe),
    rpe_target: orNull(form.rpe_target),
    weight_kg: orNull(form.weight_kg),
    step_kg: orNull(form.step_kg),
    engine_target_kg: orNull(form.engine_target_kg),
    joint_pain: form.joint_pain,
    muscle_pain: orNull(form.muscle_pain),
    previous: hasPrevious ? { reps: orNull(form.previous_reps), rpe: orNull(form.previous_rpe) } : null,
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
