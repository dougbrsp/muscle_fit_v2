// Dashboard de nutrição (N5, mapa `docs/motores/FONTES_DA_VERDADE.md` §82): tipos
// da resposta de `GET /nutrition/dashboard`, chamada e funções PURAS que só
// REMONTAM o JSON para os gráficos (recharts). Nenhuma regra numérica mora aqui:
// aderência, média móvel, ritmo, projeção e estados vazios vêm prontos da API
// (dono: `api/app/domain/nutrition/dashboard.py`). Só se converte UNIDADE para
// exibir (fração -> %, via `shareToPercent`) e se escolhe o rótulo/ordem.

import { apiFetch } from './api';
import { ENERGY_NUTRIENT, nutrientField } from './nutrition-constants';
import { shareToPercent } from './nutrition-format';
import type { BalanceRow, NutritionLimitation, NutritionWeek, WeekReport } from './nutrition-types';

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

export type DashboardDay = {
  local_date: string;
  has_target: boolean;
  logged: boolean;
  intake_incomplete: boolean;
  /** Por código do nutriente (`energy_kcal`, `protein_g`, `carbohydrates_g`, `total_fat_g`). */
  macros: Record<string, BalanceRow>;
  kcal_share: number | null;
  protein_share: number | null;
  kcal_within_tolerance: boolean | null;
  protein_ok: boolean | null;
};

export type DashboardAdherence = {
  window_days: number;
  logged_days: number;
  days_with_target: number;
  days_without_target: number;
  kcal_within_tolerance_days: number;
  protein_ok_days: number;
  incomplete_days: string[];
};

export type DashboardWeek = {
  start: string;
  end: string;
  /** A janela cobre só parte da semana. */
  partial: boolean;
  report: Omit<WeekReport, 'limitations'> & { limitations: NutritionLimitation[] };
};

export type BodyPoint = {
  local_date: string;
  weight_kg: number | null;
  /** Média móvel do peso (dono: `dashboard.trailing_mean`). */
  weight_trend_kg: number | null;
  body_fat_percent: number | null;
  muscle_mass_kg: number | null;
};

export type WeightTrendSummary = {
  slope_kg_per_day: number | null;
  readings: number;
  span_days: number | null;
  weekly_change_kg: number | null;
  limitations: string[];
  limitation_items: NutritionLimitation[];
};

export type DashboardPlan = {
  id: number;
  strategy: string;
  effective_from: string;
  goal_weight_kg: number | null;
  goal_body_fat_percent: number | null;
};

export type GoalProjection = {
  status: 'projected' | 'goal_reached' | 'incoherent_with_strategy' | 'insufficient_data';
  goal_kind: 'weight' | 'body_fat' | null;
  current: number | null;
  target: number | null;
  target_weight_kg: number | null;
  weeks: number | null;
  eta_date: string | null;
  progress_percent: number | null;
  weekly_rate_share: number | null;
  reasons: string[];
  reason_items: NutritionLimitation[];
  /** Alvo de %G com a massa livre de gordura atual suposta constante: o prazo é otimista (limite inferior). */
  is_minimum_estimate: boolean;
};

export type ObservedTdee = {
  kcal: number | null;
  quality: string;
  window_days: number;
  logged_days: number;
  mean_intake_kcal: number | null;
  limitation_items: NutritionLimitation[];
};

export type TdeeSuggestion = {
  formula_kcal: number | null;
  observed_kcal: number | null;
  suggested_kcal: number | null;
  delta_kcal: number | null;
  should_suggest: boolean;
  limitation_items: NutritionLimitation[];
};

export type NutritionDashboard = {
  user: { id: number; username: string; full_name: string | null };
  today: string;
  start: string;
  end: string;
  window_days: number;
  plan: DashboardPlan | null;
  empty_states: NutritionLimitation[];
  days: DashboardDay[];
  adherence: DashboardAdherence;
  weeks: DashboardWeek[];
  body: { points: BodyPoint[]; weight_trend: WeightTrendSummary };
  projection: GoalProjection | null;
  /** Sempre `applied: false`: o dashboard só sugere, nunca altera o plano. */
  adaptive_tdee: { observed: ObservedTdee; suggestion: TdeeSuggestion; applied: false };
};

export const dashboardKeys = {
  all: ['nutrition-dashboard'] as const,
  window: (userId: number | null, days: number) => ['nutrition-dashboard', userId ?? 'me', days] as const,
};

export function fetchNutritionDashboard(days: number, userId: number | null): Promise<NutritionDashboard> {
  const params = new URLSearchParams({ days: String(days) });
  if (userId != null) params.set('user_id', String(userId));
  return apiFetch<NutritionDashboard>(`/nutrition/dashboard?${params.toString()}`);
}

// ---------------------------------------------------------------------------
// Montagem dos gráficos (puras)
// ---------------------------------------------------------------------------

/** Ordem dos macros na tela (kcal primeiro); os códigos são os do `meal_math`. */
export const MACRO_ORDER: readonly string[] = [ENERGY_NUTRIENT, 'protein_g', 'carbohydrates_g', 'total_fat_g'];

/** Linhas do balanço do dia na ordem da tela; macro ausente na resposta é omitido. */
export function macroRows(rows: readonly BalanceRow[]): BalanceRow[] {
  return MACRO_ORDER.map((codigo) => rows.find((linha) => linha.field === codigo)).filter(
    (linha): linha is BalanceRow => linha !== undefined,
  );
}

export type RingPart = { key: 'within' | 'to_consume' | 'excess'; label: string; value: number };

const RING_LABELS: Record<RingPart['key'], string> = {
  within: 'Consumido',
  to_consume: 'A consumir',
  excess: 'Acima da meta',
};

/** Partes da rosca de um macro, já prontas do motor; sem meta ou com ingestão incompleta = `null` (sem gráfico). */
export function ringParts(row: BalanceRow): RingPart[] | null {
  if (row.within_target == null || row.to_consume == null || row.excess == null) return null;
  const partes: RingPart[] = [
    { key: 'within', label: RING_LABELS.within, value: row.within_target },
    { key: 'to_consume', label: RING_LABELS.to_consume, value: row.to_consume },
    { key: 'excess', label: RING_LABELS.excess, value: row.excess },
  ];
  return partes.filter((parte) => parte.value > 0 || parte.key !== 'excess');
}

export type WeekBarRow = { date: string; label: string; consumido: number | null; meta: number | null; incompleto: boolean };

/** Barras da semana do diário: kcal consumida (só de dia com total completo) × meta, direto da semana da API. */
export function weekBarRows(week: NutritionWeek | undefined, label: (isoDate: string) => string): WeekBarRow[] {
  if (!week) return [];
  return week.days.map((dia) => {
    const completo = dia.totals != null && !dia.totals.incomplete_fields.includes(ENERGY_NUTRIENT);
    return {
      date: dia.local_date,
      label: label(dia.local_date),
      consumido: completo ? (dia.totals?.nutrients[ENERGY_NUTRIENT] ?? null) : null,
      meta: dia.target?.kcal ?? null,
      incompleto: dia.intake_incomplete,
    };
  });
}

export type ShareRow = { date: string; label: string; percent: number | null; registrado: boolean; incompleto: boolean };

/** Fração da meta de um macro por dia, em % (a fração vem do motor; só muda a unidade). */
export function shareRows(days: readonly DashboardDay[], field: string, label: (isoDate: string) => string): ShareRow[] {
  return days.map((dia) => ({
    date: dia.local_date,
    label: label(dia.local_date),
    percent: shareToPercent(dia.macros[field]?.share),
    registrado: dia.logged,
    incompleto: dia.intake_incomplete,
  }));
}

export type WeekAdherenceRow = {
  start: string;
  label: string;
  kcal: number | null;
  proteina: number | null;
  parcial: boolean;
  diasRegistrados: number;
};

/** Aderência por semana (kcal e proteína, em % da meta), da `weekly_budget.week_report` de cada semana. */
export function weekAdherenceRows(weeks: readonly DashboardWeek[], label: (isoDate: string) => string): WeekAdherenceRow[] {
  return weeks.map((semana) => ({
    start: semana.start,
    label: label(semana.start),
    kcal: shareToPercent(semana.report.adherence.kcal_share),
    proteina: shareToPercent(semana.report.adherence.protein_share),
    parcial: semana.partial,
    diasRegistrados: semana.report.adherence.logged_days,
  }));
}

export type BodyRow = { date: string; label: string; peso: number | null; media: number | null; gordura: number | null; musculo: number | null };

/** Séries de peso (com média móvel), %G e massa muscular; cada gráfico filtra os pontos que tem. */
export function bodyRows(points: readonly BodyPoint[], label: (isoDate: string) => string): BodyRow[] {
  return points.map((ponto) => ({
    date: ponto.local_date,
    label: label(ponto.local_date),
    peso: ponto.weight_kg,
    media: ponto.weight_trend_kg,
    gordura: ponto.body_fat_percent,
    musculo: ponto.muscle_mass_kg,
  }));
}

/** Há ao menos um ponto com o valor (o gráfico só aparece com dado). */
export function hasSeries(rows: readonly BodyRow[], key: 'peso' | 'gordura' | 'musculo'): boolean {
  return rows.some((linha) => linha[key] != null);
}

/** Rótulo curto dd/mm de uma data ISO, sem escorregar de fuso. */
export function shortDateLabel(isoDate: string): string {
  const [, mes, dia] = isoDate.slice(0, 10).split('-');
  return mes && dia ? `${dia}/${mes}` : isoDate;
}

/** Rótulo do macro (nome da RDC 429) com a unidade, para o título do gráfico. */
export function macroTitle(field: string): string {
  const campo = nutrientField(field);
  return campo ? `${campo.label}` : field;
}

export function macroUnit(field: string): string {
  return nutrientField(field)?.unit ?? '';
}
