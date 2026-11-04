/**
 * Falha de download de chunk (rede, rota nunca visitada offline) ou de
 * `import()` dinamico: so isso vira "Sem conexao". Qualquer outro erro de
 * render deve subir para o `error.tsx` do app.
 */
const CHUNK_MESSAGE =
  /loading (css )?chunk|dynamically imported module|importing a module script failed|failed to fetch dynamically/i;

export function isChunkLoadError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false;
  const { name, message } = error as { name?: unknown; message?: unknown };
  if (name === 'ChunkLoadError') return true;
  return typeof message === 'string' && CHUNK_MESSAGE.test(message);
}
