// Gera os ícones PNG do app instalável (PWA / "Adicionar à Tela de Início" do
// iOS) a partir do logo oficial `public/logo.svg`. Reprodutível: rode
// `npm run icons:generate` sempre que o logo mudar e versione os PNGs.
//
// - PNG OPACO (fundo = cor de fundo do app): o iOS pinta de preto qualquer
//   transparência do apple-touch-icon e aplica a própria máscara arredondada,
//   então o fundo não pode ter cantos transparentes.
// - O logo ocupa ~72% da largura, deixando margem de segurança para a máscara
//   do iOS e para ícones "maskable" do Android.
import { readFileSync, writeFileSync } from 'node:fs';

import { Resvg } from '@resvg/resvg-js';

const ICON_BACKGROUND = '#0a0a0a'; // igual a src/app/globals.css (body)

const ICONS = [
  { file: 'apple-touch-icon.png', size: 180 },
  { file: 'icon-192.png', size: 192 },
  { file: 'icon-512.png', size: 512 },
];

const LOGO_WIDTH_RATIO = 0.72;

/**
 * Lê o viewBox de `public/logo.svg` em vez de fixá-lo no script: o recorte do
 * logo pode mudar (e mudou, ao remover o fundo preto e apertar a moldura) sem
 * exigir edição manual aqui.
 */
function readViewBox(logoSvg) {
  const m = /viewBox="\s*([-\d.]+)[,\s]+([-\d.]+)[,\s]+([-\d.]+)[,\s]+([-\d.]+)\s*"/.exec(logoSvg);
  if (!m) throw new Error('public/logo.svg sem viewBox reconhecível.');
  const [minX, minY, width, height] = m.slice(1).map(Number);
  if (!(width > 0) || !(height > 0)) throw new Error('viewBox de public/logo.svg inválido.');
  return { minX, minY, width, height };
}

function buildIconSvg(logoSvg, viewBox, size) {
  const logoWidth = size * LOGO_WIDTH_RATIO;
  const logoHeight = logoWidth * (viewBox.height / viewBox.width);
  const x = (size - logoWidth) / 2;
  const y = (size - logoHeight) / 2;
  // Aninha o <svg> do logo (sem o prólogo XML) com posição/tamanho explícitos.
  const inner = logoSvg
    .replace(/<\?xml[^>]*\?>/, '')
    .replace(/<svg\b[^>]*>/, `<svg xmlns="http://www.w3.org/2000/svg" x="${x}" y="${y}" width="${logoWidth}" height="${logoHeight}" viewBox="${viewBox.minX} ${viewBox.minY} ${viewBox.width} ${viewBox.height}">`);
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}"><rect width="100%" height="100%" fill="${ICON_BACKGROUND}"/>${inner}</svg>`;
}

const logoSvg = readFileSync(new URL('../public/logo.svg', import.meta.url), 'utf8');
const logoViewBox = readViewBox(logoSvg);

for (const { file, size } of ICONS) {
  const png = new Resvg(buildIconSvg(logoSvg, logoViewBox, size), {
    fitTo: { mode: 'width', value: size },
    background: ICON_BACKGROUND,
  })
    .render()
    .asPng();
  writeFileSync(new URL(`../public/${file}`, import.meta.url), png);
  console.log(`public/${file} (${size}x${size})`);
}
