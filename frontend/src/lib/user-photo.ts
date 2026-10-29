import { getApiBaseUrl } from './config';

/**
 * GET /users/{id}/photo exige sessão autenticada (cookie httpOnly, ver
 * C:\muscle_fit\api\app\users.py) — mesmo padrão de `gym-logo.ts`/
 * `equipment-photo.ts`: busca a foto como blob autenticado
 * (`credentials: 'include'`) e devolve uma Object URL para uso em `<img>`.
 *
 * A tela Usuários deixa o próprio caller "invalidar" a foto de um usuário
 * incrementando `version` (ver `UsuariosClient.tsx`) após upload/remoção —
 * a tabela `users` não expõe um `updated_at` confiável para basear o cache
 * nele como o `gym-logo.ts` faz.
 *
 * Retorna `null` em qualquer falha (404 sem foto, rede, etc.) — quem chama
 * trata como "sem foto disponível", nunca como erro bloqueante.
 */
const photoUrlCache = new Map<string, string>();
const inFlight = new Map<string, Promise<string | null>>();

export function fetchUserPhotoUrl(userId: number, version = 0): Promise<string | null> {
  const key = `${userId}:${version}`;

  const cached = photoUrlCache.get(key);
  if (cached) return Promise.resolve(cached);

  const pending = inFlight.get(key);
  if (pending) return pending;

  const promise = (async () => {
    try {
      const response = await fetch(`${getApiBaseUrl()}/users/${userId}/photo`, {
        credentials: 'include',
      });
      if (!response.ok) return null;
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      photoUrlCache.set(key, objectUrl);
      return objectUrl;
    } catch {
      return null;
    } finally {
      inFlight.delete(key);
    }
  })();

  inFlight.set(key, promise);
  return promise;
}
