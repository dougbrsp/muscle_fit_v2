import { clearSession } from './auth';
import { getApiBaseUrl } from './config';
import { sanitizeInternalPath } from './safe-path';

/**
 * Erro lançado por `apiFetch` para respostas não-ok ou falha de rede,
 * carregando o status HTTP quando disponível (0 para falha de rede/CORS).
 */
export class ApiError extends Error {
  status: number;
  /**
   * `detail` do FastAPI quando NÃO é string (ex.: `{error, message,
   * generation_id}` das rotas de cards ilustrados, ou a lista de erros de
   * validação do 422). `message` continua a mesma de antes nesses casos —
   * quem precisa do código lê daqui (ver `toExerciseCardError`).
   */
  detail?: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

// Evita redirecionar mais de uma vez quando várias chamadas em paralelo
// recebem 401 ao mesmo tempo (ex.: várias queries de dashboard). Não precisa
// ser resetado: `window.location.href` abaixo troca de página (reload
// completo), o que já reinicia o módulo e este flag.
let redirectingToLogin = false;

/**
 * Sanitiza o destino pós-login: só aceita caminho interno (SEG-18, ver
 * `src/lib/safe-path.ts`) e ignora a própria raiz. Ver `LoginClient.tsx` para
 * o lado que lê esse parâmetro de volta.
 */
function sanitizeNextPath(path: string): string | null {
  if (path === '/') return null;
  return sanitizeInternalPath(path);
}

/** `code` devolvido pela API (403) enquanto a troca de senha obrigatória está pendente (SEG-06). */
export const MUST_CHANGE_PASSWORD_CODE = 'must_change_password';
const PASSWORD_CHANGE_PATH = '/perfil';

function redirectToPasswordChange(): void {
  if (typeof window === 'undefined') return;
  // Já na tela de troca: nada a fazer (evita reload em loop).
  if (window.location.pathname.startsWith(PASSWORD_CHANGE_PATH)) return;
  window.location.href = `${PASSWORD_CHANGE_PATH}?troque-senha=1`;
}

function redirectToLogin(): void {
  if (typeof window === 'undefined') return;
  // Já na tela de login (`/`): um 401 aqui é o caso normal de "ainda não
  // logado" (ex.: checagem de sessão ao montar a página) — nada a fazer,
  // evita um reload desnecessário da própria página de login.
  if (window.location.pathname === '/') return;
  if (redirectingToLogin) return;
  redirectingToLogin = true;

  clearSession();

  const currentPath = `${window.location.pathname}${window.location.search}`;
  const next = sanitizeNextPath(currentPath);
  window.location.href = next ? `/?next=${encodeURIComponent(next)}` : '/';
}

// ---------------------------------------------------------------------------
// M56: só 401 desloga. Falha transitória (503 da API, erro de rede/CORS) NÃO
// é "sessão encerrada": a API responde 503 quando não conseguiu CONSULTAR a
// sessão (ex.: Aurora voltando do auto-pause depois do retry do backend,
// erro transitório da RDS Data API — ver `api/app/security.py`). Nesses casos
// a sessão continua válida no banco; deslogar seria errado.
//
// Retry curto só para métodos IDEMPOTENTES (GET/HEAD): um POST que estourou
// (ex.: 504 do API Gateway com a escrita já feita, caso já visto em produção)
// não pode ser repetido às cegas. Escritas sobem o erro para a tela, que já
// oferece "tentar de novo".
// ---------------------------------------------------------------------------

/** Status que indicam indisponibilidade transitória (0 = rede/CORS). */
const TRANSIENT_STATUSES: ReadonlySet<number> = new Set([0, 503]);

/** Esperas (ms) entre as tentativas extras de um GET/HEAD transitório. */
export const TRANSIENT_RETRY_DELAYS_MS: readonly number[] = [800, 2000];

export const TRANSIENT_ERROR_MESSAGE =
  'Serviço temporariamente indisponível. Tente novamente em instantes.';

const NETWORK_ERROR_MESSAGE = 'Não foi possível conectar à API. Verifique sua conexão e tente novamente.';

/** True para erro que não diz nada sobre a sessão (503, rede) — nunca desloga. */
export function isTransientApiError(error: unknown): boolean {
  return error instanceof ApiError && TRANSIENT_STATUSES.has(error.status);
}

function isIdempotent(init: RequestInit): boolean {
  const method = (init.method ?? 'GET').toUpperCase();
  return method === 'GET' || method === 'HEAD';
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * `fetch` com o retry transitório acima. Devolve a resposta final (inclusive
 * um 503 depois de esgotar as tentativas) ou lança `ApiError(status 0)` se a
 * última tentativa falhou na rede.
 */
async function fetchWithTransientRetry(url: string, init: RequestInit): Promise<Response> {
  const delays = isIdempotent(init) ? TRANSIENT_RETRY_DELAYS_MS : [];
  for (let attempt = 0; ; attempt += 1) {
    let response: Response | null = null;
    try {
      response = await fetch(url, init);
    } catch {
      response = null;
    }
    const transient = response === null || TRANSIENT_STATUSES.has(response.status);
    if (!transient || attempt >= delays.length) {
      if (response === null) throw new ApiError(NETWORK_ERROR_MESSAGE, 0);
      return response;
    }
    await wait(delays[attempt]);
  }
}

/**
 * Cliente HTTP centralizado para a Fitness API (Fase 3: sessão em cookie
 * httpOnly — `credentials: 'include'` envia/recebe o cookie automaticamente,
 * sem token nenhum em JS). Trata 401 de forma uniforme: limpa qualquer
 * resquício de sessão local e redireciona para a tela de login uma única vez.
 * 503 e erro de rede NÃO deslogam (M56): GET/HEAD tentam de novo algumas vezes
 * e, se persistir, a chamada falha com `ApiError` (status 503 ou 0) para a
 * tela mostrar a mensagem — ver `isTransientApiError`.
 *
 * Não duplicar tratamento de 401/erro em cada tela — sempre passar pelo
 * `apiFetch`.
 */
export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  // FormData (upload multipart, ex.: POST /users/{id}/photo) precisa que o
  // browser defina Content-Type sozinho (inclui o boundary) — nunca fixar
  // 'application/json' nesse caso.
  const isFormData = typeof FormData !== 'undefined' && init.body instanceof FormData;

  const headers: HeadersInit = {
    ...(init.body && !isFormData ? { 'Content-Type': 'application/json' } : {}),
    ...init.headers,
  };

  const response = await fetchWithTransientRetry(`${getApiBaseUrl()}${path}`, {
    ...init,
    headers,
    credentials: 'include',
  });

  if (response.status === 401) {
    redirectToLogin();
    throw new ApiError('Sessão expirada. Faça login novamente.', 401);
  }

  if (!response.ok) {
    let detail =
      response.status === 503 ? TRANSIENT_ERROR_MESSAGE : `Erro ao consultar ${path} (HTTP ${response.status}).`;
    let structuredDetail: unknown;
    try {
      const body = await response.json();
      if (response.status === 403 && body?.code === MUST_CHANGE_PASSWORD_CODE) {
        redirectToPasswordChange();
      }
      if (typeof body?.detail === 'string') {
        detail = body.detail;
      } else if (body?.detail != null) {
        structuredDetail = body.detail;
      }
    } catch {
      // corpo sem JSON válido — mantém a mensagem padrão
    }
    throw new ApiError(detail, response.status, structuredDetail);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

/**
 * Variante de `apiFetch` para respostas binárias (ex.: foto de perfil via
 * `GET /users/{id}/photo`, PDF de relatório etc.). Reaproveita o envio do
 * cookie de sessão e o tratamento de 401 — nunca usar `fetch` cru para isso,
 * pois um `<img src>` direto não envia o cookie httpOnly.
 */
export async function apiFetchBlob(path: string, init: RequestInit = {}): Promise<Blob> {
  const response = await fetchWithTransientRetry(`${getApiBaseUrl()}${path}`, { ...init, credentials: 'include' });

  if (response.status === 401) {
    redirectToLogin();
    throw new ApiError('Sessão expirada. Faça login novamente.', 401);
  }

  if (!response.ok) {
    throw new ApiError(
      response.status === 503 ? TRANSIENT_ERROR_MESSAGE : `Erro ao consultar ${path} (HTTP ${response.status}).`,
      response.status,
    );
  }

  return response.blob();
}
