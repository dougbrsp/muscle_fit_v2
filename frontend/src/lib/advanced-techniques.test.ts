import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  ADVANCED_TECHNIQUES_PATH,
  FALLBACK_ADVANCED_TECHNIQUES,
  getAdvancedTechniqueApplicationNoun,
  getAdvancedTechniqueCatalog,
  getAdvancedTechniqueDescription,
  getAdvancedTechniqueLabel,
  getAdvancedTechniqueOptions,
  getAdvancedTechniqueSetType,
  isExtraRepsTechnique,
  loadAdvancedTechniques,
  resetAdvancedTechniqueCatalog,
  type AdvancedTechniqueCatalogItem,
} from './advanced-techniques';

// Consumo do catálogo `GET /advanced-techniques` (PR-06) e o fallback local.

const CODES = ['rest_pause', 'drop_set', 'fst7', 'gvt', 'forced_reps', 'negative_reps', 'partial_reps', 'cheat_reps'];

function apiItem(code: string, extra: Partial<AdvancedTechniqueCatalogItem> = {}): AdvancedTechniqueCatalogItem {
  const base = FALLBACK_ADVANCED_TECHNIQUES.find((item) => item.code === code);
  return { ...(base ?? FALLBACK_ADVANCED_TECHNIQUES[0]), code, ...extra };
}

function fetcherWith(items: unknown) {
  return vi.fn(async () => ({ items })) as unknown as <T>(path: string) => Promise<T>;
}

afterEach(() => resetAdvancedTechniqueCatalog());

describe('fallback local (antes da API responder)', () => {
  it('tem os 8 códigos do seed, todos ativos', () => {
    expect(FALLBACK_ADVANCED_TECHNIQUES.map((item) => item.code)).toEqual(CODES);
    expect(FALLBACK_ADVANCED_TECHNIQUES.every((item) => item.is_active)).toBe(true);
  });

  // Comportamento do Modo Treino congelado: mesmo set_type/família/rótulos de antes do catálogo.
  it.each([
    ['rest_pause', 'rest_pause', false, 'Mini-bloco', 'Rest-pause'],
    ['drop_set', 'drop_set', false, 'Drop', 'Drop-set'],
    ['fst7', 'working', false, 'Série', 'FST-7'],
    ['gvt', 'working', false, 'Série', 'GVT (German Volume Training)'],
    ['forced_reps', 'forced_rep', true, 'Repetição extra', 'Repetições forçadas'],
    ['negative_reps', 'forced_rep', true, 'Repetição extra', 'Negativas'],
    ['partial_reps', 'forced_rep', true, 'Repetição extra', 'Parciais'],
    ['cheat_reps', 'forced_rep', true, 'Repetição extra', 'Cheat reps'],
  ])('%s -> set_type %s, extra reps %s, "%s", "%s"', (code, setType, extraReps, noun, label) => {
    expect(getAdvancedTechniqueSetType(code)).toBe(setType);
    expect(isExtraRepsTechnique(code)).toBe(extraReps);
    expect(getAdvancedTechniqueApplicationNoun(code)).toBe(noun);
    expect(getAdvancedTechniqueLabel(code)).toBe(label);
    expect(getAdvancedTechniqueDescription(code)).toBeTruthy();
  });

  it('código desconhecido/nulo não quebra', () => {
    expect(getAdvancedTechniqueSetType('bi_set')).toBe('working');
    expect(isExtraRepsTechnique('bi_set')).toBe(false);
    expect(isExtraRepsTechnique(null)).toBe(false);
    expect(getAdvancedTechniqueApplicationNoun('bi_set')).toBe('Repetição extra');
    expect(getAdvancedTechniqueLabel('bi_set')).toBe('bi_set');
    expect(getAdvancedTechniqueLabel(null)).toBeNull();
    expect(getAdvancedTechniqueDescription('bi_set')).toBeNull();
  });
});

describe('loadAdvancedTechniques', () => {
  it('chama GET /advanced-techniques (com inativas) e passa a usar o catálogo da API', async () => {
    const fetcher = fetcherWith([
      apiItem('cheat_reps', { is_active: false }),
      apiItem('drop_set', { name: 'Drop-set (API)' }),
      apiItem('cluster_set', { name: 'Cluster set', set_type: 'rest_pause' }),
      apiItem('rest_pause'),
    ]);

    const result = await loadAdvancedTechniques(fetcher);

    expect(fetcher).toHaveBeenCalledWith(ADVANCED_TECHNIQUES_PATH);
    expect(ADVANCED_TECHNIQUES_PATH).toBe('/advanced-techniques?include_inactive=true');
    expect(result.isFallback).toBe(false);
    // ordem de exibição do builder; código novo do catálogo vai para o fim
    expect(result.items.map((item) => item.code)).toEqual(['rest_pause', 'drop_set', 'cheat_reps', 'cluster_set']);
    expect(getAdvancedTechniqueCatalog()).toBe(result.items);
    expect(getAdvancedTechniqueLabel('drop_set')).toBe('Drop-set (API)');
    // técnica nova funciona sem mudança de código no front
    expect(getAdvancedTechniqueSetType('cluster_set')).toBe('rest_pause');
    expect(getAdvancedTechniqueApplicationNoun('cluster_set')).toBe('Mini-bloco');
  });

  it.each([
    ['erro de rede/500', () => Promise.reject(new Error('boom'))],
    ['lista vazia', () => Promise.resolve({ items: [] })],
    ['resposta malformada', () => Promise.resolve({ items: [{ code: 1 }] })],
    ['sem items', () => Promise.resolve({})],
  ])('%s -> fallback local, sem rejeitar', async (_name, impl) => {
    const result = await loadAdvancedTechniques(impl as unknown as <T>(path: string) => Promise<T>);
    expect(result.isFallback).toBe(true);
    expect(result.items).toBe(FALLBACK_ADVANCED_TECHNIQUES);
    expect(getAdvancedTechniqueSetType('drop_set')).toBe('drop_set');
  });

  it('falha depois de um sucesso mantém o último catálogo bom', async () => {
    await loadAdvancedTechniques(fetcherWith([apiItem('rest_pause', { name: 'Rest-pause (API)' })]));
    const result = await loadAdvancedTechniques((() => Promise.reject(new Error('boom'))) as never);
    expect(result.isFallback).toBe(true);
    expect(getAdvancedTechniqueLabel('rest_pause')).toBe('Rest-pause (API)');
  });
});

describe('getAdvancedTechniqueOptions (Select do builder)', () => {
  const items = [apiItem('rest_pause'), apiItem('drop_set'), apiItem('cheat_reps', { is_active: false })];

  it('lista só técnicas ativas, com o nome do catálogo', () => {
    expect(getAdvancedTechniqueOptions(items)).toEqual([
      { value: 'rest_pause', label: 'Rest-pause' },
      { value: 'drop_set', label: 'Drop-set' },
    ]);
  });

  it('mantém a técnica já salva no item mesmo inativa ou fora do catálogo', () => {
    expect(getAdvancedTechniqueOptions(items, 'cheat_reps').at(-1)).toEqual({
      value: 'cheat_reps',
      label: 'Cheat reps (indisponível)',
    });
    expect(getAdvancedTechniqueOptions(items, 'bi_set').at(-1)).toEqual({ value: 'bi_set', label: 'bi_set (indisponível)' });
    expect(getAdvancedTechniqueOptions(items, 'drop_set')).toHaveLength(2);
  });
});
