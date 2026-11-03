// Check-in semanal da nutrição (N7, mapa `docs/motores/FONTES_DA_VERDADE.md` §83): tipos das respostas de
// `GET /nutrition/checkin`, `GET /nutrition/checkins`, `POST /nutrition/checkins/{id}/accept|decline` e
// `GET|PUT /nutrition/checkin/settings`, as chamadas e funções PURAS de apresentação. NENHUMA regra de nutrição
// mora aqui: veredito, ajuste de kcal, metas propostas, validade e visibilidade vêm prontos da API (dono:
// `api/app/domain/nutrition/weekly_checkin.py`). Só se converte UNIDADE para exibir (fração -> %, via
// `shareToPercent`) e se escolhe a cor da etiqueta.

import { apiFetch } from './api';
import { formatKcal, shareToPercent } from './nutrition-format';
import type { NutritionLimitation } from './nutrition-types';

/** Veredito do motor (`weekly_checkin.CheckinVerdict`). */
export type CheckinVerdict = 'on_track' | 'slow' | 'fast' | 'above_band' | 'below_band' | 'inconclusive' | 'goal_reached';

/** Estado da linha (`weekly_checkin.CheckinStatus`). */
export type CheckinStatus = 'proposed' | 'accepted' | 'declined' | 'expired' | 'informative';

export type CheckinTrend = {
  weekly_change: number | null;
  readings: number;
  span_days: number | null;
  mean: number | null;
  valid_for_bia: boolean;
};

/** Resumo da semana avaliada, com os valores já calculados pelo motor. */
export type CheckinSummary = {
  complete_days: number;
  days_with_target: number;
  mean_intake_kcal: number | null;
  mean_target_kcal: number | null;
  mean_tdee_kcal: number | null;
  planned_balance_kcal: number | null;
  kcal_share: number | null;
  protein_share: number | null;
  adherence_valid: boolean;
  kcal_within_tolerance: boolean | null;
  weight_week_mean_kg: number | null;
  weight_previous_week_mean_kg: number | null;
  weight_weekly_change_kg: number | null;
  weight_weekly_change_share: number | null;
  expected_weekly_change_kg: number | null;
  maintenance_band_kg: number | null;
  body_fat_percent: number | null;
  muscle_mass_kg: number | null;
  weight_trend: CheckinTrend;
  lean_trend: CheckinTrend;
  fat_trend: CheckinTrend;
  observed_tdee: { kcal: number | null; quality: string; limitation_items?: NutritionLimitation[] } | null;
};

export type CheckinDayMacros = { kcal: number | null; protein_g: number | null; carb_g: number | null; fat_g: number | null };

/** O que muda no plano, campo a campo (`unit`: `share` = fração para exibir em %, `g` = gramas). */
export type CheckinChangeItem = { field: string; label: string; unit: 'share' | 'g' | 'kcal'; before: number | null; after: number | null };

export type CheckinProposal = {
  before: CheckinDayMacros;
  after: CheckinDayMacros;
  delta_kcal: number;
  changes: Record<string, unknown>;
  change_items: CheckinChangeItem[];
  /** O que `before`/`after` representam (`rest_day` ou `cycle_average`) e o rótulo pronto da API. */
  scope?: string;
  scope_label?: string;
};

export type CheckinFrozenDay = {
  local_date: string;
  target_kcal: number | null;
  intake_kcal: number | null;
  tdee_kcal: number | null;
  weight_kg: number | null;
  body_fat_percent: number | null;
  muscle_mass_kg: number | null;
};

export type Checkin = {
  id: number;
  user_id: number;
  week_start: string;
  week_end: string;
  due_date: string | null;
  status: CheckinStatus;
  status_label: string;
  verdict: CheckinVerdict;
  verdict_label: string;
  /** Falta resposta do usuário (proposta ainda válida). */
  can_respond: boolean;
  /** O card da home/diário deve aparecer (decidido pelo motor). */
  show_card: boolean;
  expires_at: string | null;
  /** Último dia em que a proposta ainda vale. */
  expires_on: string | null;
  adjustment_kcal: number | null;
  missing_items: NutritionLimitation[];
  reason_items: NutritionLimitation[];
  proposal: CheckinProposal | null;
  engine_version: string | null;
  source_plan_id: number | null;
  generated_plan_id: number | null;
  responded_at: string | null;
  created_at: string | null;
  summary: CheckinSummary | Partial<CheckinSummary> | null;
  /** Só no detalhe (`GET /nutrition/checkin`), não no histórico. */
  days?: CheckinFrozenDay[] | null;
  strategy?: string | null;
  goal?: { goal_weight_kg: number | null; goal_body_fat_percent: number | null } | null;
};

export type CheckinWeekdayOption = { value: number; label: string };

export type CheckinSettings = { weekday: number; weekday_label: string; is_default: boolean };

export type CheckinResponse = {
  user: { id: number; username: string; full_name: string | null };
  today: string;
  settings: CheckinSettings;
  weekday_options: CheckinWeekdayOption[];
  due_date: string;
  week_start: string;
  week_end: string;
  checkin: Checkin | null;
  /** Por que não há check-in (sem plano, plano mais novo que a semana). */
  none_items: NutritionLimitation[];
};

export type CheckinHistoryResponse = {
  user: { id: number; username: string; full_name: string | null };
  today: string;
  items: Checkin[];
};

export type CheckinSettingsResponse = CheckinSettings & { user_id: number; weekday_options: CheckinWeekdayOption[] };

export type CheckinAcceptResponse = {
  checkin: Checkin;
  already_accepted: boolean;
  plan: { id: number; effective_from: string | null } | null;
};

export type CheckinDeclineResponse = { checkin: Checkin; already_declined: boolean };

export const checkinKeys = {
  all: ['nutrition-checkin'] as const,
  current: (userId: number | null) => ['nutrition-checkin', 'current', userId ?? 'me'] as const,
  history: (userId: number | null) => ['nutrition-checkin', 'history', userId ?? 'me'] as const,
  settings: (userId: number | null) => ['nutrition-checkin', 'settings', userId ?? 'me'] as const,
};

function withUser(path: string, userId: number | null): string {
  return userId != null ? `${path}?user_id=${userId}` : path;
}

export function fetchCheckin(userId: number | null): Promise<CheckinResponse> {
  return apiFetch<CheckinResponse>(withUser('/nutrition/checkin', userId));
}

export function fetchCheckinHistory(userId: number | null): Promise<CheckinHistoryResponse> {
  return apiFetch<CheckinHistoryResponse>(withUser('/nutrition/checkins', userId));
}

export function acceptCheckin(checkinId: number): Promise<CheckinAcceptResponse> {
  return apiFetch<CheckinAcceptResponse>(`/nutrition/checkins/${checkinId}/accept`, { method: 'POST' });
}

export function declineCheckin(checkinId: number): Promise<CheckinDeclineResponse> {
  return apiFetch<CheckinDeclineResponse>(`/nutrition/checkins/${checkinId}/decline`, { method: 'POST' });
}

export function fetchCheckinSettings(userId: number | null): Promise<CheckinSettingsResponse> {
  return apiFetch<CheckinSettingsResponse>(withUser('/nutrition/checkin/settings', userId));
}

export function saveCheckinWeekday(weekday: number, userId: number | null): Promise<CheckinSettingsResponse> {
  return apiFetch<CheckinSettingsResponse>('/nutrition/checkin/settings', {
    method: 'PUT',
    body: JSON.stringify(userId != null ? { weekday, user_id: userId } : { weekday }),
  });
}

// ---------------------------------------------------------------------------
// Apresentação (puras)
// ---------------------------------------------------------------------------

export type VerdictTone = 'green' | 'yellow' | 'orange' | 'gray' | 'blue';

/** Cor da etiqueta do veredito (só apresentação; o veredito é do motor). */
export function verdictTone(verdict: CheckinVerdict): VerdictTone {
  switch (verdict) {
    case 'on_track':
      return 'green';
    case 'slow':
    case 'fast':
    case 'above_band':
    case 'below_band':
      return 'orange';
    case 'goal_reached':
      return 'blue';
    default:
      return 'gray';
  }
}

/** Valor de um item de mudança para exibir: fração vira `%` e gramas ganham a unidade; ausente = `-`. */
export function formatChangeValue(item: Pick<CheckinChangeItem, 'unit'>, value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '-';
  if (item.unit === 'g') return `${Math.round(value)} g`;
  if (item.unit === 'kcal') return `${Math.round(value)} kcal`;
  const percent = shareToPercent(value);
  return percent == null ? '-' : `${percent.toLocaleString('pt-BR', { maximumFractionDigits: 2 })}%`;
}

/** Diferença de kcal/dia da proposta com sinal (`+120 kcal`, `-85 kcal`); sem proposta = `null`. */
export function formatAdjustment(deltaKcal: number | null | undefined): string | null {
  if (deltaKcal == null || !Number.isFinite(deltaKcal)) return null;
  const arredondado = Math.round(deltaKcal);
  if (arredondado === 0) return formatKcal(0);
  return `${arredondado > 0 ? '+' : '-'}${formatKcal(Math.abs(arredondado))}`;
}

/** `2026-11-01` -> `01/11` (sem escorregar de fuso). */
export function shortDay(isoDate: string | null | undefined): string {
  if (!isoDate) return '-';
  const [, mes, dia] = isoDate.slice(0, 10).split('-');
  return mes && dia ? `${dia}/${mes}` : isoDate;
}

/** `26/10 a 01/11`. */
export function weekRangeLabel(checkin: Pick<Checkin, 'week_start' | 'week_end'>): string {
  return `${shortDay(checkin.week_start)} a ${shortDay(checkin.week_end)}`;
}
