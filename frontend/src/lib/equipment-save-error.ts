import { ApiError } from './api';

export const EQUIPMENT_SAVE_FORBIDDEN_MESSAGE = 'Ação restrita a administradores.';
export const EQUIPMENT_SAVE_FALLBACK_MESSAGE = 'Não foi possível salvar o equipamento.';

/** Mensagem do formulário de equipamento: 403 → permissão; ApiError → texto do servidor; resto → fallback. */
export function equipmentSaveErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 403) return EQUIPMENT_SAVE_FORBIDDEN_MESSAGE;
  return error instanceof ApiError ? error.message : EQUIPMENT_SAVE_FALLBACK_MESSAGE;
}
