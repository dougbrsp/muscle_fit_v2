// Esquema de chaves do armazenamento offline (plano mobile 3.5). Toda chave é
// prefixada por versão do esquema e por userId: um usuário nunca lê a partição
// de outro (sem leitura cruzada entre contas). Sem regra de negócio aqui.

export const DB_NAME = 'musclefit';
/** Versão do banco IndexedDB; cada incremento exige uma entrada em `MIGRATIONS` (backend.ts). */
export const DB_VERSION = 1;
/** Versão do formato dos registros (prefixo das chaves). */
export const SCHEMA_VERSION = 1;

export const STORE_NAMES = ['workout_session', 'op_queue', 'meta'] as const;
export type StoreName = (typeof STORE_NAMES)[number];

/** Marcador (só o id numérico, sem dados) do último usuário ativo neste aparelho. */
export const ACTIVE_USER_MARKER_KEY = 'musclefit.offline.active-user';

/** Chaves legadas do localStorage (anteriores ao escopo por usuário). */
export const LEGACY_SESSION_KEY = 'musclefit.workout-mode-session';
export const LEGACY_PARKED_KEY = 'musclefit.workout-mode-parked-session';

export function userPrefix(userId: number): string {
  return `v${SCHEMA_VERSION}:${userId}:`;
}

export function makeKey(userId: number, name: string): string {
  return `${userPrefix(userId)}${name}`;
}
