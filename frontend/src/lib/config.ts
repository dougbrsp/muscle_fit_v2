// Base da API. Fase 4: sem proxy — o navegador chama a API diretamente.
// Sem segredo aqui: este valor é público por definição (embutido no bundle
// estático que roda no navegador). O token mestre (API_TOKEN) da API NUNCA
// entra neste frontend — a sessão por usuário (Fase 3) vive num cookie
// httpOnly emitido por POST /auth/login (`musclefit_session`, ver
// api/app/auth.py), nunca acessível a JavaScript; toda chamada usa
// `credentials: 'include'` (ver src/lib/api.ts) para enviá-lo automaticamente.
//
// Marcador `{hostname}` (só DEV): `http://{hostname}:8000` vira o host pelo
// qual o app foi aberto — `localhost` no desktop, o nome Tailscale no celular.
// Assim o cookie de sessão sai do mesmo site da página (SameSite=Lax) nos dois
// acessos. No servidor/prerender (sem `window`) o marcador vira `localhost`.
// Sem marcador o valor é usado como está; produção não define a variável e
// cai no default fixo abaixo.
export const DEFAULT_API_BASE_URL = 'https://api.musclefit.com.br';
export const API_HOSTNAME_PLACEHOLDER = '{hostname}';

/** Resolve o valor bruto da variável para a URL final (função pura). */
export function resolveApiBaseUrl(raw: string | undefined, hostname: string | undefined): string {
  const base = raw?.replace(/\/+$/, '') ?? DEFAULT_API_BASE_URL;
  if (!base.includes(API_HOSTNAME_PLACEHOLDER)) return base;
  return base.split(API_HOSTNAME_PLACEHOLDER).join(hostname || 'localhost');
}

/**
 * Base da API para a chamada corrente. Avaliada a cada chamada (não no nível
 * do módulo) para que o `{hostname}` use o `window` do navegador mesmo quando
 * o módulo foi carregado antes dele existir (prerender/SSR).
 */
export function getApiBaseUrl(): string {
  // `process.env.NEXT_PUBLIC_API_BASE_URL` precisa ficar literal: o Next o
  // substitui pelo valor em build-time.
  const hostname = typeof window === 'undefined' ? undefined : window.location?.hostname;
  return resolveApiBaseUrl(process.env.NEXT_PUBLIC_API_BASE_URL, hostname);
}

// Site key do Cloudflare Turnstile (M5, captcha de "Esqueci minha senha").
// Valor PUBLICO por definicao (vai no HTML do widget), resolvido em build-time
// pelo Next. Sem valor, a pagina /esqueci-senha fica fechada (botao
// desabilitado e aviso) - o backend tambem falha fechado sem o secret.
export const TURNSTILE_SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY?.trim() ?? '';
