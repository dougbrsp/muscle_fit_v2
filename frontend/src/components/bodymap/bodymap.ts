// Lógica pura do mapa corporal v1 (sem React): resolver `muscle_group -> slugs
// -> região`, escolher a cor de cada região e montar a matriz que tinge a arte.
// O desenho fica em `BodyMapFigure.tsx`; a API pública em `MuscleBodyMap.tsx`.
import {
  BODYMAP_ALIASES,
  BODYMAP_CANVAS,
  BODYMAP_COMPOSITOR,
  BODYMAP_SLUGS,
  BODYMAP_VIEWS,
  type BodyMapSlug,
} from './regions.v1';

/** Arte base (WebP com alfa, frente e costas). Mesmos bytes do pacote aprovado. */
export const BODYMAP_BASE_IMAGES = {
  low: { href: '/bodymap/v1/bodymap-base@768.webp', width: 768 },
  high: { href: '/bodymap/v1/bodymap-base.webp', width: 1536 },
} as const;
export type BodyMapBaseImage = (typeof BODYMAP_BASE_IMAGES)[keyof typeof BODYMAP_BASE_IMAGES];

/**
 * Largura máxima do par frente + costas, em px CSS. Mesma ordem de grandeza
 * do mapa antigo (duas figuras de 220 px): no celular ocupa a largura do
 * contêiner; no desktop não vira um pôster.
 */
export const BODYMAP_MAX_WIDTH = 480;

/** Espaço entre as duas figuras, em px CSS (o vão da arte original é cortado). */
export const BODYMAP_VIEW_GAP = 12;

const SLUGS = new Set<string>(BODYMAP_SLUGS);

/**
 * Slug do banco -> slug que tem região na arte (alias resolvido, ex.:
 * `abductors` -> `adductor`). `null` quando não há região (`knees`, `head`,
 * slug desconhecido): o item é ignorado sem erro.
 */
export function canonicalSlug(slug: string): BodyMapSlug | null {
  const normalized = slug.trim().toLowerCase();
  const target = Object.prototype.hasOwnProperty.call(BODYMAP_ALIASES, normalized)
    ? BODYMAP_ALIASES[normalized]
    : normalized;
  return SLUGS.has(target) ? (target as BodyMapSlug) : null;
}

/** Nível 1-based de um item: inteiro >= 1 (0, negativo ou NaN contam como 1). */
export function intensityLevel(intensity: number): number {
  if (!Number.isFinite(intensity)) return 1;
  return Math.max(1, Math.round(intensity));
}

/** Cor de um nível: `colors[nível - 1]`, limitado à última cor (como a lib antiga). */
export function colorForLevel(level: number, colors: readonly string[]): string | null {
  if (colors.length === 0) return null;
  return colors[Math.min(colors.length, Math.max(1, level)) - 1] ?? null;
}

export type BodyMapEntryLike = { muscle_group: string; intensity: number; tooltip: string };

export type ResolvedRegion<T extends BodyMapEntryLike = BodyMapEntryLike> = {
  slug: BodyMapSlug;
  /** Maior nível entre os itens que pintam a região. */
  level: number;
  /** `null` quando `colors` está vazio: a região continua clicável, sem tinta. */
  color: string | null;
  /** Itens que caem nesta região, do mais intenso para o menos intenso. */
  entries: T[];
};

/**
 * Agrupa os itens por região da arte. Um mesmo slug pode receber mais de um
 * grupo muscular (ex.: "Peitoral superior" e "Peitoral médio/inferior" ->
 * `chest`): vale o MAIOR nível (o contrato de `MuscleMapEntry` diz "cor mais
 * forte/prioritária = maior intensidade") e o detalhe lista todos os itens.
 * A lib antiga somava as frequências e limitava à última cor.
 */
export function resolveRegions<T extends BodyMapEntryLike>(
  items: readonly T[],
  groupToSlugs: Readonly<Record<string, readonly string[]>>,
  colors: readonly string[],
): ResolvedRegion<T>[] {
  const bySlug = new Map<BodyMapSlug, { level: number; entries: Array<{ item: T; level: number }> }>();
  for (const item of items) {
    const slugs = Object.prototype.hasOwnProperty.call(groupToSlugs, item.muscle_group)
      ? groupToSlugs[item.muscle_group]
      : undefined;
    if (!slugs) continue;
    const level = intensityLevel(item.intensity);
    const seen = new Set<BodyMapSlug>();
    for (const raw of slugs) {
      const slug = canonicalSlug(raw);
      if (!slug || seen.has(slug)) continue;
      seen.add(slug);
      const current = bySlug.get(slug) ?? { level: 0, entries: [] };
      current.level = Math.max(current.level, level);
      current.entries.push({ item, level });
      bySlug.set(slug, current);
    }
  }
  return BODYMAP_SLUGS.filter((slug) => bySlug.has(slug)).map((slug) => {
    const { level, entries } = bySlug.get(slug)!;
    return {
      slug,
      level,
      color: colorForLevel(level, colors),
      // sort estável: empate mantém a ordem de `items`.
      entries: entries
        .slice()
        .sort((a, b) => b.level - a.level)
        .map((entry) => entry.item),
    };
  });
}

/** `#rgb`, `#rgba`, `#rrggbb` ou `#rrggbbaa` -> [r, g, b] (0-255; alfa ignorado). */
export function parseHexColor(color: string): [number, number, number] | null {
  const match = /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.exec(color.trim());
  if (!match) return null;
  let hex = match[1];
  if (hex.length <= 4) {
    hex = hex
      .slice(0, 3)
      .split('')
      .map((ch) => ch + ch)
      .join('');
  }
  return [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16)) as [number, number, number];
}

function fmt(value: number): string {
  return String(Number(value.toFixed(6)));
}

/**
 * `values` de um `<feColorMatrix type="matrix">` que tinge a arte base na cor
 * dada, preservando o sombreado:
 *
 *   tinta = cor × (K0 + K1 × n),  n = (L − lo) / (hi − lo),
 *   L = 0,299·R + 0,587·G + 0,114·B  (a luminância "L" do Pillow)
 *
 * É a fórmula do compositor do backend (Pillow), transcrita do `demo.html`
 * do protótipo aprovado; o filtro precisa de
 * `color-interpolation-filters="sRGB"` para bater com o Pillow.
 */
export function tintMatrixValues(rgb: readonly [number, number, number]): string {
  const { lumLo, lumHi, k0, k1 } = BODYMAP_COMPOSITOR;
  const lo = lumLo / 255;
  const hi = lumHi / 255;
  const a = k1 / (hi - lo);
  const b = k0 - (k1 * lo) / (hi - lo);
  const rows = rgb.map((channel) => {
    const c = channel / 255;
    return [0.299 * c * a, 0.587 * c * a, 0.114 * c * a, 0, c * b].map(fmt).join(' ');
  });
  return [...rows, '0 0 0 1 0'].join(' ');
}

const WIDEST_VIEW = Math.max(BODYMAP_VIEWS.front.width, BODYMAP_VIEWS.back.width);
const VIEWS_TOTAL = BODYMAP_VIEWS.front.width + BODYMAP_VIEWS.back.width;

/**
 * Arte de 768 px ou de 1536 px conforme o tamanho em que o mapa vai aparecer.
 * A largura da viewport é um teto para o contêiner, então a escolha nunca fica
 * abaixo do necessário (nítido em retina); ela só economiza download em tela
 * com densidade 1. Decidido uma vez, sem trocar depois (não baixa as duas).
 */
export function pickBaseImage(viewportWidth: number, devicePixelRatio: number): BodyMapBaseImage {
  if (!Number.isFinite(viewportWidth) || viewportWidth <= 0) return BODYMAP_BASE_IMAGES.high;
  const dpr = Number.isFinite(devicePixelRatio) && devicePixelRatio > 0 ? devicePixelRatio : 1;
  const container = Math.min(viewportWidth, BODYMAP_MAX_WIDTH);
  const figureDevicePx = container * (WIDEST_VIEW / VIEWS_TOTAL) * dpr;
  const lowDevicePx = WIDEST_VIEW * (BODYMAP_BASE_IMAGES.low.width / BODYMAP_CANVAS.width);
  // Até 10% de ampliação não se nota; acima disso vai a arte cheia.
  return figureDevicePx <= lowDevicePx * 1.1 ? BODYMAP_BASE_IMAGES.low : BODYMAP_BASE_IMAGES.high;
}
