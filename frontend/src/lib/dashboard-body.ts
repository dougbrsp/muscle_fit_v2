import type {
  BodyMeasurementInput,
  BodyResponse,
  MeasurementPoint,
  MeasurementSite,
  PerformancePoint,
  RecompositionStatus,
  WeightPoint,
} from './dashboard-body-types';
import { isMeasurementSite, WAIST_SITE } from './body-measurement-sites';
import { fmtDate } from './dashboard-format';

/**
 * Lógica PURA de `/dashboard/corpo` (F1.4) — extraída da tela para ser
 * testada sem React e sem rede.
 *
 * Nenhuma regra do corpo mora aqui. Média móvel, variação de cintura,
 * tendência da bioimpedância e o sinal de recomposição chegam prontos de
 * `GET /analytics/dashboard/body`; este módulo apenas MONTA as linhas dos
 * gráficos (um eixo por métrica, gráficos alinhados no tempo — sem
 * dual-axis), rotula e valida o formulário de medida antes do POST.
 */

// ---------------------------------------------------------------------------
// Locais e rótulos: espelho gerado do dono (`body-measurement-sites.ts`), sem
// texto à mão aqui; reexportados para os consumidores antigos desta lib.
// ---------------------------------------------------------------------------

export { isMeasurementSite, MEASUREMENT_SITE_LABELS, MEASUREMENT_SITE_OPTIONS } from './body-measurement-sites';

/** Rótulo da fonte de uma pesagem, para o tooltip do gráfico de peso. */
export const WEIGHT_SOURCE_LABELS: Readonly<Record<string, string>> = {
  body_composition: 'Bioimpedância (balança)',
  daily_biometrics: 'Registro diário',
  intervals_wellness: 'Intervals.icu',
};

export function weightSourceLabel(source: string): string {
  return WEIGHT_SOURCE_LABELS[source] ?? source;
}

// ---------------------------------------------------------------------------
// Sinal de recomposição
// ---------------------------------------------------------------------------

export const RECOMPOSITION_VISUAL: Readonly<
  Record<RecompositionStatus, { label: string; color: string; description: string }>
> = {
  compatible: {
    label: 'Compatível com recomposição',
    color: 'green',
    description: 'Peso estável, cintura caindo e desempenho mantido — um sinal, nunca uma prova.',
  },
  not_compatible: {
    label: 'Não compatível',
    color: 'gray',
    description:
      'As três verificações foram avaliadas e ao menos uma não se confirmou — veja quais abaixo. Não é o mesmo que descartar recomposição.',
  },
  insufficient_data: {
    label: 'Dados insuficientes',
    color: 'yellow',
    description:
      'Falta dado para avaliar ao menos uma das três verificações — as limitações dizem o que registrar.',
  },
};

/** Cor/rótulo de uma verificação individual (`true` | `false` | `null`). */
export function checkVisual(result: boolean | null): { label: string; color: string } {
  if (result === true) return { label: 'Sim', color: 'green' };
  if (result === false) return { label: 'Não', color: 'red' };
  return { label: 'Sem dado', color: 'gray' };
}

// ---------------------------------------------------------------------------
// Linhas dos gráficos
// ---------------------------------------------------------------------------

/** Data ISO -> epoch ms (meio-dia UTC, para não deslizar de dia por fuso). */
export function dateToTime(isoDate: string): number {
  return Date.parse(`${isoDate}T12:00:00Z`);
}

export type WeightChartRow = {
  date: string;
  /** Eixo X numérico compartilhado entre os gráficos alinhados. */
  t: number;
  label: string;
  peso: number;
  media7: number | null;
  fonte: string;
  pontos: number;
};

/** Um ponto por dia COM pesagem (dias sem pesagem não existem: nunca zero). */
export function weightChartRows(points: readonly WeightPoint[]): WeightChartRow[] {
  return points.map((point) => ({
    date: point.local_date,
    t: dateToTime(point.local_date),
    label: fmtDate(point.local_date),
    peso: point.weight_kg,
    media7: point.ma7_kg,
    fonte: weightSourceLabel(point.source),
    pontos: point.ma7_points,
  }));
}

export type MeasurementChartRow = {
  date: string;
  t: number;
  label: string;
  /** Medida no protocolo (linha). */
  protocolo: number | null;
  /** Medida fora do protocolo (ponto isolado, confiança LOW). */
  foraProtocolo: number | null;
  notas: string | null;
};

/**
 * Série de um local: as medidas no protocolo formam a linha; as fora do
 * protocolo viram pontos separados, para ficarem VISÍVEIS sem entrar na
 * tendência — a mesma regra que o backend aplica ao sinal.
 */
export function measurementChartRows(points: readonly MeasurementPoint[]): MeasurementChartRow[] {
  return points.map((point) => ({
    date: point.local_date,
    t: dateToTime(point.local_date),
    label: fmtDate(point.local_date),
    protocolo: point.protocol_ok ? point.value_cm : null,
    foraProtocolo: point.protocol_ok ? null : point.value_cm,
    notas: point.notes,
  }));
}

export type PerformanceChartRow = {
  date: string;
  t: number;
  label: string;
  e1rm: number;
  exercicios: number;
};

export function performanceChartRows(points: readonly PerformancePoint[]): PerformanceChartRow[] {
  return points.map((point) => ({
    date: point.local_date,
    t: dateToTime(point.local_date),
    label: fmtDate(point.local_date),
    e1rm: point.e1rm_avg_kg,
    exercicios: point.exercises,
  }));
}

/**
 * Domínio comum do eixo X (datas ISO) para os gráficos alinhados no tempo:
 * a união das datas das séries, ordenada. Cada gráfico mantém o PRÓPRIO
 * eixo Y; só o X é compartilhado.
 */
export function alignedDateDomain(...series: ReadonlyArray<ReadonlyArray<{ date: string }>>): string[] {
  const all = new Set<string>();
  for (const rows of series) for (const row of rows) all.add(row.date);
  return Array.from(all).sort();
}

/**
 * `[início, fim]` em epoch ms da janela da tela, para todos os gráficos
 * usarem o MESMO eixo X numérico (o que alinha as datas visualmente mesmo
 * quando uma série tem pontos e a outra não).
 */
export function sharedTimeDomain(dateFrom: string, dateTo: string): [number, number] {
  return [dateToTime(dateFrom), dateToTime(dateTo)];
}

// ---------------------------------------------------------------------------
// Limitações e ressalvas agregadas
// ---------------------------------------------------------------------------

/** Limitações do envelope + cintura (o que o PartialState do topo mostra). */
export function collectBodyLimitations(payload: BodyResponse): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const text of [...payload.limitations, ...payload.waist.limitations, ...payload.weight.limitations]) {
    if (!seen.has(text)) {
      seen.add(text);
      out.push(text);
    }
  }
  return out;
}

/** Marcos que caem dentro de um intervalo de datas ISO (inclusivo). */
export function milestonesInRange<T extends { local_date: string }>(
  milestones: readonly T[],
  dateFrom: string,
  dateTo: string,
): T[] {
  return milestones.filter((item) => item.local_date >= dateFrom && item.local_date <= dateTo);
}

// ---------------------------------------------------------------------------
// Formulário de medida (validação antes do POST; o backend valida de novo)
// ---------------------------------------------------------------------------

export const VALUE_CM_MIN = 10;
export const VALUE_CM_MAX = 300;

/**
 * Janela do `<input type="date">` da medida — só UX. A regra mora em
 * `api/app/body_measurements.py` (`LOCAL_DATE_MIN`, `LOCAL_DATE_MAX_AHEAD_DAYS`,
 * `normalize_local_date`), que devolve 422 fora dela; a paridade dos dois
 * valores é verificada em `api/tests/unit/test_body_measurements_crud_unit.py`.
 */
export const MEASUREMENT_DATE_MIN = '2000-01-01';
const MEASUREMENT_DATE_MAX_AHEAD_DAYS = 1;

/** Teto do seletor de data: `today` (AAAA-MM-DD) + a folga do backend. */
export function measurementDateMax(today: string): string {
  const base = new Date(`${today}T00:00:00Z`);
  if (Number.isNaN(base.getTime())) return today;
  base.setUTCDate(base.getUTCDate() + MEASUREMENT_DATE_MAX_AHEAD_DAYS);
  return base.toISOString().slice(0, 10);
}

export type MeasurementFormState = {
  local_date: string;
  site: MeasurementSite;
  value_cm: number | '';
  protocol_ok: boolean;
  notes: string;
};

export function emptyMeasurementForm(today: string): MeasurementFormState {
  return { local_date: today, site: WAIST_SITE, value_cm: '', protocol_ok: true, notes: '' };
}

export type MeasurementFormErrors = Partial<Record<keyof MeasurementFormState, string>>;

export function validateMeasurementForm(form: MeasurementFormState): MeasurementFormErrors {
  const errors: MeasurementFormErrors = {};
  if (!/^\d{4}-\d{2}-\d{2}$/.test(form.local_date)) errors.local_date = 'Informe a data (AAAA-MM-DD).';
  if (!isMeasurementSite(form.site)) errors.site = 'Escolha o local medido.';
  if (form.value_cm === '' || !Number.isFinite(Number(form.value_cm))) {
    errors.value_cm = 'Informe o valor em centímetros.';
  } else if (Number(form.value_cm) < VALUE_CM_MIN || Number(form.value_cm) > VALUE_CM_MAX) {
    errors.value_cm = `O valor precisa estar entre ${VALUE_CM_MIN} e ${VALUE_CM_MAX} cm.`;
  }
  if (form.notes.length > 255) errors.notes = 'A nota tem no máximo 255 caracteres.';
  return errors;
}

/** Converte o formulário validado no payload do `POST /body-measurements`. */
export function toMeasurementInput(form: MeasurementFormState): BodyMeasurementInput {
  return {
    local_date: form.local_date,
    site: form.site,
    value_cm: Math.round(Number(form.value_cm) * 10) / 10,
    protocol_ok: form.protocol_ok,
    notes: form.notes.trim() ? form.notes.trim() : null,
  };
}
