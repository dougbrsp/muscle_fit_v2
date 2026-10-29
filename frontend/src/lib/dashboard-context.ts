import type {
  ContextEvent,
  ContextEventInput,
  ContextEventType,
  ContextResponse,
} from './dashboard-context-types';
import { CONTEXT_EVENT_TYPES } from './dashboard-context-types';
import { fmtDate } from './dashboard-format';

/**
 * Lógica PURA de `/dashboard/contexto` (F2.1) — extraída da tela para ser
 * testada sem React e sem rede.
 *
 * Nenhuma regra de contexto mora aqui. A união de manuais e derivados, a
 * ordenação, as contagens e o intervalo desde a última sessão chegam prontos
 * de `GET /analytics/dashboard/context`; este módulo apenas AGRUPA a timeline
 * por dia para a renderização, rotula e valida o formulário antes do POST.
 */

// ---------------------------------------------------------------------------
// Tipos e rótulos
// ---------------------------------------------------------------------------

export const CONTEXT_EVENT_TYPE_LABELS: Readonly<Record<ContextEventType, string>> = {
  alcohol: 'Álcool',
  travel: 'Viagem',
  illness: 'Doença',
  atypical_sleep: 'Sono atípico',
  gym_change: 'Troca de academia',
  plan_change: 'Troca de ficha/modelo',
  diet_phase: 'Fase de dieta',
  supplement: 'Suplementação',
  injury_pain: 'Lesão ou dor',
  other: 'Outro',
};

export const CONTEXT_EVENT_TYPE_OPTIONS = CONTEXT_EVENT_TYPES.map((type) => ({
  value: type,
  label: CONTEXT_EVENT_TYPE_LABELS[type],
}));

export function isContextEventType(value: string | null | undefined): value is ContextEventType {
  return value != null && (CONTEXT_EVENT_TYPES as readonly string[]).includes(value);
}

export function contextEventLabel(event: Pick<ContextEvent, 'event_type' | 'event_type_label'>): string {
  if (event.event_type_label) return event.event_type_label;
  return isContextEventType(event.event_type)
    ? CONTEXT_EVENT_TYPE_LABELS[event.event_type]
    : event.event_type;
}

/** Selo de origem — o usuário precisa saber o que ele pode editar e o que não. */
export function sourceBadge(event: Pick<ContextEvent, 'derived'>): { label: string; color: string } {
  return event.derived
    ? { label: 'Derivado', color: 'grape' }
    : { label: 'Registrado', color: 'blue' };
}

// ---------------------------------------------------------------------------
// Timeline
// ---------------------------------------------------------------------------

export type ContextDay = { local_date: string; events: ContextEvent[] };

/**
 * Agrupa a timeline por dia PRESERVANDO a ordem que veio do backend (o
 * `context_service` já ordena por dia, tipo e origem). Agrupar no cliente é
 * apresentação; reordenar seria recalcular.
 */
export function groupByDay(events: readonly ContextEvent[]): ContextDay[] {
  const days: ContextDay[] = [];
  const index = new Map<string, ContextDay>();
  for (const event of events) {
    let day = index.get(event.local_date);
    if (!day) {
      day = { local_date: event.local_date, events: [] };
      index.set(event.local_date, day);
      days.push(day);
    }
    day.events.push(event);
  }
  return days;
}

/** Mais recente primeiro, como uma timeline se lê. */
export function groupByDayDescending(events: readonly ContextEvent[]): ContextDay[] {
  return groupByDay(events).sort((a, b) => (a.local_date < b.local_date ? 1 : -1));
}

/** "12/09/2026" ou "12/09/2026 → 22/09/2026" para evento com duração. */
export function eventPeriodLabel(event: Pick<ContextEvent, 'local_date' | 'end_local_date'>): string {
  const start = fmtDate(event.local_date);
  if (!event.end_local_date || event.end_local_date === event.local_date) return start;
  return `${start} → ${fmtDate(event.end_local_date)}`;
}

/** Duração em dias (inclusiva). Evento de um dia só dura 1. */
export function eventDurationDays(event: Pick<ContextEvent, 'local_date' | 'end_local_date'>): number {
  if (!event.end_local_date) return 1;
  const start = Date.parse(`${event.local_date}T00:00:00Z`);
  const end = Date.parse(`${event.end_local_date}T00:00:00Z`);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return 1;
  return Math.round((end - start) / 86_400_000) + 1;
}

/** Limitações do envelope, sem repetir texto. */
export function collectContextLimitations(payload: ContextResponse): string[] {
  return [...new Set(payload.limitations)];
}

// ---------------------------------------------------------------------------
// Formulário (validação antes do POST; o backend valida de novo)
// ---------------------------------------------------------------------------

export const INTENSITY_MIN = 1;
export const INTENSITY_MAX = 5;

export type ContextFormState = {
  event_type: ContextEventType;
  local_date: string;
  end_local_date: string;
  /** `''` = sem gradação. Nunca 0: "sem gradação" é ausência, não o menor grau. */
  intensity: number | '';
  notes: string;
};

export function emptyContextForm(today: string): ContextFormState {
  return { event_type: 'alcohol', local_date: today, end_local_date: '', intensity: '', notes: '' };
}

export type ContextFormErrors = Partial<Record<keyof ContextFormState, string>>;

export function validateContextForm(form: ContextFormState): ContextFormErrors {
  const errors: ContextFormErrors = {};
  if (!/^\d{4}-\d{2}-\d{2}$/.test(form.local_date)) errors.local_date = 'Informe a data (AAAA-MM-DD).';
  if (!isContextEventType(form.event_type)) errors.event_type = 'Escolha o tipo do evento.';
  if (form.end_local_date) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(form.end_local_date)) {
      errors.end_local_date = 'Informe a data final (AAAA-MM-DD).';
    } else if (form.end_local_date < form.local_date) {
      errors.end_local_date = 'A data final não pode ser antes da inicial.';
    }
  }
  if (form.intensity !== '') {
    const value = Number(form.intensity);
    if (!Number.isInteger(value) || value < INTENSITY_MIN || value > INTENSITY_MAX) {
      errors.intensity = `A intensidade vai de ${INTENSITY_MIN} a ${INTENSITY_MAX} (ou deixe em branco).`;
    }
  }
  if (form.notes.length > 255) errors.notes = 'A nota tem no máximo 255 caracteres.';
  return errors;
}

/** Converte o formulário validado no payload do `POST /context-events`. */
export function toContextEventInput(form: ContextFormState): ContextEventInput {
  return {
    event_type: form.event_type,
    local_date: form.local_date,
    end_local_date: form.end_local_date ? form.end_local_date : null,
    intensity: form.intensity === '' ? null : Number(form.intensity),
    notes: form.notes.trim() ? form.notes.trim() : null,
  };
}
