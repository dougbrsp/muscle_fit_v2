import { getApiBaseUrl } from './config';

export interface SessionUser {
  id: number;
  username: string;
  full_name: string | null;
  role: string;
  must_change_password: boolean;
  intervals_configured: boolean;
  /** Fuso IANA em que o dashboard resolve "hoje" (migração 044, F0.1). */
  timezone: string;
  /**
   * Feature flags do ambiente (não do usuário). Opcional: backend anterior ao
   * PR3 dos cards não manda o campo — ausente = desligado.
   * `exercise_cards`: geração de cards ilustrados (`EXERCISE_CARD_GENERATION_ENABLED`).
   */
  features?: { exercise_cards?: boolean };
  /**
   * Perfil antropométrico (nutrição L0, migração 074) — só leitura aqui; a
   * escrita é `PUT /users/me` (tela "Meu perfil", L2a).
   * Opcionais: backend anterior ao L0 não manda; `null` = não informado.
   * `sex` e `activity_level` saem no código do motor (`male`/`female`/`other`,
   * `sedentary`…`very_active`; rótulos em `nutrition-constants.ts`), já
   * normalizados pela API desde o L2a (migração 077).
   */
  sex?: string | null;
  birth_date?: string | null;
  height_cm?: number | null;
  activity_level?: string | null;
}

export interface LoginResult {
  user: SessionUser;
}

export class AuthError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'AuthError';
    this.status = status;
  }
}

/**
 * Chama POST /auth/login diretamente na API (sem proxy — o frontend é
 * estático, servido via S3+CloudFront). A API responde com um cookie httpOnly
 * de sessão (`Set-Cookie: musclefit_session=...`, ver `api/app/auth.py`) que
 * o navegador passa a enviar automaticamente em toda chamada feita com
 * `credentials: 'include'` (ver `apiFetch`/`apiFetchBlob` em `./api.ts`).
 *
 * O token de sessão em si nunca fica acessível a JavaScript no navegador —
 * diferente do modelo anterior, que guardava o token cru em localStorage.
 * `login()` não persiste mais nada localmente: quem chama decide o que fazer
 * com `result.user` (ex.: redirecionar por `must_change_password`).
 */
export async function login(username: string, password: string): Promise<LoginResult> {
  let response: Response;
  try {
    response = await fetch(`${getApiBaseUrl()}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ username, password }),
    });
  } catch {
    throw new AuthError('Não foi possível conectar à API. Verifique sua conexão e tente novamente.', 0);
  }

  if (!response.ok) {
    let detail = 'Usuário ou senha inválidos.';
    try {
      const body = await response.json();
      if (typeof body?.detail === 'string') {
        detail = body.detail;
      }
    } catch {
      // corpo sem JSON válido — mantém a mensagem padrão
    }
    throw new AuthError(detail, response.status);
  }

  return (await response.json()) as LoginResult;
}

// Chaves usadas antes da Fase 3 (cookie httpOnly), quando o token de sessão
// vivia em localStorage. Breaking change aceito: sessões antigas guardadas
// aqui deixam de valer, sem migração — clearSession() só varre e remove
// qualquer resquício para não deixar lixo no navegador do usuário.
const LEGACY_TOKEN_STORAGE_KEY = 'musclefit.session_token';
const LEGACY_USER_STORAGE_KEY = 'musclefit.session_user';

/**
 * Limpa qualquer estado de sessão do lado do cliente. O cookie httpOnly em
 * si só pode ser limpo pelo servidor (`POST /auth/logout`, ou expirando
 * naturalmente) — inacessível a JS por definição (HttpOnly). Chamado após
 * logout explícito e pelo interceptor de 401 em `apiFetch`.
 */
export function clearSession(): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.removeItem(LEGACY_TOKEN_STORAGE_KEY);
    window.localStorage.removeItem(LEGACY_USER_STORAGE_KEY);
  } catch {
    // localStorage indisponível (modo privado, cookies desabilitados etc.).
  }
}
