import { getApiBaseUrl } from './config';
import { registerActiveWorkProvider } from './mobile/active-work';
import { LEGACY_PARKED_KEY, LEGACY_SESSION_KEY } from './offline/keys';

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
   * Permissões por grupos (RBAC, ADR 0011): `{recurso: {acao: escopo}}`, só com
   * recursos que têm alguma ação. `null` = backend de acesso indisponível;
   * ausente = backend anterior ao RBAC. Em ambos os casos o frontend esconde.
   */
  permissions?: Record<string, Record<string, string>> | null;
  /** Chaves dos grupos do usuário (`null`/ausente como em `permissions`). */
  groups?: string[] | null;
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
export function clearSession(options: { keepUnsynced?: boolean } = {}): Promise<void> {
  if (typeof window === 'undefined') return Promise.resolve();
  // Dados locais do treino da conta (IndexedDB + chaves legadas): a parte
  // síncrona roda já; a Promise devolvida conclui a limpeza do banco.
  try {
    window.localStorage.removeItem(LEGACY_TOKEN_STORAGE_KEY);
    window.localStorage.removeItem(LEGACY_USER_STORAGE_KEY);
    // Chaves do treino SEM escopo de usuário (anteriores ao IndexedDB): saem já.
    window.localStorage.removeItem(LEGACY_SESSION_KEY);
    window.localStorage.removeItem(LEGACY_PARKED_KEY);
  } catch {
    // localStorage indisponível (modo privado, cookies desabilitados etc.).
  }
  // O restante (IndexedDB) carrega sob demanda para não pesar no bundle comum.
  return import('./offline/purge').then((module) => module.purgeOfflineData(options)).catch(() => undefined);
}

/**
 * Chamar ANTES de `POST /auth/logout`: se houver séries ainda não sincronizadas
 * neste aparelho, avisa e pede confirmação explícita (nada é descartado em
 * silêncio). Devolve `false` quando o usuário desiste de sair.
 */
export async function confirmLogoutWithUnsyncedOps(): Promise<boolean> {
  let count = 0;
  try {
    count = await (await import('./offline/purge')).countUnsyncedOps();
  } catch {
    count = 0;
  }
  if (count <= 0 || typeof window === 'undefined') return true;
  const noun = count === 1 ? '1 série não sincronizada' : `${count} séries não sincronizadas`;
  return window.confirm(
    `Você tem ${noun} neste aparelho. Se sair agora, ${count === 1 ? 'ela será perdida' : 'elas serão perdidas'}. Deseja sair mesmo assim?`,
  );
}

// Contrato `hasActiveWork()` (Frente C): a implementação real é carregada sob
// demanda para não pesar no bundle comum. Erro ao carregar = "há trabalho".
// O chunk é pré-carregado já na abertura (e memorizado): pedi-lo só quando um SW
// novo entra em `waiting` atravessava o SW antigo no meio da troca e travava o
// kill switch (e2e/pwa/service-worker.spec.ts).
let activeWorkModule: Promise<typeof import('./offline/active-work')> | null = null;
function loadActiveWorkModule() {
  activeWorkModule ??= import('./offline/active-work');
  return activeWorkModule;
}
if (typeof window !== 'undefined') void loadActiveWorkModule().catch(() => undefined);
registerActiveWorkProvider(async () => (await loadActiveWorkModule()).offlineHasActiveWork());
