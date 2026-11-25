// Cliente de /resistance-profiles (issue #288). Vocabulário, rótulos, assinatura e estados vêm da API
// (dono: api/app/domain/resistance_profile.py); aqui não há código nem regra de vocabulário.
import { apiFetch } from './api';
import { pollAsyncJob } from './async-job-poll';

export type VocabularyMap = Record<string, string>;

/** Componentes que viram seletores, na ordem em que a API os exporta. */
export const COMPONENT_FIELDS = ['source', 'mechanism', 'trajectory', 'profile', 'line', 'direction_changes'] as const;
export type ComponentField = (typeof COMPONENT_FIELDS)[number];

export type ResistanceVocabulary = { version: string } & Record<ComponentField | 'flags' | 'confidence' | 'status' | 'origin', VocabularyMap> & {
    /** Estados que as ações rápidas gravam (decididos pelo dono, não deduzidos da ordem). */
    actions: { approve: string; reject: string };
    /** Estado inicial do admin ao criar/editar par sem estado (decidido pelo dono). */
    default_status: string;
    pending_statuses: string[];
    human_review_statuses: string[];
    /** Teto de pares por pedido de sugestão por IA. */
    suggest_max_pairs: number;
    /** Pares por job (cada clique vira vários jobs). */
    suggest_pairs_per_job: number;
  };

export type ResistanceCoverage = {
  vocabulary_version: string;
  active_pairs: number;
  with_profile: number;
  without_profile: number;
  by_status: Record<string, number>;
  unknown: number;
};

export type ResistanceProfile = Record<ComponentField, string> & {
  equipment_exercise_id: number;
  equipment_id: number;
  equipment_name: string;
  exercise_id: number;
  exercise_name: string;
  has_profile: boolean;
  flags: string[];
  confidence: string | null;
  status: string | null;
  origin: string | null;
  rationale: string | null;
  evidence: string | null;
  reliable: boolean;
  signature: string | null;
  signature_label: string | null;
  updated_at: string | null;
};

export type ResistanceProfileList = { items: ResistanceProfile[]; total: number; limit: number; offset: number };

export type ResistanceFilters = {
  status?: string | null;
  confidence?: string | null;
  equipment_id?: string | null;
  exercise_id?: string | null;
  pending?: boolean;
  limit?: number;
  offset?: number;
};

export type ResistanceProfilePayload = Partial<Record<ComponentField, string>> & {
  flags: string[];
  confidence: string | null;
  status: string;
  rationale: string | null;
  evidence: string | null;
  reason: string | null;
};

export type ResistanceHistoryEntry = {
  id: number;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  actor_user_id: number | null;
  origin: string;
  reason: string | null;
  created_at: string;
};

export type SuggestDispatch = { job_ids?: string[]; job_id: string | null; pairs: number; failed_batches?: number };
export type SuggestResult = {
  processed: number;
  suggested: number;
  skipped: number;
  failed: { id: number; code: string }[];
};

export const resistanceKeys = {
  all: ['resistance-profiles'] as const,
  vocabulary: ['resistance-profiles', 'vocabulary'] as const,
  coverage: ['resistance-profiles', 'coverage'] as const,
  list: (f: ResistanceFilters) => ['resistance-profiles', 'list', f] as const,
  history: (id: number) => ['resistance-profiles', 'history', id] as const,
};

export function buildListQuery(f: ResistanceFilters): string {
  const p = new URLSearchParams();
  if (f.status) p.set('status', f.status);
  if (f.confidence) p.set('confidence', f.confidence);
  if (f.equipment_id) p.set('equipment_id', f.equipment_id);
  if (f.exercise_id) p.set('exercise_id', f.exercise_id);
  if (f.pending) p.set('pending', 'true');
  if (f.limit != null) p.set('limit', String(f.limit));
  if (f.offset) p.set('offset', String(f.offset));
  const q = p.toString();
  return q ? `?${q}` : '';
}

export const fetchVocabulary = () => apiFetch<ResistanceVocabulary>('/resistance-profiles/vocabulary');
export const fetchCoverage = () => apiFetch<ResistanceCoverage>('/resistance-profiles/coverage');
export const fetchProfiles = (f: ResistanceFilters) => apiFetch<ResistanceProfileList>(`/resistance-profiles${buildListQuery(f)}`);
export const fetchHistory = (id: number) =>
  apiFetch<{ items: ResistanceHistoryEntry[] }>(`/resistance-profiles/${id}/history`);
export const putProfile = (id: number, payload: ResistanceProfilePayload) =>
  apiFetch<ResistanceProfile>(`/resistance-profiles/${id}`, { method: 'PUT', body: JSON.stringify(payload) });

/** Sem ids a API escolhe os pendentes (e o teto dela vale); com ids, o teto vem do 422 da API. */
export function buildSuggestBody(ids: number[] | null): { equipment_exercise_ids?: number[] } {
  return ids && ids.length > 0 ? { equipment_exercise_ids: ids } : {};
}

/** Código de `failed` que a API usa para par que o job não chegou a processar (dono: resistance_profile_ai.py). */
const NOT_PROCESSED_CODE = 'not_processed';

export type SuggestSummary = SuggestResult & {
  /** Pares que um job não chegou a processar (repetir o pedido). */
  not_processed: number;
  batches: number;
  /** Lotes que a API não despachou, ou cujo job falhou/expirou. */
  failed_batches: number;
};

/**
 * Dispara os jobs (um por lote) e acompanha todos em paralelo, somando os resultados. Job que falha ou expira conta
 * como lote com erro sem derrubar os demais. `null` = nada pendente (a API não criou job).
 */
export async function suggestProfiles(
  ids: number[] | null,
  onProgress?: (done: number, total: number) => void,
): Promise<SuggestSummary | null> {
  const dispatch = await apiFetch<SuggestDispatch>('/resistance-profiles/suggest', {
    method: 'POST',
    body: JSON.stringify(buildSuggestBody(ids)),
  });
  const jobIds = dispatch.job_ids ?? (dispatch.job_id ? [dispatch.job_id] : []);
  const dispatchFailed = dispatch.failed_batches ?? 0;
  if (jobIds.length === 0 && dispatchFailed === 0) return null;
  const total = jobIds.length + dispatchFailed;
  let done = dispatchFailed;
  onProgress?.(done, total);
  const summary: SuggestSummary = {
    processed: 0, suggested: 0, skipped: 0, failed: [], not_processed: 0, batches: total, failed_batches: dispatchFailed,
  };
  await Promise.all(
    jobIds.map(async (id) => {
      try {
        const r = await pollAsyncJob<SuggestResult>(id, {
          failedFallbackMessage: 'A sugestão por IA falhou. Tente novamente.',
          timeoutMessage: 'Tempo esgotado aguardando a sugestão por IA. Atualize a lista em instantes.',
        });
        summary.processed += r.processed;
        summary.suggested += r.suggested;
        summary.skipped += r.skipped;
        summary.failed.push(...r.failed);
        summary.not_processed += r.failed.filter((f) => f.code === NOT_PROCESSED_CODE).length;
      } catch {
        summary.failed_batches += 1;
      }
      onProgress?.(++done, total);
    }),
  );
  return summary;
}

/** Payload de substituição completa a partir do perfil atual, trocando só o estado. */
export function payloadWithStatus(p: ResistanceProfile, status: string, reason: string | null): ResistanceProfilePayload {
  return {
    source: p.source,
    mechanism: p.mechanism,
    trajectory: p.trajectory,
    profile: p.profile,
    line: p.line,
    direction_changes: p.direction_changes,
    flags: p.flags,
    confidence: p.confidence,
    status,
    rationale: p.rationale,
    evidence: p.evidence,
    reason,
  };
}
