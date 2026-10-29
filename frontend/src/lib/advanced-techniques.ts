// Técnicas avançadas do modo "Equipamentos e séries".
//
// O CATÁLOGO (códigos válidos, nome, `set_type`, exigência de falha etc.) vem
// de `GET /advanced-techniques` (tabela `advanced_techniques` — PR-06 da
// refatoração dos motores). Aqui fica só:
//
// - o mapeamento de UI que não pertence ao catálogo: descrição curta exibida
//   na execução e o substantivo do contador de aplicações;
// - `FALLBACK_ADVANCED_TECHNIQUES`: cópia mínima do seed, usada APENAS
//   enquanto a chamada não respondeu ou se ela falhar — o Modo Treino não
//   pode depender de rede para saber o `set_type` de uma série que está
//   sendo registrada.
//
// Os helpers são síncronos e leem o catálogo corrente do módulo (fallback até
// `loadAdvancedTechniques` responder). Componentes usam o hook
// `useAdvancedTechniques` (`use-advanced-techniques.ts`) para disparar a
// carga e re-renderizar quando ela chega.

import { SETS_ONLY_CODES } from './technique-accounting';

export type AdvancedTechniqueCode = string;
export type { AdvancedTechniqueCode as AdvancedTechniqueValue };

export type AdvancedTechniqueFailureRequirement =
  | 'failure_not_required'
  | 'may_reach_failure'
  | 'requires_failure'
  | 'beyond_failure';

// Item de `GET /advanced-techniques` (api/app/advanced_techniques.py).
export interface AdvancedTechniqueCatalogItem {
  code: AdvancedTechniqueCode;
  name: string;
  failure_requirement: AdvancedTechniqueFailureRequirement;
  requires_partner: boolean;
  auto_applicable: boolean;
  min_experience: 'beginner' | 'intermediate' | 'advanced';
  set_type: string;
  activation_set_equivalent: number;
  recovery_cost_factor: number | null;
  // Migração 060 (Fase 5). Opcionais: resposta de uma API anterior à 060.
  // NULL = não calibrado (a regra usa teto de 1 série).
  max_equivalent_per_anchor?: number | null;
  default_sets_count?: number | null;
  is_active: boolean;
}

export const ADVANCED_TECHNIQUES_PATH = '/advanced-techniques?include_inactive=true';
export const ADVANCED_TECHNIQUES_QUERY_KEY = ['advanced-techniques'] as const;

// set_type das técnicas "além da falha": lembrete no card da série de
// trabalho, sem sub-etapa `technique` própria (ver PlanExecutionScreen.tsx).
const EXTRA_REPS_SET_TYPE = 'forced_rep';

function fallbackItem(
  code: string,
  name: string,
  failure_requirement: AdvancedTechniqueFailureRequirement,
  set_type: string,
  extra: Partial<AdvancedTechniqueCatalogItem> = {},
): AdvancedTechniqueCatalogItem {
  return {
    code,
    name,
    failure_requirement,
    requires_partner: false,
    auto_applicable: false,
    min_experience: 'advanced',
    set_type,
    activation_set_equivalent: 0,
    recovery_cost_factor: null,
    is_active: true,
    ...extra,
  };
}

// Coeficientes calibrados pela migração 060 (activation_set_equivalent,
// max_equivalent_per_anchor, recovery_cost_factor, default_sets_count): sem
// eles, o feedback "= 0,5 série equivalente" do Modo Treino ficaria zerado
// enquanto a API não responde.
function coefficients(
  activation: number,
  cap: number,
  recovery: number,
  defaultSets: number | null = null,
): Partial<AdvancedTechniqueCatalogItem> {
  return {
    activation_set_equivalent: activation,
    max_equivalent_per_anchor: cap,
    recovery_cost_factor: recovery,
    default_sets_count: defaultSets,
  };
}

// Mesmo conteúdo do seed da migração 031 + coeficientes da 060 (api/db/migrations).
export const FALLBACK_ADVANCED_TECHNIQUES: readonly AdvancedTechniqueCatalogItem[] = [
  fallbackItem('rest_pause', 'Rest-pause', 'requires_failure', 'rest_pause', {
    auto_applicable: true,
    min_experience: 'intermediate',
    ...coefficients(0.4, 1.0, 1.1),
  }),
  fallbackItem('drop_set', 'Drop-set', 'requires_failure', 'drop_set', {
    auto_applicable: true,
    min_experience: 'intermediate',
    ...coefficients(0.5, 1.0, 1.15),
  }),
  fallbackItem('fst7', 'FST-7', 'requires_failure', 'working', coefficients(0, 0, 1.0, 7)),
  fallbackItem('gvt', 'GVT (German Volume Training)', 'failure_not_required', 'working', {
    min_experience: 'intermediate',
    ...coefficients(0, 0, 1.0, 10),
  }),
  fallbackItem('forced_reps', 'Repetições forçadas', 'beyond_failure', EXTRA_REPS_SET_TYPE, {
    requires_partner: true,
    ...coefficients(0.25, 0.5, 1.2),
  }),
  fallbackItem('negative_reps', 'Negativas', 'beyond_failure', EXTRA_REPS_SET_TYPE, {
    requires_partner: true,
    ...coefficients(0.25, 0.5, 1.25),
  }),
  fallbackItem('partial_reps', 'Parciais', 'beyond_failure', EXTRA_REPS_SET_TYPE, coefficients(0.15, 0.5, 1.05)),
  fallbackItem('cheat_reps', 'Cheat reps', 'beyond_failure', EXTRA_REPS_SET_TYPE, coefficients(0.15, 0.5, 1.1)),
];

// Ordem de exibição no Select do builder (a API ordena por código). Códigos
// novos do catálogo entram depois, em ordem alfabética de nome.
const DISPLAY_ORDER = FALLBACK_ADVANCED_TECHNIQUES.map((item) => item.code);

let currentCatalog: readonly AdvancedTechniqueCatalogItem[] = FALLBACK_ADVANCED_TECHNIQUES;

export function getAdvancedTechniqueCatalog(): readonly AdvancedTechniqueCatalogItem[] {
  return currentCatalog;
}

export function sortAdvancedTechniques(items: readonly AdvancedTechniqueCatalogItem[]): AdvancedTechniqueCatalogItem[] {
  const rank = (code: string) => {
    const index = DISPLAY_ORDER.indexOf(code);
    return index === -1 ? DISPLAY_ORDER.length : index;
  };
  return [...items].sort((a, b) => rank(a.code) - rank(b.code) || a.name.localeCompare(b.name, 'pt-BR'));
}

function isCatalogItem(value: unknown): value is AdvancedTechniqueCatalogItem {
  const item = value as Partial<AdvancedTechniqueCatalogItem> | null;
  return Boolean(item) && typeof item?.code === 'string' && typeof item?.name === 'string' && typeof item?.set_type === 'string';
}

/** Troca o catálogo corrente. Lista vazia/malformada é ignorada (mantém o atual). */
export function setAdvancedTechniqueCatalog(items: unknown): boolean {
  if (!Array.isArray(items) || items.length === 0 || !items.every(isCatalogItem)) return false;
  currentCatalog = sortAdvancedTechniques(items);
  return true;
}

export function resetAdvancedTechniqueCatalog(): void {
  currentCatalog = FALLBACK_ADVANCED_TECHNIQUES;
}

export interface AdvancedTechniquesResult {
  items: readonly AdvancedTechniqueCatalogItem[];
  // true = a API falhou (ou devolveu algo inutilizável) e a lista é a local.
  isFallback: boolean;
}

/**
 * Carrega o catálogo pela API (`fetcher` = `apiFetch`). NUNCA rejeita: em
 * falha devolve o fallback local, para o builder e o Modo Treino continuarem
 * utilizáveis. 401 continua tratado dentro do `apiFetch` (redirect de login).
 */
export async function loadAdvancedTechniques(
  fetcher: <T>(path: string) => Promise<T>,
): Promise<AdvancedTechniquesResult> {
  try {
    const response = await fetcher<{ items?: unknown }>(ADVANCED_TECHNIQUES_PATH);
    if (setAdvancedTechniqueCatalog(response?.items)) return { items: currentCatalog, isFallback: false };
  } catch {
    // cai no fallback abaixo
  }
  return { items: currentCatalog, isFallback: true };
}

function findTechnique(value: string | null | undefined): AdvancedTechniqueCatalogItem | undefined {
  if (!value) return undefined;
  return currentCatalog.find((item) => item.code === value);
}

/**
 * Opções do Select do builder: só técnicas ATIVAS. `currentValue` (técnica já
 * salva no item) é mantida na lista mesmo se inativa/fora do catálogo, para o
 * campo não aparecer vazio — o backend rejeita (422) se ela for salva de novo.
 */
export function getAdvancedTechniqueOptions(
  items: readonly AdvancedTechniqueCatalogItem[] = currentCatalog,
  currentValue?: string | null,
): { value: string; label: string }[] {
  const options = items.filter((item) => item.is_active).map((item) => ({ value: item.code, label: item.name }));
  if (currentValue && !options.some((option) => option.value === currentValue)) {
    const known = items.find((item) => item.code === currentValue);
    options.push({ value: currentValue, label: `${known?.name ?? currentValue} (indisponível)` });
  }
  return options;
}

export function getAdvancedTechniqueLabel(value: string | null | undefined): string | null {
  if (!value) return null;
  return findTechnique(value)?.name ?? value;
}

// Família "além da falha" (set_type 'forced_rep' no catálogo): usada SÓ como
// lembrete no card da série de trabalho do modo "Fazer treino avançado" —
// não gera sub-etapa `technique` própria nem séries extras persistidas
// separadamente (ver PlanExecutionScreen.tsx).
export function isExtraRepsTechnique(value: string | null | undefined): boolean {
  return findTechnique(value)?.set_type === EXTRA_REPS_SET_TYPE;
}

// Técnicas que abrem a sub-etapa `technique` do Modo Treino (um card por
// mini-bloco/drop, lançado com reps/RPE/carga): as de `set_type`
// rest_pause/drop_set. FST-7/GVT (`working`) são séries de trabalho comuns e a
// família além da falha vira o campo "reps extras" da última série.
export function isApplicationStageTechnique(value: string | null | undefined): boolean {
  const setType = findTechnique(value)?.set_type;
  return setType === 'rest_pause' || setType === 'drop_set';
}

/**
 * Fase 5 (PR 5): nº de séries que a técnica preenche no Modelo
 * (`default_sets_count`: FST-7 = 7, GVT = 10); null nas demais. Técnica
 * assim não tem "Nº de aplicações" nem "Pausa" — o builder oculta e zera os
 * dois campos, e a API/validador da IA fazem o mesmo (`is_sets_only_technique`).
 */
export function getAdvancedTechniqueDefaultSetsCount(value: string | null | undefined): number | null {
  const item = findTechnique(value);
  if (item?.default_sets_count != null) return item.default_sets_count;
  if (value && SETS_ONLY_CODES.has(value)) return value === 'gvt' ? 10 : 7;
  return null;
}

export function isSetsOnlyTechnique(value: string | null | undefined): boolean {
  return getAdvancedTechniqueDefaultSetsCount(value) != null;
}

// set_type gravado por cada aplicação da técnica durante a execução em tempo
// real do modo treino — ver `workout_sessions.py`/`exercise_sets.set_type`.
export function getAdvancedTechniqueSetType(technique: string): string {
  return findTechnique(technique)?.set_type ?? 'working';
}

// ---------------------------------------------------------------------------
// Mapeamento de UI (não faz parte do catálogo)
// ---------------------------------------------------------------------------

// Texto descritivo curto (mesmo conteúdo do RAG TECNICAS_AVANCADAS.md),
// exibido durante a execução real da sub-etapa `technique`.
const ADVANCED_TECHNIQUE_DESCRIPTIONS: Record<string, string> = {
  rest_pause: 'Após a falha, pause 5–15s e retome com a mesma carga para mais repetições. Repita por mini-blocos.',
  drop_set: 'Ao atingir a falha, reduza a carga (~20%) sem descanso e continue até nova falha. Repita a cada drop.',
  fst7: '7 séries até a falha com 30s de intervalo, alongando a musculatura durante a pausa para expandir a fáscia.',
  gvt: '10 séries de 10 repetições com carga moderada e 1 min de intervalo, focando em volume total (German Volume Training).',
  forced_reps: 'Após a falha, receba ajuda mínima de um parceiro (ou mão oposta) para completar 2–3 repetições extras.',
  negative_reps: 'O parceiro realiza a fase concêntrica; você controla apenas a descida (fase excêntrica) de forma lenta.',
  partial_reps: 'Após a falha em amplitude completa, continue com repetições em amplitude reduzida.',
  cheat_reps:
    'Use impulso corporal controlado para vencer o ponto cego, controlando a fase excêntrica. Contraindicado para iniciantes.',
};

export function getAdvancedTechniqueDescription(value: string | null | undefined): string | null {
  if (!value) return null;
  return ADVANCED_TECHNIQUE_DESCRIPTIONS[value] ?? null;
}

// Orientação de alongamento durante a pausa (Modo Intensivo): só para as
// técnicas cuja descrição cadastrada acima pede alongar na pausa. Técnica fora
// do mapa não mostra orientação nenhuma (nunca inventada pela tela).
const ADVANCED_TECHNIQUE_STRETCH_GUIDANCE: Record<string, string> = {
  fst7: 'Na pausa, alongue o músculo trabalhado, sem dor.',
};

export function getAdvancedTechniqueStretchGuidance(value: string | null | undefined): string | null {
  if (!value) return null;
  return ADVANCED_TECHNIQUE_STRETCH_GUIDANCE[value] ?? null;
}

// Rótulo curto usado no contador de aplicações durante a execução, ex.
// "Mini-bloco 1/4", "Drop 1/3".
export function getAdvancedTechniqueApplicationNoun(technique: string): string {
  const setType = getAdvancedTechniqueSetType(technique);
  if (setType === 'rest_pause') return 'Mini-bloco';
  if (setType === 'drop_set') return 'Drop';
  if (setType === EXTRA_REPS_SET_TYPE) return 'Repetição extra';
  // set_type 'working': técnica de séries inteiras (fst7, gvt). Código fora
  // do catálogo mantém o rótulo antigo.
  return findTechnique(technique) ? 'Série' : 'Repetição extra';
}
