import { describe, expect, it } from 'vitest';
import {
  BODYMAP_BASE_IMAGES,
  canonicalSlug,
  colorForLevel,
  intensityLevel,
  parseHexColor,
  pickBaseImage,
  resolveRegions,
  tintMatrixValues,
} from './bodymap';
import { BODYMAP_COMPOSITOR } from './regions.v1';

/** Aplica a matriz 4x5 do feColorMatrix a um pixel RGB (0-1), sem o clamp do navegador. */
function applyMatrix(values: string, [r, g, b]: [number, number, number]): number[] {
  const m = values.split(' ').map(Number);
  return [0, 1, 2].map((row) => m[row * 5] * r + m[row * 5 + 1] * g + m[row * 5 + 2] * b + m[row * 5 + 4]);
}

describe('canonicalSlug', () => {
  it('resolve alias e aceita os slugs com região', () => {
    expect(canonicalSlug('abductors')).toBe('adductor');
    expect(canonicalSlug('adductor')).toBe('adductor');
    expect(canonicalSlug('upper-back')).toBe('upper-back');
    expect(canonicalSlug(' Chest ')).toBe('chest');
  });

  it('slug sem região ou desconhecido vira null', () => {
    expect(canonicalSlug('knees')).toBeNull();
    expect(canonicalSlug('head')).toBeNull();
    expect(canonicalSlug('inexistente')).toBeNull();
    expect(canonicalSlug('constructor')).toBeNull();
  });
});

describe('intensidade -> cor', () => {
  it('nível é inteiro >= 1', () => {
    expect(intensityLevel(2)).toBe(2);
    expect(intensityLevel(0)).toBe(1);
    expect(intensityLevel(-3)).toBe(1);
    expect(intensityLevel(Number.NaN)).toBe(1);
    expect(intensityLevel(2.6)).toBe(3);
  });

  it('cor = colors[nível - 1], limitada à última (como a lib antiga)', () => {
    const colors = ['#111111', '#222222'];
    expect(colorForLevel(1, colors)).toBe('#111111');
    expect(colorForLevel(2, colors)).toBe('#222222');
    expect(colorForLevel(5, colors)).toBe('#222222');
    expect(colorForLevel(1, [])).toBeNull();
  });
});

describe('resolveRegions', () => {
  const colors = ['#facc15', '#ef4444'];

  it('mapeia grupo -> slugs e ignora o que não tem região', () => {
    const regions = resolveRegions(
      [
        { muscle_group: 'Peitoral', intensity: 2, tooltip: 'Peitoral (principal)' },
        { muscle_group: 'Joelho', intensity: 1, tooltip: 'Joelho' },
        { muscle_group: 'Sem mapa', intensity: 1, tooltip: 'Sem mapa' },
        { muscle_group: 'Glúteos/Quadril', intensity: 1, tooltip: 'Glúteos (secundário)' },
      ],
      { Peitoral: ['chest'], Joelho: ['knees', 'inexistente'], 'Glúteos/Quadril': ['gluteal', 'abductors'] },
      colors,
    );
    expect(regions.map((r) => [r.slug, r.color])).toEqual([
      ['adductor', '#facc15'],
      ['chest', '#ef4444'],
      ['gluteal', '#facc15'],
    ]);
  });

  it('dois grupos no mesmo slug: vale o maior nível e o detalhe lista os dois', () => {
    const [chest] = resolveRegions(
      [
        { muscle_group: 'Peitoral médio/inferior', intensity: 1, tooltip: 'Peitoral médio/inferior' },
        { muscle_group: 'Peitoral superior', intensity: 2, tooltip: 'Peitoral superior' },
      ],
      { 'Peitoral médio/inferior': ['chest'], 'Peitoral superior': ['chest'] },
      colors,
    );
    expect(chest.level).toBe(2);
    expect(chest.color).toBe('#ef4444');
    expect(chest.entries.map((e) => e.tooltip)).toEqual(['Peitoral superior', 'Peitoral médio/inferior']);
  });

  it('slug repetido no mesmo grupo (alias + alvo) conta uma vez', () => {
    const [adductor] = resolveRegions(
      [{ muscle_group: 'Adutores', intensity: 1, tooltip: 'Adutores' }],
      { Adutores: ['adductor', 'abductors'] },
      colors,
    );
    expect(adductor.entries).toHaveLength(1);
  });

  it('sem cores a região continua clicável, sem tinta', () => {
    const [chest] = resolveRegions([{ muscle_group: 'P', intensity: 1, tooltip: 'P' }], { P: ['chest'] }, []);
    expect(chest.slug).toBe('chest');
    expect(chest.color).toBeNull();
  });
});

describe('tinta (paridade com o compositor Pillow)', () => {
  it('lê hex de 3, 4, 6 e 8 dígitos; o resto vira null', () => {
    expect(parseHexColor('#ef4444')).toEqual([239, 68, 68]);
    expect(parseHexColor('#FFF')).toEqual([255, 255, 255]);
    expect(parseHexColor('#0f08')).toEqual([0, 255, 0]);
    expect(parseHexColor('#ff950080')).toEqual([255, 149, 0]);
    expect(parseHexColor('red')).toBeNull();
    expect(parseHexColor('var(--mantine-color-red-6)')).toBeNull();
  });

  it('matriz do demo.html: branco puro', () => {
    expect(tintMatrixValues([255, 255, 255])).toBe(
      [
        '0.301534 0.591975 0.114966 0 0.284181',
        '0.301534 0.591975 0.114966 0 0.284181',
        '0.301534 0.591975 0.114966 0 0.284181',
        '0 0 0 1 0',
      ].join(' '),
    );
  });

  it('tinta = cor × (K0 + K1 × n): K0 no tom mais escuro, a cor cheia no mais claro', () => {
    const color: [number, number, number] = [239, 68, 68];
    const values = tintMatrixValues(color);
    const { lumLo, lumHi, k0 } = BODYMAP_COMPOSITOR;
    const lo = lumLo / 255;
    const hi = lumHi / 255;
    const rgb01 = color.map((c) => c / 255);
    applyMatrix(values, [lo, lo, lo]).forEach((v, i) => expect(v).toBeCloseTo(rgb01[i] * k0, 4));
    applyMatrix(values, [hi, hi, hi]).forEach((v, i) => expect(v).toBeCloseTo(rgb01[i], 4));
  });
});

describe('pickBaseImage', () => {
  it('retina sempre recebe a arte cheia', () => {
    expect(pickBaseImage(390, 3)).toBe(BODYMAP_BASE_IMAGES.high);
    expect(pickBaseImage(1440, 2)).toBe(BODYMAP_BASE_IMAGES.high);
  });

  it('densidade 1 fica com a de 768 px (o mapa tem no máximo 480 px)', () => {
    expect(pickBaseImage(1920, 1)).toBe(BODYMAP_BASE_IMAGES.low);
    expect(pickBaseImage(360, 1)).toBe(BODYMAP_BASE_IMAGES.low);
  });

  it('sem medida confiável usa a arte cheia', () => {
    expect(pickBaseImage(0, 1)).toBe(BODYMAP_BASE_IMAGES.high);
    expect(pickBaseImage(Number.NaN, 2)).toBe(BODYMAP_BASE_IMAGES.high);
  });
});
