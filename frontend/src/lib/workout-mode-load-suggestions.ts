// Cargas das séries que NÃO são de trabalho no Modo Treino (aquecimento,
// série de ajuste, aplicações de técnica). M76, Lote 1b (C7).
//
// O DONO da regra é o backend: `api/app/domain/session_loads.py`. Este módulo
// não tem constante própria e segue esta ordem:
//
// 1. Quando a âncora é a carga de trabalho PRESCRITA (`load_anchor_kg` da
//    prescrição/briefing), lê as cargas que a API já mandou calculadas
//    (`warmup_loads_kg`, `feeler_loads_kg`, `application_loads_kg`).
// 2. Quando a âncora só existe DURANTE a sessão (série de trabalho real antes
//    de um drop, sugestão da IA, carga após trocar de equipamento), usa o
//    ESPELHO abaixo. Os fatores vêm de `motor-constants.generated.json`,
//    exportado pelo backend, e `session-loads.parity.json` (também exportado
//    por ele) prova no vitest que o espelho dá o mesmo resultado do dono. Ir ao
//    servidor a cada drop-set (descanso de 0 a 10 s, sinal ruim na academia)
//    não é viável. Ver `api/tests/unit/test_m76_lote1b_espelhos_fe.py`.
//
// Toda carga nova cai no passo REAL do equipamento (`load_increment_kg` da
// API; ausente → o default do backend). A repetição de carga (rest-pause e
// demais técnicas sem redução) não é arredondada, como no dono.
//
// São só sugestões de default EDITÁVEL do campo de carga.

import motorConstants from './motor-constants.generated.json';
import type { AdvancedTechnique } from './workout-plan-templates-types';

const FACTORS = motorConstants.session_loads;

/** Bloco de cargas que a API manda em `prescription`/`briefing.prescription`. */
export type SessionLoadPlan = {
  load_anchor_kg?: number | null;
  load_increment_kg?: number | null;
  warmup_loads_kg?: number[] | null;
  feeler_load_kg?: number | null;
  feeler_loads_kg?: number[] | null;
  application_loads_kg?: number[] | null;
};

export type SessionLoadContext = {
  /** Cargas já calculadas pela API (só vale enquanto o equipamento é o da ficha). */
  plan?: SessionLoadPlan | null;
  /** Passo do equipamento efetivo (`load_increment_kg` da API). */
  step?: number | null;
};

// Precisão das contas intermediárias. O backend calcula em `Decimal`; aqui a
// quantização tira o ruído binário (11,35 × 0,5 = 5,674999…) antes de decidir
// o meio-passo, senão 5,675 / 2,27 = 2,4999… arredondaria para baixo.
const PRODUCT_DECIMALS = 6;
const QUOTIENT_DECIMALS = 9;
const RESULT_DECIMALS = 4;

function quantize(value: number, decimals: number): number {
  return Number(value.toFixed(decimals));
}

/** Passo efetivo: `load_increment_kg` > 0, senão o default do backend. */
export function loadStep(step: number | null | undefined): number {
  return step != null && Number.isFinite(step) && step > 0 ? step : FACTORS.default_load_step_kg;
}

/** Múltiplo mais próximo do passo (meio arredonda para cima), como `session_loads.round_to_step`. */
export function roundToStep(value: number, step?: number | null): number {
  const passo = loadStep(step);
  const quotient = quantize(quantize(value, PRODUCT_DECIMALS) / passo, QUOTIENT_DECIMALS);
  const steps = Math.sign(quotient) * Math.floor(Math.abs(quotient) + 0.5);
  return quantize(steps * passo, RESULT_DECIMALS);
}

// Mesma âncora da API (tolerância abaixo da precisão de `DECIMAL(8,2)`).
function planFor(context: SessionLoadContext | undefined, anchorKg: number): SessionLoadPlan | null {
  const plan = context?.plan;
  if (plan?.load_anchor_kg == null) return null;
  return Math.abs(Number(plan.load_anchor_kg) - anchorKg) < 0.005 ? plan : null;
}

// ---------------------------------------------------------------------------
// Espelho de `session_loads.py` (paridade: `session-loads.parity.json`)
// ---------------------------------------------------------------------------

export function mirrorWarmupLoads(anchorKg: number, step?: number | null): number[] {
  return FACTORS.warmup_load_factors.map((factor) => roundToStep(anchorKg * factor, step));
}

export function mirrorFeelerLoad(anchorKg: number, index: number, step?: number | null): number {
  const factor = FACTORS.feeler_first_factor + FACTORS.feeler_step_factor * (index - 1);
  return roundToStep(anchorKg * factor, step);
}

export function mirrorTechniqueApplicationLoad(
  technique: AdvancedTechnique | string,
  anchorKg: number,
  applicationIndex: number,
  step?: number | null,
): number {
  if (technique === 'drop_set') {
    return roundToStep(anchorKg * FACTORS.drop_set_load_factor ** applicationIndex, step);
  }
  return anchorKg;
}

// ---------------------------------------------------------------------------
// Ajuste rápido de carga do Modo Intensivo (espelho de
// `session_loads.quick_load_adjust`; paridade: `quick_load_adjust_cases` de
// `session-loads.parity.json`). Ações, fatores e origens vêm do bloco
// `session_loads` de `motor-constants.generated.json`: nenhum número aqui.
//
// Unidade e convenção são as EXIBIDAS: `current` e `step` chegam na mesma
// unidade (kg, ou lb convertido por `weight-units.ts`) em que o stepper
// mostra a carga. Quem chama converte o resultado de volta para kg.
// ---------------------------------------------------------------------------

export type QuickAdjustAction = 'minus_step' | 'minus_5pct' | 'minus_10pct';
export type LoadAdjustSource = 'protocol' | 'fatigue' | 'manual';

export const QUICK_ADJUST_ACTIONS = FACTORS.quick_adjust_actions as readonly QuickAdjustAction[];
export const LOAD_ADJUST_SOURCES = FACTORS.load_adjust_sources as readonly LoadAdjustSource[];
const QUICK_ADJUST_FACTORS = FACTORS.quick_adjust_factors as Readonly<Record<string, number>>;

/** Fator da ação percentual; null na ação de um degrau. */
export function quickAdjustFactor(action: QuickAdjustAction): number | null {
  return QUICK_ADJUST_FACTORS[action] ?? null;
}

export function isQuickAdjustAction(value: unknown): value is QuickAdjustAction {
  return typeof value === 'string' && (QUICK_ADJUST_ACTIONS as readonly string[]).includes(value);
}

export function isLoadAdjustSource(value: unknown): value is LoadAdjustSource {
  return typeof value === 'string' && (LOAD_ADJUST_SOURCES as readonly string[]).includes(value);
}

/**
 * Carga planejada depois do ajuste `action`:
 * - ação sem fator (o degrau): um degrau abaixo na grade do passo (carga fora
 *   da grade cai no degrau imediatamente inferior);
 * - ação com fator: `current × fator` arredondado PARA BAIXO ao passo;
 * - piso 0. Ação desconhecida lança erro, como o dono.
 */
export function mirrorQuickLoadAdjust(current: number, step: number | null | undefined, action: QuickAdjustAction): number {
  if (!isQuickAdjustAction(action)) throw new Error(`acao de ajuste desconhecida: ${String(action)}`);
  const passo = loadStep(step);
  const atual = Math.max(Number.isFinite(current) ? current : 0, 0);
  const factor = QUICK_ADJUST_FACTORS[action];
  let steps: number;
  if (factor == null) {
    const quotient = quantize(quantize(atual, PRODUCT_DECIMALS) / passo, QUOTIENT_DECIMALS);
    const floor = Math.floor(quotient);
    steps = floor === quotient ? floor - 1 : floor;
  } else {
    steps = Math.floor(quantize(quantize(atual * factor, PRODUCT_DECIMALS) / passo, QUOTIENT_DECIMALS));
  }
  return Math.max(quantize(steps * passo, RESULT_DECIMALS), 0);
}

// ---------------------------------------------------------------------------
// O que a tela usa: API primeiro, espelho para âncora dinâmica
// ---------------------------------------------------------------------------

/** Aquecimento genérico (2 séries antes do 1º exercício de um grupo ainda não ativado). */
export function warmupLoads(anchorKg: number, context?: SessionLoadContext): number[] {
  const fromApi = planFor(context, anchorKg)?.warmup_loads_kg;
  if (fromApi && fromApi.length > 0) return fromApi;
  return mirrorWarmupLoads(anchorKg, context?.step);
}

/** Aquecimento configurado do modo avançado: a 1ª carga em todas as séries. */
export function warmupLoad(anchorKg: number, context?: SessionLoadContext): number {
  return warmupLoads(anchorKg, context)[0];
}

/** Série de ajuste `index` (1-based). */
export function feelerSetLoad(anchorKg: number, index: number, context?: SessionLoadContext): number {
  const fromApi = planFor(context, anchorKg)?.feeler_loads_kg?.[index - 1];
  if (fromApi != null) return fromApi;
  return mirrorFeelerLoad(anchorKg, index, context?.step);
}

/**
 * Aplicação `applicationIndex` (1-based) da técnica, a partir da série de
 * trabalho que serve de âncora (a REAL, quando já foi lançada).
 */
export function techniqueApplicationLoad(
  technique: AdvancedTechnique | string,
  anchorKg: number,
  applicationIndex: number,
  context?: SessionLoadContext,
): number {
  const fromApi = planFor(context, anchorKg)?.application_loads_kg?.[applicationIndex - 1];
  if (fromApi != null) return fromApi;
  return mirrorTechniqueApplicationLoad(technique, anchorKg, applicationIndex, context?.step);
}
