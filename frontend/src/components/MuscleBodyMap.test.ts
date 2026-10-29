import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { RECOVERY_BODY_MAP_COLORS } from '@/lib/dashboard-recovery';
import { statusLegend, statusVisual } from '@/lib/dashboard-status';
import { buildMuscleMapItems, MUSCLE_MAP_COLORS, MUSCLE_MAP_LEGEND } from '@/lib/workout-mode-types';
import { theme } from '@/theme';
import { BODYMAP_BASE_IMAGES, parseHexColor, tintMatrixValues } from './bodymap/bodymap';
import { MUSCLE_GROUP_BODY_MAP_QUERY_KEY, MuscleBodyMap, type MuscleMapEntry } from './MuscleBodyMap';

/**
 * Mapa corporal v1 renderizado no servidor (sem jsdom), com o
 * `GET /analytics/muscle-group-body-map` já no cache — os mesmos props que o
 * Modo Treino e o recovery passam.
 */

const GROUP_TO_SLUGS: Record<string, string[]> = {
  Peitoral: ['chest'],
  'Peitoral superior': ['chest'],
  Tríceps: ['triceps'],
  'Costas médio': ['upper-back'],
  'Glúteos/Quadril': ['gluteal', 'abductors'],
  Quadríceps: ['quadriceps'],
  Joelho: ['knees'],
  Inventado: ['slug-que-nao-existe'],
};

type Props = {
  items: MuscleMapEntry[];
  colors: string[];
  legend: Array<{ color: string; label: string }>;
  bodyColor?: string;
};

function render(props: Props, groupToSlugs: Record<string, string[]> | null = GROUP_TO_SLUGS): string {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (groupToSlugs) client.setQueryData(MUSCLE_GROUP_BODY_MAP_QUERY_KEY, groupToSlugs);
  return renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client },
      createElement(MantineProvider, { theme }, createElement(MuscleBodyMap, props)),
    ),
  );
}

/** Trecho do SVG de uma vista. */
function view(html: string, name: 'front' | 'back'): string {
  const start = html.indexOf(`data-bodymap-view="${name}"`);
  expect(start, `vista ${name}`).toBeGreaterThan(-1);
  return html.slice(start, html.indexOf('</svg>', start));
}

/** Camadas tingidas de uma vista: cor -> slugs. */
function layers(svg: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const match of svg.matchAll(/data-bodymap-layer="" data-color="([^"]+)" data-level="\d+" data-slugs="([^"]*)"/g)) {
    out[match[1]] = match[2];
  }
  return out;
}

function buttons(svg: string): string[] {
  return [...svg.matchAll(/role="button" tabindex="0" aria-label="([^"]+)" data-slug="([^"]+)"/g)].map(
    (m) => `${m[2]}|${m[1]}`,
  );
}

describe('MuscleBodyMap (Modo Treino: principal + secundário)', () => {
  const html = render({
    items: buildMuscleMapItems('Peitoral', 'Tríceps'),
    colors: MUSCLE_MAP_COLORS,
    legend: MUSCLE_MAP_LEGEND,
  });

  it('pinta o principal com colors[1] e o secundário com colors[0]', () => {
    expect(layers(view(html, 'front'))).toEqual({ '#facc15': 'triceps', '#ef4444': 'chest' });
    // O peitoral não aparece de costas; o tríceps aparece nas duas vistas.
    expect(layers(view(html, 'back'))).toEqual({ '#facc15': 'triceps' });
  });

  it('tinge com a matriz do compositor, em sRGB, sobre a arte base', () => {
    const front = view(html, 'front');
    expect(front).toContain(`values="${tintMatrixValues(parseHexColor('#ef4444')!)}"`);
    expect(front).toContain(`values="${tintMatrixValues(parseHexColor('#facc15')!)}"`);
    expect(front).toContain('color-interpolation-filters="sRGB"');
    expect(front).toContain('clip-rule="evenodd"');
    expect(front).toContain(`href="${BODYMAP_BASE_IMAGES.high.href}"`);
    // Recorte de cada figura (caixas do manifest).
    expect(html).toContain('viewBox="211 26 453 921"');
    expect(html).toContain('viewBox="831 26 483 920"');
  });

  it('cada região destacada é um botão acessível com o tooltip do item', () => {
    expect(buttons(view(html, 'front'))).toEqual([
      'chest|Peitoral (principal), frente',
      'triceps|Tríceps (secundário), frente',
    ]);
    expect(buttons(view(html, 'back'))).toEqual(['triceps|Tríceps (secundário), costas']);
    expect(html).toContain('aria-label="Mapa corporal: frente"');
    expect(html).toContain('aria-label="Mapa corporal: costas"');
    expect(html).toContain('<title>Peitoral (principal)</title>');
    expect(html).toContain('aria-live="polite"');
  });

  it('mostra a legenda', () => {
    expect(html).toContain('Principal');
    expect(html).toContain('Secundário');
    expect(html).toContain('Frente');
    expect(html).toContain('Costas');
  });
});

describe('MuscleBodyMap (recovery: escala de prontidão)', () => {
  // Igual a DashboardRecoveryClient: intensidade 1 = pronto ... 3 = evitar.
  const colors = RECOVERY_BODY_MAP_COLORS;
  const hex = (code: string) => statusVisual('readiness', code).hex;
  const items: MuscleMapEntry[] = [
    { muscle_group: 'Peitoral', intensity: 3, tooltip: 'Peitoral: Evitar trabalho direto (20%)' },
    { muscle_group: 'Costas médio', intensity: 2, tooltip: 'Costas médio: Dose reduzida (60%)' },
    { muscle_group: 'Quadríceps', intensity: 1, tooltip: 'Quadríceps: Pronto (100%)' },
    { muscle_group: 'Glúteos/Quadril', intensity: 1, tooltip: 'Glúteos/Quadril: Pronto (95%)' },
  ];
  const html = render({ items, colors, legend: statusLegend('readiness') });

  it('três cores diferentes, uma por estado', () => {
    expect(new Set(colors).size).toBe(3);
    expect(layers(view(html, 'front'))).toEqual({
      [hex('READY')]: 'adductor quadriceps',
      [hex('NO_DIRECT_WORK')]: 'chest',
    });
    expect(layers(view(html, 'back'))).toEqual({
      [hex('READY')]: 'adductor gluteal',
      [hex('REDUCED_DOSE')]: 'upper-back',
    });
  });

  it('alias abductors desenha a região do adutor', () => {
    expect(buttons(view(html, 'back'))).toContain('adductor|Glúteos/Quadril: Pronto (95%), costas');
  });

  it('legenda do domínio de prontidão', () => {
    for (const entry of statusLegend('readiness')) expect(html).toContain(entry.label);
  });
});

describe('MuscleBodyMap (casos de borda)', () => {
  const legend = MUSCLE_MAP_LEGEND;

  it('slug sem região, slug desconhecido e grupo fora do mapa são ignorados sem erro', () => {
    const html = render({
      items: [
        { muscle_group: 'Joelho', intensity: 2, tooltip: 'Joelho' },
        { muscle_group: 'Inventado', intensity: 2, tooltip: 'Inventado' },
        { muscle_group: 'Grupo sem mapa', intensity: 2, tooltip: 'Grupo sem mapa' },
      ],
      colors: MUSCLE_MAP_COLORS,
      legend,
    });
    expect(html).not.toContain('data-bodymap-layer');
    expect(html).not.toContain('role="button"');
    // A arte base aparece mesmo sem destaque.
    expect(html).toContain(BODYMAP_BASE_IMAGES.high.href);
  });

  it('dois grupos no mesmo slug: um botão, com os dois tooltips', () => {
    const html = render({
      items: [
        { muscle_group: 'Peitoral', intensity: 1, tooltip: 'Peitoral (secundário)' },
        { muscle_group: 'Peitoral superior', intensity: 2, tooltip: 'Peitoral superior (principal)' },
      ],
      colors: MUSCLE_MAP_COLORS,
      legend,
    });
    expect(layers(view(html, 'front'))).toEqual({ '#ef4444': 'chest' });
    expect(buttons(view(html, 'front'))).toEqual([
      'chest|Peitoral superior (principal); Peitoral (secundário), frente',
    ]);
  });

  it('bodyColor é aceito e não muda nada (a arte já tem cor)', () => {
    const props = { items: buildMuscleMapItems('Peitoral', null), colors: MUSCLE_MAP_COLORS, legend };
    expect(render({ ...props, bodyColor: '#123456' })).toBe(render(props));
  });

  it('cor que não é hex vira preenchimento chapado, sem quebrar', () => {
    const html = render({
      items: buildMuscleMapItems('Peitoral', null),
      colors: ['var(--x)', 'var(--y)'],
      legend,
    });
    expect(layers(view(html, 'front'))).toEqual({ 'var(--y)': 'chest' });
    expect(view(html, 'front')).toContain('fill="var(--y)"');
  });

  it('enquanto o mapa grupo -> slugs carrega, mostra o loader', () => {
    const html = render({ items: buildMuscleMapItems('Peitoral', null), colors: MUSCLE_MAP_COLORS, legend }, null);
    expect(html).not.toContain('data-bodymap-view');
    expect(html).toContain('mantine-Loader-root');
  });
});
