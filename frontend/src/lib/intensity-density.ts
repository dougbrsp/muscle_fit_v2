// "Intensidade × Densidade" do Modelo de treino, por grupo muscular — ESPELHO
// do dono no backend, `api/app/domain/intensity_density.py` (E13, 2026-10-13).
// Só exibição: nada é salvo e nenhum endpoint novo existe para isto.
//
// O espelho existe porque o cálculo roda na TELA: os parâmetros do item (RPE,
// reps, descanso, pico, técnica) vêm do estado do builder (`previewSlots`),
// não da resposta — o corpo do preview não os leva (M10), então editá-los
// recalcula na hora sem nova requisição. Sem constante própria: limiares,
// faixas, banda de equilíbrio e papéis vêm de `motor-constants.generated.json`
// (seção `intensity_density`), e o vitest roda este arquivo contra
// `intensity-density.parity.json`, ambos gerados pelo dono
// (`api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`). Regra nova ou
// diferente muda NO DONO; aqui só se regenera.
//
// Resumo da regra (os números estão no dono):
//   Intensidade I ∈ [0,1] = média, série a série, dos pontos de RPE; a rampa
//     "8-9-10" é lida como em `parse_rpe_ramp`; rampa curta repete o último
//     degrau; técnica que exige falha faz a última série contar o RPE de falha.
//     Sem RPE, I fica AUSENTE (não entra como neutro).
//   Densidade/Volume D ∈ [0,1] = média dos subíndices que existirem (reps,
//     pico de contração, descanso — curto = mais denso — e séries).
//   Posição p = (I − D + 1) / 2 (0 = Densidade/Volume, 1 = Intensidade);
//     só um lado com dado: I ou 1 − D.
//
// Por grupo: média de p ponderada pelo `contribution.value` do preview de
// ativação (só os papéis da regra). O `value` JÁ inclui `occurrences` na
// janela de 7 dias (`workout_plan_templates.py`), então `occurrences` NÃO é
// multiplicado de novo aqui.

import type { AdvancedTechniqueCatalogItem } from './advanced-techniques';
import { forEachMatchedPreviewItem } from './item-activation-summary';
import motorConstants from './motor-constants.generated.json';
import { isRepBased } from './set-measures';
import { parseRepsTarget } from './template-time-estimate';
import type {
  ActivationRole,
  PreviewActivationItemContribution,
  PreviewActivationResponse,
  WorkoutPlanTemplateSlotItemPayload,
  WorkoutPlanTemplateSlotPayload,
} from './workout-plan-templates-types';

// ---------------------------------------------------------------------------
// Constantes (geradas pelo dono)
// ---------------------------------------------------------------------------

const RULE = motorConstants.intensity_density;

/** Pontos de intensidade por RPE (abaixo do menor limiar → 0). Ordem decrescente. */
export const RPE_INTENSITY_POINTS: ReadonlyArray<{ minRpe: number; points: number }> = RULE.rpe_intensity_points.map(
  (threshold) => ({ minRpe: threshold.min_rpe, points: threshold.points }),
);

/** RPE que a última série conta quando a técnica exige falha. */
const FAILURE_LAST_SET_RPE: number = RULE.failure_last_set_rpe;

/** Reps (ponto médio da faixa): ≤ min → 0, ≥ max → 1. */
export const REPS_DENSITY_RANGE: Readonly<{ min: number; max: number }> = RULE.reps_density_range;
/** Pico de contração em segundos: ≤ min → 0, ≥ max → 1. */
export const PEAK_DENSITY_RANGE: Readonly<{ min: number; max: number }> = RULE.peak_density_range;
/** Descanso em segundos: ≥ longest → 0, ≤ shortest → 1 (curto = mais denso). */
export const REST_DENSITY_RANGE: Readonly<{ longest: number; shortest: number }> = RULE.rest_density_range;
/** Séries de trabalho do exercício: ≤ min → 0, ≥ max → 1. */
export const SETS_VOLUME_RANGE: Readonly<{ min: number; max: number }> = RULE.sets_volume_range;
/** |p − meio| abaixo disto → "Equilibrado". */
export const BALANCED_BAND: number = RULE.balanced_band;
const POSITION_MIDPOINT: number = RULE.position_midpoint;

/** Papéis que entram na atribuição por grupo (STABILIZER fica fora). */
export const INTENSITY_DENSITY_ROLES: ReadonlySet<ActivationRole> = new Set<ActivationRole>(
  RULE.roles as ActivationRole[],
);

const FAILURE_REQUIREMENTS: ReadonlySet<string> = new Set(RULE.failure_requirements);

// ---------------------------------------------------------------------------
// Tipos
// ---------------------------------------------------------------------------

export type DensitySubindices = {
  reps: number | null;
  peak: number | null;
  rest: number | null;
  sets: number | null;
};

export type ItemIntensityDensity = {
  intensity: number | null;
  density: number | null;
  subindices: DensitySubindices;
  position: number | null;
};

export type IntensityDensityDirection = 'intensity' | 'density' | 'balanced';

export type IntensityDensityGroupRow = {
  muscleGroupId: number | null;
  muscleGroup: string;
  /** Soma dos pesos (séries equivalentes P+S na janela) — ordena as linhas. */
  weight: number;
  position: number | null;
  intensity: number | null;
  density: number | null;
  subindices: DensitySubindices;
  direction: IntensityDensityDirection | null;
  /** "→ Intensidade" | "← Densidade/Volume" | "Equilibrado" | "sem dados". */
  label: string;
};

/** Campos do item usados no cálculo. */
export type IntensityDensityItemInput = Pick<
  WorkoutPlanTemplateSlotItemPayload,
  | 'sets_count'
  | 'reps_target'
  | 'rpe_target'
  | 'rest_seconds'
  | 'peak_contraction_seconds'
  | 'advanced_technique'
  | 'measure'
>;

type TechniqueCatalog = ReadonlyArray<Pick<AdvancedTechniqueCatalogItem, 'code' | 'failure_requirement'>>;

// ---------------------------------------------------------------------------
// Parsers e subíndices
// ---------------------------------------------------------------------------

/**
 * Rampa de RPE por série, espelho de `parse_rpe_ramp` (paridade em
 * `intensity-density.parity.json`, seção `ramps`): `"8-9-10"` → [8, 9, 10];
 * `"9"` → [9]; `"8,5"` → [8.5]; vazio/sem número → [].
 */
export function parseRpeRamp(value: string | number | null | undefined): number[] {
  if (value == null) return [];
  if (typeof value === 'number') return Number.isFinite(value) ? [value] : [];
  return (String(value).match(/\d+(?:[.,]\d+)?/g) ?? [])
    .map((n) => Number(n.replace(',', '.')))
    .filter((n) => Number.isFinite(n));
}

export function rpeIntensityPoints(rpe: number): number {
  for (const threshold of RPE_INTENSITY_POINTS) {
    if (rpe >= threshold.minRpe) return threshold.points;
  }
  return 0;
}

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value));
}

/** Linear de `from` (→ 0) a `to` (→ 1); funciona com from > to (escala invertida). */
function linear(value: number, from: number, to: number): number {
  return clamp01((value - from) / (to - from));
}

function positiveOrNull(value: number | null | undefined): number | null {
  return value != null && Number.isFinite(value) && value > 0 ? value : null;
}

function mean(values: ReadonlyArray<number | null>): number | null {
  const known = values.filter((v): v is number => v != null);
  if (known.length === 0) return null;
  return known.reduce((sum, v) => sum + v, 0) / known.length;
}

export function isFailureTechnique(code: string | null | undefined, catalog: TechniqueCatalog): boolean {
  if (!code) return false;
  const entry = catalog.find((t) => t.code === code);
  return entry != null && FAILURE_REQUIREMENTS.has(entry.failure_requirement);
}

/**
 * Intensidade do item: média dos pontos de RPE por série. Séries = `sets_count`
 * (ou o tamanho da rampa, se não houver); rampa curta repete o último degrau.
 * Técnica de falha → última série = RPE 10. Sem RPE → `null`.
 */
export function itemIntensity(item: IntensityDensityItemInput, catalog: TechniqueCatalog): number | null {
  const ramp = parseRpeRamp(item.rpe_target);
  if (ramp.length === 0) return null;
  const sets = positiveOrNull(item.sets_count) != null ? Math.round(item.sets_count as number) : ramp.length;
  if (sets <= 0) return null;
  const perSet = Array.from({ length: sets }, (_, i) => ramp[Math.min(i, ramp.length - 1)]);
  if (isFailureTechnique(item.advanced_technique, catalog)) perSet[perSet.length - 1] = FAILURE_LAST_SET_RPE;
  return perSet.reduce((sum, rpe) => sum + rpeIntensityPoints(rpe), 0) / perSet.length;
}

export function densitySubindices(item: IntensityDensityItemInput): DensitySubindices {
  // Medida da série (Lote T2): item por tempo / máximo de reps não tem alvo de
  // repetições — o subíndice de reps fica ausente (espelho do dono).
  const reps = isRepBased(item.measure) ? parseRepsTarget(item.reps_target) : null;
  const peak = item.peak_contraction_seconds;
  const rest = item.rest_seconds;
  const sets = positiveOrNull(item.sets_count);
  return {
    reps: reps == null ? null : linear(reps, REPS_DENSITY_RANGE.min, REPS_DENSITY_RANGE.max),
    peak:
      peak == null || !Number.isFinite(peak) ? null : linear(peak, PEAK_DENSITY_RANGE.min, PEAK_DENSITY_RANGE.max),
    rest:
      rest == null || !Number.isFinite(rest)
        ? null
        : linear(rest, REST_DENSITY_RANGE.longest, REST_DENSITY_RANGE.shortest),
    sets: sets == null ? null : linear(sets, SETS_VOLUME_RANGE.min, SETS_VOLUME_RANGE.max),
  };
}

export function combinePosition(intensity: number | null, density: number | null): number | null {
  if (intensity != null && density != null) return clamp01((intensity - density + 1) / 2);
  if (intensity != null) return intensity;
  if (density != null) return 1 - density;
  return null;
}

export function computeItemIntensityDensity(
  item: IntensityDensityItemInput,
  catalog: TechniqueCatalog,
): ItemIntensityDensity {
  const intensity = itemIntensity(item, catalog);
  const subindices = densitySubindices(item);
  const density = mean([subindices.reps, subindices.peak, subindices.rest, subindices.sets]);
  return { intensity, density, subindices, position: combinePosition(intensity, density) };
}

export function directionOf(position: number | null): IntensityDensityDirection | null {
  if (position == null) return null;
  if (Math.abs(position - POSITION_MIDPOINT) < BALANCED_BAND) return 'balanced';
  return position > POSITION_MIDPOINT ? 'intensity' : 'density';
}

export const DIRECTION_LABEL: Record<IntensityDensityDirection | 'none', string> = {
  intensity: '→ Intensidade',
  density: '← Densidade/Volume',
  balanced: 'Equilibrado',
  none: 'sem dados',
};

// ---------------------------------------------------------------------------
// Agregação por grupo
// ---------------------------------------------------------------------------

type WeightedMean = { sum: number; weight: number };

function addWeighted(acc: WeightedMean, value: number | null, weight: number): void {
  if (value == null) return;
  acc.sum += value * weight;
  acc.weight += weight;
}

function resolve(acc: WeightedMean): number | null {
  return acc.weight > 0 ? acc.sum / acc.weight : null;
}

type GroupAccumulator = {
  muscleGroupId: number | null;
  muscleGroup: string;
  weight: number;
  position: WeightedMean;
  intensity: WeightedMean;
  density: WeightedMean;
  reps: WeightedMean;
  peak: WeightedMean;
  rest: WeightedMean;
  sets: WeightedMean;
};

function emptyMean(): WeightedMean {
  return { sum: 0, weight: 0 };
}

/**
 * Linhas por grupo. `slots` é o MESMO corpo de slots do builder enviado ao
 * preview (`previewSlots`); `response` é a resposta do preview de ativação.
 * Grupo com peso P+S mas sem nenhum insumo vira "sem dados".
 */
export function buildIntensityDensityByGroup(
  slots: WorkoutPlanTemplateSlotPayload[],
  response: PreviewActivationResponse | null | undefined,
  catalog: TechniqueCatalog,
): IntensityDensityGroupRow[] {
  const entries: IntensityDensityEntry[] = [];
  forEachMatchedPreviewItem(slots, response, ({ item, echoed }) => {
    entries.push({ item, contributions: echoed.contributions });
  });
  return aggregateIntensityDensityByGroup(entries, catalog);
}

/** Item do Modelo com as contribuições que o preview devolveu para ele. */
export type IntensityDensityEntry = {
  item: IntensityDensityItemInput;
  contributions: ReadonlyArray<
    Pick<PreviewActivationItemContribution, 'muscle_group' | 'muscle_group_id' | 'role' | 'value'>
  >;
};

/** Média por grupo (espelho de `aggregate_by_group` do dono). */
export function aggregateIntensityDensityByGroup(
  entries: ReadonlyArray<IntensityDensityEntry>,
  catalog: TechniqueCatalog,
): IntensityDensityGroupRow[] {
  const groups = new Map<string, GroupAccumulator>();

  for (const { item, contributions } of entries) {
    const computed = computeItemIntensityDensity(item, catalog);
    for (const contribution of contributions) {
      if (!INTENSITY_DENSITY_ROLES.has(contribution.role)) continue;
      const weight = contribution.value;
      if (!(weight > 0)) continue;
      let acc = groups.get(contribution.muscle_group);
      if (!acc) {
        acc = {
          muscleGroupId: contribution.muscle_group_id ?? null,
          muscleGroup: contribution.muscle_group,
          weight: 0,
          position: emptyMean(),
          intensity: emptyMean(),
          density: emptyMean(),
          reps: emptyMean(),
          peak: emptyMean(),
          rest: emptyMean(),
          sets: emptyMean(),
        };
        groups.set(contribution.muscle_group, acc);
      }
      acc.weight += weight;
      addWeighted(acc.position, computed.position, weight);
      addWeighted(acc.intensity, computed.intensity, weight);
      addWeighted(acc.density, computed.density, weight);
      addWeighted(acc.reps, computed.subindices.reps, weight);
      addWeighted(acc.peak, computed.subindices.peak, weight);
      addWeighted(acc.rest, computed.subindices.rest, weight);
      addWeighted(acc.sets, computed.subindices.sets, weight);
    }
  }

  const rows: IntensityDensityGroupRow[] = [];
  for (const acc of groups.values()) {
    const position = resolve(acc.position);
    const direction = directionOf(position);
    rows.push({
      muscleGroupId: acc.muscleGroupId,
      muscleGroup: acc.muscleGroup,
      weight: acc.weight,
      position,
      intensity: resolve(acc.intensity),
      density: resolve(acc.density),
      subindices: {
        reps: resolve(acc.reps),
        peak: resolve(acc.peak),
        rest: resolve(acc.rest),
        sets: resolve(acc.sets),
      },
      direction,
      label: DIRECTION_LABEL[direction ?? 'none'],
    });
  }

  return rows.sort((a, b) => b.weight - a.weight || a.muscleGroup.localeCompare(b.muscleGroup, 'pt-BR'));
}

/** 0,734 → "73%"; null → "—". */
export function formatIndexPercent(value: number | null): string {
  return value == null ? '—' : `${Math.round(value * 100)}%`;
}
