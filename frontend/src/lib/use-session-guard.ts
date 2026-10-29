'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from './api';
import { classifySessionCheckError } from './session-guard';

export type SessionGuardState = 'checking' | 'ok' | 'unavailable';

/**
 * Guarda de sessão compartilhada pelos layouts autenticados `(app)` e
 * `(fullscreen)`. A sessão vive num cookie httpOnly (inacessível a JS), então
 * a checagem é uma chamada real a `GET /auth/me`.
 *
 * - sucesso -> `ok`;
 * - 401 -> continua em `checking` (loader): `apiFetch` já redireciona;
 * - qualquer outra falha -> `unavailable` + `retry()`, sem deslogar (M56,
 *   ver `classifySessionCheckError`).
 */
export function useSessionGuard(): { state: SessionGuardState; error: unknown; retry: () => void } {
  const [state, setState] = useState<SessionGuardState>('checking');
  const [error, setError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState('checking');
    apiFetch('/auth/me')
      .then(() => {
        if (!cancelled) setState('ok');
      })
      .catch((err: unknown) => {
        if (cancelled || classifySessionCheckError(err) === 'unauthenticated') return;
        setError(err);
        setState('unavailable');
      });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);
  return { state, error, retry };
}
