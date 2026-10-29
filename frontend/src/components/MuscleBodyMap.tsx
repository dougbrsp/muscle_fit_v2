'use client';

import { Group, Loader, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { apiFetch } from '@/lib/api';
import type { MuscleGroupBodyMapResponse } from '@/lib/analytics-types';
import { BRAND } from '@/theme';
import { BodyMapFigure } from './bodymap/BodyMapFigure';
import { BODYMAP_BASE_IMAGES, pickBaseImage, resolveRegions } from './bodymap/bodymap';
import type { BodyMapSlug } from './bodymap/regions.v1';

export type MuscleMapEntry = {
  muscle_group: string;
  /** Índice 1-based em `colors` (cor mais forte/prioritária = maior intensidade). */
  intensity: number;
  /** Texto completo mostrado ao clicar no grupo muscular. */
  tooltip: string;
};

/** Cache compartilhado do `GET /analytics/muscle-group-body-map` (mesma chave de antes). */
export const MUSCLE_GROUP_BODY_MAP_QUERY_KEY = ['muscle-group-body-map'] as const;

async function fetchGroupToSlugs(): Promise<Record<string, string[]>> {
  const payload = await apiFetch<MuscleGroupBodyMapResponse>('/analytics/muscle-group-body-map');
  const map: Record<string, string[]> = {};
  for (const row of payload.items || []) {
    map[row.muscle_group] = row.slugs;
  }
  return map;
}

/** Arte de 768 ou 1536 px, decidida uma vez na montagem (no servidor: a cheia). */
function initialBaseHref(): string {
  if (typeof window === 'undefined') return BODYMAP_BASE_IMAGES.high.href;
  return pickBaseImage(window.innerWidth, window.devicePixelRatio).href;
}

/**
 * Mapa corporal visual (frente e costas) colorido por grupo muscular conforme
 * `intensity`. Componente de apresentação puro — não faz fetch de
 * recuperação, só resolve `muscle_group -> slugs` via
 * `GET /analytics/muscle-group-body-map`.
 *
 * Desenho: arte base aprovada em 2026-10-03 (`public/bodymap/v1/`) com as
 * regiões de `bodymap/regions.v1.ts`; cada região destacada é a própria arte
 * tingida na cor `colors[intensity - 1]` (detalhes em `BodyMapFigure`). Slug
 * sem região na arte (`knees`, desconhecido) é ignorado sem erro.
 */
export function MuscleBodyMap({
  items,
  colors,
  legend,
}: {
  items: MuscleMapEntry[];
  colors: string[];
  /**
   * Mantido por compatibilidade com o mapa antigo (cor dos músculos sem
   * destaque). É IGNORADO: a arte base já traz a cor do corpo.
   * @deprecated sem efeito desde o mapa corporal v1.
   */
  bodyColor?: string;
  legend: Array<{ color: string; label: string }>;
}) {
  const [selectedSlug, setSelectedSlug] = useState<BodyMapSlug | null>(null);
  const [baseHref] = useState(initialBaseHref);

  const groupToSlugsQuery = useQuery({
    queryKey: MUSCLE_GROUP_BODY_MAP_QUERY_KEY,
    queryFn: fetchGroupToSlugs,
    staleTime: 5 * 60_000,
  });
  const groupToSlugs = groupToSlugsQuery.data;

  const regions = useMemo(
    () => resolveRegions(items, groupToSlugs ?? {}, colors),
    [items, groupToSlugs, colors],
  );

  if (groupToSlugsQuery.isLoading) {
    return <Loader size="sm" color="brand" />;
  }

  const selected = regions.find((region) => region.slug === selectedSlug);

  return (
    <Stack gap="md">
      <BodyMapFigure
        regions={regions}
        baseHref={baseHref}
        selectedSlug={selected ? selected.slug : null}
        onSelect={setSelectedSlug}
      />

      <div aria-live="polite">
        {selected && (
          <Stack gap={2}>
            {selected.entries.map((entry, index) => (
              <Text key={`${entry.muscle_group}-${index}`} ta="center" size="xs" c={BRAND.textPrimary}>
                <strong>{entry.tooltip}</strong>
              </Text>
            ))}
          </Stack>
        )}
      </div>

      <Group justify="center" gap="lg">
        {legend.map((entry) => (
          <Group key={entry.label} gap={6}>
            <div style={{ width: 14, height: 14, borderRadius: 4, background: entry.color }} />
            <Text size="xs" c={BRAND.textMuted}>
              {entry.label}
            </Text>
          </Group>
        ))}
      </Group>
    </Stack>
  );
}
