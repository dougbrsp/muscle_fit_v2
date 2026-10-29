// Capacidades de segurança por equipamento e override por par (PR-07 da
// refatoração dos motores). Contratos de `api/app/training_safety_admin.py`
// e a lógica pura das telas de admin (`/equipamentos-exercicios` e
// `/dominio-exercicios`). A regra de negócio mora no backend
// (`api/app/domain/training_safety.py`); aqui só há montagem de formulário,
// payload e rótulos — o "efetivo" exibido vem sempre da API.

import type { AdvancedTechniqueCatalogItem } from './advanced-techniques';
import motorConstants from './motor-constants.generated.json';

// M76 (C10): os limites de RPE seguro vêm do dono (`api/app/domain/training_safety.py`),
// exportados para `motor-constants.generated.json` e conferidos por
// `api/tests/unit/test_m76_lote1b_espelhos_fe.py`. Nenhum número escrito aqui.
export const MIN_SAFE_RPE: number = motorConstants.training_safety.min_safe_rpe;
export const MAX_SAFE_RPE: number = motorConstants.training_safety.max_safe_rpe;
// D3: `max_safe_rpe` desconhecido usa o default do backend (falha não permitida).
export const DEFAULT_MAX_SAFE_RPE: number = motorConstants.training_safety.default_max_safe_rpe;
export const SAFETY_NOTES_MAX_LENGTH = 2000;
export const LOAD_INCREMENT_MAX_KG = 999.99;

export const MAX_SAFE_RPE_UNKNOWN_HELP = `Vazio = desconhecido, tratado como ${DEFAULT_MAX_SAFE_RPE} (falha não permitida).`;
export const LOAD_INCREMENT_HELP =
  'Menor incremento de carga TOTAL do equipamento. Halteres: soma dos dois (halter de 1 em 1 kg por mão = 2 kg).';

export type SafetySource = 'exercise_equipment_override' | 'equipment_default' | null;
export type TechniquePermission = { code: string; source: Exclude<SafetySource, null> };
export type BlockedTechnique = TechniquePermission & { reason: string };

// `true`/`false` = marcação explícita; ausência = sem marcação.
export type TechniqueMarks = Record<string, boolean>;
// No PUT: `null` apaga a marcação (equipamento: volta a "sem marcação"; par: volta a herdar).
export type TechniqueMarksPayload = Record<string, boolean | null>;

export type EquipmentSafety = {
  equipment_id: number;
  equipment_name: string;
  equipment_type_id: number | null;
  max_safe_rpe: number | null;
  load_increment_kg: number | null;
  safety_notes: string | null;
  techniques: TechniqueMarks;
  effective: {
    max_safe_rpe: number;
    failure_allowed: boolean;
    safety_source: SafetySource;
    allowed_advanced_techniques: TechniquePermission[];
    blocked_advanced_techniques: BlockedTechnique[];
  };
};

export type EquipmentSafetyPayload = {
  max_safe_rpe?: number | null;
  load_increment_kg?: number | null;
  safety_notes?: string | null;
  techniques?: TechniqueMarksPayload;
};

export type EquipmentExerciseSafety = {
  equipment_exercise_id: number;
  equipment_id: number;
  equipment_name: string;
  exercise_id: number;
  exercise_name: string;
  is_active: boolean;
  max_safe_rpe_override: number | null;
  safety_notes: string | null;
  techniques: TechniqueMarks;
  inherited: { max_safe_rpe: number | null; techniques: TechniqueMarks };
  resolved: {
    max_safe_rpe: number;
    failure_allowed: boolean;
    safety_source: SafetySource;
    allowed_advanced_techniques: TechniquePermission[];
    blocked_advanced_techniques: BlockedTechnique[];
  };
};

export type EquipmentExerciseSafetyPayload = {
  max_safe_rpe_override?: number | null;
  safety_notes?: string | null;
  techniques?: TechniqueMarksPayload;
};

export const equipmentSafetyPath = (equipmentId: number) => `/equipment/${equipmentId}/safety`;
export const equipmentExerciseSafetyPath = (linkId: number) => `/equipment-exercises/${linkId}/safety`;
export const equipmentSafetyQueryKey = (equipmentId: number) => ['equipment-safety', equipmentId] as const;
export const equipmentExerciseSafetyQueryKey = (linkId: number) => ['equipment-exercise-safety', linkId] as const;

// ---------------------------------------------------------------------------
// RPE
// ---------------------------------------------------------------------------

/** Opções do select (faixa do backend); o vazio (desconhecido/herdar) é o `clearable` do Select. */
export const MAX_SAFE_RPE_OPTIONS: { value: string; label: string }[] = Array.from(
  { length: MAX_SAFE_RPE - MIN_SAFE_RPE + 1 },
  (_, index) => MIN_SAFE_RPE + index,
).map((rpe) => ({
  value: String(rpe),
  label: rpe === MAX_SAFE_RPE ? `${MAX_SAFE_RPE} = falha concêntrica permitida` : String(rpe),
}));

export function rpeToSelectValue(rpe: number | null | undefined): string | null {
  return rpe == null ? null : String(rpe);
}

export function selectValueToRpe(value: string | null): number | null {
  if (!value) return null;
  const rpe = Number(value);
  return Number.isInteger(rpe) && rpe >= MIN_SAFE_RPE && rpe <= MAX_SAFE_RPE ? rpe : null;
}

/** Rótulo do valor cadastrado no equipamento: "—" vira o default do backend. */
export function describeEquipmentMaxSafeRpe(rpe: number | null | undefined): string {
  return rpe == null ? `${DEFAULT_MAX_SAFE_RPE} (padrão: não cadastrado)` : String(rpe);
}

export type PairRpeView = { value: number; origin: 'override' | 'inherited' | 'default'; label: string };

/** Valor herdado × sobrescrito do RPE máximo seguro de um par. */
export function describePairMaxSafeRpe(override: number | null, inherited: number | null): PairRpeView {
  if (override != null) return { value: override, origin: 'override', label: `${override} (sobrescrito neste par)` };
  if (inherited != null) return { value: inherited, origin: 'inherited', label: `${inherited} (herdado do equipamento)` };
  return { value: DEFAULT_MAX_SAFE_RPE, origin: 'default', label: `${DEFAULT_MAX_SAFE_RPE} (padrão: equipamento sem cadastro)` };
}

// ---------------------------------------------------------------------------
// Incremento de carga (TOTAL — D12)
// ---------------------------------------------------------------------------

export type LoadIncrementParse = { value: number | null; error: string | null };

/** Aceita vírgula ou ponto; vazio = não cadastrado. Máx. 2 casas, > 0 e ≤ 999,99. */
export function parseLoadIncrement(text: string): LoadIncrementParse {
  const trimmed = text.trim();
  if (!trimmed) return { value: null, error: null };
  if (!/^\d{1,3}([.,]\d{1,2})?$/.test(trimmed)) {
    return { value: null, error: 'Informe um número com até 2 casas decimais (ex.: 2,5).' };
  }
  const value = Number(trimmed.replace(',', '.'));
  if (!(value > 0)) return { value: null, error: 'O incremento precisa ser maior que zero.' };
  if (value > LOAD_INCREMENT_MAX_KG) return { value: null, error: 'Valor acima do máximo (999,99 kg).' };
  return { value, error: null };
}

export function formatLoadIncrement(value: number | null | undefined): string {
  return value == null ? '' : String(value).replace('.', ',');
}

// ---------------------------------------------------------------------------
// Técnicas — equipamento (checkbox)
// ---------------------------------------------------------------------------

const NEEDS_FAILURE = new Set(['requires_failure', 'beyond_failure']);

export function techniqueNeedsFailure(item: Pick<AdvancedTechniqueCatalogItem, 'failure_requirement'>): boolean {
  return NEEDS_FAILURE.has(item.failure_requirement);
}

/** Técnica marcada que o backend vai bloquear: exige falha e o RPE efetivo é abaixo do de falha. */
export function isBlockedByFailure(
  item: Pick<AdvancedTechniqueCatalogItem, 'failure_requirement'>,
  effectiveMaxSafeRpe: number,
): boolean {
  return techniqueNeedsFailure(item) && effectiveMaxSafeRpe < MAX_SAFE_RPE;
}

export function compatibleCodes(marks: TechniqueMarks): string[] {
  return Object.keys(marks)
    .filter((code) => marks[code])
    .sort();
}

/**
 * Salvar o formulário do equipamento = o admin REVISOU todas as técnicas
 * ativas: marcada → `true`, desmarcada → `false` (incompatível explícito, some
 * do relatório de backfill como "sem marcação"). Códigos inativos que já
 * tinham marcação não são tocados.
 */
export function buildEquipmentTechniquesPayload(
  catalog: readonly Pick<AdvancedTechniqueCatalogItem, 'code' | 'is_active'>[],
  checked: ReadonlySet<string>,
): TechniqueMarksPayload {
  const payload: TechniqueMarksPayload = {};
  for (const item of catalog) if (item.is_active) payload[item.code] = checked.has(item.code);
  return payload;
}

export type EquipmentSafetyForm = {
  maxSafeRpe: string | null;
  loadIncrement: string;
  safetyNotes: string;
  checkedTechniques: ReadonlySet<string>;
};

export function equipmentSafetyToForm(safety: EquipmentSafety): EquipmentSafetyForm {
  return {
    maxSafeRpe: rpeToSelectValue(safety.max_safe_rpe),
    loadIncrement: formatLoadIncrement(safety.load_increment_kg),
    safetyNotes: safety.safety_notes ?? '',
    checkedTechniques: new Set(compatibleCodes(safety.techniques)),
  };
}

export type PayloadResult<T> = { payload: T; error: null } | { payload: null; error: string };

export function buildEquipmentSafetyPayload(
  form: EquipmentSafetyForm,
  catalog: readonly Pick<AdvancedTechniqueCatalogItem, 'code' | 'is_active'>[],
): PayloadResult<EquipmentSafetyPayload> {
  const increment = parseLoadIncrement(form.loadIncrement);
  if (increment.error) return { payload: null, error: increment.error };
  if (form.safetyNotes.length > SAFETY_NOTES_MAX_LENGTH) {
    return { payload: null, error: `Notas com no máximo ${SAFETY_NOTES_MAX_LENGTH} caracteres.` };
  }
  return {
    error: null,
    payload: {
      max_safe_rpe: selectValueToRpe(form.maxSafeRpe),
      load_increment_kg: increment.value,
      safety_notes: form.safetyNotes.trim() || null,
      techniques: buildEquipmentTechniquesPayload(catalog, form.checkedTechniques),
    },
  };
}

// ---------------------------------------------------------------------------
// Edição em lote por tipo de equipamento
// ---------------------------------------------------------------------------

export type BatchSafetyForm = {
  applyMaxSafeRpe: boolean;
  maxSafeRpe: string | null;
  applyLoadIncrement: boolean;
  loadIncrement: string;
  applyTechniques: boolean;
  checkedTechniques: ReadonlySet<string>;
};

/**
 * Só os campos com "aplicar" ligado entram no payload — o PUT não toca campo
 * ausente, então o lote nunca apaga o que o admin não quis mexer (notas nunca
 * entram em lote).
 */
export function buildBatchSafetyPayload(
  form: BatchSafetyForm,
  catalog: readonly Pick<AdvancedTechniqueCatalogItem, 'code' | 'is_active'>[],
): PayloadResult<EquipmentSafetyPayload> {
  const payload: EquipmentSafetyPayload = {};
  if (form.applyMaxSafeRpe) payload.max_safe_rpe = selectValueToRpe(form.maxSafeRpe);
  if (form.applyLoadIncrement) {
    const increment = parseLoadIncrement(form.loadIncrement);
    if (increment.error) return { payload: null, error: increment.error };
    payload.load_increment_kg = increment.value;
  }
  if (form.applyTechniques) payload.techniques = buildEquipmentTechniquesPayload(catalog, form.checkedTechniques);
  if (Object.keys(payload).length === 0) return { payload: null, error: 'Escolha ao menos um campo para aplicar.' };
  return { payload, error: null };
}

export function equipmentOfType<T extends { id: number; equipment_type_id?: number | null }>(
  equipment: readonly T[],
  typeId: number | null,
): T[] {
  return typeId == null ? [] : equipment.filter((item) => item.equipment_type_id === typeId);
}

export type BatchOutcome = { id: number; ok: boolean; error?: string };

/** Um PUT por equipamento, em sequência; falha de um não interrompe os demais. */
export async function runBatch(
  ids: readonly number[],
  save: (id: number) => Promise<unknown>,
  onProgress?: (done: number, total: number) => void,
): Promise<BatchOutcome[]> {
  const outcomes: BatchOutcome[] = [];
  for (const id of ids) {
    try {
      await save(id);
      outcomes.push({ id, ok: true });
    } catch (error) {
      outcomes.push({ id, ok: false, error: error instanceof Error ? error.message : 'Erro desconhecido.' });
    }
    onProgress?.(outcomes.length, ids.length);
  }
  return outcomes;
}

// ---------------------------------------------------------------------------
// Técnicas — par (herdar × sobrescrever)
// ---------------------------------------------------------------------------

export type PairTechniqueChoice = 'inherit' | 'compatible' | 'incompatible';

export const PAIR_TECHNIQUE_CHOICES: { value: PairTechniqueChoice; label: string }[] = [
  { value: 'inherit', label: 'Herdar' },
  { value: 'compatible', label: 'Compatível' },
  { value: 'incompatible', label: 'Incompatível' },
];

export function pairTechniqueChoice(code: string, overrides: TechniqueMarks): PairTechniqueChoice {
  if (!(code in overrides)) return 'inherit';
  return overrides[code] ? 'compatible' : 'incompatible';
}

export function pairChoicesFromSafety(
  safety: Pick<EquipmentExerciseSafety, 'techniques'>,
  catalog: readonly Pick<AdvancedTechniqueCatalogItem, 'code'>[],
): Record<string, PairTechniqueChoice> {
  const choices: Record<string, PairTechniqueChoice> = {};
  for (const item of catalog) choices[item.code] = pairTechniqueChoice(item.code, safety.techniques);
  return choices;
}

/** "Compatível"/"Incompatível" que o par herda do equipamento (sem marcação = incompatível, D3). */
export function describeInheritedTechnique(code: string, inherited: TechniqueMarks): string {
  if (!(code in inherited)) return 'Incompatível (equipamento sem marcação)';
  return inherited[code] ? 'Compatível (equipamento)' : 'Incompatível (equipamento)';
}

/** Compatibilidade efetiva do par antes da validação de falha do backend. */
export function pairTechniqueCompatible(choice: PairTechniqueChoice, code: string, inherited: TechniqueMarks): boolean {
  if (choice === 'inherit') return inherited[code] === true;
  return choice === 'compatible';
}

export type PairSafetyForm = {
  maxSafeRpeOverride: string | null;
  safetyNotes: string;
  choices: Readonly<Record<string, PairTechniqueChoice>>;
};

export function pairSafetyToForm(
  safety: EquipmentExerciseSafety,
  catalog: readonly Pick<AdvancedTechniqueCatalogItem, 'code'>[],
): PairSafetyForm {
  return {
    maxSafeRpeOverride: rpeToSelectValue(safety.max_safe_rpe_override),
    safetyNotes: safety.safety_notes ?? '',
    choices: pairChoicesFromSafety(safety, catalog),
  };
}

/**
 * Só envia técnica cuja escolha MUDOU em relação ao salvo: `inherit` → `null`
 * (apaga o override). Evita gravar `updated_by` em linhas que o admin não tocou.
 */
export function buildPairSafetyPayload(
  form: PairSafetyForm,
  saved: Pick<EquipmentExerciseSafety, 'techniques'>,
): PayloadResult<EquipmentExerciseSafetyPayload> {
  if (form.safetyNotes.length > SAFETY_NOTES_MAX_LENGTH) {
    return { payload: null, error: `Notas com no máximo ${SAFETY_NOTES_MAX_LENGTH} caracteres.` };
  }
  const techniques: TechniqueMarksPayload = {};
  for (const [code, choice] of Object.entries(form.choices)) {
    if (choice === pairTechniqueChoice(code, saved.techniques)) continue;
    techniques[code] = choice === 'inherit' ? null : choice === 'compatible';
  }
  return {
    error: null,
    payload: {
      max_safe_rpe_override: selectValueToRpe(form.maxSafeRpeOverride),
      safety_notes: form.safetyNotes.trim() || null,
      ...(Object.keys(techniques).length > 0 ? { techniques } : {}),
    },
  };
}

export function hasPairOverride(safety: Pick<EquipmentExerciseSafety, 'max_safe_rpe_override' | 'techniques'>): boolean {
  return safety.max_safe_rpe_override != null || Object.keys(safety.techniques).length > 0;
}
