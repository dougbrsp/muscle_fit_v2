/**
 * "Esqueci minha senha" (M5) — helpers puros das páginas públicas
 * `/esqueci-senha` e `/redefinir-senha`. Sem fetch aqui: só a montagem do
 * payload e a leitura do token, para serem testáveis com vitest.
 */

export const MIN_PASSWORD_LENGTH = 8;

export interface PasswordResetRequestPayload {
  email?: string;
  username?: string;
  captcha_token: string;
}

/**
 * Um único campo na tela ("usuário ou e-mail"): com `@` vira `email`, senão
 * `username` — mesmo contrato de `POST /auth/password-reset/request`.
 * Devolve `null` se o identificador estiver vazio.
 */
export function buildPasswordResetRequestPayload(
  identifier: string,
  captchaToken: string,
): PasswordResetRequestPayload | null {
  const value = identifier.trim();
  if (!value) return null;
  const base = value.includes('@') ? { email: value.toLowerCase() } : { username: value };
  return { ...base, captcha_token: captchaToken };
}

/** Token da URL do e-mail (`/redefinir-senha?token=...`); `null` se ausente/vazio. */
export function readResetToken(search: URLSearchParams | null): string | null {
  const token = search?.get('token')?.trim();
  return token ? token : null;
}

/** Validação local da nova senha (a política real é a da API: mínimo 8). */
export function validateNewPassword(password: string, confirmation: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `A nova senha deve ter no mínimo ${MIN_PASSWORD_LENGTH} caracteres.`;
  }
  if (password !== confirmation) {
    return 'As senhas não conferem.';
  }
  return null;
}
