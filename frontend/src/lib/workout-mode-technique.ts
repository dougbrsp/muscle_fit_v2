// Fase 5 — sub-etapa de aplicação da técnica avançada no Modo Treino (modo
// normal E "Fazer treino avançado"): âncora, aplicações já lançadas do bloco,
// equivalente ao vivo e rótulos. Funções puras; a orquestração fica em
// `modo-treino/PlanExecutionScreen.tsx`.

import { getAdvancedTechniqueApplicationNoun, isApplicationStageTechnique } from './advanced-techniques';
import {
  coefficientsFor,
  realizedBlockEquivalent,
  referenceReps,
  applicationEquivalent,
  type TechniqueApplication,
  type TechniqueCatalog,
} from './technique-accounting';
import { isTechniqueApplicationSetType, isWorkingSetType } from './set-types';

// `set_type` das linhas de aplicação: espelho único `./set-types` (dono
// `set_types.TECHNIQUE_APPLICATION_SET_TYPES` no backend, M76 Lote 3b).
export { isTechniqueApplicationSetType };

export type TechniqueSetRow = {
  set_number?: number | null;
  set_type?: string | null;
  reps?: number | null;
  weight_kg?: number | string | null;
  rpe?: number | string | null;
  technique_code?: string | null;
  anchor_set_number?: number | null;
};

function toNumber(value: unknown): number | null {
  if (value == null || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}


/** Séries de TRABALHO gravadas ("Série N de S" conta só estas). */
export function countWorkingSets(sets: ReadonlyArray<{ set_type?: string | null }>): number {
  return sets.filter((set) => isWorkingSetType(set.set_type)).length;
}

/** Nº de aplicações que viram sub-etapa (rest-pause/drop-set); 0 para FST-7/GVT/além da falha/sem técnica. */
export function techniqueStageTarget(planExercise: {
  advanced_technique?: string | null;
  advanced_technique_applications?: number | null;
}): number {
  const code = planExercise.advanced_technique;
  if (!code || !isApplicationStageTechnique(code)) return 0;
  return Math.max(0, planExercise.advanced_technique_applications || 0);
}

/** Âncora = última série de trabalho gravada (maior `set_number`). */
export function findAnchorWorkingSet<T extends TechniqueSetRow>(sets: ReadonlyArray<T>): T | null {
  let anchor: T | null = null;
  for (const set of sets) {
    if (!isWorkingSetType(set.set_type) || set.set_number == null) continue;
    if (!anchor || (anchor.set_number ?? 0) < set.set_number) anchor = set;
  }
  return anchor;
}

/**
 * Aplicações já gravadas ligadas à âncora: vínculo explícito
 * (`anchor_set_number`) ou, em linha sem vínculo, posterior à âncora — a
 * mesma derivação do backend (`derive_anchor_set_number`).
 */
export function blockApplicationRows<T extends TechniqueSetRow>(sets: ReadonlyArray<T>, anchorSetNumber: number | null): T[] {
  if (anchorSetNumber == null) return [];
  const workingNumbers = sets
    .filter((set) => isWorkingSetType(set.set_type) && set.set_number != null)
    .map((set) => set.set_number as number);
  // `derive_anchor_set_number`: última série de trabalho ANTERIOR à linha.
  const derivedAnchor = (setNumber: number) =>
    workingNumbers.filter((number) => number < setNumber).reduce<number | null>((max, n) => (max == null || n > max ? n : max), null);
  return sets
    .filter((set) => isTechniqueApplicationSetType(set.set_type))
    .filter((set) =>
      set.anchor_set_number != null
        ? set.anchor_set_number === anchorSetNumber
        : set.set_number != null && derivedAnchor(set.set_number) === anchorSetNumber,
    )
    .sort((a, b) => (a.set_number ?? 0) - (b.set_number ?? 0));
}

export function toTechniqueApplication(row: TechniqueSetRow, fallbackCode: string | null): TechniqueApplication {
  return {
    techniqueCode: row.technique_code ?? fallbackCode,
    reps: toNumber(row.reps),
    rpe: toNumber(row.rpe),
  };
}

export type TechniqueLivePreview = { application: number; block: number; cap: number; capped: boolean };

/**
 * Feedback ao vivo do card: quanto a aplicação digitada vale e como fica o
 * bloco (aplicações já gravadas da âncora + esta), com o teto do catálogo.
 * `null` quando a técnica não tem teto (fora do catálogo, FST-7/GVT).
 */
export function previewTechniqueApplication(params: {
  catalog: TechniqueCatalog;
  techniqueCode: string;
  anchor: TechniqueSetRow | null;
  previousApplications: ReadonlyArray<TechniqueApplication>;
  reps: number | null;
  rpe: number | null;
  repsTarget?: string | null;
}): TechniqueLivePreview | null {
  const { catalog, techniqueCode, anchor, previousApplications, reps, rpe, repsTarget } = params;
  const cap = coefficientsFor(techniqueCode, catalog).maxEquivalentPerAnchor;
  if (cap <= 0) return null;
  const anchorReps = anchor?.reps ?? null;
  const anchorRpe = toNumber(anchor?.rpe);
  const current: TechniqueApplication = { techniqueCode, reps, rpe };
  const application = applicationEquivalent(current, {
    refReps: referenceReps(anchorReps, repsTarget),
    anchorRpe,
    catalog,
  });
  const block = realizedBlockEquivalent([...previousApplications, current], {
    anchorReps,
    anchorRpe,
    repsTarget,
    catalog,
  });
  return {
    application,
    block: block.byTechnique[techniqueCode] ?? 0,
    cap,
    capped: block.capped,
  };
}

function formatRpe(rpe: number): string {
  return rpe.toLocaleString('pt-BR', { maximumFractionDigits: 1 });
}

/** Meta do card: "até a falha (RPE 10)" — nunca as reps da série. */
export function techniqueGoalLabel(anchorRpe: unknown): string {
  const rpe = toNumber(anchorRpe) ?? 10;
  return `até a falha (RPE ${formatRpe(rpe)})`;
}

/** "Rest-pause · mini-bloco 1/3" / "Drop 1/2". */
export function techniqueStageLabel(techniqueCode: string, techniqueName: string | null, index: number, total: number): string {
  const noun = getAdvancedTechniqueApplicationNoun(techniqueCode);
  if (noun === 'Mini-bloco') return `${techniqueName || 'Rest-pause'} · mini-bloco ${index}/${total}`;
  return `${noun} ${index}/${total}`;
}

/** Subitens do card: "Mini-bloco 1: 6 reps · RPE 10". */
export function describeAppliedRows(techniqueCode: string, rows: ReadonlyArray<TechniqueSetRow>): string[] {
  const noun = getAdvancedTechniqueApplicationNoun(techniqueCode);
  return rows.map((row, index) => {
    const rpe = toNumber(row.rpe);
    return `${noun} ${index + 1}: ${row.reps ?? '-'} reps${rpe != null ? ` · RPE ${formatRpe(rpe)}` : ''}`;
  });
}
