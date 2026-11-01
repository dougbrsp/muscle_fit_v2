'use client';

import { useCallback, useEffect, useState } from 'react';
import { ApiError, apiFetch } from '../api';
import type { SessionUser } from '../auth';
import { activateOfflineUser, type LegacyOwnership } from './session-store';

export type OfflineUserState = 'loading' | 'ready' | 'error';

/** `GET /activities/{id}` é filtrado por usuário no backend: 200 = da conta atual, 404/403 = de outra. */
export async function checkLegacySessionOwnership(sessionId: number): Promise<LegacyOwnership> {
  try {
    await apiFetch(`/activities/${sessionId}`);
    return 'owned';
  } catch (error) {
    if (error instanceof ApiError && (error.status === 404 || error.status === 403)) return 'foreign';
    return 'unknown';
  }
}

/**
 * Descobre o usuário da sessão (`/auth/me`) e ativa a partição offline dele
 * (hidrata o espelho e migra o localStorage legado). O treino só deve renderizar
 * com `state === 'ready'`. Chamar de novo (rotação, remontagem) é idempotente.
 */
export function useOfflineUser(): { state: OfflineUserState; userId: number | null; retry: () => void } {
  const [state, setState] = useState<OfflineUserState>('loading');
  const [userId, setUserId] = useState<number | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState('loading');
    apiFetch<SessionUser>('/auth/me')
      .then(async (me) => {
        await activateOfflineUser(me.id, { validateLegacySession: checkLegacySessionOwnership });
        if (cancelled) return;
        setUserId(me.id);
        setState('ready');
      })
      .catch(() => {
        if (!cancelled) setState('error');
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);
  return { state, userId, retry };
}
