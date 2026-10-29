'use client';

import { useEffect, useRef } from 'react';

const TURNSTILE_SCRIPT_SRC = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';

interface TurnstileRenderOptions {
  sitekey: string;
  theme?: 'light' | 'dark' | 'auto';
  callback: (token: string) => void;
  'expired-callback'?: () => void;
  'error-callback'?: () => void;
}

interface TurnstileApi {
  render: (container: HTMLElement, options: TurnstileRenderOptions) => string;
  remove: (widgetId: string) => void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

let scriptPromise: Promise<TurnstileApi> | null = null;

/** Carrega o script da Cloudflare uma única vez (idempotente entre montagens). */
function loadTurnstile(): Promise<TurnstileApi> {
  if (typeof window === 'undefined') return Promise.reject(new Error('sem window'));
  if (window.turnstile) return Promise.resolve(window.turnstile);
  if (scriptPromise) return scriptPromise;

  scriptPromise = new Promise<TurnstileApi>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = TURNSTILE_SCRIPT_SRC;
    script.async = true;
    script.defer = true;
    script.onload = () => {
      if (window.turnstile) resolve(window.turnstile);
      else reject(new Error('Turnstile não inicializou.'));
    };
    script.onerror = () => {
      scriptPromise = null;
      reject(new Error('Não foi possível carregar o Turnstile.'));
    };
    document.head.appendChild(script);
  });
  return scriptPromise;
}

interface TurnstileWidgetProps {
  siteKey: string;
  /** Token válido; `null` quando expirou, deu erro ou o widget foi reiniciado. */
  onToken: (token: string | null) => void;
  /** Muda para forçar um novo desafio (ex.: após 400 de captcha da API). */
  resetKey?: number;
}

/**
 * Widget do Cloudflare Turnstile (captcha de "Esqueci minha senha", M5).
 * Renderização explícita: o script é carregado sob demanda só nesta página,
 * e o token vai para o pai via `onToken` — quem envia o formulário decide o
 * que fazer com ele. A CSP do app (hoje só Report-Only, CloudFront Function
 * `infra/cloudfront-functions/security-headers.js`) libera
 * `challenges.cloudflare.com` em `script-src` e `frame-src`; trocar o host do
 * script aqui exige atualizar a política lá.
 */
export function TurnstileWidget({ siteKey, onToken, resetKey = 0 }: TurnstileWidgetProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const onTokenRef = useRef(onToken);
  onTokenRef.current = onToken;

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !siteKey) return;

    let cancelled = false;
    let widgetId: string | null = null;
    let api: TurnstileApi | null = null;

    loadTurnstile()
      .then((turnstile) => {
        if (cancelled) return;
        api = turnstile;
        widgetId = turnstile.render(container, {
          sitekey: siteKey,
          theme: 'dark',
          callback: (token) => onTokenRef.current(token),
          'expired-callback': () => onTokenRef.current(null),
          'error-callback': () => onTokenRef.current(null),
        });
      })
      .catch(() => {
        if (!cancelled) onTokenRef.current(null);
      });

    return () => {
      cancelled = true;
      if (api && widgetId) {
        try {
          api.remove(widgetId);
        } catch {
          // widget já removido pelo próprio script
        }
      }
      onTokenRef.current(null);
    };
  }, [siteKey, resetKey]);

  return <div ref={containerRef} style={{ minHeight: 65 }} />;
}
