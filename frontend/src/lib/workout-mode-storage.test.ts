import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { loadParkedSession, loadStoredSession, restoreBriefedKeys, saveStoredSession } from './workout-mode-storage';
import { mergeResumedSession } from './workout-mode-plan-flow';
import type { StoredWorkoutModeSession } from './workout-mode-types';

// Ambiente `node` (sem jsdom): um localStorage mínimo em memória.
function installLocalStorage(): Map<string, string> {
  const store = new Map<string, string>();
  (globalThis as unknown as { window: unknown }).window = {
    localStorage: {
      getItem: (key: string) => (store.has(key) ? (store.get(key) as string) : null),
      setItem: (key: string, value: string) => void store.set(key, String(value)),
      removeItem: (key: string) => void store.delete(key),
    },
  };
  return store;
}

// Sessão como era gravada ANTES do campo `briefed_keys` (novo visual, PR1/3).
const OLD_SESSION_JSON = JSON.stringify({
  session_id: 230,
  source: 'plan',
  workout_plan_id: 119,
  gym_id: 4,
  skipped_exercise_ids: [12],
  cancelled_exercise_ids: [],
  jump_target_exercise_id: null,
  equipment_overrides: {},
  advanced_mode: true,
});

describe('StoredWorkoutModeSession.briefed_keys — compatibilidade', () => {
  let store: Map<string, string>;
  beforeEach(() => {
    store = installLocalStorage();
  });
  afterEach(() => {
    delete (globalThis as unknown as { window?: unknown }).window;
  });

  it('sessão antiga sem o campo continua carregando, com nenhum briefing lido', () => {
    store.set('musclefit.workout-mode-session', OLD_SESSION_JSON);
    const session = loadStoredSession();
    expect(session?.session_id).toBe(230);
    expect(session?.advanced_mode).toBe(true);
    expect(session?.briefed_keys).toBeUndefined();
    expect(restoreBriefedKeys(session).size).toBe(0);
  });

  it('ida e volta: briefed_keys gravado é lido de novo', () => {
    const session: StoredWorkoutModeSession = { session_id: 231, source: 'plan', briefed_keys: ['wpe-1', 'mapping-9'] };
    saveStoredSession(session);
    expect(Array.from(restoreBriefedKeys(loadStoredSession()))).toEqual(['wpe-1', 'mapping-9']);
  });

  it('valor estranho no storage não quebra: só strings não vazias', () => {
    expect(restoreBriefedKeys({ briefed_keys: 'wpe-1' as unknown as string[] }).size).toBe(0);
    expect(Array.from(restoreBriefedKeys({ briefed_keys: ['wpe-1', '', 3 as unknown as string] }))).toEqual(['wpe-1']);
    expect(restoreBriefedKeys(null).size).toBe(0);
  });

  it('retomada pelo snapshot estacionado preserva briefed_keys', () => {
    store.set(
      'musclefit.workout-mode-parked-session',
      JSON.stringify({ session_id: 232, source: 'plan', briefed_keys: ['wpe-5'] }),
    );
    const merged = mergeResumedSession({ session_id: 232, source: 'plan', workout_plan_id: 7 }, loadParkedSession());
    expect(merged.briefed_keys).toEqual(['wpe-5']);
    expect(merged.workout_plan_id).toBe(7);
  });
});
