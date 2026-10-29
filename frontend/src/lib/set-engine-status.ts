// "Uma voz no Modo Treino" (RPE 10 Lote 6/P9, 2026-10-11): o card da série,
// o descanso e a avaliação da IA falam o vocabulário do MOTOR.
//
// Só apresentação. Quem decide é o servidor:
// - status da série: `progression_engine.set_intensity_status`, devolvido no
//   salvamento da série (`intensity_status` + `intensity_status_label`);
// - decisão vigente: `prescription.decision` congelada na ficha;
// - categoria da IA: código de ação do motor (`domain/set_evaluation_categories.py`).
// Rótulos, códigos e direções chegam por `motor-constants.generated.json`
// (paridade em `api/tests/unit/test_m76_lote1b_espelhos_fe.py`). Aqui só a cor.

import { PROGRESSION_DECISION_LABELS } from './exercise-progress';
import {
  INTENSITY_STATUS_COLORS,
  INTENSITY_STATUS_LABELS,
  REPS_POSITION_LABELS,
  SAFETY_EVENT_LABELS,
  isSafetyAlertEvent,
} from './intensity-status';
import motorConstants from './motor-constants.generated.json';

const SET_EVALUATION = motorConstants.set_evaluation;
const NEXT_SET_LOAD = motorConstants.next_set_load;
const NEXT_SET_REASON_LABELS: Readonly<Record<string, string>> = NEXT_SET_LOAD.reason_labels;

export const SET_EVALUATION_CATEGORY_LABELS: Readonly<Record<string, string>> = SET_EVALUATION.category_labels;
const SET_EVALUATION_CATEGORY_DIRECTIONS: Readonly<Record<string, string>> = SET_EVALUATION.category_directions;

// Cor por DIREÇÃO de carga (o código e a direção vêm do backend).
const DIRECTION_COLORS: Readonly<Record<string, string>> = { UP: 'green', HOLD: 'blue', DOWN: 'orange' };
const NEUTRAL_COLOR = 'gray';

export type BadgeView = { label: string; color: string };

/**
 * Carga da PRÓXIMA série pelo motor (regra do usuário de 2026-10-12, dono
 * `api/app/domain/next_set_load.py`). Devolvida no salvamento da série e na
 * avaliação da IA; `null` fora da regra (série que não é de trabalho, sem
 * carga, assistido/peso corporal).
 */
export type NextSetLoad = {
  direction: string;
  direction_label?: string | null;
  load_kg: number | null;
  executed_kg?: number | null;
  step_kg?: number | null;
  reason: string;
  reason_label?: string | null;
  // RPE planejado desta série pela rampa da ficha (null = sem rampa).
  planned_rpe?: number | null;
  // R1 (regras de carga, aditivo): a regra que casou (null = dor ou nenhuma),
  // o seu texto pronto, a versão do conjunto (0 = padrão em código) e a
  // magnitude aplicada pelo dono. Só exibição; nenhuma regra aqui.
  rule_position?: number | null;
  rule_text?: string | null;
  ruleset_version?: number | null;
  magnitude?: { kind: string; value: number } | null;
};

/** Resposta de `POST /workout-sessions/{id}/exercises/{eid}/sets` (campos da intensidade). */
export type SavedSetIntensity = {
  intensity_status?: string | null;
  intensity_status_label?: string | null;
  // set-eval-v5: reps contra a faixa da ficha (motor); null sem faixa.
  reps_position?: string | null;
  reps_position_label?: string | null;
  safety_event?: string | null;
  safety_event_label?: string | null;
  next_set_load?: NextSetLoad | null;
  // M102: reps/RPE OFICIAIS gravados (pior lado quando há lados) e o texto dos lados.
  reps?: number | null;
  rpe?: number | null;
  sides_text?: string | null;
};

/**
 * Status do motor para a série recém-salva. Laranja só no alerta de segurança
 * do par (`ABOVE_SAFE_CEILING`); o evento informativo (RPE acima do alvo do
 * dia) sai como segundo selo, azul. `null` = sem status (treino livre sem
 * alvo, série preparatória/técnica). O status é de ESFORÇO ("Esforço
 * atingido"); ao lado, as reps contra a faixa (selo neutro, set-eval-v5).
 */
export function savedSetStatusViews(saved: SavedSetIntensity | null | undefined): BadgeView[] {
  if (!saved) return [];
  const views: BadgeView[] = [];
  const status = saved.intensity_status ?? null;
  if (status) {
    views.push({
      label: saved.intensity_status_label || INTENSITY_STATUS_LABELS[status] || status,
      color: INTENSITY_STATUS_COLORS[status] || NEUTRAL_COLOR,
    });
  }
  const repsPosition = saved.reps_position ?? null;
  if (repsPosition) {
    views.push({
      label: saved.reps_position_label || REPS_POSITION_LABELS[repsPosition] || repsPosition,
      color: NEUTRAL_COLOR,
    });
  }
  const event = saved.safety_event ?? null;
  if (event && !isSafetyAlertEvent(event)) {
    views.push({ label: saved.safety_event_label || SAFETY_EVENT_LABELS[event] || event, color: 'blue' });
  }
  return views;
}

/** "Ajuste de hoje: Manter" — a decisão do motor congelada na ficha. */
export function engineDecisionText(decision: string | null | undefined): string | null {
  if (!decision) return null;
  const label = PROGRESSION_DECISION_LABELS[decision];
  return label ? `Ajuste de hoje: ${label}` : null;
}

/**
 * Selo da categoria da IA por série. Categoria desconhecida (resposta antiga
 * em cache, ex.: "PROGREDIR") cai num selo neutro com o texto recebido.
 */
export function setEvaluationCategoryView(category: string | null | undefined, label?: string | null): BadgeView | null {
  if (!category) return null;
  const known = SET_EVALUATION_CATEGORY_LABELS[category];
  if (!known) return { label: label || category, color: NEUTRAL_COLOR };
  return { label: known, color: DIRECTION_COLORS[SET_EVALUATION_CATEGORY_DIRECTIONS[category]] || NEUTRAL_COLOR };
}

/**
 * Direção (UP/HOLD/DOWN) de um código de ação do motor, pelo JSON gerado.
 * Só para escolher ícone; `null` em código desconhecido.
 */
export function setEvaluationCategoryDirection(category: string | null | undefined): 'UP' | 'HOLD' | 'DOWN' | null {
  if (!category) return null;
  const direction = SET_EVALUATION_CATEGORY_DIRECTIONS[category];
  return direction === 'UP' || direction === 'HOLD' || direction === 'DOWN' ? direction : null;
}

export type NextSetLoadView = { badge: BadgeView; text: string; reason: string | null };

/**
 * "Próxima série: Aumentar carga → 22,5 kg" + o motivo. Rótulos e direção do
 * backend (JSON gerado); nenhuma regra aqui. Sem carga (reduzir sem passo
 * abaixo): só a direção.
 */
export function nextSetLoadView(next: NextSetLoad | null | undefined): NextSetLoadView | null {
  if (!next || !next.direction) return null;
  const badge = setEvaluationCategoryView(next.direction, next.direction_label);
  if (!badge) return null;
  const load = next.load_kg != null ? ` → ${formatKg(next.load_kg)}` : '';
  return {
    badge,
    text: `Próxima série: ${badge.label}${load}`,
    // O rótulo do servidor vem com o detalhe (ex.: "RPE dentro da rampa planejada
    // (alvo 8 nesta série)"); o do JSON gerado é o fallback pelo código.
    reason: next.reason_label || NEXT_SET_REASON_LABELS[next.reason] || null,
  };
}

export type LoadAdjustment = { field: string; from: number | null; to: number | null; reason: string };

function formatKg(value: number | null): string {
  if (value == null) return '—';
  return `${String(Number(value.toFixed(2))).replace('.', ',')} kg`;
}

/** Texto do ajuste de carga feito pelo servidor na sugestão da IA (separado do corte de RPE). */
export function loadAdjustmentText(adjustment: LoadAdjustment): string | null {
  const range = `${formatKg(adjustment.from)} → ${formatKg(adjustment.to)}`;
  if (adjustment.reason === SET_EVALUATION.load_adjustment_reason_engine_target) {
    return `Carga sugerida limitada ao alvo do motor: ${range}`;
  }
  if (adjustment.reason === NEXT_SET_LOAD.load_adjustment_reason_rule) {
    return `Carga sugerida ajustada à regra da próxima série: ${range}`;
  }
  if (adjustment.reason === SET_EVALUATION.load_adjustment_reason_equipment_step) {
    return `Carga sugerida ajustada ao passo do equipamento: ${range}`;
  }
  return null;
}
