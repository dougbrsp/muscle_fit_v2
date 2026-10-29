import type {
  CardioAdherenceStatus,
  CardioPai,
  CardioResponse,
  CardioSession,
  CardioWeek,
  CardioWellnessPoint,
  SessionHeartRate,
} from './dashboard-cardio-types';
import { fmtDate, fmtInt } from './dashboard-format';
import type { HrZoneSource } from './hr-zones';

/**
 * Lógica PURA de `/dashboard/cardio` (F2.3) — extraída da tela para ser
 * testada sem React e sem rede.
 *
 * Nenhuma regra de cardio mora aqui. Totais, agregado por modalidade,
 * cobertura de FC, status do casamento e as limitações chegam prontos de
 * `GET /analytics/dashboard/cardio`. Este módulo só APRESENTA: rótulo em
 * pt-BR da modalidade, selo do status do casamento e o agrupamento da lista
 * por dia.
 */

// ---------------------------------------------------------------------------
// Rótulos
// ---------------------------------------------------------------------------

/**
 * `sport_type` do Intervals.icu → rótulo em pt-BR.
 *
 * O que NÃO está no mapa aparece como veio: inventar uma tradução para um
 * tipo desconhecido esconderia do usuário o nome real que o relógio gravou —
 * e é por esse nome que ele encontra a atividade no Intervals.
 */
export const SPORT_TYPE_LABELS: Readonly<Record<string, string>> = {
  Run: 'Corrida',
  VirtualRun: 'Corrida virtual',
  TrailRun: 'Trail run',
  Walk: 'Caminhada',
  Hike: 'Trilha',
  Ride: 'Pedal',
  VirtualRide: 'Pedal virtual',
  GravelRide: 'Pedal (gravel)',
  MountainBikeRide: 'Mountain bike',
  Swim: 'Natação',
  Rowing: 'Remo',
  Elliptical: 'Elíptico',
  StairStepper: 'Escada',
  Workout: 'Treino',
  WeightTraining: 'Musculação',
};

export function sportLabel(sportType: string | null | undefined): string {
  if (!sportType) return 'Sem modalidade';
  return SPORT_TYPE_LABELS[sportType] ?? sportType;
}

// ---------------------------------------------------------------------------
// Casamento treino ↔ atividade do Intervals
// ---------------------------------------------------------------------------

export type MatchBadge = { label: string; color: string; hint: string };

/**
 * Selo da ORIGEM da FC daquela sessão. Quatro estados, quatro frases, e a
 * ordem entre eles é a mesma do backend:
 *
 * 1. `activity` — a FC foi MEDIDA no próprio treino. Não depende de
 *    casamento nenhum, e por isso não carrega a ressalva dele (nem quando o
 *    job achou candidatos ambíguos para essa sessão);
 * 2. link AMBÍGUO — a FC veio de um casamento com mais de um candidato na
 *    janela. Nunca se disfarça de certeza: é este selo que diz "pode ser de
 *    outro treino";
 * 3. link limpo;
 * 4. sem FC alguma.
 */
export function matchBadge(
  item: Pick<SessionHeartRate, 'match_status' | 'candidates_count' | 'hr_source'>,
): MatchBadge {
  if (item.hr_source === 'activity') {
    return {
      label: 'FC do treino',
      color: 'teal',
      hint: 'Medida registrada na própria sessão (importada do relógio): não depende do casamento com o Intervals.icu.',
    };
  }
  if (item.hr_source === 'intervals_link' && item.match_status === 'MATCHED_AMBIGUOUS') {
    return {
      label: 'FC ambígua',
      color: 'yellow',
      hint: `${item.candidates_count ?? 2} atividades do Intervals.icu caíram na janela de tolerância; a mostrada é a mais próxima no tempo.`,
    };
  }
  if (item.hr_source === 'intervals_link') {
    return {
      label: 'FC associada',
      color: 'blue',
      hint: 'Casada com a atividade WeightTraining do Intervals.icu pelo horário de início — associação por proximidade, não medida da sessão.',
    };
  }
  return {
    label: 'Sem FC',
    color: 'gray',
    hint: 'A sessão não trouxe FC própria e nenhuma atividade WeightTraining do Intervals.icu caiu no horário dela.',
  };
}

/** "3 min" a partir do `match_delta_seconds` do link; `null` sem casamento. */
export function matchDeltaLabel(seconds: number | null | undefined): string | null {
  if (seconds == null) return null;
  const minutos = Math.round(Math.abs(seconds) / 60);
  return minutos <= 1 ? 'menos de 1 min de diferença' : `${minutos} min de diferença`;
}

// ---------------------------------------------------------------------------
// Listas
// ---------------------------------------------------------------------------

export type CardioDay = { activity_date: string; sessions: CardioSession[] };

/**
 * Agrupa as atividades por dia PRESERVANDO a ordem que veio do backend (ele
 * já ordena do mais recente para o mais antigo). Agrupar é apresentação;
 * reordenar seria recalcular.
 */
export function groupSessionsByDay(sessions: readonly CardioSession[]): CardioDay[] {
  const days: CardioDay[] = [];
  const index = new Map<string, CardioDay>();
  for (const session of sessions) {
    const key = session.activity_date ?? 'sem-data';
    let day = index.get(key);
    if (!day) {
      day = { activity_date: key, sessions: [] };
      index.set(key, day);
      days.push(day);
    }
    day.sessions.push(session);
  }
  return days;
}

// ---------------------------------------------------------------------------
// Envelope
// ---------------------------------------------------------------------------

/** Limitações do envelope, sem repetir texto. */
export function collectCardioLimitations(payload: CardioResponse): string[] {
  return [...new Set(payload.limitations)];
}

/**
 * Há ALGO para mostrar? Separa "tela vazia" de "tela com dados", sem
 * confundir com "Intervals não configurado" — que tem aviso próprio.
 *
 * A FC por sessão conta: ela vem do histórico já associado e existe mesmo
 * quando nenhuma atividade de cardio foi sincronizada.
 */
export function hasAnythingToShow(payload: CardioResponse): boolean {
  return (
    payload.totals.sessions > 0 ||
    payload.strength_heart_rate.sessions_with_hr > 0 ||
    (payload.adherence?.sessions.length ?? 0) > 0 ||
    (payload.wellness?.points.length ?? 0) > 0 ||
    (payload.pai?.points.length ?? 0) > 0 ||
    payload.next_session != null
  );
}

// ---------------------------------------------------------------------------
// M98 C4 — acompanhamento. Só FORMA dados prontos para o gráfico e rótulos;
// nenhum número é calculado aqui (zonas, classe da sessão, aderência, TSB e a
// meta de PAI chegam prontos do backend).
// ---------------------------------------------------------------------------

/** Zonas do app na ordem do gráfico (rótulos do backend: `hr_zones.zone_label`). */
export const ZONE_KEYS = ['Z1', 'Z2', 'Z3', 'Z4', 'Z5'] as const;

export type WeekZoneRow = { semana: string; week_start: string } & Partial<Record<(typeof ZONE_KEYS)[number], number>>;

/** "dd/mm" da segunda-feira da semana (eixo X). */
export function weekLabel(weekStart: string): string {
  return fmtDate(weekStart).slice(0, 5);
}

/**
 * Linhas do gráfico de minutos por zona: uma por semana; semana sem tempo em
 * zona fica sem valores (o gráfico mostra a lacuna, não um zero inventado).
 */
export function weeklyZoneRows(weeks: readonly CardioWeek[]): WeekZoneRow[] {
  return weeks.map((week) => {
    const row: WeekZoneRow = { semana: weekLabel(week.week_start), week_start: week.week_start };
    if (week.zone_minutes) {
      for (const key of ZONE_KEYS) {
        const value = week.zone_minutes[key];
        if (value != null) row[key] = value;
      }
    }
    return row;
  });
}

export type WeekModeRow = {
  semana: string;
  week_start: string;
  continuo: number;
  intensa: number;
  semClasse: number;
  sessoesContinuo: number;
  sessoesIntensa: number;
  sessoesSemClasse: number;
};

/** Contínuo × intensa (× sem tempo em zona) por semana, como o backend classificou. */
export function weeklyModeRows(weeks: readonly CardioWeek[]): WeekModeRow[] {
  return weeks.map((week) => ({
    semana: weekLabel(week.week_start),
    week_start: week.week_start,
    continuo: week.continuous_minutes,
    intensa: week.high_intensity_minutes,
    semClasse: week.unclassified_minutes,
    sessoesContinuo: week.continuous_sessions,
    sessoesIntensa: week.high_intensity_sessions,
    sessoesSemClasse: week.unclassified_sessions,
  }));
}

/** Há alguma semana com tempo em zona? Sem nenhuma, o gráfico de zonas não aparece. */
export function hasZoneData(weeks: readonly CardioWeek[]): boolean {
  return weeks.some((week) => week.zone_minutes != null);
}

/** Descrição textual do gráfico de zonas (acessibilidade): a última semana com zona. */
export function zoneChartSummary(weeks: readonly CardioWeek[]): string {
  const last = [...weeks].reverse().find((week) => week.zone_minutes != null);
  if (!last || !last.zone_minutes) return 'Minutos por zona de FC por semana: sem tempo em zona na janela.';
  const parts = ZONE_KEYS.map((key) => `${key} ${fmtInt(last.zone_minutes?.[key] ?? 0)} min`);
  return `Minutos por zona de FC por semana. Semana de ${fmtDate(last.week_start)}: ${parts.join(', ')}.`;
}

/** Descrição textual do gráfico contínuo × intensa: a semana mais recente. */
export function modeChartSummary(weeks: readonly CardioWeek[]): string {
  const last = weeks[weeks.length - 1];
  if (!last) return 'Cardio contínuo e intenso por semana: sem semanas na janela.';
  return (
    `Cardio contínuo e intenso por semana. Semana de ${fmtDate(last.week_start)}: ` +
    `${fmtInt(last.continuous_sessions)} contínua(s) (${fmtInt(last.continuous_minutes)} min), ` +
    `${fmtInt(last.high_intensity_sessions)} intensa(s) (${fmtInt(last.high_intensity_minutes)} min), ` +
    `${fmtInt(last.unclassified_sessions)} sem tempo em zona.`
  );
}

export type WellnessRow = {
  data: string;
  ctl: number | null;
  atl: number | null;
  tsb: number | null;
  hrv: number | null;
  fcRepouso: number | null;
};

export function wellnessRows(points: readonly CardioWellnessPoint[]): WellnessRow[] {
  return points.map((p) => ({
    data: fmtDate(p.date).slice(0, 5),
    ctl: p.ctl,
    atl: p.atl,
    tsb: p.tsb,
    hrv: p.hrv_ms,
    fcRepouso: p.resting_hr_bpm,
  }));
}

export function paiRows(pai: CardioPai): { data: string; pai: number }[] {
  return pai.points.map((p) => ({ data: fmtDate(p.date).slice(0, 5), pai: p.pai }));
}

/** Cor do selo do status da aderência (o rótulo vem do backend). */
export function adherenceStatusColor(status: CardioAdherenceStatus | string): string {
  switch (status) {
    case 'adherent':
      return 'teal';
    case 'partial':
      return 'yellow';
    case 'low':
      return 'orange';
    case 'not_done':
      return 'red';
    default:
      return 'gray';
  }
}

/** Origem das zonas de FC em pt-BR. */
export function zoneSourceLabel(source: HrZoneSource | string | null | undefined): string {
  switch (source) {
    case 'intervals':
      return 'Intervals.icu';
    case 'manual_max_hr':
      return 'FCmáx do Perfil';
    default:
      return 'Sem zonas definidas';
  }
}
