import type {
  CheckinScoreField,
  DailyCheckin,
  DailyCheckinInput,
  DailyCheckinsListResponse,
} from './dashboard-checkin-types';
import {
  CHECKIN_SCALE_ENDS,
  CHECKIN_SCORE_FIELDS,
  CHECKIN_SCORE_LABELS,
  CHECKIN_SCORE_MAX,
  CHECKIN_SCORE_MIN,
} from './dashboard-checkin-types';
import type { Insight, InsightCooldown } from './dashboard-overview-types';

/**
 * Lógica PURA de `/dashboard/checkin` (F2.2) — extraída da tela para ser
 * testada sem React e sem rede.
 *
 * Nenhuma regra de recuperação mora aqui. Baseline pessoal, direção do sinal e
 * concordância são do backend (`recovery_signals_service`); este módulo só
 * valida o formulário antes do POST, rotula a escala e formata o estado de
 * cooldown de um insight.
 *
 * A regra que este arquivo protege na borda: **escore em branco é `null`, nunca
 * 0**. O `<NumberInput>` do Mantine devolve `''` quando o campo está vazio, e
 * um `Number('')` descuidado viraria 0 — um valor fora da escala que a baseline
 * do backend trataria como medida.
 */

// ---------------------------------------------------------------------------
// Formulário
// ---------------------------------------------------------------------------

/** Valor de um campo de escore no formulário: número, ou vazio. */
export type CheckinScoreValue = number | '';

export type CheckinFormState = {
  local_date: string;
  energy: CheckinScoreValue;
  soreness: CheckinScoreValue;
  stress: CheckinScoreValue;
  mood: CheckinScoreValue;
  notes: string;
};

export type CheckinFormErrors = Partial<Record<keyof CheckinFormState, string>>;

export function emptyCheckinForm(localDate: string): CheckinFormState {
  return { local_date: localDate, energy: '', soreness: '', stress: '', mood: '', notes: '' };
}

/** Pré-carrega o formulário com o check-in já gravado do dia (corrigir, não duplicar). */
export function formFromCheckin(checkin: DailyCheckin): CheckinFormState {
  return {
    local_date: checkin.local_date,
    energy: checkin.energy ?? '',
    soreness: checkin.soreness ?? '',
    stress: checkin.stress ?? '',
    mood: checkin.mood ?? '',
    notes: checkin.notes ?? '',
  };
}

/** "1 = sem dor, 5 = muita dor" — a escala sem âncora não quer dizer nada. */
export function scaleHint(field: CheckinScoreField): string {
  const ends = CHECKIN_SCALE_ENDS[field];
  return `${CHECKIN_SCORE_MIN} = ${ends.low}, ${CHECKIN_SCORE_MAX} = ${ends.high}`;
}

export function isScoreInRange(value: CheckinScoreValue): boolean {
  if (value === '') return true;
  return Number.isInteger(value) && value >= CHECKIN_SCORE_MIN && value <= CHECKIN_SCORE_MAX;
}

export function answeredFields(form: CheckinFormState): CheckinScoreField[] {
  return CHECKIN_SCORE_FIELDS.filter((field) => form[field] !== '');
}

export function validateCheckinForm(form: CheckinFormState): CheckinFormErrors {
  const errors: CheckinFormErrors = {};
  if (!form.local_date) errors.local_date = 'Informe a data.';
  for (const field of CHECKIN_SCORE_FIELDS) {
    if (!isScoreInRange(form[field])) {
      errors[field] = `Use um valor de ${CHECKIN_SCORE_MIN} a ${CHECKIN_SCORE_MAX}, ou deixe em branco.`;
    }
  }
  if (form.notes.length > 255) errors.notes = 'Máximo de 255 caracteres.';
  if (answeredFields(form).length === 0 && !form.notes.trim()) {
    errors.energy = 'Responda ao menos um item (ou escreva uma nota).';
  }
  return errors;
}

/**
 * Payload do POST. Campo em branco é OMITIDO, não enviado como `null`: o upsert
 * do backend só atualiza as colunas enviadas, então omitir preserva o que já
 * estava gravado (responder só a dor à tarde não apaga a disposição da manhã).
 * Para APAGAR um escore existe o PUT com `null` explícito.
 */
export function toCheckinInput(form: CheckinFormState): DailyCheckinInput {
  const payload: DailyCheckinInput = { local_date: form.local_date };
  for (const field of CHECKIN_SCORE_FIELDS) {
    const value = form[field];
    if (value !== '') payload[field] = value;
  }
  const notes = form.notes.trim();
  if (notes) payload.notes = notes;
  return payload;
}

// ---------------------------------------------------------------------------
// Leitura
// ---------------------------------------------------------------------------

/** O check-in de um dia específico, ou `null` — nunca um objeto de zeros. */
export function checkinForDate(
  items: readonly DailyCheckin[],
  localDate: string,
): DailyCheckin | null {
  return items.find((item) => item.local_date === localDate) ?? null;
}

/**
 * Quantos dias da lista têm ESTE escore respondido. "Você respondeu dor em 3
 * dos 28 dias" explica por que o sinal ainda não tem baseline — muito mais útil
 * que um "sem dados".
 */
export function answeredDays(items: readonly DailyCheckin[], field: CheckinScoreField): number {
  return items.filter((item) => item[field] != null).length;
}

// ---------------------------------------------------------------------------
// Cooldown (F2.2) — apresentação do estado que o backend decidiu
// ---------------------------------------------------------------------------

const COOLDOWN_REASON_LABELS: Record<InsightCooldown['reason'], string> = {
  first_seen: 'Novo',
  new_episode: 'Voltou a acontecer',
  escalated: 'Piorou',
  cooldown_expired: 'Reavaliado',
  cooldown_active: 'Já visto',
};

export function cooldownBadge(
  cooldown: InsightCooldown | null | undefined,
): { label: string; color: string } | null {
  if (!cooldown) return null;
  const label = COOLDOWN_REASON_LABELS[cooldown.reason] ?? cooldown.reason;
  if (cooldown.reason === 'escalated') return { label, color: 'red' };
  if (cooldown.reason === 'cooldown_active') return { label, color: 'gray' };
  if (cooldown.reason === 'new_episode') return { label, color: 'orange' };
  if (cooldown.reason === 'first_seen') return { label, color: 'blue' };
  return { label, color: 'teal' };
}

/** `true` quando o cartão está na lista apenas porque o usuário pediu para ver os silenciados. */
export function isSuppressed(insight: Pick<Insight, 'cooldown'>): boolean {
  return Boolean(insight.cooldown?.suppressed);
}
