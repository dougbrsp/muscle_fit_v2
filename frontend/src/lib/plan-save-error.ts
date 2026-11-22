// Mensagem de erro ao salvar ficha: o servidor devolve `detail` OBJETO
// (`{reason, message}`, ex.: `muscle_group_locked`) ou LISTA do Pydantic
// (`[{loc, msg: "Value error, ..."}]`), e o `apiFetch` mantém a mensagem genérica
// nesses casos. Aqui só se lê o texto pronto do servidor.
import { ApiError } from './api';

const PYDANTIC_PREFIX = 'Value error, ';

function detailText(detail: unknown): string | null {
  const first = Array.isArray(detail) ? detail[0] : detail;
  if (!first || typeof first !== 'object') return null;
  const { message, msg } = first as { message?: unknown; msg?: unknown };
  const text = typeof message === 'string' ? message : typeof msg === 'string' ? msg : '';
  const clean = text.startsWith(PYDANTIC_PREFIX) ? text.slice(PYDANTIC_PREFIX.length) : text;
  return clean.trim() ? clean : null;
}

export function planSaveErrorMessage(error: unknown, fallback: string): string {
  if (!(error instanceof ApiError)) return fallback;
  return detailText(error.detail) ?? error.message;
}
