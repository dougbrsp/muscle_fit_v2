import type { QueryClient } from '@tanstack/react-query';
import { foodKeys } from '@/lib/nutrition-api';

/** Alimento criado, editado, excluído ou favoritado: invalida a busca e a lista por letra (#208). */
export function invalidateFoodQueries(client: QueryClient): Promise<void> {
  return Promise.all([
    client.invalidateQueries({ queryKey: foodKeys.searchAll }),
    client.invalidateQueries({ queryKey: foodKeys.byLetter }),
  ]).then(() => undefined);
}
