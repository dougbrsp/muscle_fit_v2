import { ApiError } from '@/lib/api';

export type TemplateSaveError = {
  titulo: string;
  mensagem: string;
  /** Caminho do campo apontado pelo servidor (`slots.N.items.M.campo`), quando houver. */
  campo?: string;
  /** Só o bloqueio de segurança fica na tela até o usuário fechar. */
  seguranca: boolean;
};

/** `code` do 422 de segurança do servidor (#237); escrito à mão, como `MUST_CHANGE_PASSWORD_CODE`. */
export const TEMPLATE_SAFETY_BLOCKED_CODE = 'template_safety_blocked';
// Reserva para resposta antiga (durante o deploy): prefixo do 422 de segurança do servidor (`workout_plan_templates.py`, D14):
// `detail` string, sem `loc`. A validação do FastAPI/Pydantic traz `detail` lista.
const SAFETY_PREFIX = 'Itens novos ou alterados com incompatibilidade de segurança';
const PYDANTIC_PREFIX = /^Value error,\s*/i;

type ValidationItem = { loc?: unknown; msg?: unknown };

function validationFirst(detail: unknown): ValidationItem | null {
  if (!Array.isArray(detail) || detail.length === 0) return null;
  const first = detail[0];
  return first && typeof first === 'object' ? (first as ValidationItem) : null;
}

/** Erro de "Salvar"/"Publicar"/"Salvar rascunho" do Modelo -> título, mensagem e campo. */
export function templateSaveError(error: unknown, fallback: string, defaultTitle = 'Erro ao salvar'): TemplateSaveError {
  if (!(error instanceof ApiError)) return { titulo: defaultTitle, mensagem: fallback, seguranca: false };
  if (error.status !== 422) {
    const d = error.detail as { message?: unknown } | null | undefined;
    const mensagem = typeof d?.message === 'string' && d.message ? d.message : error.message;
    return { titulo: defaultTitle, mensagem, seguranca: false };
  }
  const first = validationFirst(error.detail);
  if (first) {
    const msg = typeof first.msg === 'string' ? first.msg.replace(PYDANTIC_PREFIX, '') : fallback;
    const loc = Array.isArray(first.loc) ? first.loc.map(String) : [];
    const at = loc.join('.').match(/slots\.(\d+)\.items\.(\d+)(?:\.(.+))?$/);
    if (at) {
      return {
        titulo: 'Dados inválidos',
        mensagem: `Slot ${Number(at[1]) + 1}, item ${Number(at[2]) + 1}: ${msg}`,
        campo: loc.slice(loc.indexOf('slots')).join('.'),
        seguranca: false,
      };
    }
    return { titulo: 'Dados inválidos', mensagem: msg, seguranca: false };
  }
  if (error.code === TEMPLATE_SAFETY_BLOCKED_CODE || error.message.startsWith(SAFETY_PREFIX)) {
    return { titulo: 'Bloqueado por segurança', mensagem: error.message, seguranca: true };
  }
  // 422 de regra de negócio do servidor (detail string em português).
  return { titulo: 'Dados inválidos', mensagem: error.message, seguranca: false };
}
