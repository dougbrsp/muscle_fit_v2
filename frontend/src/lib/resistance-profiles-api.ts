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

export type SuggestDispatch = { job_id: string | null; pairs: number };
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

/** Dispara o job e espera o resultado. `null` = nada pendente (a API não criou job). */
export async function suggestProfiles(ids: number[] | null): Promise<SuggestResult | null> {
  const dispatch = await apiFetch<SuggestDispatch>('/resistance-profiles/suggest', {
    method: 'POST',
    body: JSON.stringify(buildSuggestBody(ids)),
  });
  if (!dispatch.job_id) return null;
  return pollAsyncJob<SuggestResult>(dispatch.job_id, {
    failedFallbackMessage: 'A sugestão por IA falhou. Tente novamente.',
    timeoutMessage: 'Tempo esgotado aguardando a sugestão por IA. Atualize a lista em instantes.',
  });
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
