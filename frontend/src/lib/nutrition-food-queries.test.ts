import { QueryClient } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';
import { foodKeys } from '@/lib/nutrition-api';
import { invalidateFoodQueries } from './nutrition-food-queries';

describe('invalidateFoodQueries', () => {
  it('invalida a busca e a lista por letra (inclusive a primeira carga sem letra)', async () => {
    const client = new QueryClient();
    const chaves = [foodKeys.search('arroz', 'visible', null), foodKeys.byLetterKey('A'), foodKeys.byLetterKey(undefined)];
    chaves.forEach((k) => client.setQueryData(k, {}));
    client.setQueryData(['outra'], {});
    await invalidateFoodQueries(client);
    expect(chaves.every((k) => client.getQueryState(k)?.isInvalidated)).toBe(true);
    expect(client.getQueryState(['outra'])?.isInvalidated).toBe(false);
  });
});
