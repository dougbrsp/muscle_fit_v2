import { ApiError } from './api';
import type { CardioApplyResultItem, CardioRecommendationView } from './analytics-types';

/**
 * Apoio da tela da recomendação de cardio estruturada (M98 C3b). Nenhuma
 * regra de cardio aqui: quem decide se a sessão pode ir para o Modelo, o que
 * muda no rascunho e as mensagens é o backend (`cardio_recommendation_v3.
 * build_view` e `POST /cardio-recommendations/{id}/apply`). Isto só escolhe o
 * que mostrar a partir do que a API devolveu.
 */

/** Datas das sessões que a API marcou como aplicáveis ("Aplicar todas"). */
export function applicableDates(view: CardioRecommendationView | null | undefined): string[] {
  return (view?.sessions ?? []).filter((session) => session.applicable).map((session) => session.date);
}

/** Caminho do editor do Modelo, onde o usuário revisa e publica o rascunho. */
export function templateEditorPath(templateId: number): string {
  return `/modelos-treino/${templateId}`;
}

const RESULT_COLORS: Record<CardioApplyResultItem['status'], string> = {
  applied: 'green',
  already_applied: 'gray',
  rejected: 'red',
};

export function applyResultColor(status: CardioApplyResultItem['status']): string {
  return RESULT_COLORS[status] ?? 'gray';
}

/** Mensagem de um erro do apply: o `detail.message` do 409/404, senão a do `ApiError`. */
export function applyErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.detail as { message?: unknown } | undefined;
    if (detail && typeof detail.message === 'string') return detail.message;
    return error.message;
  }
  return 'Não foi possível aplicar no Modelo.';
}

/** Rótulo do vínculo da sessão com o slot do Modelo. */
export function planActionLabel(action: 'replace' | 'add' | null, slotName: string | null): string | null {
  if (!action || !slotName) return null;
  return action === 'replace' ? `Substitui o cardio de ${slotName}` : `Acrescenta cardio em ${slotName}`;
}
