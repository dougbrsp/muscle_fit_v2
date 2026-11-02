/**
 * Detecção de plataforma para o convite de instalação. Funções puras (entrada
 * explícita) para testar sem DOM. Não é regra de negócio: só decide qual
 * instrução de instalação mostrar.
 */

export type InstallEnv = {
  userAgent: string;
  platform?: string;
  maxTouchPoints?: number;
  /** `navigator.standalone` (iOS). */
  navigatorStandalone?: boolean;
  /** `matchMedia('(display-mode: standalone)').matches`. */
  displayModeStandalone?: boolean;
};

export function isIos(env: InstallEnv): boolean {
  if (/iPhone|iPad|iPod/i.test(env.userAgent)) return true;
  // iPadOS 13+ se apresenta como Macintosh; o que o diferencia é o toque.
  return env.platform === 'MacIntel' && (env.maxTouchPoints ?? 0) > 1;
}

/** Safari de verdade no iOS: exclui navegadores de terceiros (Chrome, Firefox, Edge, Opera, Google app) e webviews. */
export function isIosSafari(env: InstallEnv): boolean {
  if (!isIos(env)) return false;
  if (/CriOS|FxiOS|EdgiOS|OPiOS|GSA\/|DuckDuckGo|FBAN|FBAV|Instagram|Line\//i.test(env.userAgent)) return false;
  return /Safari\//i.test(env.userAgent);
}

export function isStandalone(env: InstallEnv): boolean {
  return env.navigatorStandalone === true || env.displayModeStandalone === true;
}

export type InstallMode = 'ios-safari' | 'none';

/** Qual instrução manual mostrar (Android usa `beforeinstallprompt`, tratado à parte). */
export function manualInstallMode(env: InstallEnv): InstallMode {
  if (isStandalone(env)) return 'none';
  return isIosSafari(env) ? 'ios-safari' : 'none';
}

export function readInstallEnv(): InstallEnv {
  const nav = window.navigator as Navigator & { standalone?: boolean };
  let displayModeStandalone = false;
  try {
    displayModeStandalone = window.matchMedia('(display-mode: standalone)').matches;
  } catch {
    displayModeStandalone = false;
  }
  return {
    userAgent: nav.userAgent,
    platform: nav.platform,
    maxTouchPoints: nav.maxTouchPoints,
    navigatorStandalone: nav.standalone,
    displayModeStandalone,
  };
}
