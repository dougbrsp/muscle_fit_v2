// Mensagem de erro ao salvar ficha: o servidor devolve 422 com `detail` OBJETO
// (`{reason, message}`, ex.: `muscle_group_locked`) e o `apiFetch` mantém a
// mensagem genérica nesse caso. Aqui só se lê o texto pronto do servidor.
import { ApiError } from './api';

export function planSaveErrorMessage(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return fallback;
  const detail = error.detail as { message?: unknown } | null | undefined;
  const message = detail && typeof detail === 'object' ? detail.message : undefined;
  return typeof message === 'string' && message.trim() ? message : error.message;
}
