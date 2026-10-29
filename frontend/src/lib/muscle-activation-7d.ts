import type {
  MethodologyRealizedItem,
  MethodologyRealizedRange,
  MethodologyRealizedStatus,
  MuscleRecoveryConsecutiveCheckItem,
} from '@/lib/analytics-types';
import type { MethodologyFrequencyBucket } from '@/lib/methodology-volume-types';
import { FREQUENCY_BUCKET_LABEL, formatDecimal } from '@/lib/volume-classes';

/**
 * Apresentação da tabela "Ativação muscular (últimos 7 dias)" de
 * `/dashboard/musculos`, comparada com as metas dos MACRO-GRUPOS de volume.
 *
 * M76 E6: a metodologia NÃO é reavaliada aqui. `GET /analytics/methodology-realized`
 * (`intelligence/methodology_realized.py`, regras do motor `methodology_volume`)
 * devolve, por BLOCO (fatia -> bloco pela cadeia inteira): séries diretas por
 * dia, frequência, faixa por treino, meta semanal e os status (do dia, da
 * MÉDIA por treino e da semana). Este módulo só traduz os códigos para o
 * desenho e monta os textos.
 *
 * Unidade: séries DIRETAS (só contribuições PRIMARY, sem `stimulus_weight`) —
 * a unidade das faixas dos macro-grupos. A ponderada aparece só no tooltip.
 */

export const ACTIVATION_WINDOW_DAYS = 7;

export type RangeStatus = 'BELOW' | 'WITHIN' | 'ABOVE' | 'NO_TARGET';

export type SetsRange = MethodologyRealizedRange;

// Constrói `YYYY-MM-DD` em horário LOCAL (nunca `toISOString`, que converte
// para UTC e pode deslocar o dia perto da meia-noite em America/Sao_Paulo).
export function toLocalIsoDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

/**
 * Os 7 dias corridos terminando hoje, do mais antigo para o mais recente — só
 * para o cabeçalho enquanto a resposta (que traz `days`) não chega.
 */
export function buildLast7Days(today: Date = new Date()): string[] {
  const days: string[] = [];
  for (let i = ACTIVATION_WINDOW_DAYS - 1; i >= 0; i -= 1) {
    const d = new Date(today);
    d.setDate(d.getDate() - i);
    days.push(toLocalIsoDate(d));
  }
  return days;
}

export function formatDayMonth(isoDate: string): string {
  const parsed = new Date(`${isoDate.slice(0, 10)}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return isoDate;
  return parsed.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' });
}

/** Código do motor -> desenho da célula. Sem faixa (NOT_EVALUABLE) = sem cor. */
const RANGE_STATUS: Record<MethodologyRealizedStatus, RangeStatus> = {
  WITHIN_RANGE: 'WITHIN',
  BELOW_RANGE: 'BELOW',
  ABOVE_RANGE: 'ABOVE',
  NOT_EVALUABLE: 'NO_TARGET',
  NOT_DIRECTLY_TRAINED: 'NO_TARGET',
};

export function toRangeStatus(status: MethodologyRealizedStatus | null | undefined): RangeStatus {
  return status ? (RANGE_STATUS[status] ?? 'NO_TARGET') : 'NO_TARGET';
}

export function formatRange(range: SetsRange): string {
  return range.min === range.max ? formatDecimal(range.min) : `${formatDecimal(range.min)}–${formatDecimal(range.max)}`;
}

// ---------------------------------------------------------------------------
// Grade
// ---------------------------------------------------------------------------

export type ActivationDayCell = {
  date: string;
  directSets: number;
  /** Série equivalente ponderada (todos os papéis) — só informativa. */
  effectiveSets: number;
  /** `null` = dia sem séries diretas (célula neutra). */
  status: RangeStatus | null;
};

export type ActivationRow = {
  /** Nome do bloco (grupo pai, quando a série veio de uma fatia). */
  group: string;
  groupId: number | null;
  /** Bloco e fatias que somaram na linha (casa os pares de recuperação). */
  memberGroupIds: number[];
  memberGroupNames: string[];
  volumeClassName: string | null;
  days: ActivationDayCell[];
  /** Dias da janela com séries diretas > 0. */
  daysTrained: number;
  bucket: MethodologyFrequencyBucket | null;
  sessionRange: SetsRange | null;
  /** Média de séries diretas por treino na janela e o status dela (a leitura da metodologia). */
  perSession: number | null;
  sessionStatus: RangeStatus;
  weeklyTotal: number;
  weeklyRange: SetsRange | null;
  weeklyStatus: RangeStatus;
};

/** Resposta do backend -> linhas da tabela (ordem alfabética, pt-BR). */
export function toActivationRows(items: MethodologyRealizedItem[]): ActivationRow[] {
  return items
    .map((item) => ({
      group: item.muscle_group,
      groupId: item.muscle_group_id,
      memberGroupIds: item.member_groups.flatMap((m) => (m.muscle_group_id != null ? [m.muscle_group_id] : [])),
      memberGroupNames: item.member_groups.map((m) => m.muscle_group),
      volumeClassName: item.volume_class_name,
      days: item.days.map((day) => ({
        date: day.date,
        directSets: day.direct_sets,
        effectiveSets: day.effective_sets,
        status: day.status ? toRangeStatus(day.status) : null,
      })),
      daysTrained: item.days_trained,
      bucket: item.frequency_bucket,
      sessionRange: item.session_range,
      perSession: item.direct_sets_per_session,
      sessionStatus: toRangeStatus(item.session_status),
      weeklyTotal: item.weekly_total,
      weeklyRange: item.weekly_range,
      weeklyStatus: toRangeStatus(item.weekly_status),
    }))
    .sort((a, b) => a.group.localeCompare(b.group, 'pt-BR'));
}

// ---------------------------------------------------------------------------
// Recuperação entre dias consecutivos (cruzamento que a tabela de /treinos fazia)
// ---------------------------------------------------------------------------

export type ConsecutivePair = {
  item: MuscleRecoveryConsecutiveCheckItem;
  fromIndex: number;
  toIndex: number;
};

/**
 * Pares dentro da janela visível, agrupados pela LINHA (bloco) em que o grupo
 * do par somou — o agrupamento fatia -> bloco veio do backend
 * (`member_groups`); casa por id e, sem id, pelo nome.
 */
export function consecutivePairsByBlock(
  items: MuscleRecoveryConsecutiveCheckItem[],
  days: string[],
  rows: ActivationRow[],
): Map<string, ConsecutivePair[]> {
  const blockById = new Map<number, string>();
  const blockByName = new Map<string, string>();
  for (const row of rows) {
    for (const id of row.memberGroupIds) blockById.set(id, row.group);
    if (row.groupId != null) blockById.set(row.groupId, row.group);
    for (const name of [row.group, ...row.memberGroupNames]) {
      blockByName.set(name.trim().toLocaleLowerCase('pt-BR'), row.group);
    }
  }
  const map = new Map<string, ConsecutivePair[]>();
  for (const item of items) {
    const fromIndex = days.indexOf(item.date_from);
    const toIndex = days.indexOf(item.date_to);
    if (fromIndex === -1 || toIndex === -1) continue;
    const key =
      (item.muscle_group_id != null ? blockById.get(item.muscle_group_id) : undefined) ??
      blockByName.get(item.muscle_group.trim().toLocaleLowerCase('pt-BR')) ??
      item.muscle_group;
    const list = map.get(key) ?? [];
    list.push({ item, fromIndex, toIndex });
    map.set(key, list);
  }
  return map;
}

/** Primeiro par com recuperação INSUFICIENTE que toca cada dia. */
export function insufficientPairByDay(pairs: ConsecutivePair[]): Map<number, ConsecutivePair> {
  const map = new Map<number, ConsecutivePair>();
  for (const pair of pairs) {
    if (!pair.item.insufficient_recovery) continue;
    if (!map.has(pair.fromIndex)) map.set(pair.fromIndex, pair);
    if (!map.has(pair.toIndex)) map.set(pair.toIndex, pair);
  }
  return map;
}

export function recoveryLabel(pair: ConsecutivePair): string {
  const elapsed = Math.round(pair.item.elapsed_hours);
  const required = Math.round(pair.item.required_hours);
  return `${formatDayMonth(pair.item.date_from)} → ${formatDayMonth(pair.item.date_to)}: ~${elapsed}h de intervalo, recomendado ~${required}h — descanso insuficiente`;
}

// ---------------------------------------------------------------------------
// Textos
// ---------------------------------------------------------------------------

export function frequencyLabel(bucket: MethodologyFrequencyBucket | null): string {
  return bucket ? FREQUENCY_BUCKET_LABEL[bucket].replace('/semana', '') : '0x';
}

/** "4 séries · meta 3–5 (freq 2x)" ou "4 séries · sem meta". */
export function dayTooltip(cell: ActivationDayCell, row: ActivationRow): string {
  const sets = `${formatDecimal(cell.directSets)} ${cell.directSets === 1 ? 'série direta' : 'séries diretas'}`;
  const effective = `${formatDecimal(cell.effectiveSets)} equivalentes (com indiretas)`;
  if (cell.directSets <= 0) {
    return cell.effectiveSets > 0 ? `Só trabalho indireto · ${effective}` : 'Sem séries neste dia';
  }
  if (!row.sessionRange) {
    const reason = row.volumeClassName ? `sem faixa para ${frequencyLabel(row.bucket)}` : 'sem macro-grupo';
    return `${sets} · sem meta (${reason}) · ${effective}`;
  }
  return `${sets} · meta ${formatRange(row.sessionRange)} (freq ${frequencyLabel(row.bucket)}) · ${effective}`;
}

export const WEEKLY_STATUS_LABEL: Record<RangeStatus, string> = {
  WITHIN: 'Estímulo suficiente',
  BELOW: 'Estímulo insuficiente',
  ABOVE: 'Acima da meta',
  NO_TARGET: 'Sem meta',
};

export const SESSION_STATUS_LABEL: Record<RangeStatus, string> = {
  WITHIN: 'média por treino na faixa',
  BELOW: 'média por treino abaixo da faixa',
  ABOVE: 'média por treino acima da faixa',
  NO_TARGET: 'média por treino sem faixa',
};

/** "média 4 por treino" (vazio sem treino na janela). */
export function perSessionLabel(row: ActivationRow): string {
  return row.perSession == null ? '' : `média ${formatDecimal(row.perSession)}/treino`;
}

/** Tooltip da média: "Média 4 séries diretas por treino · meta 3–5 (freq 3x+) — na faixa". */
export function sessionTooltip(row: ActivationRow): string {
  if (row.perSession == null) return 'Sem treino direto na janela';
  const base = `Média ${formatDecimal(row.perSession)} séries diretas por treino`;
  if (!row.sessionRange) return `${base} · sem meta`;
  return `${base} · meta ${formatRange(row.sessionRange)} (freq ${frequencyLabel(row.bucket)}) — ${SESSION_STATUS_LABEL[row.sessionStatus]}`;
}

export function weeklyTooltip(row: ActivationRow): string {
  const total = `${formatDecimal(row.weeklyTotal)} séries diretas em 7 dias`;
  if (!row.weeklyRange) {
    return `${total} · sem meta (${row.volumeClassName ? 'macro-grupo sem meta semanal' : 'sem macro-grupo'})`;
  }
  return `${total} · meta semanal ${formatRange(row.weeklyRange)} (${row.volumeClassName})`;
}
