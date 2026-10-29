// "Tempo total de treino" de um slot de Modelo no modo 'equipment_series'
// (Fase 7, Frente C) — ESPELHO do dono no backend,
// `api/app/periodization/time_estimate.py` (M76, Lote 4c — E7). O preview do
// Modelo devolve o mesmo número por slot (`slot_times[]`); o espelho existe
// porque o builder recalcula a cada tecla e o preview só responde depois do
// debounce. Sem constante própria: as premissas vêm de
// `motor-constants.generated.json` (seção `time_estimate`), e o vitest roda
// este arquivo contra `template-time-estimate.parity.json` — resultado
// COMPLETO (total, parcelas, itens sem descanso, reps estimadas), ambos
// gerados pelo dono (`api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`).
//
//   execução(reps) = reps × (3 + pico de contração)
//   aquecimento    = séries × (execução(reps) + intervalo do aquecimento)
//   ajuste         = séries × (execução(reps) + intervalo do ajuste)
//   trabalho       = séries × (execução(reps alvo) + descanso do exercício)
//                    (por tempo: o tempo por série; máximo com janela: a janela)
//   técnica        = aplicações × duração
//   cardio         = tempo total da prescrição (espelho `cardio-prescription.ts`
//                    do dono `domain/cardio_prescription.py`; item antigo = minutos × 60)
//
// Exercício sem descanso definido conta 0 s nas séries de trabalho (decisão
// do usuário) e entra em `itemsWithoutRest`, para a tela avisar. Só exibição:
// nada disso é salvo.

import { cardioTotalSeconds, type CardioPrescriptionFields } from './cardio-prescription';
import motorConstants from './motor-constants.generated.json';
import { plannedSetExecutionSeconds } from './set-measures';
import type { VolumeMode } from './workout-plan-templates-types';

const PREMISES = motorConstants.time_estimate;

/** Segundos de execução por repetição (premissa exibida no tooltip). */
export const EXEC_SECONDS_PER_REP: number = PREMISES.exec_seconds_per_rep;

/** Repetições assumidas quando o alvo está vazio ou ilegível. */
export const DEFAULT_ESTIMATED_REPS: number = PREMISES.default_estimated_reps;

/** Descanso das séries de trabalho quando o item não tem `rest_seconds`. */
export const REST_WHEN_UNDEFINED_SECONDS: number = PREMISES.rest_when_undefined_seconds;

/** Campos do item de Modelo usados na estimativa (cardio: os da prescrição, M98 C0). */
export type TimeEstimateItem = CardioPrescriptionFields & {
  item_kind: string;
  sets_count: number | null;
  reps_target: string | null;
  // Código da técnica (rest-pause sem duração usa a pausa padrão do dono).
  advanced_technique?: string | null;
  advanced_technique_applications: number | null;
  advanced_technique_duration_seconds: number | null;
  warmup_sets_count: number | null;
  warmup_reps: number | null;
  warmup_rest_seconds: number | null;
  feeler_set_sets_count: number | null;
  feeler_set_reps: number | null;
  feeler_set_rest_seconds: number | null;
  peak_contraction_seconds: number | null;
  rest_seconds: number | null;
  // Medida da série (Lote T2): ausente = repetições (conta de sempre).
  measure?: string | null;
  duration_seconds_target?: number | null;
  time_window_seconds?: number | null;
};

export type SlotTimeEstimate = {
  totalSeconds: number;
  breakdown: {
    warmup: number;
    feeler: number;
    work: number;
    technique: number;
    cardio: number;
    /** Parte de execução (repetições) de aquecimento, ajuste e trabalho. */
    execSeconds: number;
    /** Parte de descanso de aquecimento, ajuste, trabalho e técnica. */
    restSeconds: number;
  };
  /** Exercícios de equipamento com séries de trabalho e sem descanso definido. */
  itemsWithoutRest: number;
  /** Algum alvo de repetições vazio/ilegível foi estimado em 10. */
  hasEstimatedReps: boolean;
};

/**
 * Repetições de um alvo textual: número simples ("10"), faixa ("8-12",
 * "8 a 12", "8–12") vira a média. Vazio ou ilegível devolve `null`.
 */
export function parseRepsTarget(value: string | null | undefined): number | null {
  if (value == null) return null;
  const numbers = (value.match(/\d+(?:[.,]\d+)?/g) ?? []).map((n) => Number(n.replace(',', '.')));
  const valid = numbers.filter((n) => Number.isFinite(n) && n > 0);
  if (valid.length === 0) return null;
  if (valid.length === 1) return valid[0];
  return (valid[0] + valid[1]) / 2;
}

function positive(value: number | null | undefined): number {
  return value != null && Number.isFinite(value) && value > 0 ? value : 0;
}

/**
 * `defaultRestSeconds`: descanso das séries de trabalho quando o item não tem
 * `rest_seconds` (nulo). A tela usa `REST_WHEN_UNDEFINED_SECONDS` (decisão do
 * usuário acima); o parâmetro existe para a paridade com a conta do
 * complemento de volume da Periodização (M46) — mesma fórmula, um parâmetro só.
 */
export function estimateSlotTime(
  items: ReadonlyArray<TimeEstimateItem>,
  defaultRestSeconds: number = REST_WHEN_UNDEFINED_SECONDS,
): SlotTimeEstimate {
  const breakdown = { warmup: 0, feeler: 0, work: 0, technique: 0, cardio: 0, execSeconds: 0, restSeconds: 0 };
  let itemsWithoutRest = 0;
  let hasEstimatedReps = false;

  for (const item of items) {
    if (item.item_kind === 'cardio') {
      breakdown.cardio += cardioTotalSeconds(item);
      continue;
    }

    const perRep = EXEC_SECONDS_PER_REP + positive(item.peak_contraction_seconds);

    const warmupSets = positive(item.warmup_sets_count);
    const warmupExec = warmupSets * positive(item.warmup_reps) * perRep;
    const warmupRest = warmupSets * positive(item.warmup_rest_seconds);

    const feelerSets = positive(item.feeler_set_sets_count);
    const feelerExec = feelerSets * positive(item.feeler_set_reps) * perRep;
    const feelerRest = feelerSets * positive(item.feeler_set_rest_seconds);

    const workSets = positive(item.sets_count);
    let workExec = 0;
    let workRest = 0;
    if (workSets > 0) {
      // Medida da série (Lote T2): tempo por série / janela do máximo vêm do
      // espelho do dono (`plannedSetExecutionSeconds`); null = conta por reps.
      const planned = plannedSetExecutionSeconds(item.measure, item.duration_seconds_target, item.time_window_seconds);
      let setExec: number;
      if (planned != null) {
        setExec = positive(planned);
      } else {
        let reps = parseRepsTarget(item.reps_target);
        if (reps == null) {
          reps = DEFAULT_ESTIMATED_REPS;
          hasEstimatedReps = true;
        }
        setExec = reps * perRep;
      }
      workExec = workSets * setExec;
      workRest = workSets * (item.rest_seconds == null ? positive(defaultRestSeconds) : positive(item.rest_seconds));
      if (item.item_kind === 'equipment' && positive(item.rest_seconds) === 0) itemsWithoutRest += 1;
    }

    // Pausa da técnica: a do item ou, no rest-pause sem duração, o padrão do
    // dono (`progression_engine.REST_PAUSE_DEFAULT_PAUSE_SECONDS`, via JSON gerado).
    const pause =
      positive(item.advanced_technique_duration_seconds) ||
      (item.advanced_technique === PREMISES.rest_pause_technique_code ? PREMISES.rest_pause_default_pause_seconds : 0);
    const technique = positive(item.advanced_technique_applications) * pause;

    breakdown.warmup += warmupExec + warmupRest;
    breakdown.feeler += feelerExec + feelerRest;
    breakdown.work += workExec + workRest;
    breakdown.technique += technique;
    breakdown.execSeconds += warmupExec + feelerExec + workExec;
    breakdown.restSeconds += warmupRest + feelerRest + workRest + technique;
  }

  const rounded = {
    warmup: Math.round(breakdown.warmup),
    feeler: Math.round(breakdown.feeler),
    work: Math.round(breakdown.work),
    technique: Math.round(breakdown.technique),
    cardio: Math.round(breakdown.cardio),
    execSeconds: Math.round(breakdown.execSeconds),
    restSeconds: Math.round(breakdown.restSeconds),
  };
  const totalSeconds = rounded.warmup + rounded.feeler + rounded.work + rounded.technique + rounded.cardio;
  return { totalSeconds, breakdown: rounded, itemsWithoutRest, hasEstimatedReps };
}

/** "0s", "45s", "52min", "3min 20s". */
export function formatRestSeconds(totalSeconds: number): string {
  if (totalSeconds <= 0) return '0s';
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes === 0) return `${seconds}s`;
  if (seconds === 0) return `${minutes}min`;
  return `${minutes}min ${seconds}s`;
}

/**
 * Segundos do "Tempo total de treino" para o resumo do slot RECOLHIDO — o
 * mesmo `estimateSlotTime(items).totalSeconds` que o `SlotTotalTime` mostra
 * no slot expandido. Segue a condição do expandido (modo 'equipment_series'
 * e slot com itens) e, além disso, devolve `null` em slot de descanso ou
 * quando o total é zero, casos em que o resumo não exibe nada.
 */
export function collapsedSlotTotalSeconds(
  slot: { slot_type: string; items: ReadonlyArray<TimeEstimateItem> },
  volumeMode: VolumeMode,
): number | null {
  if (volumeMode !== 'equipment_series') return null;
  if (slot.slot_type === 'descanso' || slot.items.length === 0) return null;
  const { totalSeconds } = estimateSlotTime(slot.items);
  return Number.isFinite(totalSeconds) && totalSeconds > 0 ? totalSeconds : null;
}
