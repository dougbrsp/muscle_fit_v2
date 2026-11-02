import { expect, test } from '@playwright/test';

type ManifestIcon = { src: string; sizes: string; type: string; purpose?: string };

test.describe('manifest', () => {
  test('é válido, standalone, escopo raiz e ícones existentes com o tamanho declarado', async ({ request }) => {
    const res = await request.get('/manifest.json');
    expect(res.status()).toBe(200);
    const m = await res.json();

    expect(m.name).toBe('MuscleFit');
    expect(m.short_name).toBeTruthy();
    expect(m.id).toBe('/');
    expect(m.start_url).toBe('/');
    expect(m.scope).toBe('/');
    expect(m.display).toBe('standalone');
    expect(m.background_color).toBe('#0a0a0a');
    expect(m.theme_color).toBe('#0a0a0a');
    expect(m.orientation).toBeUndefined();
    expect(JSON.stringify(m)).not.toMatch(/slogan/i);

    const icons = m.icons as ManifestIcon[];
    expect(icons.some((i) => i.sizes === '192x192' && (i.purpose ?? 'any') === 'any')).toBe(true);
    expect(icons.some((i) => i.sizes === '512x512' && (i.purpose ?? 'any') === 'any')).toBe(true);
    const maskable = icons.filter((i) => i.purpose === 'maskable');
    expect(maskable.length).toBeGreaterThanOrEqual(1);
    // O ícone maskable não pode reaproveitar o PNG "any" (sem safe zone).
    for (const i of maskable) expect(i.src).toMatch(/maskable/);

    for (const icon of icons) {
      const r = await request.get(icon.src);
      expect(r.status(), icon.src).toBe(200);
      const buf = await r.body();
      // PNG: assinatura + IHDR (largura/altura nos bytes 16..23).
      expect(buf.subarray(1, 4).toString('ascii')).toBe('PNG');
      const [w, h] = icon.sizes.split('x').map(Number);
      expect(buf.readUInt32BE(16), icon.src).toBe(w);
      expect(buf.readUInt32BE(20), icon.src).toBe(h);
    }
  });

  test('a página referencia o manifest e o apple-touch-icon', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('link[rel="manifest"]')).toHaveAttribute('href', '/manifest.json');
    await expect(page.locator('link[rel="apple-touch-icon"]')).toHaveCount(1);
    await expect(page.locator('meta[name="apple-mobile-web-app-capable"]')).toHaveCount(1);
  });
});
