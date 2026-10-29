// Lógica PURA da tela de briefing do exercício (M4). Sem React, sem fetch:
// é o que o vitest (`environment: node`) consegue testar de verdade, e é onde
// mora toda a decisão de apresentação da tela.

import { statusVisual } from './dashboard-status';
import { progressionDecisionLabel, statusReasonsText } from './exercise-progress';
import {
  DEFAULT_WEIGHT_UNIT,
  WEIGHT_UNIT_LABEL,
  fromKg,
  normalizeWeightUnit,
  toKg,
  type WeightUnit,
} from './weight-units';
import type {
  BriefingAdjustment,
  BriefingLastExecution,
  BriefingMuscle,
  BriefingPerformance,
  BriefingPrescription,
} from './exercise-briefing-types';
import type { StrengthLoadRange } from './workout-plans-types';

/**
 * Formatação da CARGA esperada, isolada de propósito num helper só seu.
 *
 * A carga sugerida pelo motor tem precedência sobre o texto livre da ficha
 * (`load_target` pode ser "40kg", "peso corporal" ou vazio), e texto livre é
 * devolvido como está, sem inventar unidade.
 *
 * Unidade (M3, novo visual do Modo Treino): o backend fala SEMPRE kg; a
 * conversão para a preferência do usuário passa por `weight-units.ts`
 * (`fromKg`/`toKg`), a borda única de conversão. Em kg (padrão) a saída é
 * idêntica à de antes; em lb converte a sugestão e o `load_target` que for
 * UM número (com "kg"/"lb" opcional). Faixa ou texto livre volta como veio.
 */
export function formatExpectedLoad(
  prescription: Pick<BriefingPrescription, 'suggested_load_kg' | 'load_target'>,
  unit: WeightUnit = DEFAULT_WEIGHT_UNIT,
): string {
  const target = normalizeWeightUnit(unit);
  const suggested = prescription.suggested_load_kg;
  if (typeof suggested === 'number' && Number.isFinite(suggested) && suggested > 0) {
    return formatLoadIn(suggested, target);
  }
  const text = (prescription.load_target || '').trim();
  if (!text) return '—';
  if (target === 'kg') {
    // "40" sozinho é um número sem unidade: só aí o helper acrescenta "kg".
    return /^\d+([.,]\d+)?$/.test(text) ? `${text.replace(',', '.')} kg` : text;
  }
  const match = text.replace(',', '.').match(/^(\d+(?:\.\d+)?)\s*(kg|lb)?$/i);
  if (!match) return text;
  return formatLoadIn(toKg(Number(match[1]), normalizeWeightUnit(match[2])), target);
}

/**
 * Faixa de carga de FORÇA por %1RM (fix M97): "100–102,5 kg (80–85% 1RM)".
 * Os números (cargas e percentuais) vêm prontos do backend
 * (`progression_engine.e1rm_load_range`); aqui só a formatação e a unidade.
 * Sem faixa (hipertrofia, força sem e1RM) -> null.
 */
export function formatStrengthLoadRange(
  range: StrengthLoadRange | null | undefined,
  unit: WeightUnit = DEFAULT_WEIGHT_UNIT,
): string | null {
  if (!range) return null;
  const { load_min_kg: low, load_max_kg: high, pct_min: pctMin, pct_max: pctMax } = range;
  if (![low, high, pctMin, pctMax].every((value) => typeof value === 'number' && Number.isFinite(value))) return null;
  const target = normalizeWeightUnit(unit);
  const loads =
    low === high
      ? formatLoadIn(low, target)
      : `${formatKg(fromKg(low, target))}–${formatLoadIn(high, target)}`;
  return `${loads} (${formatKg(pctMin)}–${formatKg(pctMax)}% 1RM)`;
}

/** Carga em kg (backend) -> "42,5 kg" / "93,7 lb" na unidade preferida. */
export function formatLoadIn(weightKg: number, unit: WeightUnit = DEFAULT_WEIGHT_UNIT): string {
  const target = normalizeWeightUnit(unit);
  return `${formatKg(fromKg(weightKg, target))} ${WEIGHT_UNIT_LABEL[target]}`;
}

export function formatKg(value: number): string {
  // 42.5 -> "42,5"; 40.0 -> "40". Vírgula decimal (pt-BR) sem arrastar zeros.
  return Number(value.toFixed(2)).toString().replace('.', ',');
}

/** "3 x 10-12" — séries e repetições como a ficha pede. */
export function formatSetsAndReps(prescription: Pick<BriefingPrescription, 'sets_count' | 'reps_target'>): string {
  const sets = prescription.sets_count;
  const reps = (prescription.reps_target || '').trim();
  if (!sets && !reps) return '—';
  if (!sets) return `${reps} reps`;
  if (!reps) return `${sets} série${sets === 1 ? '' : 's'}`;
  return `${sets} x ${reps}`;
}

/**
 * RPE esperado. Mostra o alvo EFETIVO (depois do overlay de segurança) e, se
 * houver teto, o teto — é o número que o usuário deve perseguir hoje, não o
 * que foi congelado semanas atrás.
 */
export function formatExpectedRpe(prescription: BriefingPrescription): string {
  const target = prescription.effective_rpe_target ?? toNumber(prescription.rpe_target);
  if (target == null) return '—';
  const ceiling = prescription.effective_rpe_ceiling;
  return ceiling != null && ceiling !== target ? `RPE ${target} (teto ${ceiling})` : `RPE ${target}`;
}

function toNumber(value: string | number | null | undefined): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  const match = String(value ?? '').match(/\d+([.,]\d+)?/);
  return match ? Number(match[0].replace(',', '.')) : null;
}

// `hex`: a mesma cor do registro do dashboard (`statusVisual().hex`), para o
// chip do novo visual; ausente em "Sem comparação" (cinza neutro).
export type PerformanceBadge = { label: string; color: string; hex?: string; description?: string };

export const NO_COMPARISON_LABEL = 'Sem comparação';

/**
 * Badge de DESEMPENHO do briefing: mesmo rótulo e cor do dashboard
 * (`dashboard-status.ts`, domínio `exerciseProgression`). Sem duas execuções
 * comparáveis — ou resposta antiga sem `performance` — "Sem comparação" em
 * cinza: nunca um veredito inventado.
 */
export function performanceBadge(performance: BriefingPerformance | null | undefined): PerformanceBadge {
  if (!performance || !performance.comparable) {
    return { label: NO_COMPARISON_LABEL, color: 'gray' };
  }
  const visual = statusVisual('exerciseProgression', performance.status);
  return { label: visual.label, color: visual.color, hex: visual.hex, description: visual.description };
}

/**
 * Frase do "Como estamos": por que o badge é o que é (razão principal de
 * `comparability`) e onde a comparação foi feita.
 */
export function performanceExplanation(performance: BriefingPerformance | null | undefined): string {
  if (!performance || !performance.comparable) {
    return 'Ainda não há duas execuções comparáveis (mesmo equipamento e convenção de carga) para avaliar o desempenho.';
  }
  const onde =
    performance.scope === 'equipment'
      ? `Última execução comparada com a anterior neste equipamento${performance.equipment_name ? ` (${performance.equipment_name})` : ''}.`
      : 'Última execução comparada com a anterior comparável deste exercício.';
  const motivo = statusReasonsText(performance.reasons).find((item) => item.known)?.text;
  return motivo ? `${onde} ${motivo}` : onde;
}

/** "Ajuste de hoje: Aumentar carga" — a decisão do motor, separada do desempenho. */
export function adjustmentText(adjustment: BriefingAdjustment | null | undefined): string | null {
  const label = adjustmentLabel(adjustment);
  return label ? `Ajuste de hoje: ${label}` : null;
}

/**
 * Só a ação ("Manter", "Aumentar carga"): o valor da faixa "Ajuste de hoje"
 * do novo briefing. Rótulo de `progressionDecisionLabel` (HOLD -> "Manter");
 * decisão desconhecida usa o rótulo do backend. Sem decisão, `null`.
 */
export function adjustmentLabel(adjustment: BriefingAdjustment | null | undefined): string | null {
  if (!adjustment?.decision) return null;
  const label = progressionDecisionLabel(adjustment.decision);
  return label === adjustment.decision ? adjustment.label || label : label;
}

export const MUSCLE_ROLE_LABELS: Record<string, string> = {
  PRIMARY: 'Primário',
  SECONDARY: 'Secundário',
  STABILIZER: 'Estabilizador',
};

/** Músculos agrupados por papel, na ordem primário → secundário → estabilizador. */
export function groupMusclesByRole(muscles: BriefingMuscle[]): { role: string; label: string; names: string[] }[] {
  const order = ['PRIMARY', 'SECONDARY', 'STABILIZER'];
  return order
    .map((role) => ({
      role,
      label: MUSCLE_ROLE_LABELS[role] || role,
      names: muscles.filter((item) => item.role === role).map((item) => item.name),
    }))
    .filter((group) => group.names.length > 0);
}

/**
 * A descrição de execução é gravada como texto simples (uma frase por linha,
 * com rótulos "Preparação:" / "Execução:" / "Atenção:"). Aqui ela vira blocos
 * para a tela, sem nenhum parser de markdown.
 */
export function parseExecutionDescription(text: string | null | undefined): { title: string | null; steps: string[] }[] {
  const lines = (text || '').split('\n').map((line) => line.trim()).filter(Boolean);
  const blocks: { title: string | null; steps: string[] }[] = [];
  for (const line of lines) {
    if (line.startsWith('-')) {
      const step = line.replace(/^-\s*/, '').trim();
      if (!step) continue;
      if (!blocks.length) blocks.push({ title: null, steps: [] });
      blocks[blocks.length - 1].steps.push(step);
    } else {
      blocks.push({ title: line.replace(/:$/, ''), steps: [] });
    }
  }
  return blocks.filter((block) => block.steps.length > 0 || block.title);
}

/** "3 séries · 40 kg · 10-12 reps · RPE 9" da ÚLTIMA execução. */
export function summarizeLastExecution(
  last: BriefingLastExecution | null | undefined,
  unit: WeightUnit = DEFAULT_WEIGHT_UNIT,
): string | null {
  if (!last) return null;
  const parts: string[] = [`${last.working_sets} série${last.working_sets === 1 ? '' : 's'}`];
  if (last.top_weight_kg != null) parts.push(formatLoadIn(last.top_weight_kg, unit));
  if (last.reps_min != null && last.reps_max != null) {
    parts.push(last.reps_min === last.reps_max ? `${last.reps_max} reps` : `${last.reps_min}-${last.reps_max} reps`);
  }
  if (last.top_rpe != null) parts.push(`RPE ${last.top_rpe}`);
  return parts.join(' · ');
}

/** "08/09/2026" a partir do `local_date` (YYYY-MM-DD) do backend. */
export function formatLocalDate(value: string | null | undefined): string | null {
  const match = String(value ?? '').match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${match[3]}/${match[2]}/${match[1]}` : null;
}

/** Uma série da última execução, como linha: "2ª: 10 x 40 kg · RPE 9". */
export function formatSetLine(
  set: BriefingLastExecution['sets'][number],
  unit: WeightUnit = DEFAULT_WEIGHT_UNIT,
): string {
  const reps = set.reps ?? '—';
  const weight = set.weight_kg != null ? formatLoadIn(set.weight_kg, unit) : '—';
  const rpe = set.rpe != null ? ` · RPE ${set.rpe}` : '';
  const tag = set.is_working ? '' : ` [${set.set_type}]`;
  return `${set.set_number}ª${tag}: ${reps} x ${weight}${rpe}`;
}

// ---------------------------------------------------------------------------
// Novo visual do briefing (Modo Treino PR1/3): acordeões e resumos
// ---------------------------------------------------------------------------

export type BriefingSectionKey = 'preparation' | 'execution' | 'attention' | 'other';

export type BriefingSection = {
  key: BriefingSectionKey;
  title: string;
  /** Primeiro enunciado do PRÓPRIO texto, visível com a seção fechada. */
  summary: string;
  /** Todos os passos, íntegros, na ordem do catálogo. */
  steps: string[];
};

const SECTION_TITLES: Record<Exclude<BriefingSectionKey, 'other'>, string> = {
  preparation: 'Preparação',
  execution: 'Execução',
  attention: 'Atenção',
};

function normalizeTitle(title: string): string {
  return title
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .trim();
}

function sectionKeyOf(title: string | null): BriefingSectionKey {
  if (!title) return 'execution';
  const normalized = normalizeTitle(title);
  if (normalized.startsWith('prepara')) return 'preparation';
  if (normalized.startsWith('execu') || normalized.startsWith('como executar')) return 'execution';
  if (normalized.startsWith('atenc') || normalized.startsWith('cuidado') || normalized.startsWith('erro')) {
    return 'attention';
  }
  return 'other';
}

/**
 * Primeiro enunciado de um passo: corta na primeira frase terminada em
 * ".", "!" ou "?" seguida de espaço. Não reescreve nada: é um prefixo exato
 * do texto do catálogo (o texto íntegro continua na seção aberta).
 */
export function firstStatement(text: string | null | undefined): string {
  const value = (text || '').trim();
  const match = value.match(/^(.+?[.!?])\s+\S/);
  return match ? match[1] : value;
}

/**
 * Blocos de `parseExecutionDescription` -> seções do acordeão, na ordem
 * Preparação, Execução, Atenção e depois qualquer bloco com outro título (na
 * ordem em que veio). Passos sem título vão para "Execução" (é o que a tela
 * antiga mostrava sob "Como executar"). Blocos com o mesmo papel se juntam.
 * Seção sem passo nenhum não aparece.
 */
export function buildBriefingSections(blocks: { title: string | null; steps: string[] }[]): BriefingSection[] {
  const fixed = new Map<Exclude<BriefingSectionKey, 'other'>, string[]>();
  const others: { title: string; steps: string[] }[] = [];
  for (const block of blocks) {
    const steps = block.steps.map((step) => step.trim()).filter(Boolean);
    if (!steps.length) continue;
    const key = sectionKeyOf(block.title);
    if (key === 'other') {
      others.push({ title: block.title || 'Orientações', steps });
    } else {
      fixed.set(key, [...(fixed.get(key) || []), ...steps]);
    }
  }
  const ordered: BriefingSection[] = (['preparation', 'execution', 'attention'] as const)
    .filter((key) => fixed.has(key))
    .map((key) => {
      const steps = fixed.get(key) as string[];
      return { key, title: SECTION_TITLES[key], summary: firstStatement(steps[0]), steps };
    });
  return [
    ...ordered,
    ...others.map((item) => ({
      key: 'other' as const,
      title: item.title,
      summary: firstStatement(item.steps[0]),
      steps: item.steps,
    })),
  ];
}

/** "Primário: Peito · Secundário: Tríceps" — resumo da seção de músculos. */
export function musclesSummary(groups: { label: string; names: string[] }[]): string {
  return groups.map((group) => `${group.label}: ${group.names.join(', ')}`).join(' · ');
}
