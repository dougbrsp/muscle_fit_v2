// Round-trip das séries na edição do registro (/registro-exercicios →
// PUT /exercises/logs/{id}). O servidor RECRIA as séries: tudo que não for
// reenviado cai no fallback por `set_number`, que erra quando o formulário
// renumera (remover a série 2 de 4 faz a antiga 3 virar 2). Por isso a tela
// carrega TODOS os campos por série e os reenvia explicitamente, inclusive
// `null`; série adicionada na tela vai com o default da coluna.
// A âncora da técnica (Fase 5) é um set_number: ao renumerar, é remapeada
// para a nova posição da série âncora; se a âncora foi removida, vai `null` e
// o servidor deriva de novo.
// Lote T5 (medidas de série): `measure`/`duration_seconds` também fazem o
// round-trip — editar uma série por tempo não pode transformá-la em série de
// reps (nem perder os segundos). A medida vai SEMPRE explícita no PUT.
// M102: os lados da série unilateral (`reps_left`/`reps_right`/`rpe_left`/
// `rpe_right`) também vão SEMPRE explícitos; o servidor recalcula reps/RPE
// oficiais pelo pior lado (`domain/set_sides.py`) — a tela não recalcula.
import { formatSetMeasureText, isRepBased, MEASURE_LIMITS, MEASURE_VALUES, normalizeMeasure } from './set-measures';
import { sidesFromRow, sidesSuffix } from './set-sides';
import { isTechniqueApplicationSetType, isWorkingSetType, normalizeSetType } from './set-types';
import type { ExerciseFormState, ExerciseSetRow } from './workout-log-types';

export type ExerciseFormSet = ExerciseFormState['sets'][number];

function toNumberOrNull(value: number | string | null | undefined): number | null {
  if (value == null || value === '') return null;
  const parsed = Number(value);
  return Number.isNaN(parsed) ? null : parsed;
}

export function setFormFromRow(set: ExerciseSetRow, index: number): ExerciseFormSet {
  const measure = normalizeMeasure(set.measure);
  return {
    set_number: index + 1,
    // Série por tempo grava reps NULL (dono `validate_set_measure`): sem o `|| 0`.
    reps: set.reps == null && !isRepBased(measure) ? null : Number(set.reps || 0),
    weight_kg: Number(set.weight_kg || 0),
    rpe: toNumberOrNull(set.rpe),
    set_type: set.set_type || 'working',
    rest_seconds: set.rest_seconds ?? null,
    notes: set.notes ?? null,
    hr_avg_bpm: toNumberOrNull(set.hr_avg_bpm),
    hr_max_bpm: toNumberOrNull(set.hr_max_bpm),
    muscle_pain: set.muscle_pain ?? null,
    joint_pain: set.joint_pain == null ? null : Boolean(set.joint_pain),
    technique_code: set.technique_code ?? null,
    anchor_set_number: set.anchor_set_number ?? null,
    measure,
    duration_seconds: set.duration_seconds ?? null,
    ...sidesFromRow(set),
    source_set_number: set.set_number,
  };
}

/**
 * Quais campos a linha da série mostra na edição, pela medida: "Repetições"
 * fora de `duration`; "Tempo (s)" fora de `reps` (segundos em `duration`, a
 * janela em `max_reps`).
 */
export function setEditFields(measure?: string | null): {
  showReps: boolean;
  showDuration: boolean;
  durationMin: number;
  durationMax: number;
  /** Piso de reps do dono em `max_reps` (`MAX_REPS_MIN_REPS`); null = a tela de sempre. */
  repsMin: number | null;
} {
  const kind = normalizeMeasure(measure);
  const isDuration = kind === MEASURE_VALUES.duration;
  return {
    showReps: !isDuration,
    showDuration: !isRepBased(kind),
    // Limites do dono (`MEASURE_LIMITS`): tempo da série × janela do máximo.
    durationMin: isDuration ? MEASURE_LIMITS.durationSecondsMin : MEASURE_LIMITS.timeWindowSecondsMin,
    durationMax: isDuration ? MEASURE_LIMITS.durationSecondsMax : MEASURE_LIMITS.timeWindowSecondsMax,
    repsMin: kind === MEASURE_VALUES.max_reps ? MEASURE_LIMITS.maxRepsMinReps : null,
  };
}

/**
 * Série adicionada na tela depois de uma série por tempo/máximo de reps: herda
 * a medida, os segundos e a carga da última. `null` quando a última é de
 * repetições (a tela usa a série nova de sempre).
 */
export function newMeasuredSetFromLast(last: ExerciseFormSet | undefined, setNumber: number): ExerciseFormSet | null {
  const measure = normalizeMeasure(last?.measure);
  if (isRepBased(measure)) return null;
  return {
    set_number: setNumber,
    reps: setEditFields(measure).showReps ? (last?.reps ?? null) : null,
    weight_kg: last?.weight_kg ?? 0,
    rpe: null,
    measure,
    duration_seconds: last?.duration_seconds ?? null,
  };
}

function remapAnchor(set: ExerciseFormSet, all?: readonly ExerciseFormSet[]): number | null {
  const anchor = set.anchor_set_number ?? null;
  if (anchor == null || !all) return anchor;
  const position = all.findIndex((other) => other.source_set_number === anchor);
  return position >= 0 ? position + 1 : null;
}

export function setUpdatePayload(set: ExerciseFormSet, index: number, all?: readonly ExerciseFormSet[]) {
  return {
    set_number: index + 1,
    reps: set.reps,
    weight_kg: set.weight_kg,
    rpe: set.rpe,
    set_type: set.set_type || 'working',
    rest_seconds: set.rest_seconds ?? null,
    notes: set.notes ?? null,
    hr_avg_bpm: set.hr_avg_bpm ?? null,
    hr_max_bpm: set.hr_max_bpm ?? null,
    muscle_pain: set.muscle_pain ?? null,
    joint_pain: set.joint_pain ?? null,
    technique_code: set.technique_code ?? null,
    anchor_set_number: remapAnchor(set, all),
    measure: normalizeMeasure(set.measure),
    duration_seconds: set.duration_seconds ?? null,
    reps_left: set.reps_left ?? null,
    reps_right: set.reps_right ?? null,
    rpe_left: set.rpe_left ?? null,
    rpe_right: set.rpe_right ?? null,
  };
}

// ---------------------------------------------------------------------------
// Fase 5 — tipo da série visível e aplicações agrupadas sob a âncora
// ---------------------------------------------------------------------------

// Rótulos de exibição. QUAL linha é aplicação/preparatória/trabalho vem do
// espelho único `./set-types` (dono: `api/app/set_types.py`, M76 Lote 3b).
const APPLICATION_NOUNS: Record<string, string> = {
  rest_pause: 'Mini-bloco',
  drop_set: 'Drop',
  forced_rep: 'Reps extras',
};

const PREPARATORY_LABELS: Record<string, string> = {
  warmup: 'Aquecimento',
  feeler: 'Série de ajuste',
};

export type SetDisplayRow = {
  /** Rótulo da série: "Série 2", "Aquecimento", "Mini-bloco 1", "Drop 2"… */
  label: string;
  /** `set_type` normalizado. */
  setType: string;
  /** true = aplicação de técnica (rest-pause, drop, reps extras): subitem da âncora. */
  isApplication: boolean;
  /** Índice (no array) da série de trabalho âncora; null = sem âncora. */
  anchorIndex: number | null;
  /** "Série 3" da âncora, para exibir o vínculo. */
  anchorLabel: string | null;
};

type SetLike = {
  set_number?: number | null;
  set_type?: string | null;
  anchor_set_number?: number | null;
  source_set_number?: number;
};

/**
 * Rótulos da lista de séries (edição do registro e tabela de exercícios
 * lançados). "Série N" conta só séries de TRABALHO; aplicações de técnica
 * viram subitens numerados por âncora. A âncora vem do vínculo gravado
 * (`anchor_set_number` → `source_set_number`/`set_number`) ou, sem vínculo, da
 * última série de trabalho anterior — a mesma derivação do backend.
 */
export function describeSets(sets: readonly SetLike[]): SetDisplayRow[] {
  // No formulário, o número GRAVADO é `source_set_number` (série nova não tem
  // e nunca é âncora); na lista vinda da API, é o próprio `set_number`.
  const fromForm = sets.some((set) => set.source_set_number != null);
  const recordedNumber = (set: SetLike) => (fromForm ? (set.source_set_number ?? null) : (set.set_number ?? null));
  const workingOrdinal = new Map<number, number>();
  let working = 0;
  sets.forEach((set, index) => {
    if (isWorkingSetType(set.set_type)) {
      working += 1;
      workingOrdinal.set(index, working);
    }
  });
  const perAnchor = new Map<number | null, number>();
  return sets.map((set, index) => {
    const setType = normalizeSetType(set.set_type);
    if (isWorkingSetType(setType)) {
      return { label: `Série ${workingOrdinal.get(index)}`, setType, isApplication: false, anchorIndex: null, anchorLabel: null };
    }
    if (!isTechniqueApplicationSetType(setType)) {
      return {
        label: PREPARATORY_LABELS[setType] ?? setType,
        setType,
        isApplication: false,
        anchorIndex: null,
        anchorLabel: null,
      };
    }
    let anchorIndex: number | null = null;
    if (set.anchor_set_number != null) {
      const found = sets.findIndex((other) => recordedNumber(other) === set.anchor_set_number);
      anchorIndex = found >= 0 && workingOrdinal.has(found) ? found : null;
    }
    if (anchorIndex == null) {
      for (let i = index - 1; i >= 0; i -= 1) {
        if (workingOrdinal.has(i)) {
          anchorIndex = i;
          break;
        }
      }
    }
    const noun = APPLICATION_NOUNS[setType] ?? setType;
    const ordinal = (perAnchor.get(anchorIndex) ?? 0) + 1;
    perAnchor.set(anchorIndex, ordinal);
    return {
      label: setType === 'forced_rep' ? noun : `${noun} ${ordinal}`,
      setType,
      isApplication: true,
      anchorIndex,
      anchorLabel: anchorIndex != null ? `Série ${workingOrdinal.get(anchorIndex)}` : null,
    };
  });
}

/** Resumo de uma linha da tabela "Exercícios lançados": aplicações como "↳ Mini-bloco 1: 6x 50kg". */
export function formatLoggedSets(
  sets: ReadonlyArray<
    SetLike & {
      reps?: number | null;
      weight_kg?: number | string | null;
      rpe?: number | string | null;
      measure?: string | null;
      duration_seconds?: number | null;
      measure_text?: string | null;
      sides_text?: string | null;
    }
  >,
): string {
  const display = describeSets(sets);
  return sets
    .map((set, index) => {
      const row = display[index];
      const weight = Number(set.weight_kg || 0).toLocaleString('pt-BR', { maximumFractionDigits: 2 });
      const rpe = set.rpe != null ? ` @RPE ${Number(set.rpe).toLocaleString('pt-BR', { maximumFractionDigits: 1 })}` : '';
      const prefix = row.isApplication ? `↳ ${row.label}` : row.label;
      if (!isRepBased(set.measure)) {
        // Lote T5: texto do dono ("58 s", "15 reps em 30 s"); carga só se houver.
        const measureText =
          set.measure_text ||
          formatSetMeasureText({ measure: set.measure, reps: set.reps, durationSeconds: set.duration_seconds });
        const load = Number(set.weight_kg || 0) > 0 ? ` x ${weight}kg` : '';
        return `${prefix}: ${measureText}${load}${rpe}`;
      }
      const reps = Number(set.reps || 0);
      // M102: lados da série unilateral, texto pronto da API.
      return `${prefix}: ${reps}x ${weight}kg${rpe}${sidesSuffix(set)}`;
    })
    .join(' | ');
}
