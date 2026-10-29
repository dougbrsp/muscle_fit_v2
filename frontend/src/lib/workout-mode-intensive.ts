// Modo Intensivo para técnicas avançadas (PR 2: FST-7 como referência; PR 3:
// GVT, drop set, rest-pause e a família "além da falha").
//
// Módulo PURO (sem React, `window`, `fetch` ou relógio próprio): perfil da
// técnica, máquina de estados do bloco e reconciliação com o servidor. A
// tela (`PlanExecutionScreen` + componentes `Intensive*`) só despacha
// eventos e desenha o estado.
//
// Regra "Motores: fonte única da verdade" (`docs/motores/FONTES_DA_VERDADE.md`):
// - nenhum número de regra mora aqui. O RPE-alvo vem pronto do backend
//   (`advanced_technique_parameters.target_rpe`, dono
//   `training_safety.protocol_target_rpe`); ausente (snapshot antigo) = null e
//   a tela diz só que o servidor define. O nº de séries vem da prescrição do
//   item (ou `default_sets_count` do catálogo) e o descanso do `rest_seconds`
//   do item. O ajuste rápido de carga é o espelho
//   `mirrorQuickLoadAdjust` (`workout-mode-load-suggestions.ts`).
// - `INTENSIVE_MODE_TECHNIQUES` é só o recorte de entrega (quais técnicas já
//   têm tela): não decide carga, esforço nem volume.
//
// PR 3: GVT usa o mesmo bloco do FST-7 (`working_block`). A família "além da
// falha" (`beyond_failure`) usa o bloco sem briefing, com "reps extras" de um
// toque na última série. Drop set e rest-pause (`application`) NÃO usam a
// máquina do bloco: as etapas seguem as sub-etapas da Fase 5
// (`workout-mode-advanced-stage.ts`, contagem por `technique_code` +
// `anchor_set_number`) e aqui ficam só a carga planejada da etapa
// (`intensiveApplicationPlannedLoad`: a do dono `session_loads`, recebida
// pronta), o ajuste por fadiga e o corpo do POST. Drop set sem descanso; a
// pausa do rest-pause é a da prescrição (`pause_seconds`, senão
// `advanced_technique_duration_seconds` do item).

import {
  getAdvancedTechniqueCatalog,
  getAdvancedTechniqueStretchGuidance,
  type AdvancedTechniqueCatalogItem,
} from './advanced-techniques';
import { isTechniqueApplicationSetType, isWorkingSetType } from './set-types';
import type { LoadConvention } from './equipment-exercises-types';
import { fromKg, loadStepperStep, toKg, type WeightUnit } from './weight-units';
import {
  isLoadAdjustSource,
  mirrorQuickLoadAdjust,
  QUICK_ADJUST_ACTIONS,
  quickAdjustFactor,
  type LoadAdjustSource,
  type QuickAdjustAction,
} from './workout-mode-load-suggestions';

// ---------------------------------------------------------------------------
// Perfil da técnica
// ---------------------------------------------------------------------------

/** Família do bloco: séries inteiras (FST-7, GVT), aplicações ligadas a uma âncora (drop, rest-pause) ou reps além da falha. */
export type IntensiveStageKind = 'working_block' | 'application' | 'beyond_failure';

/** Intervalo entre as séries/etapas do bloco. */
export type IntensiveRestBetween =
  | { kind: 'item_rest'; seconds: number }
  | { kind: 'technique_pause'; seconds: number }
  | { kind: 'none' };

export type IntensiveProfile = {
  techniqueCode: string;
  techniqueName: string;
  stageKind: IntensiveStageKind;
  /** Séries do bloco: as do item da prescrição; sem elas, `default_sets_count`. */
  totalSets: number;
  restBetween: IntensiveRestBetween;
  /** Carga constante no bloco (só muda por ajuste de fadiga/manual). */
  loadConstant: boolean;
  /** Ajuste rápido de carga por fadiga disponível. */
  allowsFatigueDrop: boolean;
  /** Orientação de alongamento cadastrada para a pausa; null = não mostrar. */
  stretchGuidance: string | null;
  /** RPE-alvo do protocolo, pronto do backend; null = snapshot sem o campo. */
  targetRpe: number | null;
};

/** `advanced_technique_parameters` da prescrição (`progression_engine.technique_parameters`). */
export type AdvancedTechniqueParameters = {
  technique_code?: string | null;
  source?: string | null;
  sets_only?: boolean | null;
  default_sets_count?: number | null;
  set_type?: string | null;
  applications?: number | null;
  pause_seconds?: number | null;
  planned_equivalent?: number | null;
  target_rpe?: number | null;
  load_factor_per_application?: number | null;
  application_loads_kg?: number[] | null;
};

/** Técnicas que já têm a tela do Modo Intensivo (recorte de entrega, não regra). */
export const INTENSIVE_MODE_TECHNIQUES: ReadonlySet<string> = new Set([
  'fst7',
  'gvt',
  'drop_set',
  'rest_pause',
  'forced_reps',
  'negative_reps',
  'partial_reps',
  'cheat_reps',
]);
/** Famílias que a tela desenha. */
export const INTENSIVE_SUPPORTED_STAGE_KINDS: ReadonlySet<IntensiveStageKind> = new Set([
  'working_block',
  'application',
  'beyond_failure',
]);

function positiveInt(value: unknown): number | null {
  const n = typeof value === 'string' ? Number(value) : value;
  return typeof n === 'number' && Number.isFinite(n) && n > 0 ? Math.floor(n) : null;
}

/**
 * Pausa entre as etapas da técnica: a da prescrição (`pause_seconds`, pronta
 * no dono `progression_engine.technique_pause_seconds`, que já aplica o padrão
 * do rest-pause), senão a duração da técnica na ficha. Os parâmetros só valem
 * quando são desta técnica (troca na ficha). Fluxo normal e Intensivo usam a
 * mesma função.
 */
export function techniquePauseSeconds(
  techniqueCode: string | null | undefined,
  parameters: AdvancedTechniqueParameters | null | undefined,
  techniqueDurationSeconds: number | null | undefined,
): number | null {
  const own = parameters && techniqueCode && parameters.technique_code === techniqueCode ? parameters : null;
  return positiveInt(own?.pause_seconds) ?? positiveInt(techniqueDurationSeconds);
}

function finiteNumber(value: unknown): number | null {
  if (value == null || value === '') return null;
  const n = typeof value === 'string' ? Number(value) : value;
  return typeof n === 'number' && Number.isFinite(n) ? n : null;
}

export function intensiveStageKindOf(item: Pick<AdvancedTechniqueCatalogItem, 'failure_requirement' | 'set_type' | 'default_sets_count'>): IntensiveStageKind | null {
  if (item.failure_requirement === 'beyond_failure') return 'beyond_failure';
  if (isTechniqueApplicationSetType(item.set_type)) return 'application';
  if (isWorkingSetType(item.set_type) && item.default_sets_count != null) return 'working_block';
  return null;
}

/**
 * Perfil do Modo Intensivo para a técnica do item, ou null quando o item
 * segue o fluxo normal (sem técnica, técnica ainda sem tela, família não
 * suportada, sem nº de séries).
 */
export function resolveIntensiveProfile(params: {
  techniqueCode: string | null | undefined;
  /** Catálogo de `GET /advanced-techniques` (ou o fallback local). */
  catalog?: ReadonlyArray<AdvancedTechniqueCatalogItem> | null;
  /** `prescription.advanced_technique_parameters` (pode faltar em snapshot antigo). */
  parameters?: AdvancedTechniqueParameters | null;
  /** Séries de trabalho do item na prescrição/meta da sessão. */
  plannedSetsCount?: number | null;
  /** Descanso do item já resolvido (`rest_seconds` do item ou o default de prescrição). */
  itemRestSeconds: number;
  /** Pausa da técnica na ficha (`advanced_technique_duration_seconds`); vale sem `pause_seconds` congelado. */
  techniqueDurationSeconds?: number | null;
  enabledTechniques?: ReadonlySet<string>;
  supportedStageKinds?: ReadonlySet<IntensiveStageKind>;
}): IntensiveProfile | null {
  const code = (params.techniqueCode ?? '').trim();
  const enabled = params.enabledTechniques ?? INTENSIVE_MODE_TECHNIQUES;
  if (!code || !enabled.has(code)) return null;
  const entry = (params.catalog ?? getAdvancedTechniqueCatalog()).find((item) => item.code === code);
  if (!entry) return null;
  const stageKind = intensiveStageKindOf(entry);
  const supported = params.supportedStageKinds ?? INTENSIVE_SUPPORTED_STAGE_KINDS;
  if (stageKind == null || !supported.has(stageKind)) return null;

  // Parâmetros congelados só valem quando são desta técnica (troca na ficha).
  const technique = params.parameters && params.parameters.technique_code === code ? params.parameters : null;
  const totalSets =
    positiveInt(params.plannedSetsCount) ??
    positiveInt(technique?.default_sets_count) ??
    positiveInt(entry.default_sets_count);
  if (totalSets == null) return null;

  const pause = techniquePauseSeconds(code, technique, params.techniqueDurationSeconds);
  const restBetween: IntensiveRestBetween =
    stageKind === 'application'
      ? pause != null
        ? { kind: 'technique_pause', seconds: pause }
        : { kind: 'none' }
      : { kind: 'item_rest', seconds: Math.max(0, Math.floor(params.itemRestSeconds)) };

  return {
    techniqueCode: code,
    techniqueName: entry.name,
    stageKind,
    totalSets,
    restBetween,
    // Só o drop set reduz a carga a cada etapa (fator do dono `session_loads`).
    loadConstant:
      stageKind !== 'application' || (technique?.load_factor_per_application == null && entry.set_type !== 'drop_set'),
    allowsFatigueDrop: true,
    stretchGuidance: restBetween.kind === 'none' ? null : getAdvancedTechniqueStretchGuidance(code),
    targetRpe: finiteNumber(technique?.target_rpe),
  };
}

/** O perfil usa a máquina do bloco (séries inteiras); `application` segue as sub-etapas da Fase 5. */
export function isIntensiveBlockProfile(profile: Pick<IntensiveProfile, 'stageKind'> | null | undefined): boolean {
  return profile != null && profile.stageKind !== 'application';
}

/** "Reps extras" de um toque: família além da falha, só na última série do bloco (como no fluxo normal). */
export function intensiveExtraRepsEnabled(
  profile: Pick<IntensiveProfile, 'stageKind'>,
  setIndex: number,
  totalSets: number,
): boolean {
  return profile.stageKind === 'beyond_failure' && setIndex >= totalSets;
}

/** Segundos de descanso entre as séries do bloco (0 = sem descanso). */
export function intensiveRestSeconds(profile: Pick<IntensiveProfile, 'restBetween'>): number {
  return profile.restBetween.kind === 'none' ? 0 : profile.restBetween.seconds;
}

// ---------------------------------------------------------------------------
// Máquina de estados do bloco
// ---------------------------------------------------------------------------

export const INTENSIVE_PHASES = [
  'BRIEFING',
  'SERIE_EM_EXECUCAO',
  // Bloco encerrado (última série ou "Encerrar técnica") com alguma série
  // ainda sem confirmação do servidor (gravando ou com erro): a revisão só
  // abre quando tudo estiver gravado. Antes da última série, a gravação em
  // curso aparece sobre o cronômetro e o descanso não espera por ela.
  'REGISTRO_PENDENTE',
  'DESCANSO',
  'PROXIMA_SERIE_PRONTA',
  'REVISAO_FINAL',
  'TECNICA_CONCLUIDA',
] as const;
export type IntensivePhase = (typeof INTENSIVE_PHASES)[number];

/** Fases em que o bloco "segura" o exercício na tela (mesmo com todas as séries gravadas). */
const ACTIVE_PHASES: ReadonlySet<IntensivePhase> = new Set([
  'SERIE_EM_EXECUCAO',
  'REGISTRO_PENDENTE',
  'DESCANSO',
  'PROXIMA_SERIE_PRONTA',
  'REVISAO_FINAL',
]);
/** Fases em que a carga planejada das próximas séries pode mudar. */
const ADJUSTABLE_PHASES: ReadonlySet<IntensivePhase> = new Set([
  'BRIEFING',
  'SERIE_EM_EXECUCAO',
  'DESCANSO',
  'PROXIMA_SERIE_PRONTA',
]);

export type IntensiveSync = 'saving' | 'saved' | 'error';

export type IntensiveSetRecord = {
  /** Posição no bloco (1..N), a do "Série N de S". */
  index: number;
  /** `set_number` FÍSICO gravado (inclui aquecimento/ajuste). */
  setNumber: number;
  /** Chave de idempotência do POST (a mesma em toda nova tentativa). */
  clientKey: string;
  /** null = repetições pendentes (gravada com `reps_pending`). */
  reps: number | null;
  rpe: number | null;
  /** Carga executada (kg). */
  weightKg: number | null;
  /** Carga planejada da série depois dos ajustes (kg). */
  plannedKg: number | null;
  loadAdjustSource: LoadAdjustSource | null;
  /** Hora real da conclusão (ISO com fuso). */
  completedAt: string;
  /** Além da falha: reps extras (linha da técnica ligada a esta série); null/ausente = nenhuma. */
  extraReps?: number | null;
  /** Chave de idempotência da linha das reps extras. */
  extraClientKey?: string | null;
  sync: IntensiveSync;
  error: string | null;
};

/** Carga planejada das próximas séries. `source` = origem a gravar na PRÓXIMA série concluída (consumida por ela). */
export type IntensivePlannedLoad = { kg: number | null; source: LoadAdjustSource | null };

export type IntensiveRest = { key: string; endsAt: number; totalSeconds: number };

export type IntensiveBlockState = {
  version: 1;
  techniqueCode: string;
  phase: IntensivePhase;
  totalSets: number;
  /** Série em execução/a seguir (1-based); `totalSets + 1` = bloco esgotado. */
  currentSet: number;
  planned: IntensivePlannedLoad;
  /** Pilha do "Desfazer" (só a carga planejada; o histórico não muda). */
  undo: IntensivePlannedLoad[];
  records: IntensiveSetRecord[];
  /** Posições puladas (nada gravado). */
  skipped: number[];
  /** Descanso em curso/terminado (fim absoluto em epoch ms). */
  rest: IntensiveRest | null;
};

export type IntensiveEvent =
  | { type: 'START' }
  | {
      type: 'COMPLETE_SET';
      reps: number | null;
      rpe?: number | null;
      clientKey: string;
      setNumber: number;
      now: number;
      restSeconds: number;
      /** Além da falha: reps extras da última série (gravadas numa linha ligada a ela). */
      extraReps?: number | null;
      extraClientKey?: string | null;
    }
  | { type: 'SAVE_OK'; clientKey: string }
  | { type: 'SAVE_FAILED'; clientKey: string; error: string }
  | { type: 'RETRY'; clientKey: string }
  | { type: 'REST_ADJUSTED'; endsAt: number; totalSeconds: number }
  | { type: 'REST_EXPIRED' }
  | { type: 'SKIP_REST' }
  | { type: 'START_NEXT' }
  | { type: 'ADJUST_LOAD'; kg: number; source: LoadAdjustSource }
  | { type: 'UNDO_ADJUST' }
  | { type: 'UPDATE_RECORD'; clientKey: string; reps?: number | null; weightKg?: number | null; rpe?: number | null }
  | { type: 'SKIP_SET' }
  | { type: 'END_TECHNIQUE' }
  | { type: 'FINISH' };

export function createIntensiveBlock(
  profile: Pick<IntensiveProfile, 'techniqueCode' | 'totalSets'> & Partial<Pick<IntensiveProfile, 'stageKind'>>,
  plannedKg: number | null,
): IntensiveBlockState {
  return {
    version: 1,
    techniqueCode: profile.techniqueCode,
    // Além da falha: sem tela própria pesada (sem briefing), direto na série.
    phase: profile.stageKind === 'beyond_failure' ? 'SERIE_EM_EXECUCAO' : 'BRIEFING',
    totalSets: profile.totalSets,
    currentSet: 1,
    planned: { kg: plannedKg, source: null },
    undo: [],
    records: [],
    skipped: [],
    rest: null,
  };
}

export function hasUnsavedRecords(state: Pick<IntensiveBlockState, 'records'>): boolean {
  return state.records.some((record) => record.sync !== 'saved');
}

export function pendingRepsRecords(state: Pick<IntensiveBlockState, 'records'>): IntensiveSetRecord[] {
  return state.records.filter((record) => record.reps == null);
}

export function isIntensiveBlockActive(state: Pick<IntensiveBlockState, 'phase'> | null | undefined): boolean {
  return state != null && ACTIVE_PHASES.has(state.phase);
}

export function canAdjustPlannedLoad(state: Pick<IntensiveBlockState, 'phase'>): boolean {
  return ADJUSTABLE_PHASES.has(state.phase);
}

/** Fecha o bloco: revisão quando tudo está gravado; senão espera a gravação. */
function closeBlock(state: IntensiveBlockState): IntensiveBlockState {
  if (state.records.length === 0) return { ...state, phase: 'TECNICA_CONCLUIDA', rest: null };
  return { ...state, phase: hasUnsavedRecords(state) ? 'REGISTRO_PENDENTE' : 'REVISAO_FINAL', rest: null };
}

function patchRecord(
  state: IntensiveBlockState,
  clientKey: string,
  patch: Partial<IntensiveSetRecord>,
): IntensiveBlockState {
  if (!state.records.some((record) => record.clientKey === clientKey)) return state;
  return {
    ...state,
    records: state.records.map((record) => (record.clientKey === clientKey ? { ...record, ...patch } : record)),
  };
}

/** Pendente de gravação resolvida: a revisão abre quando nada mais falta. */
function settleRegistration(state: IntensiveBlockState): IntensiveBlockState {
  if (state.phase === 'REGISTRO_PENDENTE' && !hasUnsavedRecords(state)) return { ...state, phase: 'REVISAO_FINAL' };
  return state;
}

export function intensiveReducer(state: IntensiveBlockState, event: IntensiveEvent): IntensiveBlockState {
  switch (event.type) {
    case 'START':
      return state.phase === 'BRIEFING' ? { ...state, phase: 'SERIE_EM_EXECUCAO' } : state;

    case 'COMPLETE_SET': {
      if (state.phase !== 'SERIE_EM_EXECUCAO' || state.currentSet > state.totalSets) return state;
      // Nunca grava 0 nem a meta no lugar das reps: sem reps válidas = pendente.
      const reps = event.reps != null && Number.isFinite(event.reps) && event.reps > 0 ? Math.floor(event.reps) : null;
      const extraReps = event.extraClientKey ? positiveInt(event.extraReps) : null;
      const record: IntensiveSetRecord = {
        index: state.currentSet,
        setNumber: event.setNumber,
        clientKey: event.clientKey,
        reps,
        rpe: event.rpe ?? null,
        weightKg: state.planned.kg,
        plannedKg: state.planned.kg,
        loadAdjustSource: state.planned.source,
        completedAt: new Date(event.now).toISOString(),
        ...(extraReps != null ? { extraReps, extraClientKey: event.extraClientKey } : {}),
        sync: 'saving',
        error: null,
      };
      const next: IntensiveBlockState = {
        ...state,
        records: [...state.records, record],
        currentSet: state.currentSet + 1,
        // A origem do ajuste vai só na série que estreou a carga ajustada.
        planned: { kg: state.planned.kg, source: null },
        undo: state.undo.map((entry) => ({ kg: entry.kg, source: null })),
      };
      if (next.currentSet > next.totalSets) return closeBlock(next);
      const seconds = Math.max(0, Math.floor(event.restSeconds));
      if (seconds === 0) return { ...next, phase: 'SERIE_EM_EXECUCAO', rest: null };
      return {
        ...next,
        phase: 'DESCANSO',
        rest: { key: `${state.techniqueCode}#${record.index}@${event.now}`, endsAt: event.now + seconds * 1000, totalSeconds: seconds },
      };
    }

    case 'SAVE_OK':
      return settleRegistration(patchRecord(state, event.clientKey, { sync: 'saved', error: null }));

    case 'SAVE_FAILED':
      return patchRecord(state, event.clientKey, { sync: 'error', error: event.error });

    case 'RETRY':
      return patchRecord(state, event.clientKey, { sync: 'saving', error: null });

    case 'REST_ADJUSTED':
      if (!state.rest || (state.rest.endsAt === event.endsAt && state.rest.totalSeconds === event.totalSeconds)) return state;
      return { ...state, rest: { ...state.rest, endsAt: event.endsAt, totalSeconds: event.totalSeconds } };

    case 'REST_EXPIRED':
      // Sem início automático: só sinaliza que a próxima série está pronta.
      return state.phase === 'DESCANSO' ? { ...state, phase: 'PROXIMA_SERIE_PRONTA' } : state;

    case 'SKIP_REST':
    case 'START_NEXT':
      return state.phase === 'DESCANSO' || state.phase === 'PROXIMA_SERIE_PRONTA'
        ? { ...state, phase: 'SERIE_EM_EXECUCAO', rest: null }
        : state;

    case 'ADJUST_LOAD': {
      if (!canAdjustPlannedLoad(state) || !Number.isFinite(event.kg) || event.kg < 0) return state;
      if (!isLoadAdjustSource(event.source)) return state;
      if (state.planned.kg === event.kg) return state;
      // Não mexe no histórico nem no cronômetro (`rest` intacto).
      return { ...state, undo: [...state.undo, state.planned], planned: { kg: event.kg, source: event.source } };
    }

    case 'UNDO_ADJUST': {
      if (!canAdjustPlannedLoad(state) || state.undo.length === 0) return state;
      const previous = state.undo[state.undo.length - 1];
      return { ...state, undo: state.undo.slice(0, -1), planned: previous };
    }

    case 'UPDATE_RECORD': {
      const patch: Partial<IntensiveSetRecord> = {};
      if (event.reps !== undefined) patch.reps = event.reps;
      if (event.weightKg !== undefined) patch.weightKg = event.weightKg;
      if (event.rpe !== undefined) patch.rpe = event.rpe;
      return patchRecord(state, event.clientKey, patch);
    }

    case 'SKIP_SET': {
      if (state.phase !== 'SERIE_EM_EXECUCAO' || state.currentSet > state.totalSets) return state;
      const next = { ...state, skipped: [...state.skipped, state.currentSet], currentSet: state.currentSet + 1 };
      return next.currentSet > next.totalSets ? closeBlock(next) : next;
    }

    case 'END_TECHNIQUE':
      if (!ACTIVE_PHASES.has(state.phase) && state.phase !== 'BRIEFING') return state;
      if (state.phase === 'REVISAO_FINAL' || state.phase === 'REGISTRO_PENDENTE') return state;
      return closeBlock(state);

    case 'FINISH':
      return state.phase === 'REVISAO_FINAL' && !hasUnsavedRecords(state) ? { ...state, phase: 'TECNICA_CONCLUIDA' } : state;

    default:
      return state;
  }
}

/** Descanso que venceu enquanto a tela estava fechada vira "próxima série pronta" (sem início automático). */
export function normalizeIntensiveBlock(state: IntensiveBlockState, now: number): IntensiveBlockState {
  if (state.phase === 'DESCANSO' && (!state.rest || state.rest.endsAt <= now)) {
    return state.rest ? { ...state, phase: 'PROXIMA_SERIE_PRONTA' } : { ...state, phase: 'SERIE_EM_EXECUCAO' };
  }
  return state;
}

// ---------------------------------------------------------------------------
// Snapshot e retomada pelo servidor
// ---------------------------------------------------------------------------

/** Série do exercício como `GET /activities/{id}` devolve (inclui as pendentes). */
export type IntensiveServerSet = {
  set_number: number;
  set_type?: string | null;
  reps?: number | null;
  reps_pending?: boolean | number | null;
  weight_kg?: number | string | null;
  rpe?: number | string | null;
  planned_weight_kg?: number | string | null;
  load_adjust_source?: string | null;
  completed_at?: string | null;
  client_key?: string | null;
  /** Linha da técnica (reps extras, drop, mini-bloco): série gravada a que ela se liga. */
  anchor_set_number?: number | null;
  technique_code?: string | null;
};

function isPendingServerSet(set: IntensiveServerSet): boolean {
  return Boolean(set.reps_pending);
}

function serverBlockSets(serverSets: ReadonlyArray<IntensiveServerSet>): IntensiveServerSet[] {
  return serverSets.filter((set) => isWorkingSetType(set.set_type)).sort((a, b) => a.set_number - b.set_number);
}

/**
 * Reps extras (além da falha) que só o servidor conhece (outro aparelho,
 * storage limpo): a linha da técnica ligada à série pelo `anchor_set_number`
 * volta para o registro, para a revisão listá-la. Registro com extras locais
 * não muda.
 */
function withServerExtraReps(
  records: IntensiveSetRecord[],
  serverSets: ReadonlyArray<IntensiveServerSet>,
  techniqueCode: string,
): IntensiveSetRecord[] {
  return records.map((record) => {
    if (record.extraReps) return record;
    const extra = serverSets.find(
      (set) =>
        set.anchor_set_number === record.setNumber &&
        !isWorkingSetType(set.set_type) &&
        (set.technique_code == null || set.technique_code === techniqueCode),
    );
    const reps = extra ? positiveInt(extra.reps) : null;
    if (!extra || reps == null) return record;
    return { ...record, extraReps: reps, extraClientKey: extra.client_key || null };
  });
}

/** `completed_at` do servidor (hora de parede, sem fuso) ou ISO com fuso -> epoch ms. */
export function parseCompletedAt(value: string | null | undefined): number | null {
  if (!value) return null;
  const normalized = value.includes('T') ? value : value.replace(' ', 'T');
  const time = new Date(normalized).getTime();
  return Number.isFinite(time) ? time : null;
}

function recordFromServer(set: IntensiveServerSet, index: number): IntensiveSetRecord {
  const weight = finiteNumber(set.weight_kg);
  return {
    index,
    setNumber: set.set_number,
    clientKey: set.client_key || `server-${set.set_number}`,
    reps: isPendingServerSet(set) ? null : (finiteNumber(set.reps) ?? null),
    rpe: finiteNumber(set.rpe),
    weightKg: weight,
    plannedKg: finiteNumber(set.planned_weight_kg) ?? weight,
    loadAdjustSource: isLoadAdjustSource(set.load_adjust_source) ? set.load_adjust_source : null,
    completedAt: set.completed_at ?? '',
    sync: 'saved',
    error: null,
  };
}

function isStoredBlock(value: unknown, techniqueCode: string): value is IntensiveBlockState {
  if (!value || typeof value !== 'object') return false;
  const state = value as Partial<IntensiveBlockState>;
  return (
    state.version === 1 &&
    state.techniqueCode === techniqueCode &&
    typeof state.phase === 'string' &&
    (INTENSIVE_PHASES as readonly string[]).includes(state.phase) &&
    typeof state.currentSet === 'number' &&
    Array.isArray(state.records) &&
    Array.isArray(state.undo) &&
    state.planned != null
  );
}

/**
 * Estado do bloco a partir do snapshot local + séries do servidor.
 *
 * - Com snapshot: o servidor confirma a gravação (série com a mesma
 *   `client_key` ou `set_number` = gravada) e traz séries que só ele tem; a
 *   carga planejada, a pilha de desfazer e o descanso continuam os locais.
 *   Reps confirmadas localmente (PATCH) não voltam a pendentes por um
 *   refetch atrasado.
 * - Sem snapshot (outro aparelho, storage limpo): reconstrói pelo servidor
 *   (`reps_pending`, `completed_at`): a série seguinte, e o descanso se a
 *   última conclusão + o descanso do item ainda não venceu.
 */
export function reconcileIntensiveBlock(params: {
  stored: unknown;
  profile: Pick<IntensiveProfile, 'techniqueCode' | 'totalSets' | 'restBetween'> & Partial<Pick<IntensiveProfile, 'stageKind'>>;
  serverSets: ReadonlyArray<IntensiveServerSet>;
  initialPlannedKg: number | null;
  now: number;
}): IntensiveBlockState {
  const { profile, now } = params;
  const server = serverBlockSets(params.serverSets);
  const restSeconds = intensiveRestSeconds(profile);

  if (isStoredBlock(params.stored, profile.techniqueCode)) {
    const stored = params.stored;
    const matched = new Set<number>();
    const records = stored.records.map((record) => {
      const hit = server.find((set) => (set.client_key && set.client_key === record.clientKey) || set.set_number === record.setNumber);
      if (!hit) return record;
      matched.add(hit.set_number);
      const fromServer = recordFromServer(hit, record.index);
      return {
        ...record,
        sync: 'saved' as const,
        error: null,
        setNumber: hit.set_number,
        reps: record.reps ?? fromServer.reps,
      };
    });
    let lastIndex = Math.max(0, ...records.map((record) => record.index), ...stored.skipped);
    for (const set of server) {
      if (matched.has(set.set_number)) continue;
      lastIndex += 1;
      records.push(recordFromServer(set, lastIndex));
    }
    records.sort((a, b) => a.index - b.index);
    // `profile.totalSets` já é inteiro > 0 (`resolveIntensiveProfile`).
    let state: IntensiveBlockState = {
      ...stored,
      totalSets: profile.totalSets,
      records,
      skipped: [...stored.skipped],
      currentSet: Math.max(stored.currentSet, lastIndex + 1),
    };
    if (state.currentSet > state.totalSets && (state.phase === 'SERIE_EM_EXECUCAO' || state.phase === 'DESCANSO' || state.phase === 'PROXIMA_SERIE_PRONTA')) {
      state = closeBlock(state);
    }
    state = { ...state, records: withServerExtraReps(state.records, params.serverSets, profile.techniqueCode) };
    return normalizeIntensiveBlock(settleRegistration(state), now);
  }

  const base = createIntensiveBlock(profile, params.initialPlannedKg);
  if (server.length === 0) return base;
  const records = withServerExtraReps(
    server.map((set, i) => recordFromServer(set, i + 1)),
    params.serverSets,
    profile.techniqueCode,
  );
  const last = records[records.length - 1];
  const state: IntensiveBlockState = {
    ...base,
    records,
    currentSet: records.length + 1,
    planned: { kg: last.plannedKg ?? last.weightKg ?? params.initialPlannedKg, source: null },
  };
  if (state.currentSet > state.totalSets) return { ...state, phase: 'REVISAO_FINAL' };
  const completed = parseCompletedAt(last.completedAt);
  const endsAt = completed != null ? completed + restSeconds * 1000 : null;
  if (endsAt != null && restSeconds > 0 && endsAt > now) {
    return {
      ...state,
      phase: 'DESCANSO',
      rest: { key: `${profile.techniqueCode}#${last.index}@${completed}`, endsAt, totalSeconds: restSeconds },
    };
  }
  return { ...state, phase: 'SERIE_EM_EXECUCAO' };
}

/**
 * O exercício com Modo Intensivo está concluído? O bloco "segura" o item
 * enquanto está ativo (a revisão abre com todas as séries já gravadas) e o
 * encerra ao concluir a técnica (inclusive "Encerrar técnica" antes da
 * última série). Sem snapshot, uma série pendente no servidor reabre a revisão.
 */
export function intensiveItemDone(params: {
  stored: Pick<IntensiveBlockState, 'phase'> | null | undefined;
  countersDone: boolean;
  serverSets: ReadonlyArray<IntensiveServerSet>;
}): boolean {
  const { stored, countersDone } = params;
  if (stored?.phase === 'TECNICA_CONCLUIDA') return true;
  if (isIntensiveBlockActive(stored)) return false;
  if (!stored && countersDone && serverBlockSets(params.serverSets).some(isPendingServerSet)) return false;
  return countersDone;
}

/** Próximo `set_number` físico: o maior entre o servidor e as séries locais ainda não confirmadas. */
export function nextIntensiveSetNumber(
  state: Pick<IntensiveBlockState, 'records'>,
  serverSets: ReadonlyArray<{ set_number?: number | null }>,
): number {
  let max = 0;
  for (const set of serverSets) if (typeof set.set_number === 'number' && set.set_number > max) max = set.set_number;
  for (const record of state.records) {
    // A linha das reps extras ocupa o número seguinte ao da série.
    const last = record.setNumber + (record.extraReps ? 1 : 0);
    if (last > max) max = last;
  }
  return max + 1;
}

/** Corpo do `POST .../sets` de uma série do bloco (contrato do PR #617). */
export function intensiveSetPayload(record: IntensiveSetRecord, restSeconds: number | null): Record<string, unknown> {
  return {
    set_number: record.setNumber,
    reps: record.reps,
    reps_pending: record.reps == null,
    weight_kg: record.weightKg,
    planned_weight_kg: record.plannedKg,
    ...(record.loadAdjustSource ? { load_adjust_source: record.loadAdjustSource } : {}),
    // O servidor calcula e grava o alvo; o cliente nunca manda o número.
    rpe_target_source: 'protocol',
    rpe: record.rpe,
    completed_at: record.completedAt,
    rest_seconds: restSeconds,
    client_key: record.clientKey,
  };
}

/**
 * Corpo do `POST .../sets` das reps extras (além da falha): uma linha da
 * técnica (`set_type` do catálogo) ligada à série pelo `anchor_set_number`,
 * sem RPE, como no fluxo normal (contabilização da Fase 5 inalterada). Mesmo
 * contrato das etapas de drop/rest-pause (`intensiveApplicationPayload`):
 * carga planejada = a executada na série (as extras saem dela) e alvo do
 * protocolo gravado pelo servidor (`rpe_target_source`).
 */
export function intensiveExtraRepsPayload(
  record: IntensiveSetRecord,
  technique: { techniqueCode: string; setType: string },
): Record<string, unknown> | null {
  if (!record.extraReps || !record.extraClientKey) return null;
  return {
    set_number: record.setNumber + 1,
    client_key: record.extraClientKey,
    set_type: technique.setType,
    reps: record.extraReps,
    reps_pending: false,
    weight_kg: record.weightKg,
    planned_weight_kg: record.weightKg,
    rpe_target_source: 'protocol',
    rpe: null,
    technique_code: technique.techniqueCode,
    anchor_set_number: record.setNumber,
    completed_at: record.completedAt,
  };
}

/** Status de uma série na revisão. */
export type IntensiveRecordStatus = 'saving' | 'error' | 'pending_reps' | 'done';

export function intensiveRecordStatus(record: Pick<IntensiveSetRecord, 'sync' | 'reps'>): IntensiveRecordStatus {
  if (record.sync === 'saving') return 'saving';
  if (record.sync === 'error') return 'error';
  return record.reps == null ? 'pending_reps' : 'done';
}

// ---------------------------------------------------------------------------
// Aplicações da técnica (drop set / rest-pause)
// ---------------------------------------------------------------------------

/** "Drop 2 de 3" / "Mini-bloco 1 de 4" (nome da etapa do catálogo, posição da sub-etapa). */
export function intensiveApplicationLabel(noun: string, index: number, total: number): string {
  return `${noun} ${index} de ${total}`;
}

/** Ajuste local da carga da etapa atual (`key` = item + âncora + etapa); some ao trocar de etapa. */
export type IntensiveApplicationAdjustment = {
  key: string;
  planned: IntensivePlannedLoad;
  undo: IntensivePlannedLoad[];
};

export function intensiveApplicationKey(itemKey: string, anchorSetNumber: number | null, index: number): string {
  return `${itemKey}#a${anchorSetNumber ?? 0}#${index}`;
}

/**
 * Carga planejada da etapa e a origem gravada em `load_adjust_source`:
 * - ajuste feito nesta etapa (fadiga/manual) vence;
 * - drop set (`loadConstant` falso): a carga do protocolo (`protocolKg`, do
 *   dono `session_loads`, via `techniqueApplicationLoad`) com origem `protocol`;
 * - carga constante (rest-pause): repete a carga EXECUTADA na etapa anterior
 *   do bloco (um ajuste por fadiga continua valendo) ou a do protocolo (= a
 *   da âncora); sem origem (não é ajuste).
 */
export function intensiveApplicationPlannedLoad(params: {
  profile: Pick<IntensiveProfile, 'loadConstant'>;
  protocolKg: number | null;
  previousApplicationKg: number | null;
  adjustment: IntensiveApplicationAdjustment | null;
  key: string;
}): IntensivePlannedLoad {
  if (params.adjustment && params.adjustment.key === params.key) return params.adjustment.planned;
  if (!params.profile.loadConstant) {
    return { kg: params.protocolKg, source: params.protocolKg != null ? 'protocol' : null };
  }
  return { kg: params.previousApplicationKg ?? params.protocolKg, source: null };
}

/** Ajuste rápido (fadiga) ou manual da carga da etapa; a pilha permite "Desfazer". */
export function adjustApplicationLoad(
  current: IntensiveApplicationAdjustment | null,
  key: string,
  base: IntensivePlannedLoad,
  kg: number,
  source: LoadAdjustSource,
): IntensiveApplicationAdjustment | null {
  const state = current && current.key === key ? current : { key, planned: base, undo: [] };
  if (!Number.isFinite(kg) || kg < 0 || !isLoadAdjustSource(source) || state.planned.kg === kg) return current;
  return { key, planned: { kg, source }, undo: [...state.undo, state.planned] };
}

export function undoApplicationLoad(
  current: IntensiveApplicationAdjustment | null,
  key: string,
): IntensiveApplicationAdjustment | null {
  if (!current || current.key !== key || current.undo.length === 0) return current;
  return { key, planned: current.undo[current.undo.length - 1], undo: current.undo.slice(0, -1) };
}

/**
 * Corpo do `POST .../sets` de uma etapa da técnica no Modo Intensivo. O vínculo
 * da Fase 5 (`technique_code` + `anchor_set_number` + `set_type` do catálogo)
 * é o mesmo do fluxo normal. Sem RPE individual: o servidor grava o alvo do
 * protocolo (`rpe_target_source`); reps vazias = pendente.
 */
export function intensiveApplicationPayload(params: {
  setNumber: number;
  clientKey: string;
  setType: string;
  techniqueCode: string;
  anchorSetNumber: number | null;
  reps: number | null;
  planned: IntensivePlannedLoad;
  completedAt: string;
  restSeconds: number | null;
}): Record<string, unknown> {
  const reps = positiveInt(params.reps);
  return {
    set_number: params.setNumber,
    client_key: params.clientKey,
    set_type: params.setType,
    technique_code: params.techniqueCode,
    ...(params.anchorSetNumber != null ? { anchor_set_number: params.anchorSetNumber } : {}),
    reps,
    reps_pending: reps == null,
    weight_kg: params.planned.kg,
    planned_weight_kg: params.planned.kg,
    ...(params.planned.source ? { load_adjust_source: params.planned.source } : {}),
    rpe_target_source: 'protocol',
    rpe: null,
    completed_at: params.completedAt,
    rest_seconds: params.restSeconds,
  };
}

/** Pausa curta da técnica em curso (fim absoluto em epoch ms). `key` = item da sessão. */
export type IntensivePause = { key: string; endsAt: number; totalSeconds: number };

/** Segundos que faltam da pausa (arredondado para cima; 0 = pronta). */
export function pauseRemainingSeconds(endsAt: number, now: number): number {
  return Math.max(0, Math.ceil((endsAt - now) / 1000));
}

/**
 * A pausa do rest-pause ainda segura o "Concluir" da etapa? Só libera quando
 * a pausa termina ou com "Pular pausa" explícito (como o "Pular descanso").
 */
export function intensivePauseHoldsCompletion(
  pause: Pick<IntensivePause, 'endsAt'> | null | undefined,
  now: number,
  skipped: boolean,
): boolean {
  return pause != null && !skipped && pauseRemainingSeconds(pause.endsAt, now) > 0;
}

// ---------------------------------------------------------------------------
// Ajuste rápido na unidade exibida
// ---------------------------------------------------------------------------

/**
 * Carga planejada (kg) depois do ajuste `action`: converte a carga e o passo
 * do equipamento para a unidade exibida (`weight-units.ts`), aplica o espelho
 * do dono (`session_loads.quick_load_adjust`) e volta para kg.
 */
export function quickAdjustPlannedKg(params: {
  plannedKg: number;
  unit: WeightUnit;
  loadIncrementKg: number | null | undefined;
  action: QuickAdjustAction;
}): number {
  const step = loadStepperStep(params.unit, params.loadIncrementKg);
  const shown = fromKg(params.plannedKg, params.unit);
  return toKg(mirrorQuickLoadAdjust(shown, step, params.action), params.unit);
}

export type QuickAdjustOption = { action: QuickAdjustAction; label: string; accessibleLabel: string };

/** Botões do ajuste rápido, na ordem do backend, com o rótulo na unidade exibida. */
export function quickAdjustOptions(unit: WeightUnit, loadIncrementKg: number | null | undefined): QuickAdjustOption[] {
  const step = loadStepperStep(unit, loadIncrementKg);
  return QUICK_ADJUST_ACTIONS.map((action) => {
    const factor = quickAdjustFactor(action);
    if (factor == null) {
      return { action, label: `−${step} ${unit}`, accessibleLabel: `Reduzir ${step} ${unit}` };
    }
    const percent = Math.round((1 - factor) * 100);
    return { action, label: `−${percent}%`, accessibleLabel: `Reduzir ${percent}%` };
  });
}

// Convenções em que o número registrado vale por unidade do implemento (o
// rótulo acompanha a carga). `total`/ausente não ganha rótulo.
const PER_UNIT_LOAD_LABELS: Partial<Record<LoadConvention, string>> = {
  per_side: 'por lado',
  per_dumbbell: 'por halter',
};

export function intensiveLoadConventionSuffix(convention: string | null | undefined): string | null {
  if (!convention) return null;
  return PER_UNIT_LOAD_LABELS[convention as LoadConvention] ?? null;
}

// ---------------------------------------------------------------------------
// Snapshot local
// ---------------------------------------------------------------------------

export const INTENSIVE_INTERRUPTED_SAVE = 'envio interrompido; toque em "Tentar de novo"';

/**
 * `intensive_blocks` do localStorage -> estado por `workout_plan_exercise_id`.
 * Uma série que estava "gravando" quando a página fechou não tem resposta: vira
 * erro visível (a nova tentativa reusa a `client_key`, então não duplica se o
 * servidor já tinha gravado). Se o servidor tiver a série, a reconciliação a
 * marca como gravada antes mesmo de o usuário ver o erro.
 */
export function restoreIntensiveBlocks(raw: unknown): Record<number, IntensiveBlockState> {
  if (!raw || typeof raw !== 'object') return {};
  const result: Record<number, IntensiveBlockState> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    const id = Number(key);
    if (!Number.isFinite(id) || !value || typeof value !== 'object') continue;
    const state = value as IntensiveBlockState;
    if (state.version !== 1 || !Array.isArray(state.records)) continue;
    result[id] = {
      ...state,
      records: state.records.map((record) =>
        record.sync === 'saving' ? { ...record, sync: 'error' as const, error: INTENSIVE_INTERRUPTED_SAVE } : record,
      ),
    };
  }
  return result;
}
