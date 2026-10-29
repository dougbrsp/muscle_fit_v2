// Gera o PNG do logo oficial usado pelo BACKEND para compor os cards
// ilustrados de exercícios (o logo é aplicado programaticamente sobre a
// ilustração gerada pela IA — nunca desenhado por ela). Fonte única:
// `public/logo.svg` (lockup vertical símbolo + wordmark + tagline, fundo
// transparente). Reprodutível: rode `node scripts/generate-brand-logo-png.mjs`
// a partir de `frontend/` sempre que o logo mudar e versione o PNG gerado.
//
// - Mesmo rasterizador dos ícones PWA (`scripts/generate-pwa-icons.mjs`):
//   @resvg/resvg-js, sem depender de fontes do sistema (o SVG só tem paths).
// - PNG RGBA com FUNDO TRANSPARENTE (sem `background`): o logo é colado com
//   alfa sobre a ilustração.
// - Largura fixa de 1200 px, proporção do viewBox preservada. O backend só
//   REDUZ o logo (≈22% da largura do card), então 1200 px cobre com folga
//   cards de até ~5000 px de largura sem nunca ampliar o raster.
//
// Saída: `api/app/assets/branding/musclefit-logo.png` (entra na imagem Docker
// da musclefit-api-core via `COPY app` — ver `api/app/assets/branding/README.md`).
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { Resvg } from '@resvg/resvg-js';

const OUTPUT_WIDTH = 1200;
const OUTPUT_URL = new URL('../../api/app/assets/branding/musclefit-logo.png', import.meta.url);

/** Mesmo parser de viewBox de `generate-pwa-icons.mjs` (o recorte do logo pode mudar). */
function readViewBox(logoSvg) {
  const m = /viewBox="\s*([-\d.]+)[,\s]+([-\d.]+)[,\s]+([-\d.]+)[,\s]+([-\d.]+)\s*"/.exec(logoSvg);
  if (!m) throw new Error('public/logo.svg sem viewBox reconhecível.');
  const [minX, minY, width, height] = m.slice(1).map(Number);
  if (!(width > 0) || !(height > 0)) throw new Error('viewBox de public/logo.svg inválido.');
  return { minX, minY, width, height };
}

const logoSvg = readFileSync(new URL('../public/logo.svg', import.meta.url), 'utf8');
const viewBox = readViewBox(logoSvg);

const rendered = new Resvg(logoSvg, {
  fitTo: { mode: 'width', value: OUTPUT_WIDTH },
  // Sem `background`: fundo transparente (RGBA).
}).render();

mkdirSync(new URL('.', OUTPUT_URL), { recursive: true });
writeFileSync(OUTPUT_URL, rendered.asPng());

const ratio = rendered.width / rendered.height;
const expected = viewBox.width / viewBox.height;
console.log(
  `${fileURLToPath(OUTPUT_URL)} (${rendered.width}x${rendered.height}, ` +
    `proporção ${ratio.toFixed(4)} vs viewBox ${expected.toFixed(4)})`,
);
