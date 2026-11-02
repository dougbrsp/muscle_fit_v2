import { INSTALL_REAPPEAR_DAYS, MS_PER_DAY, PWA_SILENT_PATH_PREFIXES } from './constants';

/** Pura: o convite foi dispensado há menos de `INSTALL_REAPPEAR_DAYS`? Valor ilegível = não dispensado. */
export function isInstallDismissed(rawDismissedAt: string | null | undefined, nowMs: number): boolean {
  if (!rawDismissedAt) return false;
  const at = Number(rawDismissedAt);
  if (!Number.isFinite(at) || at <= 0) return false;
  if (at > nowMs) return true; // relógio andou para trás: tratar como recém-dispensado
  return nowMs - at < INSTALL_REAPPEAR_DAYS * MS_PER_DAY;
}

export function isSilentPath(pathname: string | null | undefined): boolean {
  if (!pathname) return false;
  return PWA_SILENT_PATH_PREFIXES.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

export type InstallBannerInput = {
  mobileUiEnabled: boolean;
  isMobileViewport: boolean;
  /** `ios-safari` (instrução manual), `prompt` (Android com beforeinstallprompt) ou `none`. */
  mode: 'ios-safari' | 'prompt' | 'none';
  dismissed: boolean;
  pathname: string | null | undefined;
};

export function shouldShowInstallBanner(i: InstallBannerInput): boolean {
  if (!i.mobileUiEnabled || !i.isMobileViewport) return false;
  if (i.mode === 'none' || i.dismissed) return false;
  return !isSilentPath(i.pathname);
}
