// Persistência LOCAL do Modo Treino (`window.localStorage`): sessão ativa,
// snapshot "estacionado" ao sair pelo "X" e preferência da IA por série.
// Separado de `workout-mode-types.ts` (PR B da Evolução ZEPP) para que os
// tipos e helpers puros não dependam de APIs do navegador: o núcleo
// compartilhado com o relógio (`workout-mode-core/`) não importa este módulo.
// Comportamento idêntico ao de antes do split.

import {
  isOfflineStorePurged,
  isOfflineStoreReady,
  readParked,
  readSession,
  writeParked,
  writeSession,
} from './offline/session-store';
import type { StoredWorkoutModeSession } from './workout-mode-types';

// Fachada: com usuário ativo (`activateOfflineUser`, feito por WorkoutModeClient
// antes de renderizar o treino) a persistência é o IndexedDB escopado por
// usuário (`lib/offline`). Sem usuário ativo nesta página (testes, código
// legado) mantém o localStorage de antes; depois de um purge (logout/401) as
// gravações são descartadas e nunca voltam para a chave sem escopo.

const STORAGE_KEY = 'musclefit.workout-mode-session';

export function loadStoredSession(): StoredWorkoutModeSession | null {
  if (isOfflineStoreReady()) return readSession();
  if (isOfflineStorePurged()) return null;
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed.session_id !== 'number') return null;
    return parsed as StoredWorkoutModeSession;
  } catch {
    return null;
  }
}

export function saveStoredSession(session: StoredWorkoutModeSession): void {
  if (isOfflineStoreReady()) return writeSession(session);
  if (isOfflineStorePurged()) return;
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
}

/**
 * `briefed_keys` da sessão salva -> conjunto. Tolerante a sessão antiga (sem
 * o campo) e a lixo no localStorage: só strings não vazias entram.
 */
export function restoreBriefedKeys(session: Pick<StoredWorkoutModeSession, 'briefed_keys'> | null | undefined): Set<string> {
  const raw = session?.briefed_keys;
  if (!Array.isArray(raw)) return new Set();
  return new Set(raw.filter((key): key is string => typeof key === 'string' && key.length > 0));
}

export function clearStoredSession(): void {
  if (isOfflineStoreReady()) return writeSession(null);
  if (isOfflineStorePurged()) return;
  if (typeof window === 'undefined') return;
  window.localStorage.removeItem(STORAGE_KEY);
}

// Snapshot da sessão local guardado ao SAIR do modo treino pelo "X" (a
// sessão continua `in_progress` no backend). Sem ele, "Retomar" recriava a
// sessão só com os campos da activity e perdia `advanced_mode` (sumiam
// aquecimento/ajuste), skips, overrides e o progresso local das sub-etapas.
// Consumido por `mergeResumedSession` (`workout-mode-plan-flow.ts`) na
// retomada; descartado ao iniciar/encerrar qualquer sessão.
const PARKED_STORAGE_KEY = 'musclefit.workout-mode-parked-session';

export function parkStoredSession(): void {
  if (isOfflineStoreReady()) {
    const current = readSession();
    if (current) writeParked(current);
    writeSession(null);
    return;
  }
  if (isOfflineStorePurged()) return;
  if (typeof window === 'undefined') return;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (raw) window.localStorage.setItem(PARKED_STORAGE_KEY, raw);
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage indisponível: a retomada cai no fallback só com dados do servidor.
  }
}

export function loadParkedSession(): StoredWorkoutModeSession | null {
  if (isOfflineStoreReady()) return readParked();
  if (isOfflineStorePurged()) return null;
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(PARKED_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed.session_id !== 'number') return null;
    return parsed as StoredWorkoutModeSession;
  } catch {
    return null;
  }
}

export function clearParkedSession(): void {
  if (isOfflineStoreReady()) return writeParked(null);
  if (isOfflineStorePurged()) return;
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.removeItem(PARKED_STORAGE_KEY);
  } catch {
    // ignore
  }
}

// Preferência do usuário (liga/desliga) para a análise de IA disparada após
// CADA série (`POST /exercises/evaluate-set`) — persistida em localStorage.
// Default DESLIGADO quando a chave não existe: quando desligado, o frontend
// nem chama o endpoint (economiza custo/latência de OpenAI). Não afeta os
// resumos de fim de exercício/treino (sempre incondicionais).
const PER_SET_AI_PREFERENCE_KEY = 'musclefit.workout-mode-per-set-ai-enabled';

export function loadPerSetAiPreference(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(PER_SET_AI_PREFERENCE_KEY) === 'true';
  } catch {
    return false;
  }
}

export function savePerSetAiPreference(enabled: boolean): void {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(PER_SET_AI_PREFERENCE_KEY, enabled ? 'true' : 'false');
}
