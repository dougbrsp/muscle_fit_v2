import { describe, expect, it } from 'vitest';

import { INSTALL_REAPPEAR_DAYS, MS_PER_DAY } from './constants';
import { isInstallDismissed, isSilentPath, shouldShowInstallBanner } from './install-banner-policy';
import { isIosSafari, manualInstallMode } from './platform';

const IOS_SAFARI =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1';
const IOS_CHROME =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/124.0 Mobile/15E148 Safari/604.1';
const ANDROID_CHROME =
  'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36';

describe('platform', () => {
  it('iOS Safari fora do standalone recebe instrução manual', () => {
    expect(manualInstallMode({ userAgent: IOS_SAFARI })).toBe('ios-safari');
  });
  it('standalone (navigator.standalone ou display-mode) não mostra nada', () => {
    expect(manualInstallMode({ userAgent: IOS_SAFARI, navigatorStandalone: true })).toBe('none');
    expect(manualInstallMode({ userAgent: IOS_SAFARI, displayModeStandalone: true })).toBe('none');
  });
  it('Chrome no iOS e Android não recebem a instrução do Safari', () => {
    expect(isIosSafari({ userAgent: IOS_CHROME })).toBe(false);
    expect(manualInstallMode({ userAgent: ANDROID_CHROME })).toBe('none');
  });
  it('iPadOS que se apresenta como Mac é reconhecido pelo toque', () => {
    const ua = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15';
    expect(isIosSafari({ userAgent: ua, platform: 'MacIntel', maxTouchPoints: 5 })).toBe(true);
    expect(isIosSafari({ userAgent: ua, platform: 'MacIntel', maxTouchPoints: 0 })).toBe(false);
  });
});

describe('install-banner-policy', () => {
  const now = Date.UTC(2026, 10, 1);
  it('dispensado reaparece só após o período nomeado', () => {
    const before = String(now - (INSTALL_REAPPEAR_DAYS * MS_PER_DAY - 1000));
    const after = String(now - (INSTALL_REAPPEAR_DAYS * MS_PER_DAY + 1000));
    expect(isInstallDismissed(before, now)).toBe(true);
    expect(isInstallDismissed(after, now)).toBe(false);
  });
  it('valor ausente, lixo ou zero = não dispensado; data futura = dispensado', () => {
    expect(isInstallDismissed(null, now)).toBe(false);
    expect(isInstallDismissed('abc', now)).toBe(false);
    expect(isInstallDismissed('0', now)).toBe(false);
    expect(isInstallDismissed(String(now + 5000), now)).toBe(true);
  });
  it('rotas do treino são silenciosas', () => {
    expect(isSilentPath('/modo-treino')).toBe(true);
    expect(isSilentPath('/modo-treino/')).toBe(true);
    expect(isSilentPath('/dashboard')).toBe(false);
    expect(isSilentPath(null)).toBe(false);
  });
  it('só aparece com flag, viewport mobile, modo válido, não dispensado e fora do treino', () => {
    const base = { mobileUiEnabled: true, isMobileViewport: true, mode: 'ios-safari', dismissed: false, pathname: '/dashboard' } as const;
    expect(shouldShowInstallBanner(base)).toBe(true);
    expect(shouldShowInstallBanner({ ...base, mobileUiEnabled: false })).toBe(false);
    expect(shouldShowInstallBanner({ ...base, isMobileViewport: false })).toBe(false);
    expect(shouldShowInstallBanner({ ...base, mode: 'none' })).toBe(false);
    expect(shouldShowInstallBanner({ ...base, dismissed: true })).toBe(false);
    expect(shouldShowInstallBanner({ ...base, pathname: '/modo-treino' })).toBe(false);
    expect(shouldShowInstallBanner({ ...base, mode: 'prompt' })).toBe(true);
  });
});
