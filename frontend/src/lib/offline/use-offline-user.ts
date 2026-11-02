'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, apiFetch } from '../api';
import type { SessionUser } from '../auth';
import { activateOffline, opQueue } from './queue';
import { knownUserForOffline } from './scope';
import type { LegacyOwnership } from './session-store';
import { acquireWriterLock } from './tab-lock';

/** 'locked' = o treino deste usuário já está aberto em OUTRA aba (único escritor). */
export type OfflineUserState = 'loading' | 'ready' | 'error' | 'locked';

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

/** Falha que não diz nada sobre a sessão: rede (0) ou serviço indisponível/erro de servidor (5xx). */
export function isTransientSessionFailure(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 0 || error.status >= 500);
}

/**
 * Descobre o usuário da sessão (`/auth/me`) e ativa a partição offline dele
 * (hidrata o espelho e migra o localStorage legado). O treino só deve renderizar
 * com `state === 'ready'`. Chamar de novo (rotação, remontagem) é idempotente.
 *
 * - Falha transitória (rede/5xx) com usuário conhecido e recente: abre com os
 *   dados locais; 401/403 nunca usam o fallback (o `apiFetch` já redireciona).
 * - Depois de `ready`, falha de revalidação NÃO derruba o treino.
 * - Só uma aba escreve por usuário (Web Locks); a outra fica em `locked`.
 */
export function useOfflineUser(): { state: OfflineUserState; userId: number | null; retry: () => void } {
  const [state, setState] = useState<OfflineUserState>('loading');
  const [userId, setUserId] = useState<number | null>(null);
  const [attempt, setAttempt] = useState(0);
  const readyRef = useRef(false);
  const forceReloadRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    if (!readyRef.current) setState('loading');

    const open = async (id: number, verified: boolean) => {
      const lock = await acquireWriterLock(id);
      if (cancelled) return;
      if (lock.role === 'follower') {
        readyRef.current = false;
        setState('locked');
        void lock.whenOwner.then(() => {
          if (cancelled) return;
          forceReloadRef.current = true; // a outra aba pode ter escrito: relê do disco
          setAttempt((value) => value + 1);
        });
        return;
      }
      const force = forceReloadRef.current;
      forceReloadRef.current = false;
      await activateOffline(id, { verified, force, ...(verified ? { validateLegacySession: checkLegacySessionOwnership } : {}) });
      if (cancelled) return;
      readyRef.current = true;
      setUserId(id);
      setState('ready');
    };

    apiFetch<SessionUser>('/auth/me')
      .then((me) => open(me.id, true))
      .catch(async (error: unknown) => {
        if (cancelled) return;
        // Já aberto: uma revalidação que falha (503, rede...) não derruba o treino.
        if (readyRef.current && isTransientSessionFailure(error)) return;
        const known = isTransientSessionFailure(error) ? knownUserForOffline() : null;
        if (known != null) {
          try {
            await open(known, false);
            return;
          } catch {
            // cai no erro recuperável
          }
        }
        if (!cancelled && !readyRef.current) setState('error');
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  // Gatilhos de sincronização (online, voltar ao primeiro plano, reabrir, tick).
  useEffect(() => {
    if (state !== 'ready') return undefined;
    return opQueue.start();
  }, [state, userId]);

  // Voltou a rede: revalida a conta de verdade (troca de usuário purga a anterior).
  useEffect(() => {
    const onOnline = () => setAttempt((value) => value + 1);
    window.addEventListener('online', onOnline);
    return () => window.removeEventListener('online', onOnline);
  }, []);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);
  return { state, userId, retry };
}
