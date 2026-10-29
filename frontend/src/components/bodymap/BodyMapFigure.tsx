'use client';

import { Text } from '@mantine/core';
import { Fragment, useId, useState, type KeyboardEvent } from 'react';
import { BRAND } from '@/theme';
import {
  BODYMAP_MAX_WIDTH,
  BODYMAP_VIEW_GAP,
  parseHexColor,
  tintMatrixValues,
  type BodyMapEntryLike,
  type ResolvedRegion,
} from './bodymap';
import {
  BODYMAP_CANVAS,
  BODYMAP_COMPOSITOR,
  BODYMAP_REGIONS,
  BODYMAP_VIEW_ORDER,
  BODYMAP_VIEWS,
  type BodyMapSlug,
  type BodyMapView,
} from './regions.v1';

const VIEW_LABEL: Record<BodyMapView, string> = { front: 'Frente', back: 'Costas' };

/** Opacidade da região tingida sobre a arte escurecida (a do protótipo aprovado). */
const REGION_OPACITY = 0.9;

/** Cor que não é hex (ex.: `var(--x)`) não passa pela matriz: vira preenchimento chapado. */
const FLAT_FALLBACK_OPACITY = 0.6;

const ACTIVE_STROKE = BRAND.textPrimary;

type Layer = {
  color: string;
  level: number;
  rgb: [number, number, number] | null;
  slugs: BodyMapSlug[];
};

/** Uma camada por cor (não por slug): menos filtros e recortes no DOM. */
function layersByColor(regions: readonly ResolvedRegion[]): Layer[] {
  const layers = new Map<string, Layer>();
  for (const region of regions) {
    if (!region.color) continue;
    const layer = layers.get(region.color) ?? {
      color: region.color,
      level: region.level,
      rgb: parseHexColor(region.color),
      slugs: [],
    };
    layer.level = Math.max(layer.level, region.level);
    layer.slugs.push(region.slug);
    layers.set(region.color, layer);
  }
  // Nível maior por cima (as regiões não se sobrepõem, mas a borda antisserrilhada sim).
  return [...layers.values()].sort((a, b) => a.level - b.level);
}

/**
 * As duas figuras (frente e costas) lado a lado, cada uma recortada da arte
 * base pela caixa do manifest. Técnica do `demo.html` do protótipo: a arte
 * inteira escurecida (`dim`) e, por cima, a MESMA arte recortada pelo path da
 * região (`clipPath`) e tingida por `feColorMatrix` — o sombreado da arte
 * continua visível dentro da cor. Por cima de tudo, um path transparente por
 * região destacada recebe clique/toque e foco por teclado.
 */
export function BodyMapFigure<T extends BodyMapEntryLike>({
  regions,
  baseHref,
  selectedSlug,
  onSelect,
}: {
  regions: readonly ResolvedRegion<T>[];
  baseHref: string;
  selectedSlug: BodyMapSlug | null;
  onSelect: (slug: BodyMapSlug) => void;
}) {
  // `useId` traz ":" — fora dos ids para o `url(#...)` não depender de escape.
  const uid = `bm${useId().replace(/[^A-Za-z0-9_-]/g, '')}`;
  const [focusedKey, setFocusedKey] = useState<string | null>(null);
  const layers = layersByColor(regions);
  const { front, back } = BODYMAP_VIEWS;

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: `${front.width}fr ${back.width}fr`,
        columnGap: BODYMAP_VIEW_GAP,
        width: '100%',
        maxWidth: BODYMAP_MAX_WIDTH,
        marginInline: 'auto',
      }}
    >
      {BODYMAP_VIEW_ORDER.map((view) => {
        const box = BODYMAP_VIEWS[view];
        const prefix = `${uid}-${view}`;
        const filterRegion = {
          filterUnits: 'userSpaceOnUse',
          x: box.x,
          y: box.y,
          width: box.width,
          height: box.height,
          colorInterpolationFilters: 'sRGB',
        } as const;
        const visibleLayers = layers
          .map((layer, index) => ({
            layer,
            index,
            paths: layer.slugs.flatMap((slug) => BODYMAP_REGIONS[slug][view] ?? []),
          }))
          .filter((entry) => entry.paths.length > 0);

        return (
          <figure key={view} style={{ margin: 0, minWidth: 0 }}>
            <Text component="figcaption" ta="center" size="sm" fw={600} c={BRAND.textMuted} mb={4}>
              {VIEW_LABEL[view]}
            </Text>
            <svg
              viewBox={`${box.x} ${box.y} ${box.width} ${box.height}`}
              width="100%"
              role="group"
              aria-label={`Mapa corporal: ${VIEW_LABEL[view].toLowerCase()}`}
              data-bodymap-view={view}
              style={{ display: 'block', height: 'auto', overflow: 'hidden' }}
            >
              <defs>
                <filter id={`${prefix}-dim`} {...filterRegion}>
                  <feComponentTransfer>
                    <feFuncR type="linear" slope={BODYMAP_COMPOSITOR.dim} />
                    <feFuncG type="linear" slope={BODYMAP_COMPOSITOR.dim} />
                    <feFuncB type="linear" slope={BODYMAP_COMPOSITOR.dim} />
                  </feComponentTransfer>
                </filter>
                {visibleLayers.map(({ layer, index, paths }) =>
                  layer.rgb ? (
                    <Fragment key={layer.color}>
                      <filter id={`${prefix}-tint-${index}`} {...filterRegion}>
                        <feColorMatrix type="matrix" values={tintMatrixValues(layer.rgb)} />
                      </filter>
                      <clipPath id={`${prefix}-clip-${index}`}>
                        {paths.map((d, i) => (
                          <path key={i} d={d} clipRule="evenodd" />
                        ))}
                      </clipPath>
                    </Fragment>
                  ) : null,
                )}
              </defs>

              <image
                href={baseHref}
                x={0}
                y={0}
                width={BODYMAP_CANVAS.width}
                height={BODYMAP_CANVAS.height}
                preserveAspectRatio="none"
                filter={`url(#${prefix}-dim)`}
              />

              {visibleLayers.map(({ layer, index, paths }) => (
                <g
                  key={layer.color}
                  data-bodymap-layer=""
                  data-color={layer.color}
                  data-level={layer.level}
                  data-slugs={layer.slugs.filter((slug) => BODYMAP_REGIONS[slug][view]).join(' ')}
                  clipPath={layer.rgb ? `url(#${prefix}-clip-${index})` : undefined}
                  opacity={layer.rgb ? REGION_OPACITY : FLAT_FALLBACK_OPACITY}
                >
                  {layer.rgb ? (
                    <image
                      href={baseHref}
                      x={0}
                      y={0}
                      width={BODYMAP_CANVAS.width}
                      height={BODYMAP_CANVAS.height}
                      preserveAspectRatio="none"
                      filter={`url(#${prefix}-tint-${index})`}
                    />
                  ) : (
                    paths.map((d, i) => <path key={i} d={d} fill={layer.color} fillRule="evenodd" />)
                  )}
                </g>
              ))}

              {regions.map((region) => {
                const d = BODYMAP_REGIONS[region.slug][view];
                if (!d) return null;
                const key = `${view}:${region.slug}`;
                const active = selectedSlug === region.slug || focusedKey === key;
                const label = region.entries.map((entry) => entry.tooltip).join('; ');
                const select = () => onSelect(region.slug);
                const onKeyDown = (event: KeyboardEvent<SVGPathElement>) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    select();
                  }
                };
                return (
                  <path
                    key={key}
                    d={d}
                    fillRule="evenodd"
                    fill="transparent"
                    stroke={active ? ACTIVE_STROKE : 'none'}
                    strokeWidth={active ? 2 : undefined}
                    vectorEffect="non-scaling-stroke"
                    role="button"
                    tabIndex={0}
                    aria-label={`${label}, ${VIEW_LABEL[view].toLowerCase()}`}
                    data-slug={region.slug}
                    style={{ cursor: 'pointer', outline: 'none' }}
                    onClick={select}
                    onKeyDown={onKeyDown}
                    onFocus={() => setFocusedKey(key)}
                    onBlur={() => setFocusedKey((current) => (current === key ? null : current))}
                  >
                    <title>{label}</title>
                  </path>
                );
              })}
            </svg>
          </figure>
        );
      })}
    </div>
  );
}
