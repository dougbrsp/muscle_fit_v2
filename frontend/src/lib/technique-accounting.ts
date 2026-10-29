// Contabilização de técnicas avançadas (Fase 5) — ESPELHO de
// `api/app/domain/technique_accounting.py`.
//
// O Modo Treino mostra, ao vivo, quanto cada mini-bloco/drop vale em séries
// de trabalho ("= 0,5 série equivalente · bloco: 0,5/1,0"). O número exibido
// tem de ser o MESMO que o backend vai contar, então esta lib reproduz a regra
// linha a linha, com os coeficientes do catálogo (`GET /advanced-techniques`,
// migração 060). Sem constante própria: códigos, pesos da família além da
// falha, teto padrão, RPEs de referência e pontos de esforço (dono real:
// `MuscleRecoveryService._EFFORT_POINTS`) vêm de `motor-constants.generated.json`
// (seção `technique_accounting`), e o vitest roda esta lib contra
// `technique-accounting.parity.json` — ambos gerados pelo dono
// (`api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`). Regra nova ou
// diferente muda NO DONO; aqui só se regenera.

import motorConstants from './motor-constants.generated.json';

const RULE = motorConstants.technique_accounting;

export type TechniqueCatalogRow = {
  activation_set_equivalent?: number | null;
  max_equivalent_per_anchor?: number | null;
  default_sets_count?: number | null;
  set_type?: string | null;
};

export type TechniqueCatalog = Readonly<Record<string, TechniqueCatalogRow>>;

// Técnicas cujo realizado é ponderado pelas reps E pelo esforço.
export const REPS_WEIGHTED_CODES: ReadonlySet<string> = new Set(RULE.reps_weighted_codes);

// Família além da falha: peso das reps extras.
export const BEYOND_FAILURE_WEIGHTS: Readonly<Record<string, number>> = RULE.beyond_failure_weights;

// Técnicas que só preenchem o número de séries (não abrem sub-etapa).
export const SETS_ONLY_CODES: ReadonlySet<string> = new Set(RULE.sets_only_codes);

export const DEFAULT_MAX_EQUIVALENT: number = RULE.default_max_equivalent;
export const FULL_EFFORT_RPE: number = RULE.full_effort_rpe;
export const DEFAULT_REFERENCE_RPE: number = RULE.default_reference_rpe;

// `MuscleRecoveryService._EFFORT_POINTS` (RPE → pontos, interpolação linear;
// o RPE é limitado ao primeiro e ao último ponto da tabela).
const EFFORT_POINTS: ReadonlyArray<readonly [number, number]> = RULE.effort_points.map(
  ([rpe, points]) => [rpe, points] as const,
);
const EFFORT_RPE_MIN = EFFORT_POINTS[0][0];
const EFFORT_RPE_MAX = EFFORT_POINTS[EFFORT_POINTS.length - 1][0];

function toNumber(value: unknown): number | null {
  if (value == null || value === '') return null;
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

export function effortPoints(rpe: number): number {
  const clamped = Math.min(EFFORT_RPE_MAX, Math.max(EFFORT_RPE_MIN, rpe));
  for (let i = 0; i < EFFORT_POINTS.length - 1; i += 1) {
    const [x0, y0] = EFFORT_POINTS[i];
    const [x1, y1] = EFFORT_POINTS[i + 1];
    if (x0 <= clamped && clamped <= x1) {
      if (x1 === x0) return y0;
      return y0 + ((clamped - x0) / (x1 - x0)) * (y1 - y0);
    }
  }
  return EFFORT_POINTS[EFFORT_POINTS.length - 1][1];
}

export type TechniqueCoefficients = {
  code: string;
  activationSetEquivalent: number;
  maxEquivalentPerAnchor: number;
  defaultSetsCount: number | null;
};

/** Coeficientes de `code`. Fora do catálogo vale 0; teto ausente (não calibrado) = 1 série. */
export function coefficientsFor(code: string | null | undefined, catalog: TechniqueCatalog): TechniqueCoefficients {
  const key = (code ?? '').trim();
  const row = catalog[key];
  if (!row) return { code: key, activationSetEquivalent: 0, maxEquivalentPerAnchor: 0, defaultSetsCount: null };
  const cap = toNumber(row.max_equivalent_per_anchor);
  const sets = toNumber(row.default_sets_count);
  return {
    code: key,
    activationSetEquivalent: toNumber(row.activation_set_equivalent) ?? 0,
    maxEquivalentPerAnchor: cap == null ? DEFAULT_MAX_EQUIVALENT : cap,
    defaultSetsCount: sets == null ? null : Math.trunc(sets),
  };
}

/** 1 se a aplicação chegou ao esforço da âncora (ou >= 9); sem RPE da aplicação = esforço cheio. */
export function effortFactor(rpeApplication: unknown, rpeReference: unknown): number {
  const app = toNumber(rpeApplication);
  if (app == null) return 1.0;
  const ref = toNumber(rpeReference) ?? DEFAULT_REFERENCE_RPE;
  if (app >= ref || app >= FULL_EFFORT_RPE) return 1.0;
  return Math.min(1.0, effortPoints(app) / effortPoints(ref));
}

/** `training_history.parse_reps_range`: "10-12" -> [10, 12]; "12" -> [12, 12]. */
export function parseRepsRange(value: unknown): [number | null, number | null] {
  if (value == null) return [null, null];
  const text = String(value);
  const range = /(\d+)\s*-\s*(\d+)/.exec(text);
  if (range) return [Number(range[1]), Number(range[2])];
  const single = /(\d+(?:[.,]\d+)?)/.exec(text);
  if (single) {
    const n = Math.trunc(Number(single[1].replace(',', '.')));
    return [n, n];
  }
  return [null, null];
}

/** Reps da âncora; sem âncora ou sem reps -> topo da faixa prescrita. */
export function referenceReps(anchorReps: unknown, repsTarget?: unknown): number | null {
  const reps = toNumber(anchorReps);
  if (reps != null && reps > 0) return Math.trunc(reps);
  const [, high] = parseRepsRange(repsTarget);
  return high && high > 0 ? high : null;
}

export type TechniqueApplication = {
  techniqueCode: string | null;
  reps?: number | null;
  rpe?: number | null;
};

/** Equivalente de UMA aplicação, sem teto (o teto é por bloco). */
export function applicationEquivalent(
  application: TechniqueApplication,
  params: { refReps: number | null; anchorRpe?: unknown; catalog: TechniqueCatalog },
): number {
  const code = application.techniqueCode ?? '';
  const coef = coefficientsFor(code, params.catalog);
  if (SETS_ONLY_CODES.has(code) || coef.maxEquivalentPerAnchor <= 0) return 0;
  const reps = toNumber(application.reps);
  const { refReps } = params;
  if (reps == null || refReps == null || refReps <= 0) return coef.activationSetEquivalent;
  if (Object.prototype.hasOwnProperty.call(BEYOND_FAILURE_WEIGHTS, code)) return (Math.max(0, reps) / refReps) * BEYOND_FAILURE_WEIGHTS[code];
  if (REPS_WEIGHTED_CODES.has(code)) {
    return (Math.max(0, reps) / refReps) * effortFactor(application.rpe, params.anchorRpe);
  }
  return coef.activationSetEquivalent;
}

export type BlockEquivalent = {
  total: number;
  byTechnique: Record<string, number>;
  rawByTechnique: Record<string, number>;
  refReps: number | null;
  capped: boolean;
};

/** Soma das aplicações da MESMA âncora, com o teto do catálogo aplicado por técnica. */
export function realizedBlockEquivalent(
  applications: ReadonlyArray<TechniqueApplication>,
  params: { anchorReps?: unknown; anchorRpe?: unknown; repsTarget?: unknown; catalog: TechniqueCatalog },
): BlockEquivalent {
  const refReps = referenceReps(params.anchorReps, params.repsTarget);
  const raw: Record<string, number> = {};
  for (const application of applications) {
    const code = application.techniqueCode ?? '';
    raw[code] =
      (raw[code] ?? 0) +
      applicationEquivalent(application, { refReps, anchorRpe: params.anchorRpe, catalog: params.catalog });
  }
  const byTechnique: Record<string, number> = {};
  let capped = false;
  for (const [code, value] of Object.entries(raw)) {
    const cap = Math.max(0, coefficientsFor(code, params.catalog).maxEquivalentPerAnchor);
    byTechnique[code] = Math.min(value, cap);
    if (value > byTechnique[code]) capped = true;
  }
  const total = Object.values(byTechnique).reduce((sum, value) => sum + value, 0);
  return { total, byTechnique, rawByTechnique: raw, refReps, capped };
}

/** Planejado: aplicações × activation_set_equivalent, limitado pelo teto. */
export function plannedEquivalent(
  techniqueCode: string | null | undefined,
  applications: unknown,
  catalog: TechniqueCatalog,
): number {
  const code = techniqueCode ?? '';
  const count = toNumber(applications) ?? 0;
  if (!code || SETS_ONLY_CODES.has(code) || count <= 0) return 0;
  const coef = coefficientsFor(code, catalog);
  return Math.min(count * coef.activationSetEquivalent, Math.max(0, coef.maxEquivalentPerAnchor));
}

/** Catálogo da API (lista) -> mapeamento `code -> linha` usado pela regra. */
export function techniqueCatalogFromItems(
  items: ReadonlyArray<TechniqueCatalogRow & { code: string }>,
): TechniqueCatalog {
  return Object.fromEntries(items.map((item) => [item.code, item]));
}

/** "0,5" — até 2 casas, vírgula decimal, sem zeros sobrando. */
export function formatEquivalent(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  return rounded.toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 2 });
}
